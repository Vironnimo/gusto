"""Command line for Gusto.

Thin shell around gusto.core. Every command understands --json for machine-
readable output (for agents & scripts); without --json it is formatted nicely
for the terminal. User-facing output and --help texts stay German.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import traceback
from collections.abc import Callable, Sequence
from contextlib import redirect_stderr, redirect_stdout
from datetime import date, datetime
from pathlib import Path

from . import core, uninstall


def _dump(data) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2))


def _open_editor(path) -> None:
    editor = os.environ.get("EDITOR") or ("notepad" if os.name == "nt" else "nano")
    subprocess.call([editor, str(path)])


def _collect_tags(values) -> list[str]:
    """--tag may be given multiple times AND comma-separated -> flat tag list."""
    out: list[str] = []
    for v in values or []:
        out.extend(t.strip() for t in v.split(",") if t.strip())
    return out


def _print_list(recipes, as_json: bool) -> None:
    if as_json:
        _dump([r.to_dict() for r in recipes])
        return
    if not recipes:
        print("Keine Rezepte gefunden.")
        return
    for r in recipes:
        meta = []
        if r.duration_min:
            meta.append(f"{r.duration_min} min")
        if r.servings:
            meta.append(f"{r.servings} P.")
        if r.tags:
            meta.append(", ".join(r.tags))
        if r.images:
            meta.append(f"{len(r.images)} Bild" + ("er" if len(r.images) != 1 else ""))
        extra = "  ·  ".join(meta)
        print(f"  {r.slug:<22} {r.title}" + (f"   [{extra}]" if extra else ""))


def _tag_warnings(recipe_tags: list[str]) -> list[dict]:
    tags = core.uncategorized_tags(recipe_tags)
    if not tags:
        return []
    return [{
        "code": "uncategorized_tags",
        "message": "Tags ohne Kategorie (Facet „Sonstige“): " + ", ".join(tags),
        "tags": tags,
    }]


def _print_tag_warnings(warnings: list[dict]) -> None:
    for warning in warnings:
        print(f"Warnung: {warning['message']}")


# --- Commands ---------------------------------------------------------------

def cmd_list(args):
    recipes = sorted(core.search(tags=_collect_tags(args.tag), max_time=args.max_time),
                     key=lambda r: r.title.lower())
    _print_list(recipes, args.json)


def cmd_search(args):
    recipes = sorted(core.search(query=args.query, match=args.match,
                                 tags=_collect_tags(args.tag), max_time=args.max_time),
                     key=lambda r: r.title.lower())
    _print_list(recipes, args.json)


def cmd_tags(args):
    groups = core.tag_groups(only_used=not args.all)
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
    info = core.storage_info()
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


def cmd_show(args):
    r = core.get(args.slug)
    if r is None:
        sys.exit(f"Kein Rezept mit Slug '{args.slug}'.")
    if args.json:
        d = r.to_dict()
        d["content"] = r.content()
        _dump(d)
    else:
        print(r.content().rstrip())


def cmd_new(args):
    tags = [t.strip() for t in (args.tags or "").split(",") if t.strip()]
    warnings = _tag_warnings(tags)
    try:
        r = core.add_recipe(args.title, tags=tags,
                            duration_min=args.duration, servings=args.servings)
    except ValueError as e:
        sys.exit(str(e))
    if args.json:
        result = r.to_dict()
        if warnings:
            result["warnings"] = warnings
        _dump(result)
    else:
        print(f"Angelegt: {r.slug}  ->  {r.path}")
        _print_tag_warnings(warnings)
    if args.edit:
        _open_editor(r.path)


def cmd_edit(args):
    r = core.get(args.slug)
    if r is None:
        sys.exit(f"Kein Rezept mit Slug '{args.slug}'.")
    _open_editor(r.path)


def cmd_cooked(args):
    try:
        core.log_cooked(args.slug, when=args.date)
    except ValueError as e:
        sys.exit(str(e))
    when = args.date or date.today().isoformat()
    if args.json:
        _dump({"slug": args.slug, "date": when, "ok": True})
    else:
        print(f"Notiert: '{args.slug}' am {when} gekocht.")


def cmd_log(args):
    entries = core.load_log(days=args.days)
    if args.json:
        _dump(entries)
        return
    if not entries:
        print("Logbuch ist leer.")
        return
    titles = {r.slug: r.title for r in core.load_recipes()}
    for e in reversed(entries):  # newest first
        print(f"  {e['date']}   {titles.get(e['slug'], e['slug'])}")


def cmd_suggest(args):
    try:
        candidates = core.suggest(days=args.days, limit=args.limit)
    except ValueError as error:
        sys.exit(str(error))
    if args.json:
        _dump([r.to_dict() for r in candidates])
        return
    if not candidates:
        if args.limit == 0:
            print("Keine Vorschlaege angefordert (--limit 0).")
        else:
            print(f"Keine Vorschlaege – in den letzten {args.days} Tagen war schon alles dran.")
        return
    print(f"Vorschlaege (nicht in den letzten {args.days} Tagen gekocht):")
    for r in candidates:
        print(f"  {r.title:<26} (zuletzt: {r.last_cooked or 'noch nie'})")


def cmd_check(args):
    res = core.check()
    if args.json:
        _dump(res)
        return
    print(f"Rezepte im Index: {res['recipe_count']}")
    if res["orphaned_files"]:
        print("  .md ohne Index-Eintrag:", ", ".join(res["orphaned_files"]))
    if res["missing_files"]:
        print("  Index-Eintrag ohne .md:", ", ".join(res["missing_files"]))
    if res.get("uncategorized_tags"):
        print("  Tags ohne Kategorie:", ", ".join(res["uncategorized_tags"]))
    if res.get("orphaned_image_folders"):
        print("  Bildordner ohne Rezept:", ", ".join(res["orphaned_image_folders"]))
    if res.get("orphaned_image_files"):
        print("  Bilder ohne Metadaten:", ", ".join(res["orphaned_image_files"]))
    if res.get("missing_image_files"):
        print("  Fehlende Bilddateien:", ", ".join(res["missing_image_files"]))
    if res.get("invalid_cover_images"):
        print("  Ungueltige Top-Bilder:", ", ".join(res["invalid_cover_images"]))
    if res.get("duplicate_favorite_aliases"):
        print("  Mehrdeutige Lieblingsprodukt-Aliasse:",
              ", ".join(res["duplicate_favorite_aliases"]))
    if res.get("orphaned_favorite_image_files"):
        print("  Produktbilder ohne Metadaten:",
              ", ".join(res["orphaned_favorite_image_files"]))
    if res.get("missing_favorite_image_files"):
        print("  Fehlende Produktbilder:",
              ", ".join(res["missing_favorite_image_files"]))
    if not (res["orphaned_files"] or res["missing_files"]
            or res.get("uncategorized_tags") or res.get("orphaned_image_folders")
            or res.get("orphaned_image_files") or res.get("missing_image_files")
            or res.get("invalid_cover_images")
            or res.get("duplicate_favorite_aliases")
            or res.get("orphaned_favorite_image_files")
            or res.get("missing_favorite_image_files")):
        print("  Alles konsistent.")


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
    warnings = _tag_warnings(tags) if tags is not None else []
    try:
        r = core.update_recipe(args.slug, title=args.title, tags=tags,
                               duration_min=args.duration, servings=args.servings,
                               clear_duration=args.clear_duration,
                               clear_servings=args.clear_servings)
    except ValueError as e:
        sys.exit(str(e))
    if args.json:
        result = r.to_dict()
        if warnings:
            result["warnings"] = warnings
        _dump(result)
    else:
        print(f"Aktualisiert: {r.slug}")
        _print_tag_warnings(warnings)


def cmd_delete(args):
    try:
        core.delete_recipe(args.slug)
    except ValueError as e:
        sys.exit(str(e))
    if args.json:
        _dump({"slug": args.slug, "deleted": True})
    else:
        print(f"Geloescht: {args.slug}")


# --- Recipe images ----------------------------------------------------------

def _image_dict(image, cover_id: str | None) -> dict:
    data = image.to_dict()
    data["is_cover"] = image.id == cover_id
    return data


def cmd_image_list(args):
    try:
        images, cover_id = core.list_recipe_images(args.slug)
    except ValueError as error:
        sys.exit(str(error))
    if args.json:
        _dump({"cover_image_id": cover_id,
               "images": [_image_dict(image, cover_id) for image in images]})
        return
    if not images:
        print("Keine Bilder bei diesem Rezept.")
        return
    for image in images:
        marker = " [Top-Bild]" if image.id == cover_id else ""
        caption = f" — {image.caption}" if image.caption else ""
        print(f"  {image.id}  {image.role}{marker}{caption}")


def cmd_image_add(args):
    try:
        image = core.add_recipe_image(
            args.slug, args.path, role=args.role, caption=args.caption,
            cover=args.cover,
        )
        _, cover_id = core.list_recipe_images(args.slug)
    except ValueError as error:
        sys.exit(str(error))
    if args.json:
        _dump(_image_dict(image, cover_id))
    else:
        marker = " (Top-Bild)" if image.id == cover_id else ""
        print(f"Bild hinzugefuegt: {image.id}{marker}")


def cmd_image_set(args):
    if args.role is None and args.caption is None:
        sys.exit("Gib --role und/oder --caption an.")
    try:
        image = core.update_recipe_image(
            args.slug, args.id, role=args.role, caption=args.caption,
        )
        _, cover_id = core.list_recipe_images(args.slug)
    except ValueError as error:
        sys.exit(str(error))
    if args.json:
        _dump(_image_dict(image, cover_id))
    else:
        print(f"Bild aktualisiert: {image.id}")


def cmd_image_cover(args):
    try:
        image = core.set_recipe_cover(args.slug, args.id)
    except ValueError as error:
        sys.exit(str(error))
    if args.json:
        _dump({"slug": args.slug, "cover_image_id": image.id})
    else:
        print(f"Top-Bild gesetzt: {image.id}")


def cmd_image_remove(args):
    try:
        cover_id = core.remove_recipe_image(args.slug, args.id)
    except ValueError as error:
        sys.exit(str(error))
    if args.json:
        _dump({"id": args.id, "removed": True, "cover_image_id": cover_id})
    else:
        print(f"Bild entfernt: {args.id}")


# --- Preferred products -----------------------------------------------------

def cmd_favorites_list(args):
    needs = core.favorites_load()
    if args.json:
        _dump([need.to_dict() for need in needs])
        return
    if not needs:
        print("Noch keine Lieblingsprodukte hinterlegt.")
        return
    for need in needs:
        count = len(need.products)
        noun = "Produkt" if count == 1 else "Produkte"
        print(f"  {need.id}  {need.name}  ({count} {noun})")


def cmd_favorites_show(args):
    need = core.favorite_get_need(args.need)
    if need is None:
        sys.exit(f"Kein Einkaufsbedarf mit id oder Name '{args.need}'.")
    if args.json:
        _dump(need.to_dict())
        return
    print(need.name)
    if need.aliases:
        print("  Aliasse: " + ", ".join(need.aliases))
    for position, product in enumerate(need.products, 1):
        details = " · ".join(value for value in [product.brand, product.store] if value)
        print(f"  {position}. {product.name}" + (f"  [{details}]" if details else ""))


def cmd_favorites_match(args):
    need = core.favorite_match(args.text)
    if args.json:
        _dump(need.to_dict() if need is not None else None)
    elif need is None:
        print("Keine Zuordnung gefunden.")
    else:
        print(f"{args.text} -> {need.name}")


def cmd_favorites_add(args):
    try:
        need = core.favorite_add_need(args.name, aliases=args.alias)
    except ValueError as error:
        sys.exit(str(error))
    if args.json:
        _dump(need.to_dict())
    else:
        print(f"Einkaufsbedarf angelegt: {need.name} ({need.id})")


def cmd_favorites_set(args):
    try:
        need = core.favorite_update_need(args.need, args.name)
    except ValueError as error:
        sys.exit(str(error))
    if args.json:
        _dump(need.to_dict())
    else:
        print(f"Einkaufsbedarf aktualisiert: {need.name}")


def cmd_favorites_remove(args):
    try:
        need = core.favorite_remove_need(args.need)
    except ValueError as error:
        sys.exit(str(error))
    if args.json:
        _dump({"id": need.id, "removed": True})
    else:
        print(f"Einkaufsbedarf entfernt: {need.name}")


def cmd_favorites_alias_add(args):
    try:
        need = core.favorite_add_alias(args.need, args.alias)
    except ValueError as error:
        sys.exit(str(error))
    if args.json:
        _dump(need.to_dict())
    else:
        print(f"Alias bei '{need.name}' hinterlegt: {args.alias}")


def cmd_favorites_alias_remove(args):
    try:
        need = core.favorite_remove_alias(args.need, args.alias)
    except ValueError as error:
        sys.exit(str(error))
    if args.json:
        _dump(need.to_dict())
    else:
        print(f"Alias bei '{need.name}' entfernt: {args.alias}")


def cmd_favorites_product_add(args):
    try:
        product = core.favorite_add_product(
            args.need, args.name, brand=args.brand or "", store=args.store or "",
            note=args.note or "", image=args.image,
        )
    except ValueError as error:
        sys.exit(str(error))
    if args.json:
        _dump(product.to_dict())
    else:
        print(f"Lieblingsprodukt hinzugefuegt: {product.name} ({product.id})")


def cmd_favorites_product_set(args):
    if not any(value is not None for value in
               [args.name, args.brand, args.store, args.note, args.image]) \
            and not args.remove_image:
        sys.exit("Gib mindestens eine Aenderung an.")
    try:
        product = core.favorite_update_product(
            args.need, args.id, name=args.name, brand=args.brand,
            store=args.store, note=args.note, image=args.image,
            remove_image=args.remove_image,
        )
    except ValueError as error:
        sys.exit(str(error))
    if args.json:
        _dump(product.to_dict())
    else:
        print(f"Lieblingsprodukt aktualisiert: {product.name}")


def cmd_favorites_product_move(args):
    try:
        need = core.favorite_move_product(args.need, args.id, args.position)
    except ValueError as error:
        sys.exit(str(error))
    if args.json:
        _dump(need.to_dict())
    else:
        print(f"Reihenfolge bei '{need.name}' aktualisiert.")


def cmd_favorites_product_remove(args):
    try:
        need = core.favorite_remove_product(args.need, args.id)
    except ValueError as error:
        sys.exit(str(error))
    if args.json:
        _dump(need.to_dict())
    else:
        print(f"Lieblingsprodukt bei '{need.name}' entfernt.")


# --- Shopping list ----------------------------------------------------------

def _print_shopping_item(item, as_json: bool, *, prefix: str = "") -> None:
    if as_json:
        _dump(item.to_dict())
        return
    marker = "[x]" if item.checked else "[ ]"
    line = f"{marker} {item.id}  {item.text}"
    if item.quantity:
        line += f"  ({item.quantity})"
    if item.source:
        line += f"  (aus {item.source})"
    print(prefix + line)


def cmd_shopping_list(args):
    items = core.shopping_list(include_done=not args.pending)
    if args.json:
        _dump([i.to_dict() for i in items])
        return
    if not items:
        print("Einkaufsliste ist leer.")
        return
    for i in items:
        _print_shopping_item(i, False, prefix="  ")


def cmd_shopping_add(args):
    try:
        item = core.shopping_add(
            args.text, quantity=args.quantity or "", source=args.source,
        )
    except ValueError as e:
        sys.exit(str(e))
    if args.json:
        _dump(item.to_dict())
    else:
        _print_shopping_item(item, False, prefix="Hinzugefuegt: ")


def cmd_shopping_add_many(args):
    try:
        items = core.shopping_add_many(args.texts)
    except ValueError as e:
        sys.exit(str(e))
    if args.json:
        _dump([item.to_dict() for item in items])
    else:
        print(f"{len(items)} Einkaufsposten hinzugefuegt.")


def cmd_shopping_add_recipe(args):
    try:
        items = core.shopping_add_recipe(args.slug)
    except ValueError as e:
        sys.exit(str(e))
    if args.json:
        _dump([i.to_dict() for i in items])
    else:
        print(f"{len(items)} Zutat(en) aus '{args.slug}' hinzugefuegt.")


def cmd_shopping_check(args):
    try:
        item = core.shopping_toggle(args.id, checked=True)
    except ValueError as e:
        sys.exit(str(e))
    if args.json:
        _dump(item.to_dict())
    else:
        _print_shopping_item(item, False, prefix="Abgehakt: ")


def cmd_shopping_uncheck(args):
    try:
        item = core.shopping_toggle(args.id, checked=False)
    except ValueError as e:
        sys.exit(str(e))
    if args.json:
        _dump(item.to_dict())
    else:
        _print_shopping_item(item, False, prefix="Wieder offen: ")


def cmd_shopping_remove(args):
    try:
        core.shopping_remove(args.id)
    except ValueError as e:
        sys.exit(str(e))
    if args.json:
        _dump({"id": args.id, "deleted": True})
    else:
        print(f"Entfernt: {args.id}")


def cmd_shopping_clear(args):
    count = core.shopping_clear_done()
    if args.json:
        _dump({"removed": count})
    else:
        print(f"{count} erledigte(s) Item(s) entfernt.")


def cmd_serve(args):
    try:
        import uvicorn
    except ImportError:
        sys.exit("Web-Abhaengigkeiten fehlen. Installiere sie mit:  pip install -e .[web]")
    print(f"Gusto laeuft auf http://{args.host}:{args.port}  (Strg+C zum Beenden)")
    uvicorn.run("gusto.web:app", host=args.host, port=args.port, reload=args.reload)


def _uninstall_choice(
    args,
    targets: uninstall.UninstallTargets,
    *,
    interactive: bool,
    input_func: Callable[[str], str] = input,
) -> bool | None:
    """Return whether to delete data; ``None`` means an interactive cancel."""
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
    sub = p.add_subparsers(dest="command", required=True)

    base = argparse.ArgumentParser(add_help=False)
    base.add_argument("--json", action="store_true",
                      help="Maschinenlesbare Ausgabe (fuer Agents/Skripte).")

    tag_help = ("Nach Tag filtern; mehrfach oder kommagetrennt moeglich "
                "(--tag italienisch --tag pizza  bzw.  --tag italienisch,pizza). "
                "ODER innerhalb einer Kategorie, UND ueber Kategorien.")

    max_time_help = (
        "Max. Dauer in Minuten; Rezepte ohne Dauerangabe werden ausgeschlossen."
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

    sp = sub.add_parser("cooked", parents=[base], help="Rezept als gekocht eintragen.")
    sp.add_argument("slug")
    sp.add_argument(
        "--date", help="Gültiges ISO-Datum YYYY-MM-DD, nicht in der Zukunft "
                       "(Standard: heute).",
    )
    sp.set_defaults(func=cmd_cooked)

    sp = sub.add_parser("log", parents=[base], help="Koch-Logbuch anzeigen.")
    sp.add_argument("--days", type=int, help="Nur die letzten N Tage.")
    sp.set_defaults(func=cmd_log)

    sp = sub.add_parser("suggest", parents=[base], help="Kandidaten fuers naechste Essen.")
    sp.add_argument("--days", type=int, default=7,
                    help="In den letzten N Tagen Gekochtes wird ausgeschlossen.")
    sp.add_argument(
        "--limit", type=int,
        help="Hoechstens N Vorschlaege (nichtnegative ganze Zahl).",
    )
    sp.set_defaults(func=cmd_suggest)

    sp = sub.add_parser("check", parents=[base], help="Konsistenz Index <-> .md pruefen.")
    sp.set_defaults(func=cmd_check)

    sp = sub.add_parser("set", parents=[base], help="Metadaten eines Rezepts aendern.")
    sp.add_argument("slug")
    sp.add_argument("--title")
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

    sp = sub.add_parser("delete", parents=[base], help="Rezept loeschen (.md + Index).")
    sp.add_argument("slug")
    sp.set_defaults(func=cmd_delete)

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

    fp = fsub.add_parser("set", parents=[base],
                         help="Einkaufsbedarf umbenennen.")
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

    ep = esub.add_parser("add-recipe", parents=[base],
                         help="Zutaten einmalig aus einem Rezept importieren.")
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

    clear_help = (
        "Alle erledigten Eintraege entfernen; nicht per CLI wiederherstellbar."
    )
    ep = esub.add_parser(
        "clear", parents=[base], help=clear_help, description=clear_help,
    )
    ep.set_defaults(func=cmd_shopping_clear)

    sp = sub.add_parser("serve", parents=[base], help="Web-Oberflaeche starten.")
    sp.add_argument("--host", default="0.0.0.0")
    sp.add_argument("--port", type=int, default=8000)
    sp.add_argument("--reload", action="store_true", help="Auto-Reload (Entwicklung).")
    sp.set_defaults(func=cmd_serve)

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
    except SystemExit as error:
        if args.json and isinstance(error.code, str):
            _dump({"ok": False, "error": error.code})
            raise SystemExit(1) from None
        raise
