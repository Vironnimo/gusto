"""Command line for Gusto.

Normal domain commands are thin clients of the running Gusto server. Explicit
lifecycle and recovery commands remain local. Every command understands
``--json``; user-facing output and help text stay German.
"""
from __future__ import annotations

import argparse
import importlib
import json
import os
import subprocess
import sys
import tempfile
import traceback
from collections.abc import Callable, Sequence
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime
from pathlib import Path

from . import client


def _dump(data) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2))


def _open_editor(path, *, quiet: bool = False) -> dict:
    editor = os.environ.get("EDITOR") or ("notepad" if os.name == "nt" else "nano")
    options = {}
    if quiet:
        options = {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
    try:
        exit_code = subprocess.call([editor, str(path)], **options)
    except OSError as error:
        raise ValueError(f"Editor '{editor}' konnte nicht gestartet werden: {error}") from error
    if exit_code:
        raise ValueError(f"Editor '{editor}' wurde mit Status {exit_code} beendet.")
    return {
        "editor": editor,
        "path": os.fspath(Path(path).resolve()),
        "exit_code": exit_code,
    }


def _collect_tags(values) -> list[str]:
    """--tag may be given multiple times AND comma-separated -> flat tag list."""
    out: list[str] = []
    for v in values or []:
        out.extend(t.strip() for t in v.split(",") if t.strip())
    return out


def _port(value: str) -> int:
    try:
        port = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("Port muss eine ganze Zahl sein.") from error
    if not 1 <= port <= 65535:
        raise argparse.ArgumentTypeError("Port muss zwischen 1 und 65535 liegen.")
    return port


def _remote(args, operation: str, arguments: dict | None = None, *,
            attachments: list[dict] | None = None):
    return client.command(
        operation,
        arguments,
        attachments=attachments,
        server_url=getattr(args, "server", None),
    )


def _print_list(recipes: list[dict], as_json: bool) -> None:
    if as_json:
        _dump(recipes)
        return
    if not recipes:
        print("Keine Rezepte gefunden.")
        return
    for r in recipes:
        meta = []
        if r.get("duration_min"):
            meta.append(f"{r['duration_min']} min")
        if r.get("servings"):
            meta.append(f"{r['servings']} P.")
        if r.get("tags"):
            meta.append(", ".join(r["tags"]))
        if r.get("images"):
            meta.append(
                f"{len(r['images'])} Bild"
                + ("er" if len(r["images"]) != 1 else "")
            )
        extra = "  ·  ".join(meta)
        print(
            f"  {r['slug']:<22} {r['title']}"
            + (f"   [{extra}]" if extra else "")
        )


def _print_tag_warnings(warnings: list[dict]) -> None:
    for warning in warnings:
        print(f"Warnung: {warning['message']}")


# --- Commands ---------------------------------------------------------------

def cmd_list(args):
    recipes = _remote(args, "catalog.list", {
        "tags": _collect_tags(args.tag),
        "max_time": args.max_time,
    })
    _print_list(recipes, args.json)


def cmd_search(args):
    recipes = _remote(args, "catalog.search", {
        "query": args.query,
        "match": args.match,
        "tags": _collect_tags(args.tag),
        "max_time": args.max_time,
    })
    _print_list(recipes, args.json)


def cmd_tags(args):
    groups = _remote(args, "tags.list", {"all": args.all})
    if args.json:
        _dump(groups)
        return
    if not groups:
        print("Keine Tag-Kategorien definiert (data/categories.json fehlt?).")
        return
    for g in groups:
        print(f"{g['label']}:")
        print("  " + (", ".join(g["tags"]) if g["tags"] else "—"))


def cmd_home(args):
    from . import core

    info = core.storage_info()
    server_url = client.resolve_server_url(getattr(args, "server", None))
    info["server_url"] = server_url
    try:
        service = client.health(server_url, retries=0)
    except client.ClientError as error:
        info["server_reachable"] = False
        info["service_status"] = "unreachable"
        info["service_error"] = str(error)
    else:
        info["server_reachable"] = True
        info["service_status"] = str(service.get("status", "running"))
        info["server_data_path"] = service.get("data_path")
        info["server_version"] = service.get("version")
        info["server_revision"] = service.get("revision")
    if args.json:
        _dump(info)
        return
    labels = {
        "environment": "GUSTO_HOME",
        "settings": "Instanz-Settings",
        "legacy": "bestehender Checkout (Kompatibilitätsmodus)",
        "platform_default": "Standard-Benutzerdatenordner",
    }
    print(info["path"])
    print(f"  Quelle: {labels.get(info['source'], info['source'])}")
    if info["source"] == "settings":
        print(f"  Settings: {info['settings_path']}")
    if info["source"] == "legacy":
        print(f"  Neuer Plattformstandard: {info['platform_default']}")
    print(f"  Server: {server_url}")
    print(
        "  Dienst: "
        + ("erreichbar" if info["server_reachable"] else "nicht erreichbar")
    )
    if info["server_reachable"] and info.get("server_data_path"):
        print(f"  Server-Daten: {info['server_data_path']}")


def cmd_show(args):
    recipe = _remote(args, "recipe.show", {"slug": args.slug})
    if args.json:
        _dump(recipe)
    else:
        print(recipe["content"].rstrip())


def cmd_new(args):
    tags = [t.strip() for t in (args.tags or "").split(",") if t.strip()]
    recipe = _remote(args, "recipe.create", {
        "title": args.title,
        "tags": tags,
        "duration_min": args.duration,
        "servings": args.servings,
    })
    editor_result = None
    if args.edit:
        editor_result = _edit_remote(args, recipe["slug"])
    if args.json:
        result = dict(recipe)
        if editor_result is not None:
            result["editor"] = editor_result
        _dump(result)
    else:
        print(f"Angelegt: {recipe['slug']}")
        _print_tag_warnings(recipe.get("warnings", []))


def _edit_remote(args, slug: str) -> dict:
    recipe = _remote(args, "recipe.show", {"slug": slug})
    handle = tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", suffix=".md", prefix=f"gusto-{slug}-",
        delete=False,
    )
    temporary = Path(handle.name)
    try:
        with handle:
            handle.write(recipe["content"])
        editor_result = _open_editor(temporary, quiet=args.json)
        try:
            content = temporary.read_text(encoding="utf-8")
        except OSError as error:
            raise client.ClientError(
                f"Bearbeiteter Rezepttext konnte nicht gelesen werden: {error}"
            ) from error
        _remote(
            args, "recipe.content.set", {"slug": slug, "content": content},
        )
    finally:
        temporary.unlink(missing_ok=True)
    editor_result["slug"] = slug
    return editor_result


