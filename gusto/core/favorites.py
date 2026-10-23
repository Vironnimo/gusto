"""Household shopping needs, aliases and ranked preferred products."""
from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
import gusto.core._images as image_files
import gusto.core.records as records
import gusto.core._timestamps as timestamps
import gusto.core._validation as validation
import gusto.core.storage as storage
import gusto.core.errors as errors


def normalize_shopping_text(value: str) -> str:
    """Deterministic favorite matching: case and whitespace only.

    Quantities, punctuation and words deliberately stay untouched. A label is
    matched only after it has explicitly become a need name or alias.
    """
    lowered = re.sub(r"\s+", " ", (value or "").strip()).lower()
    return lowered.replace("ß", "ss")


def favorites_load() -> list[records.ShoppingNeed]:
    """Load the shared household preference catalog."""
    path = storage.favorites_path()
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(
            f"Ungültige Favoriten in '{path.name}': {error}"
        ) from error
    if not isinstance(data, dict):
        raise ValueError(
            f"Ungültige Favoriten in '{path.name}': JSON-Objekt erwartet."
        )
    needs = data.get("needs", [])
    if (not isinstance(needs, list)
            or not all(isinstance(need, dict) for need in needs)):
        raise ValueError(
            f"Ungültige Favoriten in '{path.name}': "
            "'needs' muss eine Liste von Objekten sein."
        )
    try:
        return [records.ShoppingNeed.from_dict(need) for need in needs]
    except ValueError as error:
        raise ValueError(f"Ungültige Favoriten in '{path.name}': {error}") from error


def _favorites_save_unlocked(needs: list[records.ShoppingNeed]) -> None:
    storage.write_json(storage.favorites_path(), {"needs": [need.to_dict() for need in needs]})


@storage.locked_mutation("favorites")
def favorites_save(needs: list[records.ShoppingNeed]) -> None:
    """Replace the full preference catalog through its public locked save."""
    _favorites_save_unlocked(needs)


def favorite_get_need(identifier: str) -> records.ShoppingNeed | None:
    """Find a need by opaque id or exact normalized canonical name."""
    normalized = normalize_shopping_text(identifier)
    return next((need for need in favorites_load()
                 if need.id == identifier
                 or normalize_shopping_text(need.name) == normalized), None)


def favorite_match(text: str, needs: list[records.ShoppingNeed] | None = None) -> records.ShoppingNeed | None:
    """Return the need whose name or explicit alias matches ``text`` exactly."""
    normalized = normalize_shopping_text(text)
    if not normalized:
        return None
    for need in favorites_load() if needs is None else needs:
        labels = [need.name, *need.aliases]
        if normalized in {normalize_shopping_text(label) for label in labels}:
            return need
    return None


def product_stores(product: records.FavoriteProduct) -> list[str]:
    """The stores a product card names; comma-separated, empty = everywhere."""
    return [store.strip() for store in product.store.split(",") if store.strip()]


def favorite_for_store(need: records.ShoppingNeed, store: str) -> dict:
    """Serialize ``need`` with its ranking narrowed to one store.

    ``products`` keeps the manual order but only contains cards available at
    ``store`` (named there, or without any store). Cards tied only to other
    stores move to ``other_products`` in the same order. Store names match
    like shopping text: case and whitespace only.
    """
    wanted = normalize_shopping_text(store)
    if not wanted:
        raise ValueError("Der Laden darf nicht leer sein.")
    data = need.to_dict()
    available, elsewhere = [], []
    for product, product_data in zip(need.products, data["products"]):
        stores = {normalize_shopping_text(name) for name in product_stores(product)}
        (available if not stores or wanted in stores else elsewhere).append(product_data)
    data["store"] = store.strip()
    data["products"] = available
    data["other_products"] = elsewhere
    return data


def _favorite_need(needs: list[records.ShoppingNeed], identifier: str) -> records.ShoppingNeed:
    normalized = normalize_shopping_text(identifier)
    need = next((item for item in needs
                 if item.id == identifier
                 or normalize_shopping_text(item.name) == normalized), None)
    if need is None:
        raise errors.NotFoundError(f"Kein Einkaufsbedarf mit id oder Name '{identifier}'.")
    return need


def _favorite_product(need: records.ShoppingNeed, product_id: str) -> records.FavoriteProduct:
    product = next((item for item in need.products if item.id == product_id), None)
    if product is None:
        raise errors.NotFoundError(
            f"Kein Lieblingsprodukt mit id '{product_id}' bei '{need.name}'."
        )
    return product


def _favorite_label_owner(label: str, needs: list[records.ShoppingNeed],
                          except_need_id: str | None = None) -> records.ShoppingNeed | None:
    normalized = normalize_shopping_text(label)
    if not normalized:
        return None
    for need in needs:
        if need.id == except_need_id:
            continue
        if normalized in {normalize_shopping_text(value)
                          for value in [need.name, *need.aliases]}:
            return need
    return None


