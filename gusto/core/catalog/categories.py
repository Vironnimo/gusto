"""Tag facets, assignment and display order."""
from __future__ import annotations

import json
import re
import gusto.core.catalog.recipes as recipe_store
import gusto.core.storage as storage
import gusto.core.errors as errors


_CATEGORY_KEY_PATTERN = re.compile(r"[a-z0-9][a-z0-9_-]*")


def _category_key(value: str) -> str:
    if (not isinstance(value, str)
            or not _CATEGORY_KEY_PATTERN.fullmatch(value.strip())):
        raise ValueError(
            "Der Kategorie-Schlüssel muss mit einem Kleinbuchstaben oder einer "
            "Ziffer beginnen und darf nur a-z, 0-9, '_' und '-' enthalten."
        )
    key = value.strip()
    if key in {"other", "__other__"}:
        raise ValueError(
            f"Der Kategorie-Schlüssel '{key}' ist für die Facette Sonstige reserviert."
        )
    return key


def _category_label(value: str) -> str:
    if (not isinstance(value, str) or not value.strip()
            or "\n" in value or "\r" in value):
        raise ValueError(
            "Die Tag-Kategorie braucht eine nicht-leere, einzeilige Bezeichnung."
        )
    return value.strip()


def _category_tags(values) -> list[str]:
    if (not isinstance(values, list)
            or not all(isinstance(value, str) and value.strip()
                       and "\n" not in value and "\r" not in value
                       for value in values)):
        raise ValueError(
            "Kategorie-Tags müssen eine Liste nicht-leerer, einzeiliger Texte sein."
        )
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        tag = value.strip()
        normalized = tag.lower()
        if normalized in seen:
            raise ValueError(f"Tag '{tag}' ist in einer Kategorie doppelt vorhanden.")
        seen.add(normalized)
        result.append(tag)
    return result


def _validated_categories(value) -> dict[str, dict]:
    if not isinstance(value, dict):
        raise ValueError("Ungültige Tag-Kategorien: JSON-Objekt erwartet.")
    result: dict[str, dict] = {}
    owners: dict[str, str] = {}
    for raw_key, raw_category in value.items():
        key = _category_key(raw_key)
        if key in result:
            raise ValueError(
                f"Tag-Kategorie '{key}' ist nach Normalisierung doppelt vorhanden."
            )
        if not isinstance(raw_category, dict):
            raise ValueError(f"Tag-Kategorie '{key}' muss ein JSON-Objekt sein.")
        unknown = set(raw_category) - {"label", "tags"}
        if unknown:
            raise ValueError(
                f"Tag-Kategorie '{key}' enthält unbekannte Felder: "
                + ", ".join(sorted(unknown)) + "."
            )
        label = _category_label(raw_category.get("label"))
        tags = _category_tags(raw_category.get("tags"))
        for tag in tags:
            normalized = tag.lower()
            previous = owners.get(normalized)
            if previous is not None:
                raise ValueError(
                    f"Tag '{tag}' gehört zugleich zu '{previous}' und '{key}'."
                )
            owners[normalized] = key
        result[key] = {"label": label, "tags": tags}
    return result


def load_categories() -> dict[str, dict]:
    """Category definition from data/categories.json (order preserved).
    If the file is missing the result is {} -- then every tag is uncategorized."""
    path = storage.categories_path()
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return _validated_categories(value)
    except (OSError, ValueError) as error:
        raise ValueError(
            f"Ungültige Tag-Kategorien in '{path.name}': {error}"
        ) from error


def _category_dict(key: str, category: dict) -> dict:
    return {"key": key, "label": category["label"], "tags": list(category["tags"])}


def _category_position(value: int | None, maximum: int) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= maximum:
        raise ValueError(f"Die Position muss zwischen 1 und {maximum} liegen.")
    return value


def _requested_tags(values: list[str]) -> list[str]:
    if (not isinstance(values, list)
            or not all(isinstance(value, str) and value.strip()
                       and "\n" not in value and "\r" not in value
                       for value in values)):
        raise ValueError("Tags müssen eine Liste nicht-leerer, einzeiliger Texte sein.")
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        tag = value.strip()
        if tag.lower() not in seen:
            seen.add(tag.lower())
            result.append(tag)
    return result


@storage.locked_mutation("catalog")
def add_category(key: str, label: str, position: int | None = None) -> dict:
    key = _category_key(key)
    label = _category_label(label)
    categories = load_categories()
    if key in categories:
        raise ValueError(f"Tag-Kategorie '{key}' existiert bereits.")
    position = _category_position(position, len(categories) + 1)
    entries = list(categories.items())
    entries.insert(len(entries) if position is None else position - 1, (
        key, {"label": label, "tags": []},
    ))
    updated = dict(entries)
    storage.write_json(storage.categories_path(), updated)
    return _category_dict(key, updated[key])


@storage.locked_mutation("catalog")
def update_category(key: str, *, label: str | None = None,
                    position: int | None = None) -> dict:
    key = _category_key(key)
    if label is None and position is None:
        raise ValueError("Wähle --label und/oder --position.")
    categories = load_categories()
    if key not in categories:
        raise errors.NotFoundError(f"Keine Tag-Kategorie mit Schlüssel '{key}'.")
    if label is not None:
        categories[key]["label"] = _category_label(label)
    position = _category_position(position, len(categories))
    if position is not None:
        entry = (key, categories.pop(key))
        entries = list(categories.items())
        entries.insert(position - 1, entry)
        categories = dict(entries)
    storage.write_json(storage.categories_path(), categories)
    return _category_dict(key, categories[key])