def cmd_edit(args):
    result = _edit_remote(args, args.slug)
    if args.json:
        _dump(result)


def cmd_content_set(args):
    if args.stdin:
        content = sys.stdin.read()
    else:
        try:
            content = Path(args.file).expanduser().read_text(encoding="utf-8")
        except OSError as error:
            raise client.ClientError(
                f"Rezeptdatei '{args.file}' konnte nicht gelesen werden: {error}"
            ) from error
    recipe = _remote(
        args, "recipe.content.set", {"slug": args.slug, "content": content},
    )
    if args.json:
        _dump(recipe)
    else:
        print(f"Inhalt aktualisiert: {args.slug}")


def cmd_cooked(args):
    result = _remote(
        args, "recipe.cooked", {"slug": args.slug, "date": args.date},
    )
    if args.json:
        _dump(result)
    else:
        print(f"Notiert: '{args.slug}' am {result['date']} gekocht.")


def cmd_log(args):
    entries = _remote(args, "log.list", {"days": args.days})
    if args.json:
        _dump(entries)
        return
    if not entries:
        print("Logbuch ist leer.")
        return
    references = {
        recipe["slug"]: {"title": recipe["title"], "archived": False}
        for recipe in _remote(args, "catalog.list", {})
    }
    references.update({
        recipe["slug"]: {"title": recipe["title"], "archived": True}
        for recipe in _remote(args, "archive.list", {})
    })
    for e in reversed(entries):  # newest first
        reference = references.get(e["slug"])
        title = reference["title"] if reference else e["slug"]
        suffix = " (archiviert)" if reference and reference["archived"] else ""
        print(f"  {e['date']}   {title}{suffix}")


def cmd_suggest(args):
    candidates = _remote(
        args, "suggest.list", {"days": args.days, "limit": args.limit},
    )
    if args.json:
        _dump(candidates)
        return
    if not candidates:
        if args.limit == 0:
            print("Keine Vorschlaege angefordert (--limit 0).")
        else:
            print(f"Keine Vorschlaege – in den letzten {args.days} Tagen war schon alles dran.")
        return
    print(f"Vorschlaege (nicht in den letzten {args.days} Tagen gekocht):")
    for r in candidates:
        print(f"  {r['title']:<26} (zuletzt: {r.get('last_cooked') or 'noch nie'})")


def cmd_check(args):
    if args.offline:
        from . import core
        res = core.check()
    else:
        res = _remote(args, "check.run")
    if args.json:
        _dump(res)
    else:
        print(
            f"Aktiv: {res['recipe_count']} Rezept(e) · "
            f"Archiv: {res['archive_count']} Rezept(e)"
        )
        labels = {
            "duplicate_recipe_slugs": "Doppelte aktive Slugs",
            "orphaned_files": ".md ohne Index-Eintrag",
            "missing_files": "Index-Eintrag ohne .md",
            "title_mismatches": "Titel stimmt nicht mit Markdown-H1 überein",
            "orphaned_image_folders": "Bildordner ohne aktives Rezept",
            "orphaned_image_files": "Aktive Bilder ohne Metadaten",
            "missing_image_files": "Fehlende aktive Bilddateien",
            "invalid_cover_images": "Ungültige aktive Top-Bilder",
            "invalid_archive_entries": "Ungültige Archiveinträge",
            "orphaned_archive_root_files": "Dateien außerhalb eines Archiveintrags",
            "stale_archive_transactions": "Unvollständige Archivvorgänge",
            "missing_archive_files": "Archiv-Metadaten ohne Markdown",
            "archived_title_mismatches": "Archiv-Titel stimmt nicht mit H1 überein",
            "missing_archive_image_files": "Fehlende Archivbilder",
            "orphaned_archive_image_files": "Archivbilder ohne Metadaten",
            "invalid_archive_cover_images": "Ungültige Top-Bilder im Archiv",
            "active_archive_conflicts": "Slug gleichzeitig aktiv und archiviert",
            "unresolved_shopping_sources": "Unbekannte Quellen auf der Einkaufsliste",
            "duplicate_favorite_aliases": "Mehrdeutige Lieblingsprodukt-Aliasse",
            "orphaned_favorite_image_files": "Produktbilder ohne Metadaten",
            "missing_favorite_image_files": "Fehlende Produktbilder",
            "uncategorized_tags": "Tags ohne Kategorie",
            "unresolved_log_references": "Historische Log-Slugs ohne Rezept",
        }
        for diagnostic in res["errors"]:
            print(
                f"  Fehler · {labels.get(diagnostic['code'], diagnostic['code'])}: "
                + ", ".join(str(item) for item in diagnostic["items"])
            )
        for diagnostic in res["warnings"]:
            print(
                f"  Warnung · {labels.get(diagnostic['code'], diagnostic['code'])}: "
                + ", ".join(str(item) for item in diagnostic["items"])
            )
        if res["ok"] and not res["warnings"]:
            print("  Alles konsistent.")
        elif res["ok"]:
            print("  Keine Integritätsfehler.")
    if not res["ok"]:
        raise SystemExit(1)


def cmd_set(args):
    if not any([
        args.title is not None,
        args.tags is not None,
        args.duration is not None,
        args.servings is not None,
        args.clear_duration,
        args.clear_servings,
    ]):
        sys.exit("Gib mindestens eine Änderung an.")
    tags = None
    if args.tags is not None:
        tags = [t.strip() for t in args.tags.split(",") if t.strip()]
    recipe = _remote(args, "recipe.update", {
        "slug": args.slug,
        "title": args.title,
        "tags": tags,
        "duration_min": args.duration,
        "servings": args.servings,
        "clear_duration": args.clear_duration,
        "clear_servings": args.clear_servings,
    })
    if args.json:
        _dump(recipe)
    else:
        print(f"Aktualisiert: {recipe['slug']}")
        _print_tag_warnings(recipe.get("warnings", []))


def cmd_delete(args):
    archived = _remote(args, "recipe.archive", {"slug": args.slug})
    if args.json:
        _dump(archived)
    else:
        print(f"Archiviert: {archived['slug']}")


