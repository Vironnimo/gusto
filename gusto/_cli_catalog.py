"""CLI catalog discovery, recipes, cooking history, archive and images."""
from __future__ import annotations

import sys

from . import _cli_editor as _editor
from . import client
from ._cli_terminal import (add_paging, command_line, dump, nonnegative, page,
                            recipe_result, remote, text)


def _collect_tags(values) -> list[str]:
    """--tag may be given multiple times AND comma-separated -> flat tag list."""
    out: list[str] = []
    for v in values or []:
        out.extend(t.strip() for t in v.split(",") if t.strip())
    return out


def _print_list(recipes: list[dict], args) -> None:
    def render(r):
        _print_recipe_row(r)
    page(args, recipes, "Rezepte (Titelreihenfolge)", render)


def _print_recipe_row(r: dict) -> None:
    meta = []
    if r.get("duration_min"):
        meta.append(f"{r['duration_min']} min")
    if r.get("servings"):
        meta.append(f"{r['servings']} P.")
    if r.get("tags"):
        meta.append(", ".join(text(tag) for tag in r["tags"]))
    if r.get("images"):
        meta.append(f"{len(r['images'])} Bilder")
    if "last_cooked" in r:
        meta.append(f"zuletzt gekocht: {r['last_cooked'] or 'noch nie'}")
    extra = "  ·  ".join(meta)
    print(f"{r['slug']:<22} {text(r['title'])}" + (f"   [{extra}]" if extra else ""))


def cmd_list(args):
    recipes = remote(args, "catalog.list", {
        "tags": _collect_tags(args.tag),
        "max_time": args.max_time,
    })
    _print_list(recipes, args)


def cmd_search(args):
    recipes = remote(args, "catalog.search", {
        "query": args.query,
        "match": args.match,
        "tags": _collect_tags(args.tag),
        "max_time": args.max_time,
    })
    _print_list(recipes, args)


def cmd_tags(args):
    groups = remote(args, "tags.list", {"all": args.all})
    if not args.json and not args.all:
        print("Hinweis: Nur verwendete Tags; --all zeigt auch unbenutzte Tags.")
    page(args, groups, "Tag-Kategorien (definierte Reihenfolge)", _print_category)


def _print_category(category: dict) -> None:
    print(f"{category.get('key') or 'Sonstige'} — {text(category['label'])}")
    print("  " + (", ".join(text(tag) for tag in category["tags"]) if category["tags"] else "—"))


def cmd_categories_list(args):
    categories = remote(args, "categories.list")
    page(args, categories, "Tag-Kategorien (definierte Reihenfolge)", _print_category)


def cmd_categories_add(args):
    category = remote(args, "categories.add", {
        "key": args.key,
        "label": args.label,
        "position": args.position,
    })
    if args.json:
        dump(category)
    else:
        print("Tag-Kategorie angelegt:")
        _print_category(category)


def cmd_categories_set(args):
    category = remote(args, "categories.set", {
        "key": args.key,
        "label": args.label,
        "position": args.position,
    })
    if args.json:
        dump(category)
    else:
        print("Tag-Kategorie aktualisiert:")
        _print_category(category)


def cmd_categories_remove(args):
    result = remote(args, "categories.remove", {"key": args.key})
    if args.json:
        dump(result)
        return
    print(f"Tag-Kategorie entfernt: {result['key']} — {text(result['label'])}")
    if result["now_uncategorized"]:
        print("Jetzt in Sonstige: " + ", ".join(text(tag) for tag in result["now_uncategorized"]))


def cmd_categories_assign(args):
    category = remote(args, "categories.assign", {
        "key": args.key,
        "tags": args.tags,
    })
    if args.json:
        dump(category)
    else:
        print("Tags zugeordnet:")
        _print_category(category)


def cmd_categories_unassign(args):
    result = remote(args, "categories.unassign", {"tags": args.tags})
    if args.json:
        dump(result)
    elif result["unassigned"]:
        print("Jetzt in Sonstige: " + ", ".join(text(tag) for tag in result["unassigned"]))
    else:
        print("Keines der Tags war einer Kategorie zugeordnet.")


