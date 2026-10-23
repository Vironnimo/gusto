"""Read active/archive references and validate archived snapshots."""
from __future__ import annotations

import json
from pathlib import Path
import gusto.core.records as records
import gusto.core._timestamps as timestamps
import gusto.core.catalog.recipes as recipe_store
import gusto.core.storage as storage


def _load_archived_entry(path: Path) -> records.ArchivedRecipe:
    try:
        raw = json.loads((path / "metadata.json").read_text(encoding="utf-8"))
        version = raw["version"]
        recipe = records.Recipe.from_dict(raw["recipe"])
        archived_at = raw["archived_at"]
    except (OSError, json.JSONDecodeError, KeyError, TypeError) as error:
        raise ValueError(
            f"Ungültiger Archiveintrag '{path.name}': {error}"
        ) from error
    if version != 1:
        raise ValueError(
            f"Ungültiger Archiveintrag '{path.name}': "
            f"nicht unterstützte Version '{version}'."
        )
    if recipe.slug != path.name:
        raise ValueError(
            f"Ungültiger Archiveintrag '{path.name}': "
            f"Metadaten gehören zu '{recipe.slug}'."
        )
    if (not isinstance(archived_at, str) or not archived_at
            or timestamps._parse_iso(archived_at) is None):
        raise ValueError(
            f"Ungültiger Archiveintrag '{path.name}': archived_at ist ungültig."
        )
    return records.ArchivedRecipe(recipe=recipe, archived_at=archived_at)


def load_archive() -> list[records.ArchivedRecipe]:
    root = storage.archive_dir()
    if not root.exists():
        return []
    entries = [
        _load_archived_entry(path)
        for path in sorted(root.iterdir(), key=lambda item: item.name)
        if path.is_dir() and not path.name.startswith(".")
    ]
    return sorted(entries, key=lambda entry: entry.archived_at, reverse=True)


def get_archived(slug: str) -> records.ArchivedRecipe | None:
    if (not isinstance(slug, str) or not slug
            or Path(slug).name != slug or slug.startswith(".")):
        return None
    path = storage.archive_recipe_dir(slug)
    return _load_archived_entry(path) if path.is_dir() else None


def recipe_references() -> dict[str, dict]:
    """Titles and lifecycle state for cross-domain slug references."""
    references = {
        recipe.slug: {
            "slug": recipe.slug,
            "title": recipe.title,
            "archived": False,
        }
        for recipe in recipe_store.load_recipes()
    }
    for entry in load_archive():
        references.setdefault(entry.slug, {
            "slug": entry.slug,
            "title": entry.title,
            "archived": True,
        })
    return references
