"""Command line for the recipe system.

Thin shell around recipe.core. Every command understands --json for machine-
readable output (for agents & scripts); without --json it is formatted nicely
for the terminal. User-facing output and --help texts stay German.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import date

from . import core


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
    try:
        r = core.add_recipe(args.title, tags=tags,
                            duration_min=args.duration, servings=args.servings)
    except ValueError as e:
        sys.exit(str(e))
    if args.json:
        _dump(r.to_dict())
    else:
        print(f"Angelegt: {r.slug}  ->  {r.path}")
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
    candidates = core.suggest(days=args.days, limit=args.limit)
    if args.json:
        _dump([r.to_dict() for r in candidates])
        return
    if not candidates:
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
    if not (res["orphaned_files"] or res["missing_files"]
            or res.get("uncategorized_tags") or res.get("orphaned_image_folders")
            or res.get("orphaned_image_files") or res.get("missing_image_files")
            or res.get("invalid_cover_images")):
        print("  Alles konsistent.")


def cmd_set(args):
    tags = None
    if args.tags is not None:
        tags = [t.strip() for t in args.tags.split(",") if t.strip()]
    try:
        r = core.update_recipe(args.slug, title=args.title, tags=tags,
                               duration_min=args.duration, servings=args.servings)
    except ValueError as e:
        sys.exit(str(e))
    if args.json:
        _dump(r.to_dict())
    else:
        print(f"Aktualisiert: {r.slug}")


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
        item = core.shopping_add(args.text, quantity=args.quantity or "")
    except ValueError as e:
        sys.exit(str(e))
    if args.json:
        _dump(item.to_dict())
    else:
        _print_shopping_item(item, False, prefix="Hinzugefuegt: ")


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
    uvicorn.run("recipe.web:app", host=args.host, port=args.port, reload=args.reload)


# --- Parser -----------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="recipe",
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

    sp = sub.add_parser("list", parents=[base], help="Rezepte auflisten/filtern.")
    sp.add_argument("--tag", action="append", metavar="TAG", help=tag_help)
    sp.add_argument("--max-time", type=int, dest="max_time", help="Max. Dauer (Minuten).")
    sp.set_defaults(func=cmd_list)

    sp = sub.add_parser("search", parents=[base],
                        help="Volltextsuche ueber Titel, Tags und Zutaten/Text.")
    sp.add_argument("query", help='Suchbegriffe, z.B. "linsen kokos".')
    sp.add_argument("--match", choices=["any", "all"], default="any",
                    help="any: irgendein Begriff; all: alle Begriffe.")
    sp.add_argument("--tag", action="append", metavar="TAG", help=tag_help)
    sp.add_argument("--max-time", type=int, dest="max_time")
    sp.set_defaults(func=cmd_search)

    sp = sub.add_parser("tags", parents=[base],
                        help="Tag-Kategorien (Facetten) anzeigen.")
    sp.add_argument("--all", action="store_true",
                    help="Alle definierten Kategorien/Tags (nicht nur verwendete).")
    sp.set_defaults(func=cmd_tags)

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
    sp.add_argument("--date", help="ISO-Datum YYYY-MM-DD (Standard: heute).")
    sp.set_defaults(func=cmd_cooked)

    sp = sub.add_parser("log", parents=[base], help="Koch-Logbuch anzeigen.")
    sp.add_argument("--days", type=int, help="Nur die letzten N Tage.")
    sp.set_defaults(func=cmd_log)

    sp = sub.add_parser("suggest", parents=[base], help="Kandidaten fuers naechste Essen.")
    sp.add_argument("--days", type=int, default=7,
                    help="In den letzten N Tagen Gekochtes wird ausgeschlossen.")
    sp.add_argument("--limit", type=int, help="Hoechstens N Vorschlaege.")
    sp.set_defaults(func=cmd_suggest)

    sp = sub.add_parser("check", parents=[base], help="Konsistenz Index <-> .md pruefen.")
    sp.set_defaults(func=cmd_check)

    sp = sub.add_parser("set", parents=[base], help="Metadaten eines Rezepts aendern.")
    sp.add_argument("slug")
    sp.add_argument("--title")
    sp.add_argument("--tags", help="Kommagetrennt; ersetzt die bisherigen Tags.")
    sp.add_argument("--duration", type=int)
    sp.add_argument("--servings", type=int)
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

    sp = sub.add_parser("shopping", help="Einkaufsliste verwalten.")
    esub = sp.add_subparsers(dest="shopping_command", required=True)

    ep = esub.add_parser("list", parents=[base], help="Einkaufsliste anzeigen.")
    ep.add_argument("--pending", action="store_true",
                    help="Nur offene (nicht abgehakte) Eintraege.")
    ep.set_defaults(func=cmd_shopping_list)

    ep = esub.add_parser("add", parents=[base], help="Eintrag hinzufuegen.")
    ep.add_argument("text", help='Was gekauft werden soll, z.B. "200 g Spaghetti".')
    ep.add_argument("--quantity", help="Optionale Mengenangabe.")
    ep.set_defaults(func=cmd_shopping_add)

    ep = esub.add_parser("add-recipe", parents=[base],
                         help="Alle Zutaten eines Rezepts auf die Liste setzen.")
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

    ep = esub.add_parser("clear", parents=[base],
                         help="Alle erledigten Eintraege entfernen.")
    ep.set_defaults(func=cmd_shopping_clear)

    sp = sub.add_parser("serve", parents=[base], help="Web-Oberflaeche starten.")
    sp.add_argument("--host", default="0.0.0.0")
    sp.add_argument("--port", type=int, default=8000)
    sp.add_argument("--reload", action="store_true", help="Auto-Reload (Entwicklung).")
    sp.set_defaults(func=cmd_serve)

    return p


def main(argv=None) -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # robust umlauts/JSON on Windows
    except Exception:
        pass
    args = build_parser().parse_args(argv)
    args.func(args)