def cmd_categories_tag_move(args):
    category = remote(args, "categories.tag.move", {
        "key": args.key,
        "tag": args.tag,
        "position": args.position,
    })
    if args.json:
        dump(category)
    else:
        print("Tag-Reihenfolge aktualisiert:")
        _print_category(category)


def cmd_show(args):
    recipe = remote(args, "recipe.show", {"slug": args.slug})
    if args.json:
        dump(recipe)
    else:
        recipe_result(args, recipe)


def cmd_new(args):
    tags = [t.strip() for t in (args.tags or "").split(",") if t.strip()]
    arguments = {
        "title": args.title,
        "tags": tags,
        "duration_min": args.duration,
        "servings": args.servings,
    }
    if args.file is not None or args.stdin:
        arguments["content"] = _editor.read_content(args)
    recipe = remote(args, "recipe.create", arguments)
    editor_result = None
    if args.edit:
        try:
            editor_result = _editor._edit_remote(args, recipe["slug"])
            recipe = {**recipe, **args._edited_recipe}
        except (OSError, ValueError) as error:
            raise client.ClientError(
                f"Rezept wurde bereits angelegt (Slug '{recipe['slug']}'); "
                f"die Bearbeitung wurde nicht abgeschlossen: {error}",
                outcome_unknown=getattr(error, "outcome_unknown", False),
            ) from error
    if args.json:
        result = dict(recipe)
        if editor_result is not None:
            result["editor"] = editor_result
        dump(result)
    else:
        recipe_result(args, recipe, heading="Rezept angelegt", mutation=True)


def cmd_cooked(args):
    result = remote(
        args, "recipe.cooked", {"slug": args.slug, "date": args.date},
    )
    if args.json:
        dump(result)
    else:
        print(f"Notiert: '{args.slug}' am {result['date']} gekocht.")


def cmd_log(args):
    entries = remote(args, "log.list", {"days": args.days})
    def render(entry):
        suffix = " (archiviert)" if entry.get("archived") else ""
        print(f"{entry['slug']:<22} {entry['date']}   {text(entry.get('title', entry['slug']))}{suffix}")
    page(args, entries if args.json else list(reversed(entries)),
         "Koch-Logbuch (neueste zuerst)", render)


def cmd_suggest(args):
    candidates = remote(
        args, "suggest.list", {"days": args.days, "limit": args.limit},
    )
    page(args, candidates, f"Vorschläge (seit {args.days} Tagen nicht gekocht; älteste zuerst)", _print_recipe_row)


def cmd_set(args):
    if not any([
        args.title is not None,
        args.tags is not None,
        args.duration is not None,
        args.servings is not None,
        args.clear_duration,
        args.clear_servings,
        args.file is not None,
        args.stdin,
    ]):
        sys.exit("Gib mindestens eine Änderung an.")
    tags = None
    if args.tags is not None:
        tags = [t.strip() for t in args.tags.split(",") if t.strip()]
    arguments = {
        "slug": args.slug,
        "title": args.title,
        "tags": tags,
        "duration_min": args.duration,
        "servings": args.servings,
        "clear_duration": args.clear_duration,
        "clear_servings": args.clear_servings,
        "revision": args.revision,
    }
    if args.file is not None or args.stdin:
        arguments["content"] = _editor.read_content(args)
    recipe = remote(args, "recipe.update", arguments)
    if args.json:
        dump(recipe)
    else:
        recipe_result(args, recipe, heading="Rezept gespeichert", mutation=True)


def cmd_delete(args):
    archived = remote(args, "recipe.archive", {"slug": args.slug})
    if args.json:
        dump(archived)
    else:
        print(f"Archiviert: {archived['slug']}")
        print("Rückgängig: " + command_line(args, command=["archive", "restore", archived["slug"]]))


def cmd_archive_list(args):
    entries = remote(args, "archive.list")
    page(args, entries, "Rezeptarchiv", lambda entry: print(
        f"{entry['slug']:<22} {text(entry['title'])}   [{entry['archived_at']}]"))


