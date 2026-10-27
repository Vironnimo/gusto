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
from unittest.mock import patch


sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import gusto  # noqa: E402, F401 - configure checkout temp before allocating data

HOME = Path(tempfile.mkdtemp(prefix="gusto-recipes-test-"))
os.environ["GUSTO_HOME"] = os.fspath(HOME)

import gusto.core.catalog.archive as core_archive  # noqa: E402
import gusto.core.catalog.categories as core_categories  # noqa: E402
import gusto.core.catalog.images as core_images  # noqa: E402
import gusto.core.catalog.recipes as core_recipes  # noqa: E402
import gusto.core.catalog.search as core_search  # noqa: E402
import gusto.core.catalog.snapshots as core_snapshots  # noqa: E402
import gusto.core.checks as core_checks  # noqa: E402
import gusto.core.errors as core_errors  # noqa: E402
import gusto.core.records as core_records  # noqa: E402
import gusto.core.shopping as core_shopping  # noqa: E402
import gusto.core.storage as core_storage  # noqa: E402


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


def transaction_failure_checks():
    """Distinguish complete rollback, failed rollback, and committed results."""
    for operation in ("add", "update", "log", "archive", "restore"):
        for failure in ("before-index", "rollback", "after-index"):
            with tempfile.TemporaryDirectory(prefix="transactions-", dir=HOME) as home:
                with patch.dict(os.environ, {"GUSTO_HOME": home}):
                    slug = "transaktion"
                    if operation != "add":
                        core_recipes.add_recipe("Transaktion", slug=slug)
                    if operation == "log":
                        core_recipes.log_cooked(slug, (date.today() - timedelta(days=1)).isoformat())
                    if operation == "restore":
                        core_archive.archive_recipe(slug)
                    actions = {
                        "add": lambda: core_recipes.add_recipe("Transaktion", slug=slug),
                        "update": lambda: core_recipes.update_recipe(slug, title="Geändert"),
                        "log": lambda: core_recipes.log_cooked(slug),
                        "archive": lambda: core_archive.archive_recipe(slug),
                        "restore": lambda: core_archive.restore_archived_recipe(slug),
                    }

                    def business_files():
                        return {
                            path.relative_to(home).as_posix(): path.read_bytes()
                            for path in Path(home).rglob("*") if path.is_file()
                            and path != core_storage.changes_path() and path.suffix != ".lock"
                        }

                    before = business_files()
                    revision = core_storage.change_state()["revision"]
                    original_save = core_recipes._save_recipes_unlocked
                    original_unlink = Path.unlink
                    original_replace = Path.replace
                    original_text = core_storage.write_text
                    original_json = core_storage.write_json
                    index_failed = False

                    def failing_index(recipes):
                        nonlocal index_failed
                        if failure == "after-index":
                            original_save(recipes)
                        index_failed = True
                        raise OSError("index failure")

                    def failing_unlink(path, *args, **kwargs):
                        if (failure == "rollback" and index_failed and operation == "add"
                                and path == core_storage.recipe_file(slug)):
                            raise OSError("rollback failure")
                        return original_unlink(path, *args, **kwargs)

                    def failing_replace(path, *args, **kwargs):
                        if failure == "rollback" and index_failed:
                            if ((operation == "archive" and path == core_storage.archive_recipe_file(slug))
                                    or (operation == "restore" and path == core_storage.recipe_file(slug))):
                                raise OSError("rollback failure")
                        return original_replace(path, *args, **kwargs)

                    def failing_text(path, *args, **kwargs):
                        if (failure == "rollback" and index_failed and operation == "update"
                                and path == core_storage.recipe_file(slug)):
                            raise OSError("rollback failure")
                        return original_text(path, *args, **kwargs)

                    def failing_json(path, *args, **kwargs):
                        if (failure == "rollback" and index_failed and operation == "log"
                                and path == core_storage.log_path()):
                            raise OSError("rollback failure")
                        return original_json(path, *args, **kwargs)

                    with patch.object(core_recipes, "_save_recipes_unlocked", failing_index), \
                            patch.object(Path, "unlink", failing_unlink), \
                            patch.object(Path, "replace", failing_replace), \
                            patch.object(core_storage, "write_text", failing_text), \
                            patch.object(core_storage, "write_json", failing_json):
                        try:
                            actions[operation]()
                        except Exception as error:
                            expected = OSError if failure == "before-index" else core_errors.PostMutationError
                            check(type(error) is expected,
                                  f"{operation}/{failure}: failure must reflect whether effects remain")
                        else:
                            raise AssertionError(f"{operation}/{failure}: expected injected failure")

                    if failure == "before-index":
                        check(business_files() == before
                              and core_storage.change_state()["revision"] == revision,
                              f"{operation}: complete rollback must restore all files without an event")
                    else:
                        check(business_files() != before
                              and core_storage.change_state()["revision"] == revision + 1,
                              f"{operation}/{failure}: remaining effects must publish a change")
                    if failure == "after-index":
                        if operation == "archive":
                            check(core_recipes.get(slug) is None and core_snapshots.get_archived(slug) is not None,
                                  "an already committed archive must not move its files back")
                        else:
                            saved = core_recipes.get(slug)
                            check(saved is not None and saved.path.is_file(),
                                  f"{operation}: a committed recipe must retain its Markdown")
                            if operation == "update":
                                check(saved.title == "Geändert"
                                      and saved.content().startswith("# Geändert\n"),
                                      "a committed title update must not restore the previous Markdown")
                            if operation == "log":
                                check(saved.last_cooked == date.today().isoformat()
                                      and len(core_recipes.load_log()) == 2,
                                      "a committed cooking entry must not lose its log record")