def cmd_archive_list(args):
    entries = _remote(args, "archive.list")
    if args.json:
        _dump(entries)
        return
    if not entries:
        print("Das Rezeptarchiv ist leer.")
        return
    for entry in entries:
        print(
            f"  {entry['slug']:<22} {entry['title']}   "
            f"[{entry['archived_at']}]"
        )


def cmd_archive_show(args):
    entry = _remote(args, "archive.show", {"slug": args.slug})
    if args.json:
        _dump(entry)
    else:
        print(entry["content"].rstrip())


def cmd_archive_restore(args):
    result = _remote(args, "archive.restore", {"slug": args.slug})
    if args.json:
        _dump(result)
    else:
        print(f"Wiederhergestellt: {result['recipe']['slug']}")


def cmd_archive_purge(args):
    if not args.yes:
        sys.exit(
            "Endgültiges Löschen braucht --yes. Der Archiveintrag kann danach "
            "nicht wiederhergestellt werden."
        )
    result = _remote(
        args, "archive.purge", {"slug": args.slug, "yes": True},
    )
    if args.json:
        _dump(result)
    else:
        print(f"Endgültig gelöscht: {result['slug']}")


# --- Recipe images ----------------------------------------------------------

def cmd_image_list(args):
    result = _remote(args, "image.list", {"slug": args.slug})
    if args.json:
        _dump(result)
        return
    images = result["images"]
    cover_id = result["cover_image_id"]
    if not images:
        print("Keine Bilder bei diesem Rezept.")
        return
    for image in images:
        marker = " [Top-Bild]" if image["id"] == cover_id else ""
        caption = f" — {image['caption']}" if image.get("caption") else ""
        print(f"  {image['id']}  {image['role']}{marker}{caption}")


def cmd_image_add(args):
    image = _remote(
        args,
        "image.add",
        {
            "slug": args.slug,
            "role": args.role,
            "caption": args.caption,
            "cover": args.cover,
        },
        attachments=[client.encode_attachment("image", args.path)],
    )
    if args.json:
        _dump(image)
    else:
        marker = " (Top-Bild)" if image.get("is_cover") else ""
        print(f"Bild hinzugefuegt: {image['id']}{marker}")


def cmd_image_set(args):
    if args.role is None and args.caption is None:
        sys.exit("Gib --role und/oder --caption an.")
    image = _remote(args, "image.set", {
        "slug": args.slug,
        "id": args.id,
        "role": args.role,
        "caption": args.caption,
    })
    if args.json:
        _dump(image)
    else:
        print(f"Bild aktualisiert: {image['id']}")


def cmd_image_cover(args):
    result = _remote(
        args, "image.cover", {"slug": args.slug, "id": args.id},
    )
    if args.json:
        _dump(result)
    else:
        print(f"Top-Bild gesetzt: {result['cover_image_id']}")


def cmd_image_remove(args):
    result = _remote(
        args, "image.remove", {"slug": args.slug, "id": args.id},
    )
    if args.json:
        _dump(result)
    else:
        print(f"Bild entfernt: {args.id}")


# --- Preferred products -----------------------------------------------------

def cmd_favorites_list(args):
    needs = _remote(args, "favorites.list")
    if args.json:
        _dump(needs)
        return
    if not needs:
        print("Noch keine Lieblingsprodukte hinterlegt.")
        return
    for need in needs:
        count = len(need["products"])
        noun = "Produkt" if count == 1 else "Produkte"
        print(f"  {need['id']}  {need['name']}  ({count} {noun})")


def cmd_favorites_show(args):
    need = _remote(args, "favorites.show", {"need": args.need})
    if args.json:
        _dump(need)
        return
    print(need["name"])
    if need["aliases"]:
        print("  Aliasse: " + ", ".join(need["aliases"]))
    for position, product in enumerate(need["products"], 1):
        details = " · ".join(
            value for value in [product["brand"], product.get("store")] if value
        )
        print(
            f"  {position}. {product['name']}"
            + (f"  [{details}]" if details else "")
        )


def cmd_favorites_match(args):
    need = _remote(args, "favorites.match", {"text": args.text})
    if args.json:
        _dump(need)
    elif need is None:
        print("Keine Zuordnung gefunden.")
    else:
        print(f"{args.text} -> {need['name']}")


def cmd_favorites_add(args):
    need = _remote(
        args, "favorites.add", {"name": args.name, "aliases": args.alias},
    )
    if args.json:
        _dump(need)
    else:
        print(f"Einkaufsbedarf angelegt: {need['name']} ({need['id']})")


def cmd_favorites_set(args):
    need = _remote(
        args, "favorites.set", {"need": args.need, "name": args.name},
    )
    if args.json:
        _dump(need)
    else:
        print(f"Einkaufsbedarf aktualisiert: {need['name']}")


def cmd_favorites_remove(args):
    result = _remote(args, "favorites.remove", {"need": args.need})
    if args.json:
        _dump(result)
    else:
        print(f"Einkaufsbedarf entfernt: {args.need}")


def cmd_favorites_alias_add(args):
    need = _remote(
        args, "favorites.alias.add", {"need": args.need, "alias": args.alias},
    )
    if args.json:
        _dump(need)
    else:
        print(f"Alias bei '{need['name']}' hinterlegt: {args.alias}")


def cmd_favorites_alias_remove(args):
    need = _remote(
        args, "favorites.alias.remove",
        {"need": args.need, "alias": args.alias},
    )
    if args.json:
        _dump(need)
    else:
        print(f"Alias bei '{need['name']}' entfernt: {args.alias}")


def cmd_favorites_product_add(args):
    attachments = (
        [client.encode_attachment("image", args.image)] if args.image else None
    )
    product = _remote(
        args,
        "favorites.product.add",
        {
            "need": args.need,
            "name": args.name,
            "brand": args.brand or "",
            "store": args.store or "",
            "note": args.note or "",
        },
        attachments=attachments,
    )
    if args.json:
        _dump(product)
    else:
        print(
            f"Lieblingsprodukt hinzugefuegt: "
            f"{product['name']} ({product['id']})"
        )