@storage.locked_mutation("favorites")
def favorite_add_need(name: str, aliases: list[str] | None = None) -> records.ShoppingNeed:
    name = (name or "").strip()
    if not name:
        raise ValueError("Der Einkaufsbedarf braucht einen Namen.")
    validation._no_newlines(name, "Der Name eines Einkaufsbedarfs")
    needs = favorites_load()
    owner = _favorite_label_owner(name, needs)
    if owner is not None:
        raise ValueError(f"'{name}' gehört bereits zu '{owner.name}'.")

    clean_aliases: list[str] = []
    seen = {normalize_shopping_text(name)}
    for alias in aliases or []:
        cleaned = alias.strip()
        validation._no_newlines(cleaned, "Ein Alias")
        normalized = normalize_shopping_text(cleaned)
        if not normalized or normalized in seen:
            continue
        owner = _favorite_label_owner(cleaned, needs)
        if owner is not None:
            raise ValueError(f"'{cleaned}' gehört bereits zu '{owner.name}'.")
        seen.add(normalized)
        clean_aliases.append(cleaned)

    need = records.ShoppingNeed(
        id=records.new_id(), name=name, aliases=clean_aliases, products=[],
        created_at=timestamps._now_iso(),
    )
    needs.append(need)
    _favorites_save_unlocked(needs)
    return need


@storage.locked_mutation("favorites")
def favorite_update_need(identifier: str, name: str) -> records.ShoppingNeed:
    needs = favorites_load()
    need = _favorite_need(needs, identifier)
    name = (name or "").strip()
    if not name:
        raise ValueError("Der Einkaufsbedarf braucht einen Namen.")
    validation._no_newlines(name, "Der Name eines Einkaufsbedarfs")
    owner = _favorite_label_owner(name, needs, except_need_id=need.id)
    if owner is not None:
        raise ValueError(f"'{name}' gehört bereits zu '{owner.name}'.")

    old_name = need.name
    if normalize_shopping_text(old_name) != normalize_shopping_text(name):
        if normalize_shopping_text(old_name) not in {
                normalize_shopping_text(alias) for alias in need.aliases}:
            need.aliases.append(old_name)
    need.name = name
    need.aliases = [alias for alias in need.aliases
                    if normalize_shopping_text(alias) != normalize_shopping_text(name)]
    _favorites_save_unlocked(needs)
    return need


@storage.locked_mutation("favorites")
def favorite_add_alias(identifier: str, alias: str) -> records.ShoppingNeed:
    needs = favorites_load()
    need = _favorite_need(needs, identifier)
    alias = (alias or "").strip()
    if not alias:
        raise ValueError("Der Alias darf nicht leer sein.")
    validation._no_newlines(alias, "Ein Alias")
    normalized = normalize_shopping_text(alias)
    own_labels = {normalize_shopping_text(value)
                  for value in [need.name, *need.aliases]}
    if normalized in own_labels:
        return need
    owner = _favorite_label_owner(alias, needs, except_need_id=need.id)
    if owner is not None:
        raise ValueError(f"'{alias}' gehört bereits zu '{owner.name}'.")
    need.aliases.append(alias)
    _favorites_save_unlocked(needs)
    return need


@storage.locked_mutation("favorites")
def favorite_remove_alias(identifier: str, alias: str) -> records.ShoppingNeed:
    needs = favorites_load()
    need = _favorite_need(needs, identifier)
    normalized = normalize_shopping_text(alias)
    original_count = len(need.aliases)
    need.aliases = [value for value in need.aliases
                    if normalize_shopping_text(value) != normalized]
    if len(need.aliases) == original_count:
        raise ValueError(f"Kein Alias '{alias}' bei '{need.name}'.")
    _favorites_save_unlocked(needs)
    return need


def _copy_favorite_image(source: str | Path) -> tuple[str, Path]:
    source_path, suffix = image_files.validate_source(source)
    filename = f"{records.new_id()}{suffix}"
    destination = storage.favorite_images_dir() / filename
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        shutil.copy2(source_path, destination)
    except Exception:
        image_files._discard_imported_image(destination)
        raise
    return filename, destination


def favorite_image_path(filename: str) -> Path:
    return storage.favorite_images_dir() / Path(filename).name


def favorite_image_is_referenced(filename: str) -> bool:
    return any(product.image_filename == filename
               for need in favorites_load() for product in need.products)