def catalog_fixture():
    core_storage.data_dir().mkdir(parents=True)
    core_storage.categories_path().write_text(json.dumps({
        "cuisine": {"label": "Küche", "tags": ["italienisch", "indisch"]},
        "dish": {"label": "Art", "tags": ["pasta", "curry"]},
        "diet": {"label": "Ernährung", "tags": ["vegan"]},
    }), encoding="utf-8")

    pasta = core_recipes.add_recipe(
        "Pasta Pomodoro", tags=["italienisch", "pasta"], duration_min=20,
        servings=2, content="# Pasta Pomodoro\n\n## Zutaten\n\n- Tomaten\n",
    )
    curry = core_recipes.add_recipe(
        "Kichererbsen-Curry", tags=["indisch", "curry", "vegan"],
        duration_min=35, servings=4,
        content="# Kichererbsen-Curry\n\n## Zutaten\n\n- Kokosmilch\n",
    )
    quick = core_recipes.add_recipe(
        "Schneller Salat", tags=["vegan"], duration_min=10,
        content="# Schneller Salat\n\n## Zutaten\n\n- Gurke\n",
    )

    return pasta, curry, quick


def creation_checks():
    pasta, _, _ = catalog_fixture()
    check(pasta.slug == "pasta-pomodoro", "title must become the expected slug")
    check(len(core_recipes.load_recipes()) == 3, "three recipes must round-trip")
    expect_valueerror(core_recipes.add_recipe, "Pasta Pomodoro")
    expect_valueerror(core_recipes.add_recipe, "   ")
    expect_valueerror(core_recipes.add_recipe, "Zeitreise", duration_min=0)
    expect_valueerror(core_recipes.add_recipe, "Hungrige Runde", servings=-1)
    # An explicitly given slug must stay a plain, non-hidden file name:
    # path parts or a leading dot could write Markdown outside recipes/.
    for invalid_slug in (
        "../evil", "a/b", "a\\b", ".versteckt", "..", "",
        "con", "NUL", "com1", "a:b", "trailing.", "trailing ",
        "_favorites", "a" * 151,
    ):
        expect_valueerror(core_recipes.add_recipe, "Böser Slug", slug=invalid_slug)
    expect_valueerror(core_recipes.add_recipe, "Falscher Typ", slug=5)
    check(core_recipes.get("evil") is None
          and not (core_storage.project_root() / "evil.md").exists(),
          "a rejected path slug must not write outside the recipes folder")

    orphan = core_storage.recipe_file("geschuetzte-datei")
    orphan_content = "# Handschriftlich übertragen\n\nDiesen Inhalt erhalten.\n"
    orphan.write_text(orphan_content, encoding="utf-8")
    expect_valueerror(core_recipes.add_recipe, "Geschützte Datei")
    check(orphan.read_text(encoding="utf-8") == orphan_content
          and core_recipes.get("geschuetzte-datei") is None,
          "creating a recipe must never overwrite an unindexed Markdown file")
    orphan.unlink()
    orphan_images = core_storage.recipe_images_dir("geschuetzte-bilder")
    orphan_images.mkdir(parents=True)
    expect_valueerror(core_recipes.add_recipe, "Geschützte Bilder")
    check(orphan_images.is_dir() and core_recipes.get("geschuetzte-bilder") is None
          and not core_storage.recipe_file("geschuetzte-bilder").exists(),
          "creating a recipe must not adopt unrelated existing image storage")
    orphan_images.rmdir()

    # Every slug that slugify produces must remain a valid explicit slug.
    for sample in ("Käse-Soufflé", "Übungs-ß-Rezept", "100% Curry!!",
                   "Nur Punkt-Slug", "...", "CON", "NUL", "LPT1"):
        derived = core_recipes.slugify(sample)
        created = core_recipes.add_recipe(f"Beliebiger Titel für {sample}", slug=derived)
        check(created.slug == derived,
              "every slugify output must remain accepted as an explicit slug")
        core_archive.purge_archived_recipe(core_archive.archive_recipe(created.slug).slug)

    # The 150-character title cap keeps the atomic writer's ".<slug>.md.<tmp>"
    # name inside the Windows filename limit (verified live: longer titles
    # crashed recipe.create with a raw OSError on save).
    long_title = "L" * 150
    long_recipe = core_recipes.add_recipe(long_title)
    check(long_recipe.title == long_title,
          "a 150-character title must stay accepted")
    expect_valueerror(core_recipes.add_recipe, "K" * 151)
    expect_valueerror(core_recipes.update_recipe, long_recipe.slug, title="K" * 151)
    check(core_recipes.get(long_recipe.slug).title == long_title,
          "a rejected overlong title must not mutate the recipe")
    expanded = core_recipes.add_recipe("Ä" * 150)
    check(expanded.title == "Ä" * 150 and len(expanded.slug) <= 150,
          "umlaut expansion must not exceed the portable slug filename bound")
    core_archive.archive_recipe(expanded.slug)
    restored_expanded = core_archive.restore_archived_recipe(expanded.slug)
    check(restored_expanded.content().startswith("# " + "Ä" * 150),
          "a maximum-length expanded slug must survive archive and restore")