@storage.locked_mutation("catalog")
def remove_category(key: str) -> dict:
    key = _category_key(key)
    categories = load_categories()
    category = categories.pop(key, None)
    if category is None:
        raise errors.NotFoundError(f"Keine Tag-Kategorie mit Schlüssel '{key}'.")
    used = {tag.lower(): tag for recipe in recipe_store.load_recipes() for tag in recipe.tags}
    now_uncategorized = [
        used[tag.lower()] for tag in category["tags"] if tag.lower() in used
    ]
    storage.write_json(storage.categories_path(), categories)
    return {
        **_category_dict(key, category),
        "removed": True,
        "now_uncategorized": now_uncategorized,
    }


@storage.locked_mutation("catalog")
def assign_category_tags(key: str, tags: list[str]) -> dict:
    key = _category_key(key)
    requested = _requested_tags(tags)
    if not requested:
        raise ValueError("Mindestens ein Tag muss zugeordnet werden.")
    categories = load_categories()
    if key not in categories:
        raise errors.NotFoundError(f"Keine Tag-Kategorie mit Schlüssel '{key}'.")

    spellings = {
        tag.lower(): tag
        for category in categories.values()
        for tag in category["tags"]
    }
    for recipe in recipe_store.load_recipes():
        for tag in recipe.tags:
            spellings.setdefault(tag.lower(), tag)
    target = categories[key]["tags"]
    target_names = {tag.lower(): tag for tag in target}
    for requested_tag in requested:
        normalized = requested_tag.lower()
        for category_key, category in categories.items():
            if category_key != key:
                category["tags"] = [
                    tag for tag in category["tags"] if tag.lower() != normalized
                ]
        if normalized not in target_names:
            canonical = spellings.get(normalized, requested_tag)
            target.append(canonical)
            target_names[normalized] = canonical
    storage.write_json(storage.categories_path(), categories)
    return _category_dict(key, categories[key])


@storage.locked_mutation("catalog")
def unassign_category_tags(tags: list[str]) -> dict:
    requested = {tag.lower() for tag in _requested_tags(tags)}
    if not requested:
        raise ValueError("Mindestens ein Tag muss entfernt werden.")
    categories = load_categories()
    removed: list[str] = []
    for category in categories.values():
        retained = []
        for tag in category["tags"]:
            if tag.lower() in requested:
                removed.append(tag)
            else:
                retained.append(tag)
        category["tags"] = retained
    storage.write_json(storage.categories_path(), categories)
    return {"unassigned": removed}


@storage.locked_mutation("catalog")
def move_category_tag(key: str, tag: str, position: int) -> dict:
    key = _category_key(key)
    requested = _category_tags([tag])[0]
    categories = load_categories()
    if key not in categories:
        raise errors.NotFoundError(f"Keine Tag-Kategorie mit Schlüssel '{key}'.")
    current = categories[key]["tags"]
    index = next(
        (index for index, value in enumerate(current)
         if value.lower() == requested.lower()),
        None,
    )
    if index is None:
        raise ValueError(f"Tag '{requested}' gehört nicht zur Kategorie '{key}'.")
    position = _category_position(position, len(current))
    value = current.pop(index)
    current.insert(position - 1, value)
    storage.write_json(storage.categories_path(), categories)
    return _category_dict(key, categories[key])


def _tag_to_category(categories: dict | None = None) -> dict[str, str]:
    """Inverse mapping tag(lowercase) -> category key."""
    categories = load_categories() if categories is None else categories
    return {t.lower(): key
            for key, cat in categories.items()
            for t in cat.get("tags", [])}


def tag_category(tag: str, categories: dict | None = None) -> str | None:
    """Category key of a tag, or None if uncategorized."""
    return _tag_to_category(categories).get(tag.lower())


def uncategorized_tags(tags: list[str]) -> list[str]:
    """Return distinct tags that have no named facet assignment."""
    mapping = _tag_to_category()
    return sorted(
        {tag for tag in tags if tag.lower() not in mapping}, key=str.lower,
    )


def tag_groups(only_used: bool = True) -> list[dict]:
    """Categories with their tags in display order -- for the tag bar and
    `gusto tags`. Returns [{"key", "label", "tags": [...]}, ...].

    only_used=True: only tags that occur in recipes; empty categories are
    dropped; actually-used tags without a category come last as a "Sonstige"
    group. only_used=False: all defined categories/tags."""
    categories = load_categories()
    used_names = None
    if only_used:
        # Facets match tags case-insensitively across recipes as well as
        # within one recipe. Keep the first catalog spelling for unassigned
        # tags; named categories still supply their own canonical spelling.
        used: dict[str, str] = {}
        for recipe in recipe_store.load_recipes():
            for tag in recipe.tags:
                used.setdefault(tag.lower(), tag)
        used_names = sorted(used.values(), key=str.lower)
    used_lower = {t.lower() for t in used_names} if used_names is not None else None

    groups: list[dict] = []
    covered: set[str] = set()
    for key, cat in categories.items():
        covered |= {t.lower() for t in cat.get("tags", [])}
        tags = [t for t in cat.get("tags", [])
                if used_lower is None or t.lower() in used_lower]
        if tags or used_lower is None:
            groups.append({"key": key, "label": cat.get("label", key), "tags": tags})

    if used_names is not None:
        leftover = [t for t in used_names if t.lower() not in covered]
        if leftover:
            groups.append({"key": "other", "label": "Sonstige", "tags": leftover})
    return groups
