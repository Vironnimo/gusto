"""Kommandozeile fuers Rezept-System.

Duenne Huelle um recipe.core. Jedes Kommando versteht --json fuer eine
maschinenlesbare Ausgabe (fuer Agents & Skripte); ohne --json wird huebsch
fuers Terminal formatiert.
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


def _ausgabe_liste(recipes, as_json: bool) -> None:
    if as_json:
        _dump([r.to_dict() for r in recipes])
        return
    if not recipes:
        print("Keine Rezepte gefunden.")
        return
    for r in recipes:
        meta = []
        if r.dauer_minuten:
            meta.append(f"{r.dauer_minuten} min")
        if r.portionen:
            meta.append(f"{r.portionen} P.")
        if r.tags:
            meta.append(", ".join(r.tags))
        extra = "  ·  ".join(meta)
        print(f"  {r.slug:<22} {r.titel}" + (f"   [{extra}]" if extra else ""))


# --- Kommandos --------------------------------------------------------------

def cmd_list(args):
    recipes = sorted(core.search(tag=args.tag, max_time=args.max_time),
                     key=lambda r: r.titel.lower())
    _ausgabe_liste(recipes, args.json)


def cmd_search(args):
    recipes = sorted(core.search(query=args.query, match=args.match,
                                 tag=args.tag, max_time=args.max_time),
                     key=lambda r: r.titel.lower())
    _ausgabe_liste(recipes, args.json)


def cmd_show(args):
    r = core.get(args.slug)
    if r is None:
        sys.exit(f"Kein Rezept mit Slug '{args.slug}'.")
    if args.json:
        d = r.to_dict()
        d["inhalt"] = r.inhalt()
        _dump(d)
    else:
        print(r.inhalt().rstrip())


def cmd_new(args):
    tags = [t.strip() for t in (args.tags or "").split(",") if t.strip()]
    try:
        r = core.add_recipe(args.titel, tags=tags,
                            dauer_minuten=args.dauer, portionen=args.portionen)
    except ValueError as e:
        sys.exit(str(e))
    if args.json:
        _dump(r.to_dict())
    else:
        print(f"Angelegt: {r.slug}  ->  {r.pfad}")
    if args.edit:
        _open_editor(r.pfad)


def cmd_edit(args):
    r = core.get(args.slug)
    if r is None:
        sys.exit(f"Kein Rezept mit Slug '{args.slug}'.")
    _open_editor(r.pfad)


def cmd_cooked(args):
    try:
        core.log_cooked(args.slug, datum=args.date)
    except ValueError as e:
        sys.exit(str(e))
    datum = args.date or date.today().isoformat()
    if args.json:
        _dump({"slug": args.slug, "datum": datum, "ok": True})
    else:
        print(f"Notiert: '{args.slug}' am {datum} gekocht.")


def cmd_log(args):
    eintraege = core.load_log(days=args.days)
    if args.json:
        _dump(eintraege)
        return
    if not eintraege:
        print("Logbuch ist leer.")
        return
    titel = {r.slug: r.titel for r in core.load_recipes()}
    for e in reversed(eintraege):  # neueste zuerst
        print(f"  {e['datum']}   {titel.get(e['slug'], e['slug'])}")


def cmd_suggest(args):
    kandidaten = core.suggest(days=args.days, limit=args.limit)
    if args.json:
        _dump([r.to_dict() for r in kandidaten])
        return
    if not kandidaten:
        print(f"Keine Vorschlaege – in den letzten {args.days} Tagen war schon alles dran.")
        return
    print(f"Vorschlaege (nicht in den letzten {args.days} Tagen gekocht):")
    for r in kandidaten:
        print(f"  {r.titel:<26} (zuletzt: {r.zuletzt_gekocht or 'noch nie'})")


def cmd_check(args):
    res = core.check()
    if args.json:
        _dump(res)
        return
    print(f"Rezepte im Index: {res['anzahl_rezepte']}")
    if res["verwaiste_dateien"]:
        print("  .md ohne Index-Eintrag:", ", ".join(res["verwaiste_dateien"]))
    if res["fehlende_dateien"]:
        print("  Index-Eintrag ohne .md:", ", ".join(res["fehlende_dateien"]))
    if not res["verwaiste_dateien"] and not res["fehlende_dateien"]:
        print("  Alles konsistent.")


def cmd_set(args):
    tags = None
    if args.tags is not None:
        tags = [t.strip() for t in args.tags.split(",") if t.strip()]
    try:
        r = core.update_recipe(args.slug, titel=args.titel, tags=tags,
                               dauer_minuten=args.dauer, portionen=args.portionen)
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
        _dump({"slug": args.slug, "geloescht": True})
    else:
        print(f"Geloescht: {args.slug}")


def cmd_serve(args):
    try:
        import uvicorn
    except ImportError:
        sys.exit("Web-Abhaengigkeiten fehlen. Installiere sie mit:  pip install -e .[web]")
    print(f"Kuechenbuch laeuft auf http://{args.host}:{args.port}  (Strg+C zum Beenden)")
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

    sp = sub.add_parser("list", parents=[base], help="Rezepte auflisten/filtern.")
    sp.add_argument("--tag", help="Nur Rezepte mit diesem Tag.")
    sp.add_argument("--max-time", type=int, dest="max_time", help="Max. Dauer (Minuten).")
    sp.set_defaults(func=cmd_list)

    sp = sub.add_parser("search", parents=[base],
                        help="Volltextsuche ueber Titel, Tags und Zutaten/Text.")
    sp.add_argument("query", help='Suchbegriffe, z.B. "linsen kokos".')
    sp.add_argument("--match", choices=["any", "all"], default="any",
                    help="any: irgendein Begriff; all: alle Begriffe.")
    sp.add_argument("--tag")
    sp.add_argument("--max-time", type=int, dest="max_time")
    sp.set_defaults(func=cmd_search)

    sp = sub.add_parser("show", parents=[base], help="Ein Rezept ausgeben.")
    sp.add_argument("slug")
    sp.set_defaults(func=cmd_show)

    sp = sub.add_parser("new", parents=[base], help="Neues Rezept anlegen (.md + Index).")
    sp.add_argument("titel")
    sp.add_argument("--tags", help="Kommagetrennt, z.B. pasta,schnell")
    sp.add_argument("--dauer", type=int, help="Dauer in Minuten.")
    sp.add_argument("--portionen", type=int)
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
    sp.add_argument("--titel")
    sp.add_argument("--tags", help="Kommagetrennt; ersetzt die bisherigen Tags.")
    sp.add_argument("--dauer", type=int)
    sp.add_argument("--portionen", type=int)
    sp.set_defaults(func=cmd_set)

    sp = sub.add_parser("delete", parents=[base], help="Rezept loeschen (.md + Index).")
    sp.add_argument("slug")
    sp.set_defaults(func=cmd_delete)

    sp = sub.add_parser("serve", parents=[base], help="Web-Oberflaeche starten.")
    sp.add_argument("--host", default="0.0.0.0")
    sp.add_argument("--port", type=int, default=8000)
    sp.add_argument("--reload", action="store_true", help="Auto-Reload (Entwicklung).")
    sp.set_defaults(func=cmd_serve)

    return p


def main(argv=None) -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # robuste Umlaute/JSON auf Windows
    except Exception:
        pass
    args = build_parser().parse_args(argv)
    args.func(args)