def search_checks():
    pasta, curry, quick = catalog_fixture()
    check([r.slug for r in core_search.search("kokosmilch")] == [curry.slug],
          "full-text search must include recipe content")
    # Live searches should only open bodies when metadata leaves a term
    # unresolved, without changing ANY/ALL or cross-field matching.
    read_slugs = []
    original_content = core_records.Recipe.content

    def record_content(recipe):
        read_slugs.append(recipe.slug)
        return original_content(recipe)

    with patch.object(core_records.Recipe, "content", record_content):
        check([r.slug for r in core_search.search("pasta italienisch", match="all")]
              == [pasta.slug] and pasta.slug not in read_slugs,
              "an ALL match in metadata must not read that recipe's Markdown")
        read_slugs.clear()
        check({r.slug for r in core_search.search("pasta kokosmilch")}
              == {pasta.slug, curry.slug} and pasta.slug not in read_slugs,
              "ANY must combine metadata hits and body-only hits without redundant reads")
        read_slugs.clear()
        check([r.slug for r in core_search.search("italienisch tomaten", match="all")]
              == [pasta.slug] and pasta.slug in read_slugs,
              "ALL must resolve remaining terms in the body after a metadata match")
        check(core_search.search("pasta kokosmilch", match="all") == [],
              "a metadata hit must not bypass an absent ALL term")
    check({r.slug for r in core_search.search(tags=["italienisch", "indisch"])}
          == {pasta.slug, curry.slug}, "tags within one facet must use OR")
    check([r.slug for r in core_search.search(tags=["indisch", "curry"])] == [curry.slug],
          "tags across facets must use AND")
    check([r.slug for r in core_search.search(max_time=15)] == [quick.slug],
          "maximum duration must filter recipes")
    expect_valueerror(core_search.search, max_time=0)
    expect_valueerror(core_search.search, max_time=-1)
    expect_valueerror(core_search.search, "kokosmilch", "alle")
    expect_valueerror(core_search.search, "kokosmilch", "ALL")
    expect_valueerror(core_search.search, None)


def facet_checks():
    pasta, _, quick = catalog_fixture()
    check(core_categories.uncategorized_tags(["VEGAN", "saisonal", "saisonal"])
          == ["saisonal"],
          "uncategorized tags must be distinct and category matching case-insensitive")

    # Equivalent spellings from separate recipes must appear as one facet
    # choice, while category spelling/order remain the shared presentation.
    core_recipes.update_recipe(
        pasta.slug, tags=["ITALIENISCH", "pasta", "Regional", "zutat-tag"],
    )
    core_recipes.update_recipe(quick.slug, tags=["vegan", "regional", "Apfel"])
    groups = core_categories.tag_groups()
    check(groups[-1] == {
        "key": "other", "label": "Sonstige",
        "tags": ["Apfel", "Regional", "zutat-tag"],
    }, "used tag groups must deduplicate equivalent spellings across recipes")
    check(groups[0] == {
        "key": "cuisine", "label": "Küche",
        "tags": ["italienisch", "indisch"],
    }, "category spelling and explicit tag order must survive mixed recipe case")
    check([group["key"] for group in groups]
          == ["cuisine", "dish", "diet", "other"],
          "used tag groups must preserve category order and place unassigned tags last")
    check([group["key"] for group in core_categories.tag_groups(only_used=False)]
          == ["cuisine", "dish", "diet"],
          "all defined tag groups must not gain unassigned recipe tags")
    check(core_recipes.get(pasta.slug).tags[0] == "ITALIENISCH"
          and "regional" in core_recipes.get(quick.slug).tags,
          "presenting facets must preserve each recipe's original tag spelling")
    core_recipes.update_recipe(pasta.slug, tags=["italienisch", "pasta"])
    core_recipes.update_recipe(quick.slug, tags=["vegan"])

    # Agents can maintain the complete facet assignment through Core instead
    # of editing categories.json behind the running service.
    core_recipes.update_recipe(quick.slug, tags=["vegan", "saisonal"])
    season = core_categories.add_category("season", "Jahreszeit", position=2)
    check(season == {"key": "season", "label": "Jahreszeit", "tags": []}
          and list(core_categories.load_categories())[1] == "season",
          "category creation must preserve an explicit display position")
    season = core_categories.update_category("season", label="Saison", position=1)
    check(season["label"] == "Saison"
          and list(core_categories.load_categories())[0] == "season",
          "category label and position must update in one transaction")
    season = core_categories.assign_category_tags(
        "season", ["saisonal", "regional", "SAISONAL"],
    )
    check(season["tags"] == ["saisonal", "regional"]
          and core_categories.uncategorized_tags(["saisonal"]) == [],
          "assignment must deduplicate input and resolve uncategorized tags")
    moved = core_categories.move_category_tag("season", "regional", 1)
    check(moved["tags"] == ["regional", "saisonal"],
          "tag display order must be agent-controllable")
    check(core_categories.unassign_category_tags(["REGIONAL", "regional"])
          == {"unassigned": ["regional"]},
          "unassign must be case-insensitive and idempotent within one request")
    removed_category = core_categories.remove_category("season")
    check(removed_category["removed"] is True
          and removed_category["now_uncategorized"] == ["saisonal"],
          "removing a category must report affected used tags")
    core_categories.add_category("season", "Saison")
    core_categories.assign_category_tags("season", ["saisonal"])
    expect_valueerror(core_categories.add_category, "season", "Doppelt")
    expect_valueerror(core_categories.add_category, "Other", "Reserviert")
    expect_valueerror(core_categories.update_category, "missing", label="Fehlt")
    expect_valueerror(core_categories.move_category_tag, "season", "saisonal", 0)
    preserved = core_categories.assign_category_tags("season", ["SommerTag"])
    check("SommerTag" in preserved["tags"],
          "an unused new tag must preserve the spelling supplied by the agent")