def cmd_favorites_product_set(args):
    if not any(value is not None for value in
               [args.name, args.brand, args.store, args.note, args.image]) \
            and not args.remove_image:
        sys.exit("Gib mindestens eine Aenderung an.")
    attachments = (
        [client.encode_attachment("image", args.image)] if args.image else None
    )
    product = _remote(
        args,
        "favorites.product.set",
        {
            "need": args.need,
            "id": args.id,
            "name": args.name,
            "brand": args.brand,
            "store": args.store,
            "note": args.note,
            "remove_image": args.remove_image,
        },
        attachments=attachments,
    )
    if args.json:
        _dump(product)
    else:
        print(f"Lieblingsprodukt aktualisiert: {product['name']}")


def cmd_favorites_product_move(args):
    need = _remote(args, "favorites.product.move", {
        "need": args.need,
        "id": args.id,
        "position": args.position,
    })
    if args.json:
        _dump(need)
    else:
        print(f"Reihenfolge bei '{need['name']}' aktualisiert.")


def cmd_favorites_product_remove(args):
    need = _remote(
        args, "favorites.product.remove", {"need": args.need, "id": args.id},
    )
    if args.json:
        _dump(need)
    else:
        print(f"Lieblingsprodukt bei '{need['name']}' entfernt.")


# --- Shopping list ----------------------------------------------------------

def _print_shopping_item(item: dict, as_json: bool, *, prefix: str = "") -> None:
    if as_json:
        _dump(item)
        return
    marker = "[x]" if item["checked"] else "[ ]"
    line = f"{marker} {item['id']}  {item['text']}"
    if item.get("quantity"):
        line += f"  ({item['quantity']})"
    if item.get("source"):
        line += f"  (aus {item['source']})"
    print(prefix + line)


def cmd_shopping_list(args):
    items = _remote(args, "shopping.list", {"pending": args.pending})
    if args.json:
        _dump(items)
        return
    if not items:
        print("Einkaufsliste ist leer.")
        return
    for i in items:
        _print_shopping_item(i, False, prefix="  ")


def cmd_shopping_add(args):
    item = _remote(args, "shopping.add", {
        "text": args.text,
        "quantity": args.quantity or "",
        "source": args.source,
    })
    if args.json:
        _dump(item)
    else:
        _print_shopping_item(item, False, prefix="Hinzugefuegt: ")


def cmd_shopping_add_many(args):
    items = _remote(args, "shopping.add_many", {"texts": args.texts})
    if args.json:
        _dump(items)
    else:
        print(f"{len(items)} Einkaufsposten hinzugefuegt.")


def cmd_shopping_add_recipe(args):
    items = _remote(args, "shopping.add_recipe", {"slug": args.slug})
    if args.json:
        _dump(items)
    else:
        print(f"{len(items)} Zutat(en) aus '{args.slug}' hinzugefuegt.")


def cmd_shopping_check(args):
    item = _remote(args, "shopping.check", {"id": args.id})
    if args.json:
        _dump(item)
    else:
        _print_shopping_item(item, False, prefix="Abgehakt: ")


def cmd_shopping_uncheck(args):
    item = _remote(args, "shopping.uncheck", {"id": args.id})
    if args.json:
        _dump(item)
    else:
        _print_shopping_item(item, False, prefix="Wieder offen: ")


def cmd_shopping_remove(args):
    result = _remote(args, "shopping.remove", {"id": args.id})
    if args.json:
        _dump(result)
    else:
        print(f"Entfernt: {args.id}")


def cmd_shopping_remove_done(args):
    result = _remote(args, "shopping.remove_done")
    if args.json:
        _dump(result)
    else:
        count = result["removed"]
        noun = "erledigter Eintrag" if count == 1 else "erledigte Einträge"
        print(f"{count} {noun} entfernt.")


def cmd_shopping_clear(args):
    result = _remote(args, "shopping.clear")
    if args.json:
        _dump(result)
    else:
        count = result["removed"]
        noun = "Eintrag" if count == 1 else "Einträge"
        print(f"{count} {noun} entfernt; die Einkaufsliste ist leer.")


_WEB_DEPENDENCIES = (
    ("fastapi", ("fastapi",)),
    ("uvicorn[standard]", ("uvicorn",)),
    ("jinja2", ("jinja2",)),
    ("markdown", ("markdown",)),
    ("python-multipart", ("python_multipart", "multipart.multipart")),
    ("pillow", ("PIL.Image",)),
)


def _missing_web_dependencies() -> list[str]:
    missing = []
    for requirement, module_names in _WEB_DEPENDENCIES:
        for module_name in module_names:
            try:
                importlib.import_module(module_name)
                break
            except ImportError:
                continue
        else:
            missing.append(requirement)
    return missing


def _web_dependency_error(missing: list[str]) -> str:
    return (
        "Web-Abhängigkeiten fehlen oder sind nicht importierbar: "
        f"{', '.join(missing)}. Installiere Gusto mit Web-Unterstützung. "
        'Projekt-Checkout: python -m pip install -e ".[web]". '
        "Release-Paket: python install.py (ohne --cli-only)."
    )


def _load_web_runtime():
    missing = _missing_web_dependencies()
    if missing:
        sys.exit(_web_dependency_error(missing))
    importlib.import_module("gusto.web")
    return importlib.import_module("uvicorn")


def cmd_serve(args):
    uvicorn = _load_web_runtime()
    local_host = "127.0.0.1" if args.host in {"0.0.0.0", "::"} else args.host
    url = f"http://{local_host}:{args.port}"
    if args.json:
        _dump({
            "status": "starting",
            "host": args.host,
            "port": args.port,
            "url": url,
            "reload": args.reload,
        })
        sys.stdout.flush()
    else:
        print(f"Gusto laeuft auf {url}  (Strg+C zum Beenden)")
    uvicorn.run("gusto.web:app", host=args.host, port=args.port, reload=args.reload)


def cmd_service(args):
    from . import service

    try:
        result = service.service_action(
            args.command,
            app_root=args.app_root,
            dry_run=args.dry_run,
        )
    except (OSError, ValueError, service.ServiceError) as error:
        raise ValueError(f"Service-Aktion fehlgeschlagen: {error}") from error

    if args.json:
        _dump(result)
        return
    if args.dry_run:
        print(f"Geplant: Gusto {args.command}")
        if result.get("command"):
            print("  " + subprocess.list2cmdline(result["command"]))
        return
    labels = {
        "status": "Status",
        "start": "Gusto wurde gestartet.",
        "stop": "Gusto wurde gestoppt.",
        "restart": "Gusto wurde neu gestartet.",
    }
    if args.command == "status":
        state = "läuft" if result["running"] else "ist gestoppt"
        print(f"Gusto {state} (Version {result['version']}).")
    else:
        print(labels[args.command])
    if result.get("url"):
        print(f"  {result['url']}")


