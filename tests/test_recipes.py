"""Hermetic checks for recipe, search, log, suggestion, and consistency logic."""
from __future__ import annotations

import json
import os
import sys
import tempfile
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

    candidates = {r.slug for r in core.suggest(days=7)}
    check(pasta.slug not in candidates and curry.slug in candidates,
          "suggestions must exclude recently cooked recipes")
    check(core.suggest(days=7, limit=0) == [],
          "a zero suggestion limit must return no recipes")
    check(len(core.suggest(days=7, limit=1)) == 1,
          "a positive suggestion limit must cap the result")
    expect_valueerror(core.suggest, 7, -1)

    initial = core.check()
    check(not initial["orphaned_files"] and not initial["missing_files"],
          "freshly created recipes must be consistent")
    check(initial["uncategorized_tags"] == [], "known tags must be categorized")
    check(not initial["missing_image_files"] and not initial["orphaned_image_files"],
          "stored image metadata and files must be consistent")

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

    core.delete_recipe(pasta.slug)
    check(core.get(pasta.slug) is None and not core.recipe_file(pasta.slug).exists(),
          "delete must remove index entry and Markdown")
    check(not core.recipe_images_dir(pasta.slug).exists(),
          "delete must remove all stored images for the recipe")
    expect_valueerror(core.delete_recipe, pasta.slug)

    print(f"OK - {checks} recipe core checks passed (GUSTO_HOME={HOME})")


if __name__ == "__main__":
    main()
