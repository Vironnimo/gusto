"""Shopping items, ingredient imports, tombstones and full-state sync."""
from __future__ import annotations

import json
import re
from fractions import Fraction
import gusto.core._ingredient_scaling as ingredient_scaling
import gusto.core.records as records
import gusto.core._timestamps as timestamps
import gusto.core._validation as validation
import gusto.core.catalog.recipes as recipe_store
import gusto.core.catalog.snapshots as snapshots
import gusto.core.storage as storage
import gusto.core.errors as errors
import gusto.core.shopping_sync as sync_state


def parse_ingredients(content: str) -> list[str]:
    """Extract ingredient lines from the recipe Markdown.

    Rule (v1): all bullet items ('-' or '*') in the section under the heading
    '## Zutaten', up to the next '##' heading. For each bullet the WHOLE line
    (without the bullet char and surrounding whitespace) as one entry. Empty
    bullets are skipped. If there is no ingredient section the result is [].
    """
    ingredients: list[str] = []
    in_section = False
    for line in content.splitlines():
        s = line.strip()
        if s == "## Zutaten":
            in_section = True
            continue
        if not in_section:
            continue
        if s.startswith("## "):       # next heading ends the section
            break
        # A Markdown bullet needs whitespace after its marker. Bold notes and
        # thematic breaks ("**Optional**", "---", "* * *") are not ingredients.
        if re.fullmatch(r"(?:-[ \t]*){3,}|(?:\*[ \t]*){3,}", s):
            continue
        bullet = re.match(r"[-*][ \t]+(.+)", s)
        if bullet:
            ingredients.append(bullet.group(1).strip())
    return ingredients


def shopping_load() -> list[records.ShoppingItem]:
    """Load all items from data/shopping_list.json -- INCLUDING tombstones
    (deleted=True). If the file does not exist the result is []."""
    p = storage.shopping_path()
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(
            f"Ungültige Einkaufsliste in '{p.name}': {error}"
        ) from error
    if not isinstance(data, dict):
        raise ValueError(
            f"Ungültige Einkaufsliste in '{p.name}': JSON-Objekt erwartet."
        )
    items = data.get("items", [])
    if (not isinstance(items, list)
            or not all(isinstance(item, dict) for item in items)):
        raise ValueError(
            f"Ungültige Einkaufsliste in '{p.name}': "
            "'items' muss eine Liste von Objekten sein."
        )
    try:
        return [records.ShoppingItem.from_dict(item) for item in items]
    except ValueError as error:
        raise ValueError(f"Ungültige Einkaufsliste in '{p.name}': {error}") from error


def _shopping_save_unlocked(items: list[records.ShoppingItem]) -> None:
    """Write items atomically to data/shopping_list.json (uses write_json).
    File shape: {"items": [ <item-dict>, ... ]}. Tombstones are kept."""
    storage.write_json(storage.shopping_path(), {"items": [i.to_dict() for i in items]})


@storage.locked_mutation("shopping")
def shopping_save(items: list[records.ShoppingItem]) -> None:
    """Replace the full shopping state through its public locked save."""
    _shopping_save_unlocked(items)


def _shopping_append(entries: list[tuple[str, str]],
                     source: str | None = None) -> list[records.ShoppingItem]:
    """Append already validated entries while the shopping lock is held."""
    items = shopping_load()
    new_items: list[records.ShoppingItem] = []
    for text, quantity in entries:
        now = timestamps._now_iso()
        new_items.append(records.ShoppingItem(
            id=records.new_id(), text=text, quantity=quantity, checked=False,
            source=source, created_at=now, updated_at=now, deleted=False,
        ))
    items.extend(new_items)
    _shopping_save_unlocked(items)
    return new_items