def cmd_archive_show(args):
    entry = remote(args, "archive.show", {"slug": args.slug})
    if args.json:
        dump(entry)
    else:
        recipe_result(args, entry, heading="Archiviertes Rezept", archived=True)


def cmd_archive_restore(args):
    result = remote(args, "archive.restore", {"slug": args.slug})
    if args.json:
        dump(result)
    else:
        recipe_result(args, result["recipe"], heading="Rezept wiederhergestellt", mutation=True)


def cmd_archive_purge(args):
    if not args.yes:
        sys.exit(
            "Endgültiges Löschen braucht --yes. Der Archiveintrag kann danach "
            "nicht wiederhergestellt werden."
        )
    result = remote(
        args, "archive.purge", {"slug": args.slug, "yes": True},
    )
    if args.json:
        dump(result)
    else:
        print(f"Endgültig gelöscht: {result['slug']}")


def cmd_image_list(args):
    result = remote(args, "image.list", {"slug": args.slug})
    if args.json:
        dump(result)
        return
    images = result["images"]
    cover_id = result["cover_image_id"]
    def render(image):
        marker = " [Top-Bild]" if image["id"] == cover_id else ""
        caption = f" — {text(image['caption'])}" if image.get("caption") else ""
        print(f"  {image['id']}  {text(image['role'])}{marker}{caption}")
        if image.get("filename"):
            print(f"  Datei: {text(image['filename'])}")
    page(args, images, f"Bilder für {text(args.slug)} (gespeicherte Reihenfolge)", render)


def _print_image(image):
    print(f"  ID: {image['id']}")
    for key, label in (("role", "Rolle"), ("caption", "Bildunterschrift"), ("filename", "Datei")):
        if key in image:
            print(f"  {label}: {text(image[key]) or 'keine'}")
    if "is_cover" in image:
        print("  Top-Bild: " + ("ja" if image["is_cover"] else "nein"))