def update_checks():
    _, _, quick = catalog_fixture()
    updated = core_recipes.update_recipe(
        quick.slug, title="Gurkensalat", tags=["vegan"], duration_min=12,
        servings=2, content="# Gurkensalat\n",
    )
    check(updated.title == "Gurkensalat" and updated.duration_min == 12,
          "recipe metadata must update")
    check(updated.content() == "# Gurkensalat\n", "recipe content must update")
    renamed = core_recipes.update_recipe(quick.slug, title="Gurkensalat Deluxe")
    check(renamed.content().startswith("# Gurkensalat Deluxe\n"),
          "renaming metadata must update the canonical Markdown H1")
    cleared = core_recipes.update_recipe(
        quick.slug, clear_duration=True, clear_servings=True,
    )
    check(cleared.duration_min is None and cleared.servings is None,
          "optional duration and servings must be removable")
    check([r.slug for r in core_search.search(max_time=15)] == [],
          "maximum duration must exclude recipes with unknown duration")
    expect_valueerror(
        core_recipes.update_recipe, quick.slug, duration_min=10, clear_duration=True,
    )
    expect_valueerror(core_recipes.update_recipe, quick.slug, title="  ")

    # Titles must stay single-line: a newline or carriage return would desync
    # the metadata title from the Markdown H1 and trip the hard title check.
    store_before = core_checks.check()
    expect_valueerror(core_recipes.add_recipe, "A\nB")
    check(core_checks.check() == store_before and core_recipes.get("a-b") is None,
          "a rejected title must not create a recipe or change the store")
    quick_before = core_recipes.get(quick.slug).to_dict()
    quick_content_before = core_recipes.get(quick.slug).content()
    expect_valueerror(core_recipes.update_recipe, quick.slug, title="A\rB")
    expect_valueerror(core_recipes.update_recipe, quick.slug, title="A\nB")
    check(core_recipes.get(quick.slug).to_dict() == quick_before
          and core_recipes.get(quick.slug).content() == quick_content_before,
          "a rejected title update must leave the recipe unchanged")

    # Recipe tags are normalized like category tags: one-line strings,
    # case-insensitively deduplicated with the first spelling kept.
    duplicate_tags = core_recipes.add_recipe("Doppel-Tags", tags=["vegan", "vegan"])
    check(duplicate_tags.tags == ["vegan"],
          "duplicate recipe tags must be stored once")
    spelled_tags = core_recipes.add_recipe(
        "Schreibweise-Tags", tags=["Vegan", "vegan"])
    check(spelled_tags.tags == ["Vegan"],
          "tag deduplication must keep the first spelling")
    clean_store = core_checks.check()
    check(clean_store["ok"] is True and clean_store["uncategorized_tags"] == [],
          "valid recipe tags must keep the store consistent")
    expect_valueerror(core_recipes.add_recipe, "Zeilen-Tag", tags=["a\nb"])
    expect_valueerror(core_recipes.add_recipe, "Zahlen-Tag", tags=[1])
    check(core_checks.check() == clean_store and core_recipes.get("zeilen-tag") is None
          and core_recipes.get("zahlen-tag") is None,
          "rejected tag lists must not create recipes")
    tidied = core_recipes.update_recipe(duplicate_tags.slug, tags=["a", "A"])
    check(tidied.tags == ["a"],
          "recipe tag updates must deduplicate like category tags")
    check(core_checks.check()["uncategorized_tags"] == ["a"],
          "tag deduplication must not duplicate uncategorized entries")
    cleared_tags = core_recipes.update_recipe(spelled_tags.slug, tags=[])
    check(cleared_tags.tags == [],
          "an empty tag list must still clear the tags")
    expect_valueerror(core_recipes.update_recipe, duplicate_tags.slug, tags="vegan")
    check(core_recipes.get(duplicate_tags.slug).tags == ["a"],
          "a non-list tag update must leave the recipe unchanged")

    # Remove the experiment recipes again so the rest of the suite starts
    # from the original store; archive plus purge is the permanent path.
    for experiment in (duplicate_tags, spelled_tags):
        core_archive.archive_recipe(experiment.slug)
        core_archive.purge_archived_recipe(experiment.slug)
    check(core_checks.check() == store_before,
          "the title and tag experiments must leave the store unchanged")


def image_source():
    source = core_storage.project_root() / "source.png"
    source.write_bytes(base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
    ))
    return source


def image_checks():
    pasta, _, _ = catalog_fixture()
    source = image_source()
    finished = core_images.add_recipe_image(
        pasta.slug, source, role="result", caption="Fertig angerichtet",
    )
    check(core_recipes.get(pasta.slug).cover_image_id == finished.id,
          "the first image must persist its id as the selected cover")
    step = core_images.add_recipe_image(
        pasta.slug, source, role="step", caption="Sauce einrühren",
    )
    images, cover_id = core_images.list_recipe_images(pasta.slug)
    check(len(images) == 2 and cover_id == finished.id,
          "the first image must become the default cover")
    check(core_images.recipe_image_path(pasta.slug, step).is_file(),
          "image files must be copied into Gusto storage")
    core_images.set_recipe_cover(pasta.slug, step.id)
    changed_image = core_images.update_recipe_image(
        pasta.slug, step.id, role="technique", caption="Vom Herd nehmen",
    )
    check(changed_image.role == "technique" and changed_image.caption == "Vom Herd nehmen",
          "image role and caption must be editable")
    check(core_recipes.get(pasta.slug).cover_image.id == step.id,
          "an arbitrary stored image must be selectable as cover")
    fallback_cover = core_images.remove_recipe_image(pasta.slug, step.id)
    check(fallback_cover == finished.id and not core_images.recipe_image_path(pasta.slug, step).exists(),
          "removing the cover must select the first remaining image")
    expect_valueerror(core_images.add_recipe_image, pasta.slug, core_storage.project_root() / "missing.png")
    fake_source = core_storage.project_root() / "fake.png"
    fake_source.write_bytes(b"not an image")
    expect_valueerror(core_images.add_recipe_image, pasta.slug, fake_source)
    # Store hygiene: role and caption are persisted as single lines (matching
    # normalizes whitespace anyway), so the write paths must reject breaks.
    expect_valueerror(core_images.add_recipe_image, pasta.slug, source, role="a\nb")
    expect_valueerror(core_images.add_recipe_image, pasta.slug, source, caption="a\nb")
    expect_valueerror(core_images.update_recipe_image, pasta.slug, finished.id, caption="a\nb")
    expect_valueerror(core_images.update_recipe_image, pasta.slug, finished.id, role="a\rb")
    check(core_images.get_recipe_image(pasta.slug, finished.id).caption == "Fertig angerichtet",
          "a rejected multiline caption must not mutate the stored image")


