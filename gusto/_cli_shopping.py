"""CLI shopping list, shopping needs and preferred products."""
from __future__ import annotations

import sys

from . import client
from ._cli_terminal import dump, remote, text


def cmd_favorites_list(args):
    needs = remote(args, "favorites.list")
    if args.json:
        dump(needs)
        return
    if not needs:
        print("Noch keine Einkaufsbedarfe hinterlegt.")
        return
    for need in needs:
        count = len(need["products"])
        noun = "Produkt" if count == 1 else "Produkte"
        print(f"  {need['id']}  {text(need['name'])}  ({count} {noun})")


def _store_arguments(args, arguments: dict) -> dict:
    if args.store is not None:
        arguments["store"] = args.store
    return arguments


def _print_favorite_products(need: dict) -> None:
    if not need["products"]:
        print(f"  Kein Lieblingsprodukt bei {text(need['store'])}." if "store" in need
              else "  Noch keine Lieblingsprodukte hinterlegt.")
    for position, product in enumerate(need["products"], 1):
        details = " · ".join(
            text(value) for value in [product["brand"], product.get("store")] if value
        )
        print(
            f"  {position}. {text(product['name'])}  (ID: {product['id']})"
            + (f"  [{details}]" if details else "")
        )
        if product.get("note"):
            print(f"     Notiz: {text(product['note'])}")
        if product.get("image_filename"):
            print(f"     Bild: {text(product['image_filename'])}")
    elsewhere = need.get("other_products") or []
    if elsewhere:
        print("  Anderswo: " + ", ".join(
            f"{text(product['name'])} ({text(product['store'])})"
            f" [ID: {product['id']}]" for product in elsewhere
        ))


def cmd_favorites_show(args):
    need = remote(args, "favorites.show",
                   _store_arguments(args, {"need": args.need}))
    if args.json:
        dump(need)
        return
    print(f"{need['id']}  {text(need['name'])}"
          + (f"  (bei {text(need['store'])})" if "store" in need else ""))
    if need["aliases"]:
        print("  Aliasse: " + ", ".join(text(alias) for alias in need["aliases"]))
    _print_favorite_products(need)


def cmd_favorites_match(args):
    need = remote(args, "favorites.match",
                   _store_arguments(args, {"text": args.text}))
    if args.json:
        dump(need)
    elif need is None:
        print("Keine Zuordnung gefunden.")
    else:
        print(f"{text(args.text)} -> {need['id']}  {text(need['name'])}")
        _print_favorite_products(need)


def cmd_favorites_add(args):
    need = remote(
        args, "favorites.add", {"name": args.name, "aliases": args.alias or []},
    )
    if args.json:
        dump(need)
    else:
        print(f"Einkaufsbedarf angelegt: {text(need['name'])} ({need['id']})")


def cmd_favorites_set(args):
    need = remote(
        args, "favorites.set", {"need": args.need, "name": args.name},
    )
    if args.json:
        dump(need)
    else:
        print(f"Einkaufsbedarf aktualisiert: {need['id']}  {text(need['name'])}")


def cmd_favorites_remove(args):
    result = remote(args, "favorites.remove", {"need": args.need})
    if args.json:
        dump(result)
    else:
        print(f"Einkaufsbedarf entfernt: {text(args.need)}")


def cmd_favorites_alias_add(args):
    need = remote(
        args, "favorites.alias.add", {"need": args.need, "alias": args.alias},
    )
    if args.json:
        dump(need)
    else:
        print(f"Alias bei '{text(need['name'])}' hinterlegt: {text(args.alias)}")


def cmd_favorites_alias_remove(args):
    need = remote(
        args, "favorites.alias.remove",
        {"need": args.need, "alias": args.alias},
    )
    if args.json:
        dump(need)
    else:
        print(f"Alias bei '{text(need['name'])}' entfernt: {text(args.alias)}")


