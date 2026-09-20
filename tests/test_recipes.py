"""Hermetic checks for recipe, search, log, suggestion, and consistency logic."""
from __future__ import annotations

import json
import os
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from pathlib import Path
import base64


HOME = Path(tempfile.mkdtemp(prefix="gusto-recipes-test-"))
os.environ["GUSTO_HOME"] = os.fspath(HOME)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from gusto import core  # noqa: E402


checks = 0


def check(condition, message):
    global checks
    assert condition, message
    checks += 1


def expect_valueerror(function, *args, **kwargs):
    try:
        function(*args, **kwargs)
    except ValueError:
        return
    raise AssertionError(f"expected ValueError from {function.__name__}")


def main():
    core.data_dir().mkdir(parents=True)
    core.categories_path().write_text(json.dumps({
        "cuisine": {"label": "Küche", "tags": ["italienisch", "indisch"]},
        "dish": {"label": "Art", "tags": ["pasta", "curry"]},
        "diet": {"label": "Ernährung", "tags": ["vegan"]},
    }), encoding="utf-8")

    pasta = core.add_recipe(
        "Pasta Pomodoro", tags=["italienisch", "pasta"], duration_min=20,
        servings=2, content="# Pasta Pomodoro\n\n## Zutaten\n\n- Tomaten\n",
    )
    curry = core.add_recipe(
        "Kichererbsen-Curry", tags=["indisch", "curry", "vegan"],
        duration_min=35, servings=4,
        content="# Kichererbsen-Curry\n\n## Zutaten\n\n- Kokosmilch\n",
    )
    quick = core.add_recipe(
        "Schneller Salat", tags=["vegan"], duration_min=10,
        content="# Schneller Salat\n\n## Zutaten\n\n- Gurke\n",
    )

    check(pasta.slug == "pasta-pomodoro", "title must become the expected slug")
    check(len(core.load_recipes()) == 3, "three recipes must round-trip")
    expect_valueerror(core.add_recipe, "Pasta Pomodoro")
    expect_valueerror(core.add_recipe, "   ")
    expect_valueerror(core.add_recipe, "Zeitreise", duration_min=0)
    expect_valueerror(core.add_recipe, "Hungrige Runde", servings=-1)
    # An explicitly given slug must stay a plain, non-hidden file name:
    # path parts or a leading dot could write Markdown outside recipes/.
    for invalid_slug in ("../evil", "a/b", ".versteckt", "..", ""):
        expect_valueerror(core.add_recipe, "Böser Slug", slug=invalid_slug)
    expect_valueerror(core.add_recipe, "Falscher Typ", slug=5)
    check(core.get("evil") is None
          and not (core.project_root() / "evil.md").exists(),
          "a rejected path slug must not write outside the recipes folder")

    check([r.slug for r in core.search("kokosmilch")] == [curry.slug],
          "full-text search must include recipe content")
    check({r.slug for r in core.search(tags=["italienisch", "indisch"])}
          == {pasta.slug, curry.slug}, "tags within one facet must use OR")
    check([r.slug for r in core.search(tags=["indisch", "curry"])] == [curry.slug],
          "tags across facets must use AND")
    check([r.slug for r in core.search(max_time=15)] == [quick.slug],
          "maximum duration must filter recipes")
    expect_valueerror(core.search, max_time=0)
    expect_valueerror(core.search, max_time=-1)
    expect_valueerror(core.search, "kokosmilch", "alle")
    expect_valueerror(core.search, "kokosmilch", "ALL")
    expect_valueerror(core.search, None)
    check(core.uncategorized_tags(["VEGAN", "saisonal", "saisonal"])
          == ["saisonal"],
          "uncategorized tags must be distinct and category matching case-insensitive")

    # Agents can maintain the complete facet assignment through Core instead
    # of editing categories.json behind the running service.
    core.update_recipe(quick.slug, tags=["vegan", "saisonal"])
    season = core.add_category("season", "Jahreszeit", position=2)
    check(season == {"key": "season", "label": "Jahreszeit", "tags": []}
          and list(core.load_categories())[1] == "season",
          "category creation must preserve an explicit display position")
    season = core.update_category("season", label="Saison", position=1)
    check(season["label"] == "Saison"
          and list(core.load_categories())[0] == "season",
          "category label and position must update in one transaction")
    season = core.assign_category_tags(
        "season", ["saisonal", "regional", "SAISONAL"],
    )
    check(season["tags"] == ["saisonal", "regional"]
          and core.uncategorized_tags(["saisonal"]) == [],
          "assignment must deduplicate input and resolve uncategorized tags")
    moved = core.move_category_tag("season", "regional", 1)
    check(moved["tags"] == ["regional", "saisonal"],
          "tag display order must be agent-controllable")
    check(core.unassign_category_tags(["REGIONAL", "regional"])
          == {"unassigned": ["regional"]},
          "unassign must be case-insensitive and idempotent within one request")
    removed_category = core.remove_category("season")
    check(removed_category["removed"] is True
          and removed_category["now_uncategorized"] == ["saisonal"],
          "removing a category must report affected used tags")
    core.add_category("season", "Saison")
    core.assign_category_tags("season", ["saisonal"])
    expect_valueerror(core.add_category, "season", "Doppelt")
    expect_valueerror(core.add_category, "Other", "Reserviert")
    expect_valueerror(core.update_category, "missing", label="Fehlt")
    expect_valueerror(core.move_category_tag, "season", "saisonal", 0)
    expect_valueerror(core._validated_categories, {
        "one": {"label": "Eins", "tags": ["gleich"]},
        "two": {"label": "Zwei", "tags": ["GLEICH"]},
    })
    expect_valueerror(core._validated_categories, {
        "one": {"label": "Eins", "tags": []},
        " one ": {"label": "Doppelt", "tags": []},
    })
    preserved = core.assign_category_tags("season", ["SommerTag"])
    check("SommerTag" in preserved["tags"],
          "an unused new tag must preserve the spelling supplied by the agent")

    updated = core.update_recipe(
        quick.slug, title="Gurkensalat", tags=["vegan"], duration_min=12,
        servings=2, content="# Gurkensalat\n",
    )
    check(updated.title == "Gurkensalat" and updated.duration_min == 12,
          "recipe metadata must update")
    check(updated.content() == "# Gurkensalat\n", "recipe content must update")
    renamed = core.update_recipe(quick.slug, title="Gurkensalat Deluxe")
    check(renamed.content().startswith("# Gurkensalat Deluxe\n"),
          "renaming metadata must update the canonical Markdown H1")
    cleared = core.update_recipe(
        quick.slug, clear_duration=True, clear_servings=True,
    )
    check(cleared.duration_min is None and cleared.servings is None,
          "optional duration and servings must be removable")
    check([r.slug for r in core.search(max_time=15)] == [],
          "maximum duration must exclude recipes with unknown duration")
    expect_valueerror(
        core.update_recipe, quick.slug, duration_min=10, clear_duration=True,
    )
    expect_valueerror(core.update_recipe, quick.slug, title="  ")

    # Titles must stay single-line: a newline or carriage return would desync
    # the metadata title from the Markdown H1 and trip the hard title check.
    store_before = core.check()
    expect_valueerror(core.add_recipe, "A\nB")
    check(core.check() == store_before and core.get("a-b") is None,
          "a rejected title must not create a recipe or change the store")
    quick_before = core.get(quick.slug).to_dict()
    quick_content_before = core.get(quick.slug).content()
    expect_valueerror(core.update_recipe, quick.slug, title="A\rB")
    expect_valueerror(core.update_recipe, quick.slug, title="A\nB")
    check(core.get(quick.slug).to_dict() == quick_before
          and core.get(quick.slug).content() == quick_content_before,
          "a rejected title update must leave the recipe unchanged")

    # Recipe tags are normalized like category tags: one-line strings,
    # case-insensitively deduplicated with the first spelling kept.
    duplicate_tags = core.add_recipe("Doppel-Tags", tags=["vegan", "vegan"])
    check(duplicate_tags.tags == ["vegan"],
          "duplicate recipe tags must be stored once")
    spelled_tags = core.add_recipe(
        "Schreibweise-Tags", tags=["Vegan", "vegan"])
    check(spelled_tags.tags == ["Vegan"],
          "tag deduplication must keep the first spelling")
    clean_store = core.check()
    check(clean_store["ok"] is True and clean_store["uncategorized_tags"] == [],
          "valid recipe tags must keep the store consistent")
    expect_valueerror(core.add_recipe, "Zeilen-Tag", tags=["a\nb"])
    expect_valueerror(core.add_recipe, "Zahlen-Tag", tags=[1])
    check(core.check() == clean_store and core.get("zeilen-tag") is None
          and core.get("zahlen-tag") is None,
          "rejected tag lists must not create recipes")
    tidied = core.update_recipe(duplicate_tags.slug, tags=["a", "A"])
    check(tidied.tags == ["a"],
          "recipe tag updates must deduplicate like category tags")
    check(core.check()["uncategorized_tags"] == ["a"],
          "tag deduplication must not duplicate uncategorized entries")
    cleared_tags = core.update_recipe(spelled_tags.slug, tags=[])
    check(cleared_tags.tags == [],
          "an empty tag list must still clear the tags")
    expect_valueerror(core.update_recipe, duplicate_tags.slug, tags="vegan")
    check(core.get(duplicate_tags.slug).tags == ["a"],
          "a non-list tag update must leave the recipe unchanged")

    # Remove the experiment recipes again so the rest of the suite starts
    # from the original store; archive plus purge is the permanent path.
    for experiment in (duplicate_tags, spelled_tags):
        core.archive_recipe(experiment.slug)
        core.purge_archived_recipe(experiment.slug)
    check(core.check() == store_before,
          "the title and tag experiments must leave the store unchanged")

    # Every slug that slugify produces must remain a valid explicit slug.
    for sample in ("Käse-Soufflé", "Übungs-ß-Rezept", "100% Curry!!",
                   "Nur Punkt-Slug", "..."):
        derived = core.slugify(sample)
        created = core.add_recipe(f"Beliebiger Titel für {sample}", slug=derived)
        check(created.slug == derived,
              "every slugify output must remain accepted as an explicit slug")
        core.purge_archived_recipe(core.archive_recipe(created.slug).slug)

    # Multiple stored images, free roles/captions, and one selected cover.
    first_source = HOME / "finished.png"
    second_source = HOME / "step.jpg"
    pixel = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
    )
    first_source.write_bytes(pixel)
    second_source = HOME / "step.png"
    second_source.write_bytes(pixel)
    finished = core.add_recipe_image(
        pasta.slug, first_source, role="result", caption="Fertig angerichtet",
    )
    check(core.get(pasta.slug).cover_image_id == finished.id,
          "the first image must persist its id as the selected cover")
    step = core.add_recipe_image(
        pasta.slug, second_source, role="step", caption="Sauce einrühren",
    )
    images, cover_id = core.list_recipe_images(pasta.slug)
    check(len(images) == 2 and cover_id == finished.id,
          "the first image must become the default cover")
    check(core.recipe_image_path(pasta.slug, step).is_file(),
          "image files must be copied into Gusto storage")
    core.set_recipe_cover(pasta.slug, step.id)
    changed_image = core.update_recipe_image(
        pasta.slug, step.id, role="technique", caption="Vom Herd nehmen",
    )
    check(changed_image.role == "technique" and changed_image.caption == "Vom Herd nehmen",
          "image role and caption must be editable")
    check(core.get(pasta.slug).cover_image.id == step.id,
          "an arbitrary stored image must be selectable as cover")
    fallback_cover = core.remove_recipe_image(pasta.slug, step.id)
    check(fallback_cover == finished.id and not core.recipe_image_path(pasta.slug, step).exists(),
          "removing the cover must select the first remaining image")
    expect_valueerror(core.add_recipe_image, pasta.slug, HOME / "missing.png")
    fake_source = HOME / "fake.png"
    fake_source.write_bytes(b"not an image")
    expect_valueerror(core.add_recipe_image, pasta.slug, fake_source)
    # Store hygiene: role and caption are persisted as single lines (matching
    # normalizes whitespace anyway), so the write paths must reject breaks.
    expect_valueerror(core.add_recipe_image, pasta.slug, second_source, role="a\nb")
    expect_valueerror(core.add_recipe_image, pasta.slug, second_source, caption="a\nb")
    expect_valueerror(core.update_recipe_image, pasta.slug, finished.id, caption="a\nb")
    expect_valueerror(core.update_recipe_image, pasta.slug, finished.id, role="a\rb")
    check(core.get_recipe_image(pasta.slug, finished.id).caption == "Fertig angerichtet",
          "a rejected multiline caption must not mutate the stored image")

    recent_cook = (date.today() - timedelta(days=1)).isoformat()
    older_cook = (date.today() - timedelta(days=3)).isoformat()
    core.log_cooked(pasta.slug, recent_cook)
    core.log_cooked(pasta.slug, older_cook)
    check(core.get(pasta.slug).last_cooked == recent_cook,
          "an older log entry must not regress last_cooked")
    check([entry["date"] for entry in core.load_log()] == [older_cook, recent_cook],
          "the log must stay chronologically sorted")
    expect_valueerror(core.log_cooked, "missing")
    for invalid_date in ["2026-13-45", "15.07.2026", "20260713"]:
        expect_valueerror(core.log_cooked, pasta.slug, invalid_date)
    expect_valueerror(
        core.log_cooked, pasta.slug, (date.today() + timedelta(days=1)).isoformat(),
    )
    check([entry["date"] for entry in core.load_log()] == [older_cook, recent_cook],
          "invalid or future cooking dates must not mutate the log")
    expect_valueerror(core.load_log, days=0)
    expect_valueerror(core.load_log, days=-1)

    # A failing index write must roll the created Markdown back, and a failing
    # catalog write must roll the new log entry back.
    def _failing_save(recipes):
        raise ValueError("simulierter Schreibfehler")
    original_save = core._save_recipes_unlocked
    core._save_recipes_unlocked = _failing_save
    try:
        expect_valueerror(core.add_recipe, "Rollback Rezept")
        check(not core.recipe_file("rollback-rezept").exists()
              and core.get("rollback-rezept") is None,
              "a failed index write must roll back the created recipe Markdown")
        expect_valueerror(core.log_cooked, pasta.slug)
        check([entry["date"] for entry in core.load_log()]
              == [older_cook, recent_cook],
              "a failed catalog write must roll back the new log entry")
        check(core.get(pasta.slug).last_cooked == recent_cook,
              "a failed catalog write must leave last_cooked unchanged")
    finally:
        core._save_recipes_unlocked = original_save

    # Delete is reversible archive: Markdown, metadata and owned images move
    # together while log and shopping keep resolving the slug.
    sourced = core.shopping_add("Tomaten", source=pasta.slug)
    archived = core.archive_recipe(pasta.slug)
    check(core.get(pasta.slug) is None and core.get_archived(pasta.slug) is not None,
          "archiving must remove the recipe only from the active catalog")
    check(archived.content().startswith("# Pasta Pomodoro")
          and core.archive_recipe_file(pasta.slug).is_file(),
          "the archive must retain the recipe Markdown")
    archived_image = core.get_archived_recipe_image(pasta.slug, finished.id)
    check(archived_image is not None
          and core.archived_recipe_image_path(pasta.slug, archived_image).is_file(),
          "the archive must retain owned recipe images")
    check(core.recipe_references()[pasta.slug]["archived"] is True
          and core.shopping_list()[0].source == pasta.slug
          and any(entry["slug"] == pasta.slug for entry in core.load_log()),
          "shopping and log references must survive archiving")
    archived_check = core.check()
    check(archived_check["archive_count"] == 1 and archived_check["ok"] is True,
          "a complete archived snapshot must pass the full integrity check")
    expect_valueerror(core.purge_archived_recipe, pasta.slug)
    restored = core.restore_archived_recipe(pasta.slug)
    check(restored.slug == pasta.slug and core.get_archived(pasta.slug) is None,
          "restore must return the complete snapshot to the active catalog")
    check(core.recipe_file(pasta.slug).is_file()
          and core.recipe_image_path(pasta.slug, finished).is_file()
          and core.recipe_references()[pasta.slug]["archived"] is False,
          "restore must reconnect Markdown, images and recipe references")
    core.shopping_remove(sourced.id)

    candidates = {r.slug for r in core.suggest(days=7)}
    check(pasta.slug not in candidates and curry.slug in candidates,
          "suggestions must exclude recently cooked recipes")
    check(core.suggest(days=7, limit=0) == [],
          "a zero suggestion limit must return no recipes")
    check(len(core.suggest(days=7, limit=1)) == 1,
          "a positive suggestion limit must cap the result")
    expect_valueerror(core.suggest, 7, -1)
    expect_valueerror(core.suggest, 0)

    initial = core.check()
    check(not initial["orphaned_files"] and not initial["missing_files"],
          "freshly created recipes must be consistent")
    check(initial["uncategorized_tags"] == [], "known tags must be categorized")
    check(not initial["missing_image_files"] and not initial["orphaned_image_files"],
          "stored image metadata and files must be consistent")
    check(initial["ok"] is True and not initial["errors"],
          "a consistent store must expose an explicit successful check result")

    # Corrupted data files become German ValueErrors, and check() reports them
    # as integrity errors instead of crashing with a raw loader exception.
    index_backup = core.index_path().read_text(encoding="utf-8")
    core.index_path().write_text('{"kaputt": true}', encoding="utf-8")
    try:
        expect_valueerror(core.load_recipes)
        corrupted = core.check()
        check(any("recipes.json" in message
                  for message in corrupted["invalid_data_files"]),
              "a corrupted catalog index must be reported as a data-file error")
        check(corrupted["ok"] is False,
              "a corrupted catalog index must be a hard error")
    finally:
        core.index_path().write_text(index_backup, encoding="utf-8")
    log_backup = core.log_path().read_text(encoding="utf-8")
    core.log_path().write_text("[kein json]", encoding="utf-8")
    try:
        expect_valueerror(core.load_log)
        corrupted = core.check()
        check(any("log.json" in message
                  for message in corrupted["invalid_data_files"]),
              "a corrupted cooking log must be reported as a data-file error")
    finally:
        core.log_path().write_text(log_backup, encoding="utf-8")
    check(core.check()["ok"] is True,
          "restored data files must check cleanly again")

    # Regression: persisted entries with broken field types must surface as
    # integrity errors, not as raw KeyError/AttributeError loader crashes.
    index_backup = core.index_path().read_text(encoding="utf-8")
    try:
        for broken_index in (
            '[{"slug": "x", "title": null}]',
            '[{"slug": "x", "title": "T", "images": {}}]',
            '[{"slug": "x", "title": "T", "images": [{"id": 5, "filename": "x.png"}]}]',
        ):
            core.index_path().write_text(broken_index, encoding="utf-8")
            expect_valueerror(core.load_recipes)
            corrupted = core.check()
            check(any("recipes.json" in message
                      for message in corrupted["invalid_data_files"]),
                  "broken recipe entry field types must be a data-file error")
    finally:
        core.index_path().write_text(index_backup, encoding="utf-8")
    log_backup = core.log_path().read_text(encoding="utf-8")
    try:
        core.log_path().write_text(
            json.dumps([{"slug": pasta.slug}]), encoding="utf-8")
        expect_valueerror(core.load_log)
        corrupted = core.check()
        check(any("log.json" in message
                  for message in corrupted["invalid_data_files"]),
              "a log entry without 'date' must be reported as a data-file error")
    finally:
        core.log_path().write_text(log_backup, encoding="utf-8")

    # The 150-character title cap keeps the atomic writer's ".<slug>.md.<tmp>"
    # name inside the Windows filename limit (verified live: longer titles
    # crashed recipe.create with a raw OSError on save).
    long_title = "L" * 150
    long_recipe = core.add_recipe(long_title)
    check(long_recipe.title == long_title,
          "a 150-character title must stay accepted")
    expect_valueerror(core.add_recipe, "K" * 151)
    expect_valueerror(core.update_recipe, long_recipe.slug, title="K" * 151)
    check(core.get(long_recipe.slug).title == long_title,
          "a rejected overlong title must not mutate the recipe")

    (core.recipes_dir() / "orphan.md").write_text("# Orphan\n", encoding="utf-8")
    core.recipe_file(curry.slug).unlink()
    recipes = core.load_recipes()
    pasta_record = next(recipe for recipe in recipes if recipe.slug == pasta.slug)
    pasta_record.tags.append("unbekannt")
    pasta_record.cover_image_id = "missing-cover"
    core.save_recipes(recipes)
    core.recipe_image_path(pasta.slug, finished).unlink()
    (core.recipe_images_dir(pasta.slug) / "orphan.png").write_bytes(b"orphan")
    (core.images_dir() / "missing-recipe").mkdir(parents=True)
    core.recipe_file(quick.slug).write_text("# Falscher Titel\n", encoding="utf-8")
    broken = core.check()
    check(broken["orphaned_files"] == ["orphan"], "orphaned Markdown must be found")
    check(broken["missing_files"] == [curry.slug], "missing Markdown must be found")
    check(broken["uncategorized_tags"] == ["unbekannt"],
          "uncategorized tags must be found")
    check(broken["missing_image_files"] == [f"{pasta.slug}/{finished.filename}"],
          "missing stored image files must be found")
    check(broken["orphaned_image_files"] == [f"{pasta.slug}/orphan.png"],
          "unreferenced stored image files must be found")
    check(broken["orphaned_image_folders"] == ["missing-recipe"],
          "image folders without recipes must be found")
    check(broken["invalid_cover_images"] == [pasta.slug],
          "cover ids must refer to stored image metadata")
    check(broken["title_mismatches"] == [quick.slug] and broken["ok"] is False,
          "title drift must be a hard consistency error")

    archived = core.delete_recipe(pasta.slug)
    check(core.get(pasta.slug) is None and not core.recipe_file(pasta.slug).exists(),
          "delete must remove the recipe from the active catalog")
    check(not core.recipe_images_dir(pasta.slug).exists(),
          "delete must move all owned images out of active storage")
    check(archived.slug == pasta.slug and core.archive_recipe_file(pasta.slug).is_file()
          and core.archive_images_dir(pasta.slug).is_dir(),
          "delete must be the reversible archive operation")
    expect_valueerror(core.delete_recipe, pasta.slug)
    core.purge_archived_recipe(pasta.slug)
    check(core.get_archived(pasta.slug) is None,
          "explicit purge must permanently remove the archived snapshot")

    # Purge parks the snapshot in a hidden transaction folder before the
    # recursive removal, so an interrupted purge can never leave a half-deleted
    # visible archive entry behind.
    purge_target = core.add_recipe("Purge-Ziel", content="# Purge-Ziel\n")
    core.archive_recipe(purge_target.slug)
    core.purge_archived_recipe(purge_target.slug)
    check(core.get_archived(purge_target.slug) is None
          and not any(path.name.startswith(".")
                      for path in core.archive_dir().iterdir()),
          "a completed purge must leave no transaction leftovers")
    interrupt_target = core.add_recipe("Abbruch-Purge", content="# Abbruch-Purge\n")
    core.archive_recipe(interrupt_target.slug)
    original_rmtree = core.shutil.rmtree
    def _failing_rmtree(path, *args, **kwargs):
        raise OSError("simulierter Abbruch")
    core.shutil.rmtree = _failing_rmtree
    try:
        try:
            core.purge_archived_recipe(interrupt_target.slug)
            raise AssertionError("purge must fail when the snapshot removal fails")
        except OSError:
            pass
    finally:
        core.shutil.rmtree = original_rmtree
    leftovers = sorted(path.name for path in core.archive_dir().iterdir()
                       if path.name.startswith("."))
    check(len(leftovers) == 1 and core.get_archived(interrupt_target.slug) is None,
          "an interrupted purge must park the snapshot in a hidden folder")
    interrupted_check = core.check()
    check(interrupted_check["stale_archive_transactions"] == leftovers
          and not interrupted_check["invalid_archive_entries"]
          and not interrupted_check["missing_archive_files"],
          "an interrupted purge must be visible as a stale transaction, "
          "not as a broken archive entry")
    core.shutil.rmtree(core.archive_dir() / leftovers[0])
    check(core.check()["stale_archive_transactions"] == [],
          "removing the stale transaction folder must clear the check finding")

    # The persisted change journal is monotone across concurrent transactions,
    # while reads and idempotent PWA merges stay silent.
    before_parallel = core.change_state()["revision"]
    with ThreadPoolExecutor(max_workers=2) as executor:
        created = list(executor.map(core.shopping_add, ["Parallel A", "Parallel B"]))
    events = core.change_events(before_parallel)
    check(
        [event["revision"] for event in events]
        == [before_parallel + 1, before_parallel + 2],
        "parallel mutations must retain two distinct monotone revisions",
    )
    check(
        all(event["resources"] == ["shopping"] for event in events),
        "change events must identify their affected resource",
    )
    after_parallel = core.change_state()["revision"]
    core.shopping_merge([item.to_dict() for item in core.shopping_load()])
    check(
        core.change_state()["revision"] == after_parallel,
        "an idempotent full-state merge must not emit a change event",
    )
    for item in created:
        core.shopping_remove(item.id)

    print(f"OK - {checks} recipe core checks passed (GUSTO_HOME={HOME})")


if __name__ == "__main__":
    main()