def cooking_checks():
    pasta, curry, _ = catalog_fixture()
    recent_cook = (date.today() - timedelta(days=1)).isoformat()
    older_cook = (date.today() - timedelta(days=3)).isoformat()
    core_recipes.log_cooked(pasta.slug, recent_cook)
    core_recipes.log_cooked(pasta.slug, older_cook)
    check(core_recipes.get(pasta.slug).last_cooked == recent_cook,
          "an older log entry must not regress last_cooked")
    check([entry["date"] for entry in core_recipes.load_log()] == [older_cook, recent_cook],
          "the log must stay chronologically sorted")
    expect_valueerror(core_recipes.log_cooked, "missing")
    for invalid_date in ["", "2026-13-45", "15.07.2026", "20260713"]:
        expect_valueerror(core_recipes.log_cooked, pasta.slug, invalid_date)
    expect_valueerror(
        core_recipes.log_cooked, pasta.slug, (date.today() + timedelta(days=1)).isoformat(),
    )
    check([entry["date"] for entry in core_recipes.load_log()] == [older_cook, recent_cook],
          "invalid or future cooking dates must not mutate the log")
    expect_valueerror(core_recipes.load_log, days=0)
    expect_valueerror(core_recipes.load_log, days=-1)
    check(core_recipes.load_log(days=10**100) == core_recipes.load_log(),
          "a calendar-spanning positive day window must include all history")

    candidates = {recipe.slug for recipe in core_recipes.suggest(days=7)}
    check(pasta.slug not in candidates and curry.slug in candidates,
          "suggestions must exclude recently cooked recipes")
    check(core_recipes.suggest(days=7, limit=0) == [],
          "a zero suggestion limit must return no recipes")
    check(len(core_recipes.suggest(days=7, limit=1)) == 1,
          "a positive suggestion limit must cap the result")
    expect_valueerror(core_recipes.suggest, 7, -1)
    expect_valueerror(core_recipes.suggest, 0)
    check(pasta.slug not in {recipe.slug for recipe in core_recipes.suggest(days=10**100)},
          "a calendar-spanning suggestion window must still exclude logged recipes")


def cooking_boundaries_checks():
    class Today(date):
        @classmethod
        def today(cls):
            return cls(2026, 10, 23)

    with patch.object(core_recipes, "date", Today):
        never = core_recipes.add_recipe("Noch nie gekocht")
        older = core_recipes.add_recipe("Ältester Kochtermin")
        cutoff = core_recipes.add_recipe("Am Stichtag")
        recent = core_recipes.add_recipe("Heute gekocht")
        core_recipes.log_cooked(older.slug, "2026-10-15")
        core_recipes.log_cooked(cutoff.slug, "2026-10-16")
        core_recipes.log_cooked(recent.slug)
        revision = core_storage.change_state()["revision"]
        core_recipes.log_cooked(recent.slug)
        check(core_recipes.load_log(days=1) == [
            {"date": "2026-10-23", "slug": recent.slug},
            {"date": "2026-10-23", "slug": recent.slug},
        ] and core_storage.change_state()["revision"] == revision + 1,
              "repeated cooking on the same date must append and publish every entry")
        check([entry["slug"] for entry in core_recipes.load_log(days=7)]
              == [cutoff.slug, recent.slug, recent.slug],
              "the log window must include its exact cutoff and exclude the previous day")
        check([recipe.slug for recipe in core_recipes.suggest(days=7)] == [never.slug, older.slug],
              "suggestions must exclude the cutoff date and order never-cooked before oldest")
        check([recipe.slug for recipe in core_recipes.suggest(days=1)]
              == [never.slug, older.slug, cutoff.slug],
              "eligible cooked recipes must follow their cooking dates, oldest first")