def cmd_favorites_product_add(args):
    attachments = (
        [client.encode_attachment("image", args.image)] if args.image else None
    )
    product = remote(
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
        dump(product)
    else:
        print(
            f"Lieblingsprodukt hinzugefuegt: "
            f"{text(product['name'])} ({product['id']})"
        )


def cmd_favorites_product_set(args):
    if not any(value is not None for value in
               [args.name, args.brand, args.store, args.note, args.image]) \
            and not args.remove_image:
        sys.exit("Gib mindestens eine Aenderung an.")
    attachments = (
        [client.encode_attachment("image", args.image)] if args.image else None
    )
    product = remote(
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
        dump(product)
    else:
        print(f"Lieblingsprodukt aktualisiert: {product['id']}  {text(product['name'])}")


def cmd_favorites_product_move(args):
    need = remote(args, "favorites.product.move", {
        "need": args.need,
        "id": args.id,
        "position": args.position,
    })
    if args.json:
        dump(need)
    else:
        print(f"Reihenfolge bei '{text(need['name'])}' aktualisiert.")
        _print_favorite_products(need)


def cmd_favorites_product_remove(args):
    need = remote(
        args, "favorites.product.remove", {"need": args.need, "id": args.id},
    )
    if args.json:
        dump(need)
    else:
        print(f"Lieblingsprodukt {args.id} bei '{text(need['name'])}' entfernt.")


def _print_shopping_item(item: dict, as_json: bool, *, prefix: str = "") -> None:
    if as_json:
        dump(item)
        return
    marker = "[x]" if item["checked"] else "[ ]"
    line = f"{marker} {item['id']}  {text(item['text'])}"
    if item.get("quantity"):
        line += f"  ({text(item['quantity'])})"
    if item.get("source"):
        line += f"  (aus {item['source']})"
    print(prefix + line)


def cmd_shopping_list(args):
    items = remote(args, "shopping.list", {"pending": args.pending})
    if args.json:
        dump(items)
        return
    if not items:
        print("Keine offenen Einkaufsposten." if args.pending else "Einkaufsliste ist leer.")
        return
    print(f"{len(items)} {'offene ' if args.pending else ''}Einkaufsposten:")
    for i in items:
        _print_shopping_item(i, False, prefix="  ")


def cmd_shopping_add(args):
    item = remote(args, "shopping.add", {
        "text": args.text,
        "quantity": args.quantity or "",
        "source": args.source,
    })
    if args.json:
        dump(item)
    else:
        _print_shopping_item(item, False, prefix="Hinzugefuegt: ")


def cmd_shopping_add_many(args):
    items = remote(args, "shopping.add_many", {"texts": args.texts})
    if args.json:
        dump(items)
    else:
        print(f"{len(items)} Einkaufsposten hinzugefuegt.")
        for item in items:
            _print_shopping_item(item, False, prefix="  ")


def cmd_shopping_add_recipe(args):
    items = remote(args, "shopping.add_recipe", {"slug": args.slug,
                   "servings": args.servings, "revision": args.revision})
    if args.json:
        dump(items)
    else:
        print(f"{len(items)} Zutat(en) aus '{args.slug}' hinzugefuegt.")
        for item in items:
            _print_shopping_item(item, False, prefix="  ")


def cmd_shopping_preview_recipe(args):
    preview = remote(args, "shopping.preview_recipe", {
        "slug": args.slug, "servings": args.servings,
    })
    if args.json:
        dump(preview)
        return
    servings = preview["target_servings"]
    label = f"{servings} Portionen" if servings is not None else "unbekannte Portionszahl"
    print(f"Zutaten für {label} (Revision: {preview['recipe_revision']}):")
    for ingredient in preview["ingredients"]:
        print("  " + text(ingredient["scaled"]))
        if ingredient.get("warning"):
            print("    Hinweis: " + text(ingredient["warning"]))


def cmd_shopping_check(args):
    item = remote(args, "shopping.check", {"id": args.id})
    if args.json:
        dump(item)
    else:
        _print_shopping_item(item, False, prefix="Abgehakt: ")


def cmd_shopping_uncheck(args):
    item = remote(args, "shopping.uncheck", {"id": args.id})
    if args.json:
        dump(item)
    else:
        _print_shopping_item(item, False, prefix="Wieder offen: ")


def cmd_shopping_remove(args):
    result = remote(args, "shopping.remove", {"id": args.id})
    if args.json:
        dump(result)
    else:
        print(f"Entfernt: {args.id}")


def cmd_shopping_remove_done(args):
    result = remote(args, "shopping.remove_done")
    if args.json:
        dump(result)
    else:
        count = result["removed"]
        noun = "erledigter Eintrag" if count == 1 else "erledigte Einträge"
        print(f"{count} {noun} entfernt.")


def cmd_shopping_clear(args):
    result = remote(args, "shopping.clear")
    if args.json:
        dump(result)
    else:
        count = result["removed"]
        noun = "Eintrag" if count == 1 else "Einträge"
        print(f"{count} {noun} entfernt; die Einkaufsliste ist leer.")


def register_commands(sub, base) -> None:
    sp = sub.add_parser("favorites", help="Lieblingsprodukte verwalten.")
    fsub = sp.add_subparsers(dest="favorites_command", required=True)

    store_filter_help = (
        "Ranking auf diesen Laden begrenzen; Produkte ohne Laden gelten überall. "
        "Andere bleiben separat sichtbar. Ohne Angabe: gesamtes Ranking."
    )
    fp = fsub.add_parser("list", parents=[base],
                         help="Alle Einkaufsbedarfe anzeigen.")
    fp.set_defaults(func=cmd_favorites_list)

    fp = fsub.add_parser("show", parents=[base],
                         help="Einen Einkaufsbedarf mit Produkten anzeigen.")
    fp.add_argument("need", help="Id oder exakter Name des Einkaufsbedarfs.")
    fp.add_argument("--store", help=store_filter_help)
    fp.set_defaults(func=cmd_favorites_show)

    fp = fsub.add_parser("match", parents=[base],
                         help="Exakten Bedarfsnamen oder Alias zuordnen und Produktranking zeigen; keine Teiltextsuche.")
    fp.add_argument("text", help="Einkaufstext; Groß-/Kleinschreibung und Leerraum werden ignoriert, Mengen nicht.")
    fp.add_argument("--store", help=store_filter_help)
    fp.set_defaults(func=cmd_favorites_match)

    fp = fsub.add_parser("add", parents=[base],
                         help="Einkaufsbedarf anlegen.")
    fp.add_argument("name", help="Name des Einkaufsbedarfs, z.B. Pizzateig.")
    fp.add_argument("--alias", action="append",
                    help="Exakte weitere Formulierung; mehrfach möglich. Ohne Angabe: keine Aliasse.")
    fp.set_defaults(func=cmd_favorites_add)

    favorites_set_help = (
        "Einkaufsbedarf umbenennen; alten Namen als Alias behalten."
    )
    fp = fsub.add_parser(
        "set", parents=[base],
        help=favorites_set_help, description=favorites_set_help,
    )
    fp.add_argument("need", help="Bedarfs-ID oder exakter Name aus favorites list.")
    fp.add_argument("--name", required=True, help="Neuer Name; der bisherige bleibt als Alias erhalten.")
    fp.set_defaults(func=cmd_favorites_set)

    fp = fsub.add_parser("remove", parents=[base],
                         help="Einkaufsbedarf samt Produktkarten und Bildern löschen; nicht wiederherstellbar.")
    fp.add_argument("need", help="Bedarfs-ID oder exakter Name aus favorites list.")
    fp.set_defaults(func=cmd_favorites_remove)

    fp = fsub.add_parser("alias-add", parents=[base],
                         help="Bekannte Formulierung zuordnen.")
    fp.add_argument("need", help="Bedarfs-ID oder exakter Name aus favorites list.")
    fp.add_argument("alias", help="Exakte weitere Formulierung, z.B. 1 Rolle Pizzateig.")
    fp.set_defaults(func=cmd_favorites_alias_add)

    fp = fsub.add_parser("alias-remove", parents=[base],
                         help="Bekannte Formulierung entfernen.")
    fp.add_argument("need", help="Bedarfs-ID oder exakter Name aus favorites list.")
    fp.add_argument("alias", help="Vorhandener Alias aus favorites show.")
    fp.set_defaults(func=cmd_favorites_alias_remove)

    fp = fsub.add_parser("product-add", parents=[base],
                         help="Lieblingsprodukt hinten an das Ranking anfügen; umordnen mit product-move.")
    fp.add_argument("need", help="Bedarfs-ID oder exakter Name aus favorites list.")
    fp.add_argument("name", help="Name des konkreten Produkts.")
    fp.add_argument("--brand", required=True, help="Marke.")
    fp.add_argument("--store", help="Läden, kommagetrennt. Ohne Angabe oder leer: überall.")
    fp.add_argument("--note", help="Persönliche Notiz. Ohne Angabe: keine.")
    fp.add_argument("--image", help="Lokales JPG, PNG, WebP oder GIF kopieren. Ohne Angabe: kein Bild.")
    fp.set_defaults(func=cmd_favorites_product_add)

    fp = fsub.add_parser("product-set", parents=[base],
                         help="Lieblingsprodukt aktualisieren.")
    fp.add_argument("need", help="Bedarfs-ID oder exakter Name aus favorites list.")
    fp.add_argument("id", help="Produkt-ID aus favorites show für diesen Bedarf.")
    fp.add_argument("--name", help="Neuer Produktname. Ohne Angabe: unverändert.")
    fp.add_argument("--brand", help="Nicht-leere Marke. Ohne Angabe: unverändert.")
    fp.add_argument("--store", help="Läden, kommagetrennt; leer bedeutet überall. Ohne Angabe: unverändert.")
    fp.add_argument("--note", help="Notiz; leer entfernt sie. Ohne Angabe: unverändert.")
    product_image = fp.add_mutually_exclusive_group()
    product_image.add_argument("--image", help="Lokales Bild kopieren und vorhandenes ersetzen. Ohne Angabe: unverändert.")
    product_image.add_argument("--remove-image", action="store_true", help="Gespeichertes Produktbild löschen; nicht wiederherstellbar.")
    fp.set_defaults(func=cmd_favorites_product_set)

    fp = fsub.add_parser("product-move", parents=[base],
                         help="Lieblingsprodukt auf eine Rangposition verschieben.")
    fp.add_argument("need", help="Bedarfs-ID oder exakter Name aus favorites list.")
    fp.add_argument("id", help="Produkt-ID aus favorites show für diesen Bedarf.")
    fp.add_argument("position", type=int, help="Position ab 1.")
    fp.set_defaults(func=cmd_favorites_product_move)

    fp = fsub.add_parser("product-remove", parents=[base],
                         help="Lieblingsprodukt samt Bild löschen; nicht wiederherstellbar.")
    fp.add_argument("need", help="Bedarfs-ID oder exakter Name aus favorites list.")
    fp.add_argument("id", help="Produkt-ID aus favorites show für diesen Bedarf.")
    fp.set_defaults(func=cmd_favorites_product_remove)

    sp = sub.add_parser("shopping", help="Einkaufsliste verwalten.")
    esub = sp.add_subparsers(dest="shopping_command", required=True)

    ep = esub.add_parser("list", parents=[base], help="Einkaufsliste anzeigen.")
    ep.add_argument("--pending", action="store_true",
                    help="Nur offene Einträge. Ohne Flag: offene und abgehakte Einträge.")
    ep.set_defaults(func=cmd_shopping_list)

    ep = esub.add_parser("add", parents=[base], help="Eintrag hinzufuegen.")
    ep.add_argument("text", help='Was gekauft werden soll, z.B. "200 g Spaghetti".')
    ep.add_argument("--quantity", help="Freie Mengenangabe, z.B. 1 L. Ohne Angabe: keine separate Menge.")
    ep.add_argument(
        "--source", metavar="SLUG",
        help="Slug eines aktiven Herkunftsrezepts; zählt für dessen Import-Sperre. Ohne Angabe: keine Rezeptquelle.",
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
    ep.add_argument("slug", help="Rezept-Slug aus list oder search; Zutatenabschnitt darf nicht leer sein.")
    ep.add_argument("--servings", type=int, help="Gewünschte Portionszahl; erfordert die Revision aus preview-recipe.")
    ep.add_argument("--revision", help="recipe_revision aus der geprüften Vorschau.")
    ep.set_defaults(func=cmd_shopping_add_recipe)

    ep = esub.add_parser("preview-recipe", parents=[base],
                         help="Zutaten für eine Portionszahl ohne Änderung prüfen.")
    ep.add_argument("slug", help="Rezept-Slug aus list oder search.")
    ep.add_argument("--servings", type=int, help="Gewünschte Portionszahl. Ohne Angabe: Rezeptportionen.")
    ep.set_defaults(func=cmd_shopping_preview_recipe)

    ep = esub.add_parser("check", parents=[base], help="Eintrag abhaken.")
    ep.add_argument("id", help="Eintrags-ID aus shopping list. Bereits abgehakt bleibt unverändert.")
    ep.set_defaults(func=cmd_shopping_check)

    ep = esub.add_parser("uncheck", parents=[base], help="Haekchen wieder entfernen.")
    ep.add_argument("id", help="Eintrags-ID aus shopping list. Bereits offen bleibt unverändert.")
    ep.set_defaults(func=cmd_shopping_uncheck)

    ep = esub.add_parser("remove", parents=[base], help="Eintrag entfernen; nicht per CLI wiederherstellbar.")
    ep.add_argument("id", help="Eintrags-ID aus shopping list; erneutes Entfernen bleibt ohne Wirkung.")
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