def cmd_image_add(args):
    image = remote(
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
        dump(image)
    else:
        marker = " (Top-Bild)" if image.get("is_cover") else ""
        print(f"Bild hinzugefuegt: {image['id']}{marker}")
        _print_image(image)


def cmd_image_set(args):
    if args.role is None and args.caption is None:
        sys.exit("Gib --role und/oder --caption an.")
    image = remote(args, "image.set", {
        "slug": args.slug,
        "id": args.id,
        "role": args.role,
        "caption": args.caption,
    })
    if args.json:
        dump(image)
    else:
        print(f"Bild aktualisiert: {image['id']}")
        _print_image(image)


def cmd_image_cover(args):
    result = remote(
        args, "image.cover", {"slug": args.slug, "id": args.id},
    )
    if args.json:
        dump(result)
    else:
        print(f"Top-Bild gesetzt: {result['cover_image_id']}")


def cmd_image_remove(args):
    result = remote(
        args, "image.remove", {"slug": args.slug, "id": args.id},
    )
    if args.json:
        dump(result)
    else:
        print(f"Bild entfernt: {args.id}")
        print(f"Top-Bild: {result.get('cover_image_id') or 'keines'}")


def register_discovery(sub, base) -> None:
    tag_help = ("Nach Tag filtern; mehrfach oder kommagetrennt moeglich "
                "(--tag italienisch --tag pizza  bzw.  --tag italienisch,pizza). "
                "ODER innerhalb einer Kategorie, UND ueber Kategorien. "
                "Ohne Angabe: alle Tags.")

    max_time_help = (
        "Positive max. Dauer in Minuten; Rezepte ohne Dauerangabe werden ausgeschlossen. "
        "Ohne Angabe: keine Zeitgrenze."
    )

    sp = sub.add_parser("list", parents=[base], help="Rezepte auflisten/filtern.")
    sp.add_argument("--tag", action="append", metavar="TAG", help=tag_help)
    sp.add_argument("--max-time", type=int, dest="max_time", help=max_time_help)
    add_paging(sp)
    sp.set_defaults(func=cmd_list)

    sp = sub.add_parser("search", parents=[base],
                        help="Volltextsuche ueber Titel, Tags und Zutaten/Text.")
    sp.add_argument("query", help='Suchbegriffe, z.B. "linsen kokos".')
    sp.add_argument("--match", choices=["any", "all"], default="any",
                    help="any: irgendein Begriff; all: alle Begriffe. Ohne Angabe: any.")
    sp.add_argument("--tag", action="append", metavar="TAG", help=tag_help)
    sp.add_argument("--max-time", type=int, dest="max_time", help=max_time_help)
    add_paging(sp)
    sp.set_defaults(func=cmd_search)

    sp = sub.add_parser("tags", parents=[base],
                        help="Tag-Kategorien (Facetten) anzeigen.")
    sp.add_argument("--all", action="store_true",
                    help="Auch unbenutzte Kategorien/Tags zeigen. Ohne Flag: verwendete Tags.")
    add_paging(sp)
    sp.set_defaults(func=cmd_tags)

    sp = sub.add_parser("categories", help="Tag-Kategorien verwalten.")
    catsub = sp.add_subparsers(dest="categories_command", required=True)

    cap = catsub.add_parser(
        "list", parents=[base], help="Alle Tag-Kategorien und Tags anzeigen.",
    )
    add_paging(cap)
    cap.set_defaults(func=cmd_categories_list)

    cap = catsub.add_parser(
        "add", parents=[base], help="Leere Tag-Kategorie anlegen.",
    )
    cap.add_argument("key", help="Stabiler technischer Schlüssel, z.B. season.")
    cap.add_argument("label", help="Sichtbare Bezeichnung, z.B. Saison.")
    cap.add_argument("--position", type=int, help="Position ab 1. Ohne Angabe: hinten anfügen.")
    cap.set_defaults(func=cmd_categories_add)

    cap = catsub.add_parser(
        "set", parents=[base], help="Bezeichnung oder Position ändern.",
    )
    cap.add_argument("key", help="Kategorie-Schlüssel aus categories list.")
    cap.add_argument("--label", help="Neue sichtbare Bezeichnung. Ohne Angabe: unverändert.")
    cap.add_argument("--position", type=int, help="Position ab 1. Ohne Angabe: unverändert.")
    cap.set_defaults(func=cmd_categories_set)

    cap = catsub.add_parser(
        "remove", parents=[base],
        help="Kategorie entfernen; ihre verwendeten Tags werden Sonstige.",
    )
    cap.add_argument("key", help="Kategorie-Schlüssel aus categories list.")
    cap.set_defaults(func=cmd_categories_remove)

    cap = catsub.add_parser(
        "assign", parents=[base],
        help="Tags genau einer Kategorie zuordnen.",
    )
    cap.add_argument("key", help="Zielkategorie-Schlüssel aus categories list.")
    cap.add_argument("tags", nargs="+", help="Ein oder mehrere Tag-Namen.")
    cap.set_defaults(func=cmd_categories_assign)

    cap = catsub.add_parser(
        "unassign", parents=[base],
        help="Tags aus ihren Kategorien lösen; verwendete Tags werden Sonstige.",
    )
    cap.add_argument("tags", nargs="+", help="Ein oder mehrere Tag-Namen.")
    cap.set_defaults(func=cmd_categories_unassign)

    cap = catsub.add_parser(
        "tag-move", parents=[base],
        help="Tag innerhalb seiner Kategorie auf eine Position verschieben.",
    )
    cap.add_argument("key", help="Kategorie-Schlüssel aus categories list.")
    cap.add_argument("tag", help="Bereits zugeordneter Tag aus categories list.")
    cap.add_argument("position", type=int, help="Position ab 1.")
    cap.set_defaults(func=cmd_categories_tag_move)


def register_recipes(sub, base) -> None:
    sp = sub.add_parser("show", parents=[base], help="Rezept mit Metadaten, Revision und Inhalt lesen; ändert nichts.")
    sp.add_argument("slug", help="Rezept-Slug aus list oder search.")
    sp.add_argument("--offset", type=nonnegative, default=0, help="Zeichenoffset in den Rezeptdetails aus Weiter. Ohne Angabe: Anfang; JSON bleibt vollständig.")
    sp.set_defaults(func=cmd_show)

    sp = sub.add_parser("new", parents=[base], help="Rezept mit Inhalt und Metadaten anlegen; ohne --file/--stdin eine leere Vorlage.")
    sp.add_argument("title", help="Titel; der Server erzeugt daraus den Rezept-Slug.")
    sp.add_argument("--tags", help="Kommagetrennt, z.B. pasta,schnell. Ohne Angabe: keine Tags.")
    sp.add_argument("--duration", type=int, help="Dauer als positive ganze Minuten. Ohne Angabe: unbekannt.")
    sp.add_argument("--servings", type=int, help="Positive ganze Portionszahl. Ohne Angabe: unbekannt.")
    source = _editor.add_content_input(sp)
    source.add_argument("--edit", action="store_true", help="Interaktiv im Editor bearbeiten; für Menschen. Ohne Flag: direkt speichern.")
    sp.set_defaults(func=cmd_new)

    _editor.register_commands(sub, base)

    sp = sub.add_parser("cooked", parents=[base], help="Rezept als gekocht eintragen.")
    sp.add_argument("slug", help="Rezept-Slug aus list oder search.")
    sp.add_argument(
        "--date", help="Gültiges ISO-Datum YYYY-MM-DD, nicht in der Zukunft "
                       "Ohne Angabe: heute auf dem Server.",
    )
    sp.set_defaults(func=cmd_cooked)

    sp = sub.add_parser("log", parents=[base], help="Koch-Logbuch anzeigen.")
    sp.add_argument("--days", type=int, help="Letzte N Tage, positive ganze Zahl. Ohne Angabe: gesamtes Logbuch.")
    add_paging(sp)
    sp.set_defaults(func=cmd_log)

    sp = sub.add_parser("suggest", parents=[base], help="Kandidaten fuers naechste Essen.")
    sp.add_argument("--days", type=int, default=7,
                    help="In den letzten N Tagen Gekochtes ausschließen; positive ganze Zahl. Ohne Angabe: 7 Tage.")
    sp.add_argument(
        "--limit", type=int,
        help="Höchstens N Vorschläge; 0 liefert keine. Ohne Angabe: alle Kandidaten, Text bis 40 je Seite; am längsten nicht gekochte zuerst.",
    )
    add_paging(sp, limit=False)
    sp.set_defaults(func=cmd_suggest)


def register_management(sub, base) -> None:
    sp = sub.add_parser("set", parents=[base], help="Inhalt und/oder Metadaten gemeinsam speichern; benötigt die gelesene Revision.")
    sp.add_argument("slug", help="Rezept-Slug aus list oder search; mindestens eine Änderung angeben.")
    sp.add_argument("--revision", required=True, help="Revision aus show; bei Konflikt erneut lesen und Änderungen prüfen.")
    sp.add_argument("--title", help="Titel und Markdown-H1 ändern; Slug bleibt gleich. Ohne Angabe: unverändert.")
    sp.add_argument("--tags", help="Kommagetrennt; ersetzt alle Tags. Leerer Wert entfernt alle. Ohne Angabe: unverändert.")
    duration = sp.add_mutually_exclusive_group()
    duration.add_argument("--duration", type=int, help="Positive ganze Minuten. Ohne Angabe: unverändert.")
    duration.add_argument("--clear-duration", action="store_true",
                          help="Gespeicherte Dauer entfernen.")
    servings = sp.add_mutually_exclusive_group()
    servings.add_argument("--servings", type=int, help="Positive ganze Portionszahl. Ohne Angabe: unverändert.")
    servings.add_argument("--clear-servings", action="store_true",
                          help="Gespeicherte Portionszahl entfernen.")
    _editor.add_content_input(sp)
    sp.set_defaults(func=cmd_set)

    sp = sub.add_parser(
        "delete", parents=[base],
        help="Rezept samt Markdown, Metadaten und Bildern reversibel archivieren.",
    )
    sp.add_argument("slug", help="Rezept-Slug aus list oder search. Rückgängig mit archive restore.")
    sp.set_defaults(func=cmd_delete)

    sp = sub.add_parser("archive", help="Archivierte Rezepte verwalten.")
    asub = sp.add_subparsers(dest="archive_command", required=True)

    ap = asub.add_parser("list", parents=[base],
                         help="Archivierte Rezepte auflisten.")
    add_paging(ap)
    ap.set_defaults(func=cmd_archive_list)

    ap = asub.add_parser("show", parents=[base],
                         help="Archiviertes Rezept ausgeben.")
    ap.add_argument("slug", help="Archiv-Slug aus archive list.")
    ap.add_argument("--offset", type=nonnegative, default=0, help="Zeichenoffset in den Rezeptdetails aus Weiter. Ohne Angabe: Anfang; JSON bleibt vollständig.")
    ap.set_defaults(func=cmd_archive_show)

    ap = asub.add_parser("restore", parents=[base],
                         help="Rezept vollständig ins aktive Kochbuch zurückholen.")
    ap.add_argument("slug", help="Archiv-Slug aus archive list.")
    ap.set_defaults(func=cmd_archive_restore)

    purge_help = (
        "Archiviertes Rezept unwiderruflich löschen; benötigt --yes und "
        "scheitert bei sichtbaren Einkaufsposten aus diesem Rezept."
    )
    ap = asub.add_parser(
        "purge", parents=[base], help=purge_help, description=purge_help,
    )
    ap.add_argument("slug", help="Archiv-Slug aus archive list.")
    ap.add_argument("--yes", action="store_true",
                    help="Unwiderrufliches Löschen ausdrücklich bestätigen.")
    ap.set_defaults(func=cmd_archive_purge)

    sp = sub.add_parser("image", help="Bilder eines Rezepts verwalten.")
    isub = sp.add_subparsers(dest="image_command", required=True)

    ip = isub.add_parser("list", parents=[base], help="Rezeptbilder anzeigen.")
    ip.add_argument("slug", help="Rezept-Slug aus list oder search.")
    add_paging(ip)
    ip.set_defaults(func=cmd_image_list)

    ip = isub.add_parser("add", parents=[base], help="Bild zu einem Rezept kopieren.")
    ip.add_argument("slug", help="Rezept-Slug aus list oder search.")
    ip.add_argument("path", help="Lokale Bilddatei (JPG, PNG, WebP oder GIF).")
    ip.add_argument("--role", default="gallery",
                    help="Freier Zweck, z.B. result, step, ingredients. Ohne Angabe: gallery.")
    ip.add_argument("--caption", default="", help="Bildunterschrift. Ohne Angabe: keine.")
    ip.add_argument("--cover", action="store_true", help="Als Top-Bild wählen. Ohne Flag: nur das erste Bild wird automatisch Top-Bild.")
    ip.set_defaults(func=cmd_image_add)

    ip = isub.add_parser("set", parents=[base], help="Rolle/Beschriftung aendern.")
    ip.add_argument("slug", help="Rezept-Slug aus list oder search.")
    ip.add_argument("id", help="Bild-ID aus image list für dieses Rezept.")
    ip.add_argument("--role", help="Freier Zweck. Ohne Angabe: unverändert.")
    ip.add_argument("--caption", help="Bildunterschrift; leer entfernt sie. Ohne Angabe: unverändert.")
    ip.set_defaults(func=cmd_image_set)

    ip = isub.add_parser("cover", parents=[base], help="Top-Bild auswaehlen.")
    ip.add_argument("slug", help="Rezept-Slug aus list oder search.")
    ip.add_argument("id", help="Bild-ID aus image list für dieses Rezept.")
    ip.set_defaults(func=cmd_image_cover)

    ip = isub.add_parser("remove", parents=[base], help="Gespeichertes Bild löschen; nicht wiederherstellbar, lokale Quelldatei bleibt erhalten.")
    ip.add_argument("slug", help="Rezept-Slug aus list oder search.")
    ip.add_argument("id", help="Bild-ID aus image list für dieses Rezept.")
    ip.set_defaults(func=cmd_image_remove)