def _shopping_text(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Ein Einkaufslisten-Eintrag braucht einen Text.")
    validation._no_newlines(value, "Der Text eines Einkaufslisten-Eintrags")
    return value.strip()


def _shopping_quantity(value: str) -> None:
    """Validate the optional single-line quantity label (text has its own
    validator; matching normalizes whitespace, so this is store hygiene)."""
    if (not isinstance(value, str) or "\n" in value or "\r" in value):
        raise ValueError(
            "Die Menge eines Einkaufslisten-Eintrags muss ein "
            "einzeiliger Text sein."
        )


def shopping_add(text: str, quantity: str = "", source: str | None = None) -> records.ShoppingItem:
    """Create, save and return a new item. Sets id (new_id()), created_at and
    updated_at (= _now_iso()), checked=False, deleted=False. A source must name
    an existing recipe and makes the item participate in that recipe's active
    import guard."""
    resources = ("shopping",) if source is None else ("catalog", "shopping")
    def add():
        if source is not None and recipe_store.get(source) is None:
            raise errors.NotFoundError(f"Kein Rezept mit Slug '{source}'.")
        _shopping_quantity(quantity)
        return _shopping_append([(_shopping_text(text), quantity)], source=source)[0]
    return storage.execute_locked_mutation(
        resources, add, event_resources=("shopping",),
    )


@storage.locked_mutation("shopping")
def shopping_add_many(texts: list[str]) -> list[records.ShoppingItem]:
    """Add a non-empty group of free-text entries in one transaction."""
    if not isinstance(texts, list) or not texts:
        raise ValueError("Mindestens ein Einkaufslisten-Eintrag ist erforderlich.")
    entries = [(_shopping_text(text), "") for text in texts]
    return _shopping_append(entries)


def _shopping_recipe_preview(recipe: records.Recipe, content: str,
                             revision: str, servings: int | None) -> dict:
    if servings is not None and (isinstance(servings, bool)
                                 or not isinstance(servings, int) or servings < 1):
        raise ValueError("Die gewünschte Portionszahl muss eine positive ganze Zahl sein.")
    if servings is not None and recipe.servings is None:
        raise ValueError(
            f"Für '{recipe.slug}' ist keine ursprüngliche Portionszahl hinterlegt. "
            "Bitte ergänze sie im Rezept, bevor du die Zutaten skalierst."
        )
    ingredients = parse_ingredients(content)
    if not ingredients:
        raise ValueError(f"Rezept '{recipe.slug}' enthält keine importierbaren Zutaten.")
    factor = Fraction(servings, recipe.servings) if servings is not None else Fraction(1)
    return {
        "slug": recipe.slug,
        "source_servings": recipe.servings,
        "target_servings": servings if servings is not None else recipe.servings,
        "factor": float(factor),
        "recipe_revision": revision,
        "ingredients": [ingredient_scaling.scale_ingredient(text, factor) for text in ingredients],
    }


def shopping_preview_recipe(slug: str, servings: int | None = None) -> dict:
    """Preview a consistent recipe snapshot and return its revision for import."""
    snapshot = recipe_store.get_edit_snapshot(slug)
    return _shopping_recipe_preview(snapshot["recipe"], snapshot["content"],
                                    snapshot["revision"], servings)


@storage.locked_mutation("catalog", "shopping", event_resources=("shopping",))
def shopping_add_recipe(slug: str, servings: int | None = None,
                        expected_revision: str | None = None) -> list[records.ShoppingItem]:
    """Put all ingredients of a recipe (parse_ingredients on its .md) onto the
    list, source=slug. Returns the newly added items. ValueError if the recipe
    is unknown, has no importable ingredients, or already has visible items on
    the list."""
    r = recipe_store.get(slug)
    if r is None:
        raise errors.NotFoundError(f"Kein Rezept mit Slug '{slug}'.")
    if servings is not None and expected_revision is None:
        raise ValueError(
            "Für die angepasste Portionszahl ist eine aktuelle Vorschau erforderlich. "
            "Bitte erstelle zuerst eine Vorschau."
        )
    content = r.path.read_text(encoding="utf-8") if r.path.is_file() else None
    revision = recipe_store.edit_revision(r, content)
    if expected_revision is not None and expected_revision != revision:
        raise errors.RevisionConflictError(
            revision, "Das Rezept wurde seit der Vorschau geändert. "
            "Es wurden keine Zutaten übernommen. Bitte erstelle eine neue Vorschau."
        )
    preview = _shopping_recipe_preview(r, content or "", revision, servings)
    existing = [
        item for item in shopping_load()
        if item.source == slug and not item.deleted
    ]
    if existing:
        verb = "steht" if len(existing) == 1 else "stehen"
        raise ValueError(
            f"{len(existing)} Einkaufsposten aus '{slug}' {verb} bereits "
            "auf der Einkaufsliste."
        )
    entries = [(ingredient["scaled"], "") for ingredient in preview["ingredients"]]
    return _shopping_append(entries, source=slug)


def shopping_list(include_done: bool = True,
                  include_deleted: bool = False) -> list[records.ShoppingItem]:
    """Visible items. Tombstones (deleted) hidden by default; include_done=False
    additionally hides done (checked) ones. Order: by created_at ascending
    (insertion order)."""
    items = [i for i in shopping_load() if include_deleted or not i.deleted]
    if not include_done:
        items = [i for i in items if not i.checked]
    items.sort(key=lambda i: i.created_at)
    return items


@storage.locked_mutation("shopping")
def shopping_toggle(item_id: str, checked: bool | None = None) -> records.ShoppingItem:
    """Set the done checkmark. checked=None toggles; otherwise the value is set.
    Updates updated_at only for a state transition and returns the item.
    ValueError if the id is unknown or the item is a tombstone."""
    items = shopping_load()
    target = next((i for i in items if i.id == item_id and not i.deleted), None)
    if target is None:
        raise errors.NotFoundError(f"Kein Einkauf-Item mit id '{item_id}'.")
    next_checked = (not target.checked) if checked is None else checked
    if target.checked == next_checked:
        return target
    target.checked = next_checked
    target.updated_at = timestamps._now_iso(target.updated_at)
    _shopping_save_unlocked(items)
    return target


@storage.locked_mutation("shopping")
def shopping_remove(item_id: str) -> None:
    """Mark item as a tombstone: deleted=True + update updated_at (do NOT hard-
    delete from the file, so the deletion syncs). Repeating the removal is a
    no-op. ValueError if the id is unknown."""
    items = shopping_load()
    target = next((i for i in items if i.id == item_id), None)
    if target is None:
        raise errors.NotFoundError(f"Kein Einkauf-Item mit id '{item_id}'.")
    if target.deleted:
        return
    target.deleted = True
    target.updated_at = timestamps._now_iso(target.updated_at)
    _shopping_save_unlocked(items)


@storage.locked_mutation("shopping")
def shopping_remove_done() -> int:
    """Tombstone every checked visible item and return the removed count."""
    items = shopping_load()
    count = 0
    for item in items:
        if item.checked and not item.deleted:
            item.deleted = True
            item.updated_at = timestamps._now_iso(item.updated_at)
            count += 1
    if count:
        _shopping_save_unlocked(items)
    return count


@storage.locked_mutation("shopping")
def shopping_clear() -> int:
    """Tombstone every visible item and return the removed count."""
    items = shopping_load()
    count = 0
    for item in items:
        if not item.deleted:
            item.deleted = True
            item.updated_at = timestamps._now_iso(item.updated_at)
            count += 1
    if count:
        _shopping_save_unlocked(items)
    return count


def shopping_merge(remote_items: list[dict]) -> list[records.ShoppingItem]:
    """Full-state sync: merge remote_items (raw item dicts from the client) into
    the local list, save the result and return it (incl. tombstones).

    Rule "last writer wins": per id the version with the later ISO timestamp
    wins. Old second-precision and new millisecond timestamps are both accepted.
    On a tie the local version stays. Ids that exist only remotely are taken
    over; tombstones (deleted=True) propagate like any other change.
    Every taken-over item needs non-empty text, and a visible item's source
    must still name a known active or archived recipe. Tombstones keep their
    deliberately unvalidated sources so that a purge of the referenced recipe
    never blocks a legitimate sync.
    """
    # Source validation needs a stable active/archive snapshot. In particular,
    # restoring a recipe temporarily hides its archive before writing the
    # active catalog. Unsourced shopping mutations remain independent.
    has_visible_sources = isinstance(remote_items, list) and any(
        isinstance(raw, dict) and raw.get("source") and not raw.get("deleted")
        for raw in remote_items
    )
    resources = ("catalog", "shopping") if has_visible_sources else ("shopping",)
    return storage.execute_locked_mutation(
        resources, lambda: _shopping_merge_unlocked(remote_items),
        event_resources=("shopping",),
    )


@storage.locked_mutation("catalog", "shopping", event_resources=("shopping",))
def shopping_exchange(remote_items: list[dict], generation: str | None = None) -> dict:
    """Exchange one generation's state, rejecting uploads from before a restore."""
    if generation is not None and (not isinstance(generation, str) or not generation):
        raise ValueError("Der Wiederherstellungsstand der Einkaufsliste ist ungültig.")
    current = sync_state.shopping_generation()
    reset = (generation or "initial") != current
    items = shopping_load() if reset else _shopping_merge_unlocked(remote_items)
    return {"items": [item.to_dict() for item in items],
            "generation": current, "reset": reset}


def _shopping_merge_unlocked(remote_items: list[dict]) -> list[records.ShoppingItem]:
    # Local state (incl. tombstones) as the source of truth, indexed by id.
    merged: dict[str, records.ShoppingItem] = {i.id: i for i in shopping_load()}
    known_sources: set[str] | None = None
    changed = False

    if not isinstance(remote_items, list):
        raise ValueError("Der Einkaufslisten-Stand muss eine Liste sein.")

    for raw in remote_items:
        if not isinstance(raw, dict):
            raise ValueError("Jeder Einkaufslisten-Eintrag muss ein Objekt sein.")
        try:
            remote = records.ShoppingItem.from_dict(raw)
        except TypeError as error:
            raise ValueError("Ein Einkaufslisten-Eintrag ist unvollständig.") from error
        if not isinstance(remote.id, str) or not remote.id:
            raise ValueError("Jeder Einkaufslisten-Eintrag braucht eine id.")
        if not isinstance(remote.text, str):
            raise ValueError("Der Text eines Einkaufslisten-Eintrags ist ungültig.")
        if not remote.text.strip():
            raise ValueError("Ein Einkaufslisten-Eintrag braucht einen Text.")
        if not isinstance(remote.quantity, str):
            raise ValueError("Die Menge eines Einkaufslisten-Eintrags ist ungültig.")
        # Ohne die Statusfelder blieben leere Zeitstempel stehen und eine nur
        # implizit abwesende Tombstone-Markierung würde ein gelöschtes Item
        # beim nächsten Sync wiederbeleben; deshalb wird Anwesenheit geprüft.
        if (not isinstance(raw.get("checked"), bool)
                or not isinstance(raw.get("deleted"), bool)):
            raise ValueError(
                "Der Status eines Einkaufslisten-Eintrags ist ungültig oder fehlt."
            )
        if remote.source is not None and not isinstance(remote.source, str):
            raise ValueError("Die Quelle eines Einkaufslisten-Eintrags ist ungültig.")
        if (not isinstance(raw.get("created_at"), str) or not raw["created_at"]
                or not isinstance(raw.get("updated_at"), str) or not raw["updated_at"]):
            raise ValueError(
                "Der Zeitstempel eines Einkaufslisten-Eintrags ist ungültig oder fehlt."
            )
        local = merged.get(remote.id)
        # id only remote -> take it over.
        # id on both sides -> later updated_at wins;
        # on a tie the local version stays.
        remote_version = timestamps._version_key(remote.updated_at)
        if local is None or remote_version > timestamps._version_key(local.updated_at):
            if remote_version[0]:
                # Do not persist a version that makes the next check/remove
                # overflow. Malformed legacy versions remain repairable by a
                # normal mutation, as before.
                timestamps._next_sync_instant(remote_version[1])
            if not remote.deleted and remote.source:
                # Only taken-over versions are persisted, so a losing stale
                # version may still reference a since-purged recipe.
                if known_sources is None:
                    known_sources = set(snapshots.recipe_references())
                if remote.source not in known_sources:
                    raise ValueError(
                        f"Die Quelle '{remote.source}' eines "
                        "Einkaufslisten-Eintrags ist kein bekanntes Rezept."
                    )
            merged[remote.id] = remote
            changed = True
    # ids that exist only locally stay unchanged.

    result = list(merged.values())
    if changed:
        _shopping_save_unlocked(result)
    return result
