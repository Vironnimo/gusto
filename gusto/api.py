"""Versioned anonymous HTTP command surface for Gusto clients.

Business rules stay in :mod:`gusto.core`.  This module only validates and
materializes transport input, delegates one command, and serializes its result.
"""
from __future__ import annotations

import asyncio
import base64
import binascii
import json
import os
import tempfile
import time
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from typing import Callable

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse

from . import __version__, core


router = APIRouter(prefix="/api/v1", tags=["api-v1"])

MAX_ATTACHMENT_BYTES = 25 * 1024 * 1024
MAX_REQUEST_BYTES = 40 * 1024 * 1024
MAX_FILENAME_CHARS = 255
_ILLEGAL_FILENAME_CHARS = set('<>:"/\\|?*')
_BODY_KEYS = {"operation", "arguments", "attachments"}
_ATTACHMENT_KEYS = {"name", "filename", "content_base64"}
_ATTACHMENT_OPERATIONS = {
    "image.add": ({"image"}, {"image"}),
    "favorites.product.add": (set(), {"image"}),
    "favorites.product.set": (set(), {"image"}),
}


class CommandError(ValueError):
    """Expected transport/command failure with an HTTP status."""

    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


def _error(message: str, status_code: int = 400) -> JSONResponse:
    return JSONResponse(
        {"ok": False, "error": message},
        status_code=status_code,
    )


def _not_found_error(error: ValueError) -> int:
    message = str(error)
    prefixes = (
        "Kein Rezept ",
        "Kein archiviertes Rezept ",
        "Kein Einkaufsbedarf ",
        "Kein Einkauf-Item ",
        "Kein Bild ",
        "Kein Lieblingsprodukt ",
        "Keine Tag-Kategorie ",
    )
    return 404 if message.startswith(prefixes) else 400


def _arguments(arguments, *, allowed: set[str],
               required: set[str] = frozenset()) -> dict:
    if not isinstance(arguments, dict):
        raise CommandError("'arguments' muss ein JSON-Objekt sein.")
    unknown = set(arguments) - allowed
    if unknown:
        raise CommandError(
            "Unbekannte Argumente: " + ", ".join(sorted(unknown)) + "."
        )
    missing = required - set(arguments)
    if missing:
        raise CommandError(
            "Fehlende Argumente: " + ", ".join(sorted(missing)) + "."
        )
    return arguments


def _string(arguments: dict, name: str, *, default=None,
            optional: bool = False) -> str | None:
    if name not in arguments:
        return default
    value = arguments[name]
    if optional and value is None:
        return None
    if not isinstance(value, str):
        raise CommandError(f"'{name}' muss ein Text sein.")
    return value


def _boolean(arguments: dict, name: str, *, default: bool = False) -> bool:
    value = arguments.get(name, default)
    if not isinstance(value, bool):
        raise CommandError(f"'{name}' muss true oder false sein.")
    return value


def _integer(arguments: dict, name: str, *, default=None,
             optional: bool = True) -> int | None:
    if name not in arguments:
        return default
    value = arguments[name]
    if optional and value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise CommandError(f"'{name}' muss eine ganze Zahl sein.")
    return value


def _strings(arguments: dict, name: str, *, default=None,
             optional: bool = False) -> list[str] | None:
    if name not in arguments:
        return [] if default is None and not optional else default
    value = arguments[name]
    if optional and value is None:
        return None
    if (not isinstance(value, list)
            or not all(isinstance(item, str) for item in value)):
        raise CommandError(f"'{name}' muss eine Liste aus Texten sein.")
    return list(value)


def _recipe_dict(recipe: core.Recipe, *, include_content: bool = False) -> dict:
    result = recipe.to_dict()
    if include_content:
        result["content"] = recipe.content()
    return result


def _tag_warnings(tags: list[str]) -> list[dict]:
    uncategorized = core.uncategorized_tags(tags)
    if not uncategorized:
        return []
    return [{
        "code": "uncategorized_tags",
        "message": (
            "Tags ohne Kategorie (Facet „Sonstige“): "
            + ", ".join(uncategorized)
        ),
        "tags": uncategorized,
    }]


def _image_dict(image: core.RecipeImage, cover_id: str | None) -> dict:
    result = image.to_dict()
    result["is_cover"] = image.id == cover_id
    return result


