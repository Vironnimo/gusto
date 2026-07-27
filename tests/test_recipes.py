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
    check(core.uncategorized_tags(["VEGAN", "saisonal", "saisonal"])
          == ["saisonal"],
          "uncategorized tags must be distinct and category matching case-insensitive")

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

    (core.recipes_dir() / "orphan.md").write_text("# Orphan\n", encoding="utf-8")
    core.recipe_file(curry.slug).unlink()
    recipes = core.load_recipes()
    pasta_record = next(recipe for recipe in recipes if recipe.slug == pasta.slug)
    pasta_record.tags.append("saisonal")
    pasta_record.cover_image_id = "missing-cover"
    core.save_recipes(recipes)
    core.recipe_image_path(pasta.slug, finished).unlink()
    (core.recipe_images_dir(pasta.slug) / "orphan.png").write_bytes(b"orphan")
    (core.images_dir() / "missing-recipe").mkdir(parents=True)
    core.recipe_file(quick.slug).write_text("# Falscher Titel\n", encoding="utf-8")
    broken = core.check()
    check(broken["orphaned_files"] == ["orphan"], "orphaned Markdown must be found")
    check(broken["missing_files"] == [curry.slug], "missing Markdown must be found")
    check(broken["uncategorized_tags"] == ["saisonal"],
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