def archive_checks():
    pasta, _, _ = catalog_fixture()
    finished = core_images.add_recipe_image(pasta.slug, image_source())
    core_recipes.log_cooked(pasta.slug)
    # Delete is reversible archive: Markdown, metadata and owned images move
    # together while log and shopping keep resolving the slug.
    sourced = core_shopping.shopping_add("Tomaten", source=pasta.slug)
    archived = core_archive.archive_recipe(pasta.slug)
    check(core_recipes.get(pasta.slug) is None and core_snapshots.get_archived(pasta.slug) is not None,
          "archiving must remove the recipe only from the active catalog")
    check(archived.content().startswith("# Pasta Pomodoro")
          and core_storage.archive_recipe_file(pasta.slug).is_file(),
          "the archive must retain the recipe Markdown")
    archived_image = core_images.get_archived_recipe_image(pasta.slug, finished.id)
    check(archived_image is not None
          and core_images.archived_recipe_image_path(pasta.slug, archived_image).is_file(),
          "the archive must retain owned recipe images")
    check(core_snapshots.recipe_references()[pasta.slug]["archived"] is True
          and core_shopping.shopping_list()[0].source == pasta.slug
          and any(entry["slug"] == pasta.slug for entry in core_recipes.load_log()),
          "shopping and log references must survive archiving")
    archived_check = core_checks.check()
    check(archived_check["archive_count"] == 1 and archived_check["ok"] is True,
          "a complete archived snapshot must pass the full integrity check")
    expect_valueerror(core_archive.purge_archived_recipe, pasta.slug)
    restored = core_archive.restore_archived_recipe(pasta.slug)
    check(restored.slug == pasta.slug and core_snapshots.get_archived(pasta.slug) is None,
          "restore must return the complete snapshot to the active catalog")
    check(core_storage.recipe_file(pasta.slug).is_file()
          and core_images.recipe_image_path(pasta.slug, finished).is_file()
          and core_snapshots.recipe_references()[pasta.slug]["archived"] is False,
          "restore must reconnect Markdown, images and recipe references")
    core_shopping.shopping_remove(sourced.id)

    archived = core_archive.archive_recipe(pasta.slug)
    check(core_recipes.get(pasta.slug) is None and not core_storage.recipe_file(pasta.slug).exists(),
          "delete must remove the recipe from the active catalog")
    check(not core_storage.recipe_images_dir(pasta.slug).exists(),
          "delete must move all owned images out of active storage")
    check(archived.slug == pasta.slug and core_storage.archive_recipe_file(pasta.slug).is_file()
          and core_storage.archive_images_dir(pasta.slug).is_dir(),
          "delete must be the reversible archive operation")
    expect_valueerror(core_archive.archive_recipe, pasta.slug)
    core_archive.purge_archived_recipe(pasta.slug)
    check(core_snapshots.get_archived(pasta.slug) is None,
          "explicit purge must permanently remove the archived snapshot")

    # Purge parks the snapshot in a hidden transaction folder before the
    # recursive removal, so an interrupted purge can never leave a half-deleted
    # visible archive entry behind.
    purge_target = core_recipes.add_recipe("Purge-Ziel", content="# Purge-Ziel\n")
    core_archive.archive_recipe(purge_target.slug)
    core_archive.purge_archived_recipe(purge_target.slug)
    check(core_snapshots.get_archived(purge_target.slug) is None
          and not any(path.name.startswith(".")
                      for path in core_storage.archive_dir().iterdir()),
          "a completed purge must leave no transaction leftovers")
    interrupt_target = core_recipes.add_recipe("Abbruch-Purge", content="# Abbruch-Purge\n")
    core_archive.archive_recipe(interrupt_target.slug)
    original_rmtree = core_archive.shutil.rmtree
    def _failing_rmtree(path, *args, **kwargs):
        raise OSError("simulierter Abbruch")
    core_archive.shutil.rmtree = _failing_rmtree
    purge_revision = core_storage.change_state()["revision"]
    try:
        try:
            core_archive.purge_archived_recipe(interrupt_target.slug)
            raise AssertionError("purge must fail when the snapshot removal fails")
        except core_errors.PostMutationError as error:
            check(isinstance(error.__cause__, OSError),
                  "an interrupted purge must report its already changed archive state")
    finally:
        core_archive.shutil.rmtree = original_rmtree
    leftovers = sorted(path.name for path in core_storage.archive_dir().iterdir()
                       if path.name.startswith("."))
    check(len(leftovers) == 1 and core_snapshots.get_archived(interrupt_target.slug) is None,
          "an interrupted purge must park the snapshot in a hidden folder")
    check(core_storage.change_state()["revision"] == purge_revision + 1,
          "a purge that already hid the snapshot must notify other clients")
    interrupted_check = core_checks.check()
    check(interrupted_check["stale_archive_transactions"] == leftovers
          and not interrupted_check["invalid_archive_entries"]
          and not interrupted_check["missing_archive_files"],
          "an interrupted purge must be visible as a stale transaction, "
          "not as a broken archive entry")
    core_archive.shutil.rmtree(core_storage.archive_dir() / leftovers[0])
    check(core_checks.check()["stale_archive_transactions"] == [],
          "removing the stale transaction folder must clear the check finding")