@storage.locked_mutation("favorites")
def favorite_add_product(identifier: str, name: str, *, brand: str = "",
                         store: str = "", note: str = "",
                         image: str | Path | None = None) -> records.FavoriteProduct:
    needs = favorites_load()
    need = _favorite_need(needs, identifier)
    name = (name or "").strip()
    if not name:
        raise ValueError("Das Lieblingsprodukt braucht einen Namen.")
    validation._no_newlines(name, "Der Produktname")
    brand = (brand or "").strip()
    if not brand:
        raise ValueError("Das Lieblingsprodukt braucht eine Marke.")
    validation._no_newlines(brand, "Die Marke")
    validation._no_newlines(store.strip(), "Das Geschäft")
    validation._no_newlines(note.strip(), "Die Notiz")
    product = records.FavoriteProduct(
        id=records.new_id(), name=name, brand=brand, store=store.strip(),
        note=note.strip(), created_at=timestamps._now_iso(),
    )
    copied: Path | None = None
    if image:
        product.image_filename, copied = _copy_favorite_image(image)
    need.products.append(product)
    index_checkpoint = storage.mutation_checkpoint()
    try:
        _favorites_save_unlocked(needs)
    except Exception:
        if copied is not None and storage.mutation_checkpoint() == index_checkpoint:
            image_files._discard_imported_image(copied)
        raise
    return product


@storage.locked_mutation("favorites")
def favorite_update_product(identifier: str, product_id: str, *,
                            name: str | None = None, brand: str | None = None,
                            store: str | None = None, note: str | None = None,
                            image: str | Path | None = None,
                            remove_image: bool = False) -> records.FavoriteProduct:
    if image and remove_image:
        raise ValueError("Bild kann nicht gleichzeitig ersetzt und entfernt werden.")
    needs = favorites_load()
    need = _favorite_need(needs, identifier)
    product = _favorite_product(need, product_id)
    if name is not None:
        cleaned_name = name.strip()
        if not cleaned_name:
            raise ValueError("Das Lieblingsprodukt braucht einen Namen.")
        validation._no_newlines(cleaned_name, "Der Produktname")
        product.name = cleaned_name
    if brand is not None:
        cleaned_brand = brand.strip()
        if not cleaned_brand:
            raise ValueError("Das Lieblingsprodukt braucht eine Marke.")
        validation._no_newlines(cleaned_brand, "Die Marke")
        product.brand = cleaned_brand
    if store is not None:
        cleaned_store = store.strip()
        validation._no_newlines(cleaned_store, "Das Geschäft")
        product.store = cleaned_store
    if note is not None:
        cleaned_note = note.strip()
        validation._no_newlines(cleaned_note, "Die Notiz")
        product.note = cleaned_note

    old_filename = product.image_filename
    copied: Path | None = None
    if image:
        product.image_filename, copied = _copy_favorite_image(image)
    elif remove_image:
        product.image_filename = ""
    index_checkpoint = storage.mutation_checkpoint()
    try:
        _favorites_save_unlocked(needs)
    except Exception:
        if copied is not None and storage.mutation_checkpoint() == index_checkpoint:
            image_files._discard_imported_image(copied)
        raise
    if old_filename and old_filename != product.image_filename:
        favorite_image_path(old_filename).unlink(missing_ok=True)
    return product


@storage.locked_mutation("favorites")
def favorite_move_product(identifier: str, product_id: str,
                          position: int) -> records.ShoppingNeed:
    needs = favorites_load()
    need = _favorite_need(needs, identifier)
    product = _favorite_product(need, product_id)
    if (isinstance(position, bool) or not isinstance(position, int)
            or not 1 <= position <= len(need.products)):
        raise ValueError(f"Position muss zwischen 1 und {len(need.products)} liegen.")
    need.products.remove(product)
    need.products.insert(position - 1, product)
    _favorites_save_unlocked(needs)
    return need


@storage.locked_mutation("favorites")
def favorite_remove_product(identifier: str, product_id: str) -> records.ShoppingNeed:
    needs = favorites_load()
    need = _favorite_need(needs, identifier)
    product = _favorite_product(need, product_id)
    need.products = [item for item in need.products if item.id != product.id]
    _favorites_save_unlocked(needs)
    if product.image_filename:
        favorite_image_path(product.image_filename).unlink(missing_ok=True)
    folder = storage.favorite_images_dir()
    if folder.exists() and not any(folder.iterdir()):
        folder.rmdir()
    return need


@storage.locked_mutation("favorites")
def favorite_remove_need(identifier: str) -> records.ShoppingNeed:
    needs = favorites_load()
    need = _favorite_need(needs, identifier)
    _favorites_save_unlocked([item for item in needs if item.id != need.id])
    for product in need.products:
        if product.image_filename:
            favorite_image_path(product.image_filename).unlink(missing_ok=True)
    folder = storage.favorite_images_dir()
    if folder.exists() and not any(folder.iterdir()):
        folder.rmdir()
    return need