def cmd_update(args):
    from . import update

    try:
        result = update.run_update(
            app_root=args.app_root,
            manifest_url=args.manifest_url,
            check=args.check,
            dry_run=args.dry_run,
        )
    except update.UpdateError as error:
        if args.json and error.result:
            _dump(error.result)
            raise SystemExit(1) from None
        raise ValueError(f"Update fehlgeschlagen: {error}") from error
    except (OSError, ValueError) as error:
        raise ValueError(f"Update fehlgeschlagen: {error}") from error

    if args.json:
        _dump(result)
        return
    status = result["status"]
    if status == "current":
        print(f"Gusto ist aktuell (Version {result['current_version']}).")
    elif status == "update_available":
        print(
            f"Update verfügbar: {result['current_version']} → "
            f"{result['latest_version']}"
        )
    elif status == "dry_run":
        print(
            f"Geplantes Update: {result['current_version']} → "
            f"{result['latest_version']}"
        )
    elif status == "updated":
        print(
            f"Gusto wurde von {result['previous_version']} auf "
            f"{result['current_version']} aktualisiert."
        )


def _uninstall_choice(
    args,
    targets,
    *,
    interactive: bool,
    input_func: Callable[[str], str] = input,
) -> bool | None:
    """Return whether to delete data; ``None`` means an interactive cancel."""
    from . import uninstall

    if args.keep_data:
        return False
    if args.delete_data:
        if args.yes or args.dry_run:
            return True
        if not interactive:
            raise uninstall.UninstallError(
                "--delete-data braucht in nicht-interaktiven Aufrufen zusätzlich --yes."
            )
        answer = input_func(
            f"Alle Rezepte und Daten unter '{targets.data}' löschen? "
            "Tippe exakt DATEN LÖSCHEN: "
        )
        if answer != "DATEN LÖSCHEN":
            return None
        return True

    if not interactive:
        raise uninstall.UninstallError(
            "Wähle --keep-data oder --delete-data; mit --delete-data ist --yes nötig."
        )

    print(f"Installation: {targets.application}")
    print(f"Daten:        {targets.data}")
    print()
    print("Was soll entfernt werden?")
    print("  1  Nur die Gusto-App (Rezepte und Daten behalten)")
    print("  2  Gusto-App und alle Rezepte/Daten unwiderruflich löschen")
    print("  3  Abbrechen")
    while True:
        choice = input_func("Auswahl [1-3]: ").strip()
        if choice == "1":
            return False
        if choice == "3":
            return None
        if choice == "2":
            if args.yes or args.dry_run:
                return True
            answer = input_func("Tippe zur Bestätigung exakt DATEN LÖSCHEN: ")
            return True if answer == "DATEN LÖSCHEN" else None
        print("Bitte 1, 2 oder 3 eingeben.")


def cmd_uninstall(args):
    from . import uninstall

    try:
        targets = uninstall.discover_targets()
        interactive = not args.json and sys.stdin.isatty()
        delete_data = _uninstall_choice(
            args, targets, interactive=interactive,
        )
        if delete_data is None:
            if args.json:
                _dump({"status": "cancelled"})
            else:
                print("Deinstallation abgebrochen.")
            return
        result = uninstall.perform_uninstall(
            targets,
            delete_data=delete_data,
            dry_run=args.dry_run,
            interactive=interactive,
        )
    except (OSError, ValueError, uninstall.UninstallError) as error:
        sys.exit(f"Deinstallation fehlgeschlagen: {error}")

    if args.json:
        _dump(result)
        return
    if args.dry_run:
        print("Geplante Deinstallation (es wurde nichts verändert):")
    else:
        print("Gusto wird nach dem Ende dieses Befehls entfernt.")
    print(f"  App:   {result['application_path']}")
    if result["delete_data"]:
        print(f"  Daten: löschen ({result['data_path']})")
    else:
        print(f"  Daten: behalten ({result['data_path']})")
    if result.get("log_path"):
        print(f"  Log:   {result['log_path']}")


def _autostart_log_path() -> Path:
    """Return the log owned by the active Gusto data store.

    Resolving instance settings can itself fail.  Keep the GUI launcher
    windowless in that case and give it a platform-default place to record the
    later startup error.
    """
    from . import core

    try:
        root = core.project_root()
    except (OSError, ValueError):
        root = core.default_data_root()
    return root / "gusto-autostart.log"


def _system_exit_code(code: object) -> int:
    """Convert SystemExit's permissive payload to a process exit code."""
    if code is None:
        return 0
    if isinstance(code, int):
        return code
    print(code, file=sys.stderr)
    return 1


def autostart_main(
    argv: Sequence[str] | None = None,
    *,
    run_cli: Callable[[Sequence[str]], object] | None = None,
    log_path: str | os.PathLike[str] | None = None,
) -> int:
    """Run ``gusto serve`` through the windowless Windows GUI entry point.

    A ``gui-scripts`` launcher has no console, so stdout and stderr may be
    ``None`` under pythonw.exe.  Redirect both before entering the normal CLI
    path; this preserves its behavior and exit code while keeping diagnostics
    in a durable file instead of opening a login console.
    """
    arguments = list(sys.argv[1:] if argv is None else argv)
    destination = Path(log_path) if log_path is not None else _autostart_log_path()
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        output = destination.open("a", encoding="utf-8", buffering=1)
    except OSError:
        output = open(os.devnull, "w", encoding="utf-8")

    with output, redirect_stdout(output), redirect_stderr(output):
        print(
            f"\n[{datetime.now().isoformat(timespec='seconds')}] "
            f"Gusto-Autostart (PID {os.getpid()})"
        )
        try:
            result = (run_cli or main)(["serve", *arguments])
        except SystemExit as error:
            return _system_exit_code(error.code)
        except KeyboardInterrupt:
            return 130
        except Exception:
            traceback.print_exc()
            return 1
        return 0 if result is None else int(result)