def consistency_checks():
    pasta, curry, quick = catalog_fixture()
    core_recipes.update_recipe(quick.slug, title="Gurkensalat")
    finished = core_images.add_recipe_image(pasta.slug, image_source())
    core_recipes.log_cooked(pasta.slug)
    initial = core_checks.check()
    check(not initial["orphaned_files"] and not initial["missing_files"],
          "freshly created recipes must be consistent")
    check(initial["uncategorized_tags"] == [], "known tags must be categorized")
    check(not initial["missing_image_files"] and not initial["orphaned_image_files"],
          "stored image metadata and files must be consistent")
    check(initial["ok"] is True and not initial["errors"],
          "a consistent store must expose an explicit successful check result")

    # Integrity checks must remain usable when the files they diagnose are
    # damaged. Keep collecting unrelated findings after a bad Markdown read
    # or a regular file occupying an expected directory.
    pasta_content = pasta.path.read_bytes()
    quick_content = quick.path.read_bytes()
    try:
        pasta.path.write_bytes(b"# Pasta Pomodoro\n\xff")
        quick.path.write_text("# Abweichender Titel\n", encoding="utf-8")
        corrupted = core_checks.check()
        check(corrupted["ok"] is False
              and any(str(pasta.path) in error for error in corrupted["invalid_data_files"])
              and corrupted["recipe_count"] == len(core_recipes.load_recipes())
              and corrupted["title_mismatches"] == [quick.slug],
              "unreadable Markdown must retain file context and unrelated diagnostics")
    finally:
        pasta.path.write_bytes(pasta_content)
        quick.path.write_bytes(quick_content)
    quick.path.unlink()
    quick.path.mkdir()
    try:
        corrupted = core_checks.check()
        check(corrupted["ok"] is False and corrupted["missing_files"] == [quick.slug],
              "a directory named like Markdown must not count as a recipe file")
    finally:
        quick.path.rmdir()
        quick.path.write_bytes(quick_content)
    invalid_image_folder = core_storage.recipe_images_dir(curry.slug)
    invalid_image_folder.write_text("keine Bildablage", encoding="utf-8")
    try:
        corrupted = core_checks.check()
        check(corrupted["ok"] is False
              and any(str(invalid_image_folder) in error
                      for error in corrupted["invalid_data_files"]),
              "a file replacing an active image directory must be diagnosed")
    finally:
        invalid_image_folder.unlink()

    core_archive.archive_recipe(quick.slug)
    archived_markdown = core_storage.archive_recipe_file(quick.slug)
    archived_images = core_storage.archive_images_dir(quick.slug)
    try:
        archived_markdown.write_bytes(b"# Gurkensalat\n\xff")
        archived_images.write_text("keine Bildablage", encoding="utf-8")
        corrupted = core_checks.check()
        check(corrupted["ok"] is False and corrupted["archive_count"] == 1
              and all(any(str(path) in error for error in corrupted["invalid_data_files"])
                      for path in (archived_markdown, archived_images)),
              "unreadable archived Markdown and image folders must both be diagnosed")
    finally:
        archived_markdown.write_bytes(quick_content)
        archived_images.unlink()
        core_archive.restore_archived_recipe(quick.slug)
    check(core_checks.check()["ok"] is True,
          "repairing the damaged active/archive files must clear their diagnostics")

    index_backup = core_storage.index_path().read_text(encoding="utf-8")
    try:
        entries = json.loads(index_backup)
        core_storage.index_path().write_text(
            json.dumps([*entries, entries[0], entries[0], entries[1]]), encoding="utf-8",
        )
        check(core_checks.check()["duplicate_recipe_slugs"]
              == sorted([entries[0]["slug"], entries[1]["slug"]]),
              "duplicate slug diagnostics must stay unique and sorted")
    finally:
        core_storage.index_path().write_text(index_backup, encoding="utf-8")

    # Corrupted data files become German ValueErrors, and check() reports them
    # as integrity errors instead of crashing with a raw loader exception.
    journal_backup = core_storage.changes_path().read_bytes()
    try:
        for broken_journal in (
            b"{broken", b"\xff",
            b'{"revision": 1, "resources": [], "events": [null]}',
            b'{"revision": 1, "resources": [], "events": '
            b'[{"revision": true, "resources": []}]}',
            b'{"revision": 2, "resources": [], "events": '
            b'[{"revision": 2, "resources": []}, {"revision": 1, "resources": []}]}',
        ):
            core_storage.changes_path().write_bytes(broken_journal)
            expect_valueerror(core_storage.change_state)
            corrupted = core_checks.check()
            check(corrupted["ok"] is False
                  and any(str(core_storage.changes_path()) in message
                          for message in corrupted["invalid_data_files"])
                  and corrupted["recipe_count"] == len(core_recipes.load_recipes()),
                  "broken journals must retain file context and other diagnostics")
            check(core_storage.changes_path().read_bytes() == broken_journal,
                  "diagnosing a journal must never silently repair or reset it")
    finally:
        core_storage.changes_path().write_bytes(journal_backup)
    index_backup = core_storage.index_path().read_text(encoding="utf-8")
    core_storage.index_path().write_text('{"kaputt": true}', encoding="utf-8")
    try:
        expect_valueerror(core_recipes.load_recipes)
        corrupted = core_checks.check()
        check(any("recipes.json" in message
                  for message in corrupted["invalid_data_files"]),
              "a corrupted catalog index must be reported as a data-file error")
        check(corrupted["ok"] is False,
              "a corrupted catalog index must be a hard error")
    finally:
        core_storage.index_path().write_text(index_backup, encoding="utf-8")
    log_backup = core_storage.log_path().read_text(encoding="utf-8")
    core_storage.log_path().write_text("[kein json]", encoding="utf-8")
    try:
        expect_valueerror(core_recipes.load_log)
        corrupted = core_checks.check()
        check(any("log.json" in message
                  for message in corrupted["invalid_data_files"]),
              "a corrupted cooking log must be reported as a data-file error")
    finally:
        core_storage.log_path().write_text(log_backup, encoding="utf-8")
    check(core_checks.check()["ok"] is True,
          "restored data files must check cleanly again")

    categories_backup = core_storage.categories_path().read_text(encoding="utf-8")
    try:
        for broken_categories in (
            "[kein json]", "[]",
            '{"diet": {"label": "Ernährung", "tags": "vegan"}}',
            json.dumps({
                "one": {"label": "Eins", "tags": ["gleich"]},
                "two": {"label": "Zwei", "tags": ["GLEICH"]},
            }),
            json.dumps({
                "one": {"label": "Eins", "tags": []},
                " one ": {"label": "Doppelt", "tags": []},
            }),
            '{"diet": {"label": "Ernährung", "tags": [], "unknown": true}}',
        ):
            core_storage.categories_path().write_text(broken_categories, encoding="utf-8")
            expect_valueerror(core_categories.load_categories)
            corrupted = core_checks.check()
            check(corrupted["ok"] is False
                  and any("categories.json" in message
                          for message in corrupted["invalid_data_files"]),
                  "broken categories must return full check diagnostics instead of crashing")
            check(corrupted["recipe_count"] == len(core_recipes.load_recipes()),
                  "category errors must not hide the remaining catalog diagnostics")
    finally:
        core_storage.categories_path().write_text(categories_backup, encoding="utf-8")

    # Regression: persisted entries with broken field types must surface as
    # integrity errors, not as raw KeyError/AttributeError loader crashes.
    index_backup = core_storage.index_path().read_text(encoding="utf-8")
    try:
        for broken_index in (
            '[{"slug": "x", "title": null}]',
            '[{"title": "T"}]',
            '[{"slug": [], "title": "T"}]',
            '[{"slug": "x", "title": "T", "tags": null}]',
            '[{"slug": "x", "title": "T", "tags": [5]}]',
            '[{"slug": "x", "title": "T", "duration_min": "schnell"}]',
            '[{"slug": "x", "title": "T", "last_cooked": []}]',
            '[{"slug": "x", "title": "T", "images": {}}]',
            '[{"slug": "x", "title": "T", "images": [{"id": 5, "filename": "x.png"}]}]',
        ):
            core_storage.index_path().write_text(broken_index, encoding="utf-8")
            expect_valueerror(core_recipes.load_recipes)
            corrupted = core_checks.check()
            check(any("recipes.json" in message
                      for message in corrupted["invalid_data_files"]),
                  "broken recipe entry field types must be a data-file error")
    finally:
        core_storage.index_path().write_text(index_backup, encoding="utf-8")
    log_backup = core_storage.log_path().read_text(encoding="utf-8")
    try:
        core_storage.log_path().write_text(
            json.dumps([{"slug": pasta.slug}]), encoding="utf-8")
        expect_valueerror(core_recipes.load_log)
        corrupted = core_checks.check()
        check(any("log.json" in message
                  for message in corrupted["invalid_data_files"]),
              "a log entry without 'date' must be reported as a data-file error")
    finally:
        core_storage.log_path().write_text(log_backup, encoding="utf-8")

    (core_storage.recipes_dir() / "orphan.md").write_text("# Orphan\n", encoding="utf-8")
    core_storage.recipe_file(curry.slug).unlink()
    recipes = core_recipes.load_recipes()
    pasta_record = next(recipe for recipe in recipes if recipe.slug == pasta.slug)
    pasta_record.tags.append("unbekannt")
    pasta_record.cover_image_id = "missing-cover"
    core_recipes.save_recipes(recipes)
    core_images.recipe_image_path(pasta.slug, finished).unlink()
    (core_storage.recipe_images_dir(pasta.slug) / "orphan.png").write_bytes(b"orphan")
    (core_storage.images_dir() / "missing-recipe").mkdir(parents=True)
    core_storage.recipe_file(quick.slug).write_text("# Falscher Titel\n", encoding="utf-8")
    broken = core_checks.check()
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


