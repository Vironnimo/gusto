"""Hermetic checks for recipe, search, log, suggestion, and consistency logic."""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path


HOME = Path(tempfile.mkdtemp(prefix="gusto-recipes-test-"))
os.environ["RECIPE_HOME"] = os.fspath(HOME)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from recipe import core  # noqa: E402


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

    check([r.slug for r in core.search("kokosmilch")] == [curry.slug],
          "full-text search must include recipe content")
    check({r.slug for r in core.search(tags=["italienisch", "indisch"])}
          == {pasta.slug, curry.slug}, "tags within one facet must use OR")
    check([r.slug for r in core.search(tags=["indisch", "curry"])] == [curry.slug],
          "tags across facets must use AND")
    check([r.slug for r in core.search(max_time=15)] == [quick.slug],
          "maximum duration must filter recipes")

    updated = core.update_recipe(
        quick.slug, title="Gurkensalat", tags=["vegan"], duration_min=12,
        servings=2, content="# Gurkensalat\n",
    )
    check(updated.title == "Gurkensalat" and updated.duration_min == 12,
          "recipe metadata must update")
    check(updated.content() == "# Gurkensalat\n", "recipe content must update")

    core.log_cooked(pasta.slug, "2026-07-12")
    core.log_cooked(pasta.slug, "2026-07-10")
    check(core.get(pasta.slug).last_cooked == "2026-07-12",
          "an older log entry must not regress last_cooked")
    check([entry["date"] for entry in core.load_log()] == ["2026-07-10", "2026-07-12"],
          "the log must stay chronologically sorted")
    expect_valueerror(core.log_cooked, "missing")

    candidates = {r.slug for r in core.suggest(days=7)}
    check(pasta.slug not in candidates and curry.slug in candidates,
          "suggestions must exclude recently cooked recipes")

    initial = core.check()
    check(not initial["orphaned_files"] and not initial["missing_files"],
          "freshly created recipes must be consistent")
    check(initial["uncategorized_tags"] == [], "known tags must be categorized")

    (core.recipes_dir() / "orphan.md").write_text("# Orphan\n", encoding="utf-8")
    core.recipe_file(curry.slug).unlink()
    recipes = core.load_recipes()
    next(recipe for recipe in recipes if recipe.slug == pasta.slug).tags.append("saisonal")
    core.save_recipes(recipes)
    broken = core.check()
    check(broken["orphaned_files"] == ["orphan"], "orphaned Markdown must be found")
    check(broken["missing_files"] == [curry.slug], "missing Markdown must be found")
    check(broken["uncategorized_tags"] == ["saisonal"],
          "uncategorized tags must be found")

    core.delete_recipe(pasta.slug)
    check(core.get(pasta.slug) is None and not core.recipe_file(pasta.slug).exists(),
          "delete must remove index entry and Markdown")
    expect_valueerror(core.delete_recipe, pasta.slug)

    print(f"OK - {checks} recipe core checks passed (RECIPE_HOME={HOME})")


if __name__ == "__main__":
    main()