def _validate_attachments(operation: str, attachments) -> list[dict]:
    if not isinstance(attachments, list):
        raise CommandError("'attachments' muss eine Liste sein.")
    required, allowed = _ATTACHMENT_OPERATIONS.get(
        operation, (set(), set()),
    )
    normalized = []
    names = set()
    for raw in attachments:
        if not isinstance(raw, dict) or set(raw) != _ATTACHMENT_KEYS:
            raise CommandError(
                "Jeder Anhang braucht genau 'name', 'filename' und "
                "'content_base64'."
            )
        name = raw["name"]
        filename = raw["filename"]
        content = raw["content_base64"]
        if not isinstance(name, str) or not name:
            raise CommandError("Der Anhangsname darf nicht leer sein.")
        if name in names:
            raise CommandError(f"Anhang '{name}' wurde mehrfach angegeben.")
        if name not in allowed:
            raise CommandError(
                f"Anhang '{name}' ist für Operation '{operation}' nicht erlaubt."
            )
        if (not isinstance(filename, str) or not filename
                or Path(filename).name != filename
                or filename in {".", ".."}):
            raise CommandError("Der Originaldateiname eines Anhangs ist ungültig.")
        if (len(filename) > MAX_FILENAME_CHARS
                or any(character in _ILLEGAL_FILENAME_CHARS
                       for character in filename)):
            raise CommandError(
                "Der Originaldateiname eines Anhangs enthält unzulässige "
                "Zeichen oder ist zu lang."
            )
        if not isinstance(content, str):
            raise CommandError("'content_base64' muss ein Text sein.")
        if len(content) > ((MAX_ATTACHMENT_BYTES + 2) // 3) * 4:
            raise CommandError("Der Anhang ist größer als 25 MB.", 413)
        names.add(name)
        normalized.append(raw)
    missing = required - names
    if missing:
        raise CommandError(
            "Fehlende Anhänge: " + ", ".join(sorted(missing)) + "."
        )
    return normalized


@contextmanager
def _materialized_attachments(attachments: list[dict]):
    with tempfile.TemporaryDirectory(prefix="gusto-api-") as temporary:
        root = Path(temporary)
        paths: dict[str, Path] = {}
        for index, attachment in enumerate(attachments):
            try:
                content = base64.b64decode(
                    attachment["content_base64"], validate=True,
                )
            except (binascii.Error, ValueError) as error:
                raise CommandError(
                    f"Anhang '{attachment['name']}' enthält kein gültiges Base64."
                ) from error
            if len(content) > MAX_ATTACHMENT_BYTES:
                raise CommandError("Der Anhang ist größer als 25 MB.", 413)
            path = root / f"{index}-{attachment['filename']}"
            try:
                path.write_bytes(content)
            except OSError as error:
                raise CommandError(
                    f"Anhang '{attachment['name']}' konnte nicht materialisiert "
                    f"werden: {error}"
                ) from error
            paths[attachment["name"]] = path
        yield paths


def _catalog_list(arguments: dict, _attachments: dict) -> list[dict]:
    values = _arguments(arguments, allowed={"tags", "max_time"})
    tags = _strings(values, "tags", default=[])
    max_time = _integer(values, "max_time")
    recipes = sorted(
        core.search(tags=tags, max_time=max_time),
        key=lambda recipe: recipe.title.lower(),
    )
    return [recipe.to_dict() for recipe in recipes]


def _catalog_search(arguments: dict, _attachments: dict) -> list[dict]:
    values = _arguments(
        arguments, allowed={"query", "match", "tags", "max_time"},
    )
    recipes = sorted(core.search(
        query=_string(values, "query", default=""),
        match=_string(values, "match", default="any"),
        tags=_strings(values, "tags", default=[]),
        max_time=_integer(values, "max_time"),
    ), key=lambda recipe: recipe.title.lower())
    return [recipe.to_dict() for recipe in recipes]


def _tags_list(arguments: dict, _attachments: dict) -> list[dict]:
    values = _arguments(arguments, allowed={"all"})
    return core.tag_groups(only_used=not _boolean(values, "all"))


def _categories_list(arguments: dict, _attachments: dict) -> list[dict]:
    _arguments(arguments, allowed=set())
    return core.tag_groups(only_used=False)


def _categories_add(arguments: dict, _attachments: dict) -> dict:
    values = _arguments(
        arguments,
        allowed={"key", "label", "position"},
        required={"key", "label"},
    )
    return core.add_category(
        _string(values, "key"),
        _string(values, "label"),
        _integer(values, "position"),
    )


def _categories_set(arguments: dict, _attachments: dict) -> dict:
    values = _arguments(
        arguments,
        allowed={"key", "label", "position"},
        required={"key"},
    )
    return core.update_category(
        _string(values, "key"),
        label=_string(values, "label", optional=True),
        position=_integer(values, "position"),
    )


def _categories_remove(arguments: dict, _attachments: dict) -> dict:
    values = _arguments(arguments, allowed={"key"}, required={"key"})
    return core.remove_category(_string(values, "key"))


def _categories_assign(arguments: dict, _attachments: dict) -> dict:
    values = _arguments(
        arguments, allowed={"key", "tags"}, required={"key", "tags"},
    )
    return core.assign_category_tags(
        _string(values, "key"), _strings(values, "tags"),
    )


def _categories_unassign(arguments: dict, _attachments: dict) -> dict:
    values = _arguments(arguments, allowed={"tags"}, required={"tags"})
    return core.unassign_category_tags(_strings(values, "tags"))


def _categories_tag_move(arguments: dict, _attachments: dict) -> dict:
    values = _arguments(
        arguments,
        allowed={"key", "tag", "position"},
        required={"key", "tag", "position"},
    )
    return core.move_category_tag(
        _string(values, "key"),
        _string(values, "tag"),
        _integer(values, "position", optional=False),
    )


def _recipe_show(arguments: dict, _attachments: dict) -> dict:
    values = _arguments(arguments, allowed={"slug"}, required={"slug"})
    slug = _string(values, "slug")
    recipe = core.get(slug)
    if recipe is None:
        raise CommandError(f"Kein Rezept mit Slug '{slug}'.", 404)
    return _recipe_dict(recipe, include_content=True)


def _recipe_create(arguments: dict, _attachments: dict) -> dict:
    values = _arguments(
        arguments,
        allowed={"title", "tags", "duration_min", "servings"},
        required={"title"},
    )
    tags = _strings(values, "tags", default=[])
    recipe = core.add_recipe(
        _string(values, "title"),
        tags=tags,
        duration_min=_integer(values, "duration_min"),
        servings=_integer(values, "servings"),
    )
    result = recipe.to_dict()
    warnings = _tag_warnings(recipe.tags)
    if warnings:
        result["warnings"] = warnings
    return result


def _recipe_content_set(arguments: dict, _attachments: dict) -> dict:
    values = _arguments(
        arguments, allowed={"slug", "content"},
        required={"slug", "content"},
    )
    recipe = core.update_recipe(
        _string(values, "slug"),
        content=_string(values, "content"),
    )
    return _recipe_dict(recipe, include_content=True)


def _recipe_update(arguments: dict, _attachments: dict) -> dict:
    allowed = {
        "slug", "title", "tags", "duration_min", "servings",
        "clear_duration", "clear_servings",
    }
    values = _arguments(arguments, allowed=allowed, required={"slug"})
    tags = _strings(values, "tags", optional=True)
    slug = _string(values, "slug")
    title = _string(values, "title", optional=True)
    duration_min = _integer(values, "duration_min")
    servings = _integer(values, "servings")
    clear_duration = _boolean(values, "clear_duration")
    clear_servings = _boolean(values, "clear_servings")
    if (title is None and tags is None and duration_min is None
            and servings is None and not clear_duration
            and not clear_servings):
        raise CommandError("Gib mindestens eine Änderung an.")
    recipe = core.update_recipe(
        slug,
        title=title,
        tags=tags,
        duration_min=duration_min,
        servings=servings,
        clear_duration=clear_duration,
        clear_servings=clear_servings,
    )
    result = recipe.to_dict()
    if tags is not None:
        warnings = _tag_warnings(recipe.tags)
        if warnings:
            result["warnings"] = warnings
    return result


def _recipe_cooked(arguments: dict, _attachments: dict) -> dict:
    values = _arguments(
        arguments, allowed={"slug", "date"}, required={"slug"},
    )
    slug = _string(values, "slug")
    when = _string(values, "date", optional=True)
    core.log_cooked(slug, when=when)
    return {"slug": slug, "date": when or date.today().isoformat(), "ok": True}


def _log_list(arguments: dict, _attachments: dict) -> list[dict]:
    values = _arguments(arguments, allowed={"days"})
    return core.load_log(days=_integer(values, "days"))


def _suggest_list(arguments: dict, _attachments: dict) -> list[dict]:
    values = _arguments(arguments, allowed={"days", "limit"})
    days = _integer(values, "days", default=7, optional=False)
    return [
        recipe.to_dict()
        for recipe in core.suggest(
            days=days, limit=_integer(values, "limit"),
        )
    ]


def _check_run(arguments: dict, _attachments: dict) -> dict:
    _arguments(arguments, allowed=set())
    return core.check()


def _recipe_archive(arguments: dict, _attachments: dict) -> dict:
    values = _arguments(arguments, allowed={"slug"}, required={"slug"})
    entry = core.archive_recipe(_string(values, "slug"))
    return {
        "slug": entry.slug,
        "archived": True,
        "archived_at": entry.archived_at,
    }


def _archive_list(arguments: dict, _attachments: dict) -> list[dict]:
    _arguments(arguments, allowed=set())
    return [entry.to_dict() for entry in core.load_archive()]


def _archive_show(arguments: dict, _attachments: dict) -> dict:
    values = _arguments(arguments, allowed={"slug"}, required={"slug"})
    slug = _string(values, "slug")
    entry = core.get_archived(slug)
    if entry is None:
        raise CommandError(
            f"Kein archiviertes Rezept mit Slug '{slug}'.", 404,
        )
    return entry.to_dict(include_content=True)


def _archive_restore(arguments: dict, _attachments: dict) -> dict:
    values = _arguments(arguments, allowed={"slug"}, required={"slug"})
    recipe = core.restore_archived_recipe(_string(values, "slug"))
    return {"restored": True, "recipe": recipe.to_dict()}


def _archive_purge(arguments: dict, _attachments: dict) -> dict:
    values = _arguments(
        arguments, allowed={"slug", "yes"}, required={"slug", "yes"},
    )
    if not _boolean(values, "yes"):
        raise CommandError(
            "Endgültiges Löschen braucht 'yes: true'. Der Archiveintrag "
            "kann danach nicht wiederhergestellt werden."
        )
    entry = core.purge_archived_recipe(_string(values, "slug"))
    return {"slug": entry.slug, "purged": True}


def _image_list(arguments: dict, _attachments: dict) -> dict:
    values = _arguments(arguments, allowed={"slug"}, required={"slug"})
    images, cover_id = core.list_recipe_images(_string(values, "slug"))
    return {
        "cover_image_id": cover_id,
        "images": [_image_dict(image, cover_id) for image in images],
    }


def _image_add(arguments: dict, attachments: dict) -> dict:
    values = _arguments(
        arguments, allowed={"slug", "role", "caption", "cover"},
        required={"slug"},
    )
    slug = _string(values, "slug")
    image = core.add_recipe_image(
        slug,
        attachments["image"],
        role=_string(values, "role", default="gallery"),
        caption=_string(values, "caption", default=""),
        cover=_boolean(values, "cover"),
    )
    _, cover_id = core.list_recipe_images(slug)
    return _image_dict(image, cover_id)


def _image_set(arguments: dict, _attachments: dict) -> dict:
    values = _arguments(
        arguments, allowed={"slug", "id", "role", "caption"},
        required={"slug", "id"},
    )
    if "role" not in values and "caption" not in values:
        raise CommandError("Gib 'role' und/oder 'caption' an.")
    slug = _string(values, "slug")
    image = core.update_recipe_image(
        slug,
        _string(values, "id"),
        role=_string(values, "role", optional=True),
        caption=_string(values, "caption", optional=True),
    )
    _, cover_id = core.list_recipe_images(slug)
    return _image_dict(image, cover_id)


def _image_cover(arguments: dict, _attachments: dict) -> dict:
    values = _arguments(
        arguments, allowed={"slug", "id"}, required={"slug", "id"},
    )
    slug = _string(values, "slug")
    image = core.set_recipe_cover(slug, _string(values, "id"))
    return {"slug": slug, "cover_image_id": image.id}


def _image_remove(arguments: dict, _attachments: dict) -> dict:
    values = _arguments(
        arguments, allowed={"slug", "id"}, required={"slug", "id"},
    )
    image_id = _string(values, "id")
    cover_id = core.remove_recipe_image(_string(values, "slug"), image_id)
    return {"id": image_id, "removed": True, "cover_image_id": cover_id}


def _favorites_list(arguments: dict, _attachments: dict) -> list[dict]:
    _arguments(arguments, allowed=set())
    return [need.to_dict() for need in core.favorites_load()]


def _favorites_show(arguments: dict, _attachments: dict) -> dict:
    values = _arguments(arguments, allowed={"need"}, required={"need"})
    identifier = _string(values, "need")
    need = core.favorite_get_need(identifier)
    if need is None:
        raise CommandError(
            f"Kein Einkaufsbedarf mit id oder Name '{identifier}'.", 404,
        )
    return need.to_dict()


def _favorites_match(arguments: dict, _attachments: dict):
    values = _arguments(arguments, allowed={"text"}, required={"text"})
    need = core.favorite_match(_string(values, "text"))
    return need.to_dict() if need is not None else None


def _favorites_add(arguments: dict, _attachments: dict) -> dict:
    values = _arguments(
        arguments, allowed={"name", "aliases"}, required={"name"},
    )
    return core.favorite_add_need(
        _string(values, "name"),
        aliases=_strings(values, "aliases", default=[]),
    ).to_dict()


def _favorites_set(arguments: dict, _attachments: dict) -> dict:
    values = _arguments(
        arguments, allowed={"need", "name"}, required={"need", "name"},
    )
    return core.favorite_update_need(
        _string(values, "need"), _string(values, "name"),
    ).to_dict()


def _favorites_remove(arguments: dict, _attachments: dict) -> dict:
    values = _arguments(arguments, allowed={"need"}, required={"need"})
    need = core.favorite_remove_need(_string(values, "need"))
    return {"id": need.id, "removed": True}


def _favorites_alias_add(arguments: dict, _attachments: dict) -> dict:
    values = _arguments(
        arguments, allowed={"need", "alias"}, required={"need", "alias"},
    )
    return core.favorite_add_alias(
        _string(values, "need"), _string(values, "alias"),
    ).to_dict()


def _favorites_alias_remove(arguments: dict, _attachments: dict) -> dict:
    values = _arguments(
        arguments, allowed={"need", "alias"}, required={"need", "alias"},
    )
    return core.favorite_remove_alias(
        _string(values, "need"), _string(values, "alias"),
    ).to_dict()


def _favorites_product_add(arguments: dict, attachments: dict) -> dict:
    values = _arguments(
        arguments,
        allowed={"need", "name", "brand", "store", "note"},
        required={"need", "name"},
    )
    return core.favorite_add_product(
        _string(values, "need"),
        _string(values, "name"),
        brand=_string(values, "brand", default=""),
        store=_string(values, "store", default=""),
        note=_string(values, "note", default=""),
        image=attachments.get("image"),
    ).to_dict()


def _favorites_product_set(arguments: dict, attachments: dict) -> dict:
    values = _arguments(
        arguments,
        allowed={
            "need", "id", "name", "brand", "store", "note", "remove_image",
        },
        required={"need", "id"},
    )
    need_id = _string(values, "need")
    product_id = _string(values, "id")
    name = _string(values, "name", optional=True)
    brand = _string(values, "brand", optional=True)
    store = _string(values, "store", optional=True)
    note = _string(values, "note", optional=True)
    remove_image = _boolean(values, "remove_image")
    if (name is None and brand is None and store is None and note is None
            and not remove_image and "image" not in attachments):
        raise CommandError("Gib mindestens eine Änderung an.")
    return core.favorite_update_product(
        need_id,
        product_id,
        name=name,
        brand=brand,
        store=store,
        note=note,
        image=attachments.get("image"),
        remove_image=remove_image,
    ).to_dict()


def _favorites_product_move(arguments: dict, _attachments: dict) -> dict:
    values = _arguments(
        arguments, allowed={"need", "id", "position"},
        required={"need", "id", "position"},
    )
    return core.favorite_move_product(
        _string(values, "need"),
        _string(values, "id"),
        _integer(values, "position", optional=False),
    ).to_dict()


def _favorites_product_remove(arguments: dict, _attachments: dict) -> dict:
    values = _arguments(
        arguments, allowed={"need", "id"}, required={"need", "id"},
    )
    return core.favorite_remove_product(
        _string(values, "need"), _string(values, "id"),
    ).to_dict()


def _shopping_list(arguments: dict, _attachments: dict) -> list[dict]:
    values = _arguments(arguments, allowed={"pending"})
    items = core.shopping_list(
        include_done=not _boolean(values, "pending"),
    )
    return [item.to_dict() for item in items]


def _shopping_add(arguments: dict, _attachments: dict) -> dict:
    values = _arguments(
        arguments, allowed={"text", "quantity", "source"}, required={"text"},
    )
    return core.shopping_add(
        _string(values, "text"),
        quantity=_string(values, "quantity", default=""),
        source=_string(values, "source", optional=True),
    ).to_dict()


def _shopping_add_many(arguments: dict, _attachments: dict) -> list[dict]:
    values = _arguments(arguments, allowed={"texts"}, required={"texts"})
    return [
        item.to_dict()
        for item in core.shopping_add_many(_strings(values, "texts"))
    ]


def _shopping_add_recipe(arguments: dict, _attachments: dict) -> list[dict]:
    values = _arguments(arguments, allowed={"slug"}, required={"slug"})
    return [
        item.to_dict()
        for item in core.shopping_add_recipe(_string(values, "slug"))
    ]


def _shopping_check(arguments: dict, _attachments: dict) -> dict:
    values = _arguments(arguments, allowed={"id"}, required={"id"})
    return core.shopping_toggle(
        _string(values, "id"), checked=True,
    ).to_dict()


def _shopping_uncheck(arguments: dict, _attachments: dict) -> dict:
    values = _arguments(arguments, allowed={"id"}, required={"id"})
    return core.shopping_toggle(
        _string(values, "id"), checked=False,
    ).to_dict()


def _shopping_remove(arguments: dict, _attachments: dict) -> dict:
    values = _arguments(arguments, allowed={"id"}, required={"id"})
    item_id = _string(values, "id")
    core.shopping_remove(item_id)
    return {"id": item_id, "deleted": True}


def _shopping_remove_done(arguments: dict, _attachments: dict) -> dict:
    _arguments(arguments, allowed=set())
    return {"removed": core.shopping_remove_done()}


def _shopping_clear(arguments: dict, _attachments: dict) -> dict:
    _arguments(arguments, allowed=set())
    return {"removed": core.shopping_clear()}


_OPERATIONS: dict[str, Callable[[dict, dict], object]] = {
    "catalog.list": _catalog_list,
    "catalog.search": _catalog_search,
    "tags.list": _tags_list,
    "categories.list": _categories_list,
    "categories.add": _categories_add,
    "categories.set": _categories_set,
    "categories.remove": _categories_remove,
    "categories.assign": _categories_assign,
    "categories.unassign": _categories_unassign,
    "categories.tag.move": _categories_tag_move,
    "recipe.show": _recipe_show,
    "recipe.create": _recipe_create,
    "recipe.content.set": _recipe_content_set,
    "recipe.update": _recipe_update,
    "recipe.cooked": _recipe_cooked,
    "log.list": _log_list,
    "suggest.list": _suggest_list,
    "check.run": _check_run,
    "recipe.archive": _recipe_archive,
    "archive.list": _archive_list,
    "archive.show": _archive_show,
    "archive.restore": _archive_restore,
    "archive.purge": _archive_purge,
    "image.list": _image_list,
    "image.add": _image_add,
    "image.set": _image_set,
    "image.cover": _image_cover,
    "image.remove": _image_remove,
    "favorites.list": _favorites_list,
    "favorites.show": _favorites_show,
    "favorites.match": _favorites_match,
    "favorites.add": _favorites_add,
    "favorites.set": _favorites_set,
    "favorites.remove": _favorites_remove,
    "favorites.alias.add": _favorites_alias_add,
    "favorites.alias.remove": _favorites_alias_remove,
    "favorites.product.add": _favorites_product_add,
    "favorites.product.set": _favorites_product_set,
    "favorites.product.move": _favorites_product_move,
    "favorites.product.remove": _favorites_product_remove,
    "shopping.list": _shopping_list,
    "shopping.add": _shopping_add,
    "shopping.add_many": _shopping_add_many,
    "shopping.add_recipe": _shopping_add_recipe,
    "shopping.check": _shopping_check,
    "shopping.uncheck": _shopping_uncheck,
    "shopping.remove": _shopping_remove,
    "shopping.remove_done": _shopping_remove_done,
    "shopping.clear": _shopping_clear,
}


def execute_command(operation: str, arguments: dict,
                    attachments: list[dict] | None = None):
    """Validate and execute one command, raising only expected CommandErrors."""
    if not isinstance(operation, str) or not operation:
        raise CommandError("'operation' muss ein nicht-leerer Text sein.")
    function = _OPERATIONS.get(operation)
    if function is None:
        raise CommandError(f"Unbekannte Operation '{operation}'.")
    raw_attachments = _validate_attachments(
        operation, [] if attachments is None else attachments,
    )
    with _materialized_attachments(raw_attachments) as paths:
        try:
            return function(arguments, paths)
        except CommandError:
            raise
        except ValueError as error:
            raise CommandError(str(error), _not_found_error(error)) from error


@router.get("/health")
def health():
    state = core.change_state()
    return {
        "ok": True,
        "status": "ready",
        "version": __version__,
        "data_path": os.fspath(core.project_root()),
        **state,
    }


@router.post("/command")
async def command(request: Request):
    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            declared = int(content_length)
        except ValueError:
            return _error("Der Content-Length-Header muss eine ganze Zahl sein.")
        if declared > MAX_REQUEST_BYTES:
            return _error(
                "Der Request-Body ist größer als "
                f"{MAX_REQUEST_BYTES // (1024 * 1024)} MB.",
                413,
            )
    try:
        body = await request.json()
    except Exception:
        return _error("Ungültiges JSON.")
    if not isinstance(body, dict):
        return _error("Der Request-Body muss ein JSON-Objekt sein.")
    unknown = set(body) - _BODY_KEYS
    if unknown:
        return _error("Unbekannte Felder: " + ", ".join(sorted(unknown)) + ".")
    try:
        if "operation" not in body:
            raise CommandError("'operation' fehlt.")
        if "attachments" in body and body["attachments"] is None:
            raise CommandError("'attachments' muss eine Liste sein.")
        result = execute_command(
            body["operation"],
            body.get("arguments", {}),
            body.get("attachments", []),
        )
    except CommandError as error:
        return _error(str(error), error.status_code)
    return {"ok": True, "result": result}


def _sse_data(event: dict) -> str:
    return "data: " + json.dumps(
        {
            "revision": event["revision"],
            "resources": event["resources"],
        },
        ensure_ascii=False,
        separators=(",", ":"),
    ) + "\n\n"


async def event_stream(request: Request, *, after_revision: int | None = None,
                       poll_seconds: float = 0.2,
                       heartbeat_seconds: float = 15.0):
    """Yield an initial state/replay, then new retained events and heartbeats."""
    current = core.change_state()
    if after_revision is None:
        initial_events = [current]
        revision = current["revision"]
    elif after_revision > current["revision"]:
        # A client revision from another/reset store must not suppress all
        # future events in this store.
        initial_events = [current]
        revision = current["revision"]
    else:
        initial_events = core.change_events(after_revision)
        revision = after_revision
    for event in initial_events:
        yield _sse_data(event)
        revision = event["revision"]
    last_output = time.monotonic()
    while True:
        if await request.is_disconnected():
            return
        events = core.change_events(revision)
        if events:
            for event in events:
                yield _sse_data(event)
                revision = event["revision"]
            last_output = time.monotonic()
            continue
        if time.monotonic() - last_output >= heartbeat_seconds:
            yield ": heartbeat\n\n"
            last_output = time.monotonic()
        await asyncio.sleep(poll_seconds)


@router.get("/events")
def events(request: Request, after: str | None = None):
    after_revision = None
    if after is not None:
        try:
            after_revision = int(after)
        except ValueError:
            return _error("'after' muss eine nichtnegative ganze Zahl sein.")
        if after_revision < 0 or str(after_revision) != after:
            return _error("'after' muss eine nichtnegative ganze Zahl sein.")
    return StreamingResponse(
        event_stream(request, after_revision=after_revision),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