def journal_checks():
    # The persisted change journal is monotone across concurrent transactions,
    # while reads and idempotent PWA merges stay silent.
    before_parallel = core_storage.change_state()["revision"]
    with ThreadPoolExecutor(max_workers=2) as executor:
        created = list(executor.map(core_shopping.shopping_add, ["Parallel A", "Parallel B"]))
    events = core_storage.change_events(before_parallel)
    check(
        [event["revision"] for event in events]
        == [before_parallel + 1, before_parallel + 2],
        "parallel mutations must retain two distinct monotone revisions",
    )
    check(
        all(event["resources"] == ["shopping"] for event in events),
        "change events must identify their affected resource",
    )

    for item in created:
        core_shopping.shopping_remove(item.id)


def image_import_failure_checks():
    recipe = core_recipes.add_recipe("Bildimport-Fehler")
    source = image_source()
    scenarios = (
        ("recipe", lambda: core_images.add_recipe_image(recipe.slug, source),
         core_storage.recipe_images_dir(recipe.slug), core_recipes, "_save_recipes_unlocked",
         lambda: core_recipes.get(recipe.slug).to_dict()),
    )
    def partial_copy(_source, destination, *args, **kwargs):
        Path(destination).write_bytes(b"partial image")
        raise OSError("copy interrupted")

    original_unlink = Path.unlink
    for name, import_image, folder, save_owner, save_name, snapshot in scenarios:
        for failed_step in ("copy", "save"):
            for cleanup_fails in (False, True):
                before_files = set(folder.iterdir()) if folder.exists() else set()
                before_state = snapshot()
                revision = core_storage.change_state()["revision"]

                def reject_cleanup(path, *args, **kwargs):
                    if path.parent == folder and path not in before_files:
                        raise PermissionError("copied image busy")
                    return original_unlink(path, *args, **kwargs)

                failure = (
                    patch.object(core_images.shutil, "copy2", partial_copy)
                    if failed_step == "copy"
                    else patch.object(save_owner, save_name, side_effect=OSError("index denied"))
                )
                with failure, patch.object(
                    Path, "unlink", reject_cleanup if cleanup_fails else original_unlink,
                ):
                    try:
                        import_image()
                    except Exception as error:
                        check(isinstance(error, core_errors.PostMutationError)
                              if cleanup_fails else isinstance(error, OSError),
                              f"{name}/{failed_step}: report uncertainty only if cleanup failed")
                    else:
                        raise AssertionError(f"{name}/{failed_step}: injected failure ignored")
                leftovers = set(folder.iterdir()) - before_files
                check(snapshot() == before_state,
                      f"{name}/{failed_step}: a rejected import must preserve existing metadata")
                check(len(leftovers) == (1 if cleanup_fails else 0)
                      and core_storage.change_state()["revision"] == revision + int(cleanup_fails),
                      f"{name}/{failed_step}: only retained partial copies may publish a change")
                for leftover in leftovers:
                    leftover.unlink()

        # The JSON save itself may succeed before a later cleanup raises.
        # Removing the copy then would break the already committed reference.
        before_files = set(folder.iterdir())
        revision = core_storage.change_state()["revision"]
        original_save = getattr(save_owner, save_name)

        def save_then_fail(records):
            original_save(records)
            raise OSError("failure after index commit")

        with patch.object(save_owner, save_name, save_then_fail):
            try:
                import_image()
            except core_errors.PostMutationError:
                pass
            else:
                raise AssertionError(f"{name}: post-commit failure must remain uncertain")
        copied = set(folder.iterdir()) - before_files
        state = snapshot()
        referenced = {image["filename"] for image in state["images"]}
        check(len(copied) == 1 and next(iter(copied)).name in referenced
              and core_storage.change_state()["revision"] == revision + 1,
              f"{name}: a post-commit error must preserve the stored image reference")



def main():
    # Each contract has its own store; no behavior depends on an earlier test.
    for behavior in (
        creation_checks, search_checks, facet_checks, update_checks,
        image_checks, image_import_failure_checks, cooking_checks,
        cooking_boundaries_checks, archive_checks, consistency_checks,
        journal_checks, transaction_failure_checks,
    ):
        with tempfile.TemporaryDirectory(prefix=behavior.__name__ + "-", dir=HOME) as home:
            with patch.dict(os.environ, {"GUSTO_HOME": home}):
                behavior()
    print(f"OK - {checks} recipe core checks passed (GUSTO_HOME={HOME})")


if __name__ == "__main__":
    main()
