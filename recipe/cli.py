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


def _collect_tags(values) -> list[str]:
    """--tag kann mehrfach UND kommagetrennt kommen -> flache Tag-Liste."""
    out: list[str] = []
    for v in values or []:
        out.extend(t.strip() for t in v.split(",") if t.strip())
    return out


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
    recipes = sorted(core.search(tags=_collect_tags(args.tag), max_time=args.max_time),
                     key=lambda r: r.titel.lower())
    _ausgabe_liste(recipes, args.json)


def cmd_search(args):
    recipes = sorted(core.search(query=args.query, match=args.match,
                                 tags=_collect_tags(args.tag), max_time=args.max_time),
                     key=lambda r: r.titel.lower())
    _ausgabe_liste(recipes, args.json)


def cmd_tags(args):
    gruppen = core.tag_groups(only_used=not args.all)
    if args.json:
        _dump(gruppen)
        return
    if not gruppen:
        print("Keine Tag-Kategorien definiert (data/categories.json fehlt?).")
        return
    for g in gruppen:
        print(f"{g['label']}:")
        print("  " + (", ".join(g["tags"]) if g["tags"] else "—"))


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
    if res.get("unsortierte_tags"):
        print("  Tags ohne Kategorie:", ", ".join(res["unsortierte_tags"]))
    if not (res["verwaiste_dateien"] or res["fehlende_dateien"]
            or res.get("unsortierte_tags")):
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


# --- Einkaufsliste ----------------------------------------------------------

def _ausgabe_einkauf_item(item, as_json: bool, *, prefix: str = "") -> None:
    if as_json:
        _dump(item.to_dict())
        return
    marker = "[x]" if item.checked else "[ ]"
    zeile = f"{marker} {item.id}  {item.text}"
    if item.menge:
        zeile += f"  ({item.menge})"
    if item.quelle:
        zeile += f"  (aus {item.quelle})"
    print(prefix + zeile)


def cmd_einkauf_list(args):
    items = core.einkauf_list(include_done=not args.offen)
    if args.json:
        _dump([i.to_dict() for i in items])
        return
    if not items:
        print("Einkaufsliste ist leer.")
        return
    for i in items:
        _ausgabe_einkauf_item(i, False, prefix="  ")


def cmd_einkauf_add(args):
    try:
        item = core.einkauf_add(args.text, menge=args.menge or "")
    except ValueError as e:
        sys.exit(str(e))
    if args.json:
        _dump(item.to_dict())
    else:
        _ausgabe_einkauf_item(item, False, prefix="Hinzugefuegt: ")


def cmd_einkauf_rezept(args):
    try:
        items = core.einkauf_add_rezept(args.slug)
    except ValueError as e:
        sys.exit(str(e))
    if args.json:
        _dump([i.to_dict() for i in items])
    else:
        print(f"{len(items)} Zutat(en) aus '{args.slug}' hinzugefuegt.")


def cmd_einkauf_check(args):
    try:
        item = core.einkauf_toggle(args.id, checked=True)
    except ValueError as e:
        sys.exit(str(e))
    if args.json:
        _dump(item.to_dict())
    else:
        _ausgabe_einkauf_item(item, False, prefix="Abgehakt: ")


def cmd_einkauf_uncheck(args):
    try:
        item = core.einkauf_toggle(args.id, checked=False)
    except ValueError as e:
        sys.exit(str(e))
    if args.json:
        _dump(item.to_dict())
    else:
        _ausgabe_einkauf_item(item, False, prefix="Wieder offen: ")


def cmd_einkauf_remove(args):
    try:
        core.einkauf_remove(args.id)
    except ValueError as e:
        sys.exit(str(e))
    if args.json:
        _dump({"id": args.id, "geloescht": True})
    else:
        print(f"Entfernt: {args.id}")


def cmd_einkauf_clear(args):
    anzahl = core.einkauf_clear_done()
    if args.json:
        _dump({"entfernt": anzahl})
    else:
        print(f"{anzahl} erledigte(s) Item(s) entfernt.")


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

    tag_hilfe = ("Nach Tag filtern; mehrfach oder kommagetrennt moeglich "
                 "(--tag italienisch --tag pizza  bzw.  --tag italienisch,pizza). "
                 "ODER innerhalb einer Kategorie, UND ueber Kategorien.")

    sp = sub.add_parser("list", parents=[base], help="Rezepte auflisten/filtern.")
    sp.add_argument("--tag", action="append", metavar="TAG", help=tag_hilfe)
    sp.add_argument("--max-time", type=int, dest="max_time", help="Max. Dauer (Minuten).")
    sp.set_defaults(func=cmd_list)

    sp = sub.add_parser("search", parents=[base],
                        help="Volltextsuche ueber Titel, Tags und Zutaten/Text.")
    sp.add_argument("query", help='Suchbegriffe, z.B. "linsen kokos".')
    sp.add_argument("--match", choices=["any", "all"], default="any",
                    help="any: irgendein Begriff; all: alle Begriffe.")
    sp.add_argument("--tag", action="append", metavar="TAG", help=tag_hilfe)
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

    sp = sub.add_parser("einkauf", help="Einkaufsliste verwalten.")
    esub = sp.add_subparsers(dest="einkauf_command", required=True)

    ep = esub.add_parser("list", parents=[base], help="Einkaufsliste anzeigen.")
    ep.add_argument("--offen", action="store_true",
                    help="Nur offene (nicht abgehakte) Eintraege.")
    ep.set_defaults(func=cmd_einkauf_list)

    ep = esub.add_parser("add", parents=[base], help="Eintrag hinzufuegen.")
    ep.add_argument("text", help='Was gekauft werden soll, z.B. "200 g Spaghetti".')
    ep.add_argument("--menge", help="Optionale Mengenangabe.")
    ep.set_defaults(func=cmd_einkauf_add)

    ep = esub.add_parser("rezept", parents=[base],
                         help="Alle Zutaten eines Rezepts auf die Liste setzen.")
    ep.add_argument("slug")
    ep.set_defaults(func=cmd_einkauf_rezept)

    ep = esub.add_parser("check", parents=[base], help="Eintrag abhaken.")
    ep.add_argument("id")
    ep.set_defaults(func=cmd_einkauf_check)

    ep = esub.add_parser("uncheck", parents=[base], help="Haekchen wieder entfernen.")
    ep.add_argument("id")
    ep.set_defaults(func=cmd_einkauf_uncheck)

    ep = esub.add_parser("remove", parents=[base], help="Eintrag entfernen.")
    ep.add_argument("id")
    ep.set_defaults(func=cmd_einkauf_remove)

    ep = esub.add_parser("clear", parents=[base],
                         help="Alle erledigten Eintraege entfernen.")
    ep.set_defaults(func=cmd_einkauf_clear)

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