# --- Parser -----------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="gusto",
        description="Markdown-Rezepte – komplett per CLI steuerbar. "
                    "Jedes Kommando versteht --json.",
    )
    p.add_argument(
        "--server",
        help="Gusto-Server-URL (vor GUSTO_URL und Instanz-Settings).",
    )
    sub = p.add_subparsers(dest="command", required=True)

    base = argparse.ArgumentParser(add_help=False)
    base.add_argument(
        "--server",
        default=argparse.SUPPRESS,
        help="Gusto-Server-URL (vor GUSTO_URL und Instanz-Settings).",
    )
    base.add_argument("--json", action="store_true",
                      help="Maschinenlesbare Ausgabe (fuer Agents/Skripte).")

    tag_help = ("Nach Tag filtern; mehrfach oder kommagetrennt moeglich "
                "(--tag italienisch --tag pizza  bzw.  --tag italienisch,pizza). "
                "ODER innerhalb einer Kategorie, UND ueber Kategorien.")

    max_time_help = (
        "Positive max. Dauer in Minuten; Rezepte ohne Dauerangabe werden ausgeschlossen."
    )

    sp = sub.add_parser("list", parents=[base], help="Rezepte auflisten/filtern.")
    sp.add_argument("--tag", action="append", metavar="TAG", help=tag_help)
    sp.add_argument("--max-time", type=int, dest="max_time", help=max_time_help)
    sp.set_defaults(func=cmd_list)

    sp = sub.add_parser("search", parents=[base],
                        help="Volltextsuche ueber Titel, Tags und Zutaten/Text.")
    sp.add_argument("query", help='Suchbegriffe, z.B. "linsen kokos".')
    sp.add_argument("--match", choices=["any", "all"], default="any",
                    help="any: irgendein Begriff; all: alle Begriffe.")
    sp.add_argument("--tag", action="append", metavar="TAG", help=tag_help)
    sp.add_argument("--max-time", type=int, dest="max_time", help=max_time_help)
    sp.set_defaults(func=cmd_search)

    sp = sub.add_parser("tags", parents=[base],
                        help="Tag-Kategorien (Facetten) anzeigen.")
    sp.add_argument("--all", action="store_true",
                    help="Alle definierten Kategorien/Tags (nicht nur verwendete).")
    sp.set_defaults(func=cmd_tags)

    sp = sub.add_parser("home", parents=[base],
                        help="Aktiven Gusto-Datenordner anzeigen.")
    sp.set_defaults(func=cmd_home)

    sp = sub.add_parser("show", parents=[base], help="Ein Rezept ausgeben.")
    sp.add_argument("slug")
    sp.set_defaults(func=cmd_show)

    sp = sub.add_parser("new", parents=[base], help="Neues Rezept anlegen (.md + Index).")
    sp.add_argument("title")
    sp.add_argument("--tags", help="Kommagetrennt, z.B. pasta,schnell")
    sp.add_argument("--duration", type=int, help="Dauer in Minuten.")
    sp.add_argument("--servings", type=int)
    sp.add_argument("--edit", action="store_true", help="Datei danach im Editor oeffnen.")
    sp.set_defaults(func=cmd_new)

    sp = sub.add_parser("edit", parents=[base], help="Rezept-Datei im Editor oeffnen.")
    sp.add_argument("slug")
    sp.set_defaults(func=cmd_edit)

    sp = sub.add_parser(
        "content", help="Rezeptinhalt agentenfähig über den Server setzen.",
    )
    csub = sp.add_subparsers(dest="content_command", required=True)
    cp = csub.add_parser(
        "set", parents=[base],
        help="Vollständigen Markdown-Inhalt aus Datei oder stdin setzen.",
    )
    cp.add_argument("slug")
    source = cp.add_mutually_exclusive_group(required=True)
    source.add_argument("--file", help="Lokale UTF-8-Markdown-Datei.")
    source.add_argument(
        "--stdin", action="store_true",
        help="Vollständigen Markdown-Inhalt von stdin lesen.",
    )
    cp.set_defaults(func=cmd_content_set)

    sp = sub.add_parser("cooked", parents=[base], help="Rezept als gekocht eintragen.")
    sp.add_argument("slug")
    sp.add_argument(
        "--date", help="Gültiges ISO-Datum YYYY-MM-DD, nicht in der Zukunft "
                       "(Standard: heute).",
    )
    sp.set_defaults(func=cmd_cooked)

    sp = sub.add_parser("log", parents=[base], help="Koch-Logbuch anzeigen.")
    sp.add_argument("--days", type=int, help="Nur die letzten positiven N Tage.")
    sp.set_defaults(func=cmd_log)

    sp = sub.add_parser("suggest", parents=[base], help="Kandidaten fuers naechste Essen.")
    sp.add_argument("--days", type=int, default=7,
                    help="In den letzten positiven N Tagen Gekochtes wird ausgeschlossen.")
    sp.add_argument(
        "--limit", type=int,
        help="Hoechstens N Vorschlaege (nichtnegative ganze Zahl).",
    )
    sp.set_defaults(func=cmd_suggest)

    sp = sub.add_parser(
        "check", parents=[base],
        help="Integrität aller Gusto-Daten prüfen; Fehler liefern Status 1.",
    )
    sp.add_argument(
        "--offline", action="store_true",
        help="Expliziter Recovery-Check direkt auf der lokalen Dateninstanz.",
    )
    sp.set_defaults(func=cmd_check)

    sp = sub.add_parser("set", parents=[base], help="Metadaten eines Rezepts aendern.")
    sp.add_argument("slug")
    sp.add_argument("--title", help="Titel und Markdown-H1 gemeinsam ändern.")
    sp.add_argument("--tags", help="Kommagetrennt; ersetzt die bisherigen Tags.")
    duration = sp.add_mutually_exclusive_group()
    duration.add_argument("--duration", type=int)
    duration.add_argument("--clear-duration", action="store_true",
                          help="Gespeicherte Dauer entfernen.")
    servings = sp.add_mutually_exclusive_group()
    servings.add_argument("--servings", type=int)
    servings.add_argument("--clear-servings", action="store_true",
                          help="Gespeicherte Portionszahl entfernen.")
    sp.set_defaults(func=cmd_set)

    sp = sub.add_parser(
        "delete", parents=[base],
        help="Rezept samt Markdown, Metadaten und Bildern reversibel archivieren.",
    )
    sp.add_argument("slug")
    sp.set_defaults(func=cmd_delete)

    sp = sub.add_parser("archive", help="Archivierte Rezepte verwalten.")
    asub = sp.add_subparsers(dest="archive_command", required=True)

    ap = asub.add_parser("list", parents=[base],
                         help="Archivierte Rezepte auflisten.")
    ap.set_defaults(func=cmd_archive_list)

    ap = asub.add_parser("show", parents=[base],
                         help="Archiviertes Rezept ausgeben.")
    ap.add_argument("slug")
    ap.set_defaults(func=cmd_archive_show)

    ap = asub.add_parser("restore", parents=[base],
                         help="Rezept vollständig ins aktive Kochbuch zurückholen.")
    ap.add_argument("slug")
    ap.set_defaults(func=cmd_archive_restore)

    purge_help = (
        "Archiviertes Rezept unwiderruflich löschen; benötigt --yes und "
        "scheitert bei sichtbaren Einkaufsposten aus diesem Rezept."
    )
    ap = asub.add_parser(
        "purge", parents=[base], help=purge_help, description=purge_help,
    )
    ap.add_argument("slug")
    ap.add_argument("--yes", action="store_true",
                    help="Unwiderrufliches Löschen ausdrücklich bestätigen.")
    ap.set_defaults(func=cmd_archive_purge)

    sp = sub.add_parser("image", help="Bilder eines Rezepts verwalten.")
    isub = sp.add_subparsers(dest="image_command", required=True)

    ip = isub.add_parser("list", parents=[base], help="Rezeptbilder anzeigen.")
    ip.add_argument("slug")
    ip.set_defaults(func=cmd_image_list)

    ip = isub.add_parser("add", parents=[base], help="Bild zu einem Rezept kopieren.")
    ip.add_argument("slug")
    ip.add_argument("path", help="Lokale Bilddatei (JPG, PNG, WebP oder GIF).")
    ip.add_argument("--role", default="gallery",
                    help="Freier Zweck, z.B. result, step, ingredients.")
    ip.add_argument("--caption", default="", help="Optionale Bildunterschrift.")
    ip.add_argument("--cover", action="store_true", help="Als Top-Bild verwenden.")
    ip.set_defaults(func=cmd_image_add)

    ip = isub.add_parser("set", parents=[base], help="Rolle/Beschriftung aendern.")
    ip.add_argument("slug")
    ip.add_argument("id")
    ip.add_argument("--role")
    ip.add_argument("--caption")
    ip.set_defaults(func=cmd_image_set)

    ip = isub.add_parser("cover", parents=[base], help="Top-Bild auswaehlen.")
    ip.add_argument("slug")
    ip.add_argument("id")
    ip.set_defaults(func=cmd_image_cover)

    ip = isub.add_parser("remove", parents=[base], help="Bild entfernen.")
    ip.add_argument("slug")
    ip.add_argument("id")
    ip.set_defaults(func=cmd_image_remove)

    sp = sub.add_parser("favorites", help="Lieblingsprodukte verwalten.")
    fsub = sp.add_subparsers(dest="favorites_command", required=True)

    fp = fsub.add_parser("list", parents=[base],
                         help="Alle Einkaufsbedarfe anzeigen.")
    fp.set_defaults(func=cmd_favorites_list)

    fp = fsub.add_parser("show", parents=[base],
                         help="Einen Einkaufsbedarf mit Produkten anzeigen.")
    fp.add_argument("need", help="Id oder exakter Name des Einkaufsbedarfs.")
    fp.set_defaults(func=cmd_favorites_show)

    fp = fsub.add_parser("match", parents=[base],
                         help="Freien Einkaufstext eindeutig zuordnen.")
    fp.add_argument("text")
    fp.set_defaults(func=cmd_favorites_match)

    fp = fsub.add_parser("add", parents=[base],
                         help="Einkaufsbedarf anlegen.")
    fp.add_argument("name")
    fp.add_argument("--alias", action="append",
                    help="Exakte weitere Formulierung; mehrfach moeglich.")
    fp.set_defaults(func=cmd_favorites_add)

    favorites_set_help = (
        "Einkaufsbedarf umbenennen; alten Namen als Alias behalten."
    )
    fp = fsub.add_parser(
        "set", parents=[base],
        help=favorites_set_help, description=favorites_set_help,
    )
    fp.add_argument("need")
    fp.add_argument("--name", required=True)
    fp.set_defaults(func=cmd_favorites_set)

    fp = fsub.add_parser("remove", parents=[base],
                         help="Einkaufsbedarf samt Produktkarten entfernen.")
    fp.add_argument("need")
    fp.set_defaults(func=cmd_favorites_remove)

    fp = fsub.add_parser("alias-add", parents=[base],
                         help="Bekannte Formulierung zuordnen.")
    fp.add_argument("need")
    fp.add_argument("alias")
    fp.set_defaults(func=cmd_favorites_alias_add)

    fp = fsub.add_parser("alias-remove", parents=[base],
                         help="Bekannte Formulierung entfernen.")
    fp.add_argument("need")
    fp.add_argument("alias")
    fp.set_defaults(func=cmd_favorites_alias_remove)

    fp = fsub.add_parser("product-add", parents=[base],
                         help="Geordnetes Lieblingsprodukt hinzufuegen.")
    fp.add_argument("need")
    fp.add_argument("name")
    fp.add_argument("--brand", required=True, help="Marke.")
    fp.add_argument("--store", help="Bevorzugter Laden.")
    fp.add_argument("--note", help="Kurze persoenliche Notiz.")
    fp.add_argument("--image", help="Lokales Produktbild.")
    fp.set_defaults(func=cmd_favorites_product_add)

    fp = fsub.add_parser("product-set", parents=[base],
                         help="Lieblingsprodukt aktualisieren.")
    fp.add_argument("need")
    fp.add_argument("id")
    fp.add_argument("--name")
    fp.add_argument("--brand")
    fp.add_argument("--store")
    fp.add_argument("--note")
    fp.add_argument("--image", help="Neues lokales Produktbild.")
    fp.add_argument("--remove-image", action="store_true")
    fp.set_defaults(func=cmd_favorites_product_set)

    fp = fsub.add_parser("product-move", parents=[base],
                         help="Lieblingsprodukt auf eine Rangposition verschieben.")
    fp.add_argument("need")
    fp.add_argument("id")
    fp.add_argument("position", type=int, help="Position ab 1.")
    fp.set_defaults(func=cmd_favorites_product_move)

    fp = fsub.add_parser("product-remove", parents=[base],
                         help="Lieblingsprodukt entfernen.")
    fp.add_argument("need")
    fp.add_argument("id")
    fp.set_defaults(func=cmd_favorites_product_remove)

    sp = sub.add_parser("shopping", help="Einkaufsliste verwalten.")
    esub = sp.add_subparsers(dest="shopping_command", required=True)

    ep = esub.add_parser("list", parents=[base], help="Einkaufsliste anzeigen.")
    ep.add_argument("--pending", action="store_true",
                    help="Nur offene (nicht abgehakte) Eintraege.")
    ep.set_defaults(func=cmd_shopping_list)

    ep = esub.add_parser("add", parents=[base], help="Eintrag hinzufuegen.")
    ep.add_argument("text", help='Was gekauft werden soll, z.B. "200 g Spaghetti".')
    ep.add_argument("--quantity", help="Optionale Mengenangabe.")
    ep.add_argument(
        "--source", metavar="SLUG",
        help="Vorhandenes Herkunftsrezept; zaehlt fuer dessen Import-Sperre.",
    )
    ep.set_defaults(func=cmd_shopping_add)

    ep = esub.add_parser(
        "add-many", parents=[base],
        help="Mehrere Eintraege gemeinsam hinzufuegen.",
    )
    ep.add_argument(
        "texts", nargs="+",
        help='Was gekauft werden soll, jeweils als eigenes Argument.',
    )
    ep.set_defaults(func=cmd_shopping_add_many)

    add_recipe_help = (
        "Zutaten importieren, solange keine sichtbaren Posten dieses Rezepts "
        "existieren."
    )
    ep = esub.add_parser(
        "add-recipe", parents=[base],
        help=add_recipe_help, description=add_recipe_help,
    )
    ep.add_argument("slug")
    ep.set_defaults(func=cmd_shopping_add_recipe)

    ep = esub.add_parser("check", parents=[base], help="Eintrag abhaken.")
    ep.add_argument("id")
    ep.set_defaults(func=cmd_shopping_check)

    ep = esub.add_parser("uncheck", parents=[base], help="Haekchen wieder entfernen.")
    ep.add_argument("id")
    ep.set_defaults(func=cmd_shopping_uncheck)

    ep = esub.add_parser("remove", parents=[base], help="Eintrag entfernen.")
    ep.add_argument("id")
    ep.set_defaults(func=cmd_shopping_remove)

    remove_done_help = (
        "Alle abgehakten Eintraege entfernen; nicht per CLI wiederherstellbar."
    )
    ep = esub.add_parser(
        "remove-done", parents=[base],
        help=remove_done_help, description=remove_done_help,
    )
    ep.set_defaults(func=cmd_shopping_remove_done)

    clear_help = (
        "Die gesamte Einkaufsliste leeren (offene und erledigte Eintraege); "
        "nicht per CLI wiederherstellbar."
    )
    ep = esub.add_parser(
        "clear", parents=[base], help=clear_help, description=clear_help,
    )
    ep.set_defaults(func=cmd_shopping_clear)

    sp = sub.add_parser("serve", parents=[base], help="Web-Oberflaeche starten.")
    sp.add_argument("--host", default="0.0.0.0")
    sp.add_argument("--port", type=_port, default=8000,
                    help="TCP-Port zwischen 1 und 65535.")
    sp.add_argument("--reload", action="store_true", help="Auto-Reload (Entwicklung).")
    sp.set_defaults(func=cmd_serve)

    for command, help_text in (
        ("status", "Status des Gusto-User-Dienstes anzeigen."),
        ("start", "Gusto-User-Dienst starten."),
        ("stop", "Gusto-User-Dienst stoppen."),
        ("restart", "Gusto-User-Dienst neu starten."),
    ):
        sp = sub.add_parser(command, parents=[base], help=help_text)
        sp.add_argument(
            "--app-root", type=Path,
            help="Verwaltete App-Wurzel explizit wählen.",
        )
        sp.add_argument(
            "--dry-run", action="store_true",
            help="Geplante Service-Aktion anzeigen, ohne sie auszuführen.",
        )
        sp.set_defaults(func=cmd_service, command=command)

    sp = sub.add_parser(
        "update", parents=[base],
        help="Gusto auf das neueste verifizierte Release aktualisieren.",
    )
    sp.add_argument(
        "--check", action="store_true",
        help="Nur prüfen, ob ein Update verfügbar ist.",
    )
    sp.add_argument(
        "--dry-run", action="store_true",
        help="Update prüfen und planen, aber nichts verändern.",
    )
    sp.add_argument(
        "--app-root", type=Path,
        help="Verwaltete App-Wurzel explizit wählen.",
    )
    sp.add_argument(
        "--manifest-url",
        help=argparse.SUPPRESS,
    )
    sp.set_defaults(func=cmd_update)

    sp = sub.add_parser(
        "uninstall", parents=[base],
        help="Installierte App entfernen; Daten optional behalten oder löschen.",
    )
    removal = sp.add_mutually_exclusive_group()
    removal.add_argument(
        "--keep-data", action="store_true",
        help="Nur die App entfernen; Rezepte und Daten behalten.",
    )
    removal.add_argument(
        "--delete-data", action="store_true",
        help="App sowie den aktiven Rezept-/Datenordner entfernen.",
    )
    sp.add_argument(
        "--yes", action="store_true",
        help="Bestätigung für --delete-data in nicht-interaktiven Aufrufen.",
    )
    sp.add_argument(
        "--dry-run", action="store_true",
        help="Ziele anzeigen, ohne Autostart, App oder Daten zu verändern.",
    )
    sp.set_defaults(func=cmd_uninstall)

    return p


def main(argv=None) -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # robust umlauts/JSON on Windows
        except Exception:
            pass
    args = build_parser().parse_args(argv)
    try:
        args.func(args)
    except (client.ClientError, ValueError) as error:
        if args.json:
            _dump({"ok": False, "error": str(error)})
        else:
            print(str(error), file=sys.stderr)
        raise SystemExit(1) from None
    except SystemExit as error:
        if args.json and isinstance(error.code, str):
            _dump({"ok": False, "error": error.code})
            raise SystemExit(1) from None
        raise
