"""CLI shopping list, shopping needs and preferred products."""
from __future__ import annotations

import sys

from . import client
from ._cli_terminal import add_paging, dump, page, remote, text


def cmd_favorites_list(args):
    needs = remote(args, "favorites.list")

    def render(need):
        count = len(need["products"])
        noun = "Produkt" if count == 1 else "Produkte"
        print(f"  {text(need['id'])}  {text(need['name'])}  ({count} {noun})")

    page(args, needs, "Einkaufsbedarfe in gespeicherter Reihenfolge", render)


def _store_arguments(args, arguments: dict) -> dict:
    if args.store is not None:
        arguments["store"] = args.store
    return arguments


def _print_favorite_products(need: dict) -> None:
    if not need["products"]:
        print(f"  Kein Lieblingsprodukt bei {text(need['store'])}." if "store" in need
              else "  Noch keine Lieblingsprodukte hinterlegt.")
    for position, product in enumerate(need["products"], 1):
        stores = ", ".join(store.strip() for store in product.get("store", "").split(",")
                           if store.strip()) or "alle Läden"
        details = " · ".join(
            text(value) for value in [product["brand"], stores] if value
        )
        print(
            f"  {position}. {text(product['name'])}  (ID: {text(product['id'])})"
            + (f"  [{details}]" if details else "")
        )
        if product.get("note"):
            print(f"     Notiz: {text(product['note'])}")
        if product.get("image_filename"):
            print(f"     Bild: {text(product['image_filename'])}")
    elsewhere = need.get("other_products") or []
    if elsewhere:
        print("  Anderswo: " + ", ".join(
            f"{text(product['name'])} ({text(product['brand'])} · {text(product['store'])})"
            f" [ID: {text(product['id'])}]" for product in elsewhere
        ))


def _print_need(need: dict) -> None:
    print(f"Einkaufsbedarf: {text(need['name'])} (ID: {text(need['id'])})"
          + (f"  (bei {text(need['store'])})" if "store" in need else ""))
    print("  Aliasse: " + (", ".join(text(alias) for alias in need["aliases"]) or "keine"))
    _print_favorite_products(need)


def _print_product(product: dict) -> None:
    print(f"  ID: {text(product['id'])}")
    print(f"  Name: {text(product['name'])}")
    print(f"  Marke: {text(product['brand'])}")
    print(f"  Laden: {text(product.get('store') or 'überall')}")
    print(f"  Notiz: {text(product.get('note') or 'keine')}")
    print(f"  Bild: {text(product.get('image_filename') or 'keines')}")


def cmd_favorites_show(args):
    need = remote(args, "favorites.show",
                   _store_arguments(args, {"need": args.need}))
    if args.json:
        dump(need)
        return
    page(args, [need], "Einkaufsbedarf mit Lieblingsprodukten, bestes zuerst", _print_need)


def cmd_favorites_match(args):
    need = remote(args, "favorites.match",
                   _store_arguments(args, {"text": args.text}))
    if args.json:
        dump(need)
    elif need is None:
        print("Keine Zuordnung gefunden.")
    else:
        print(f"Zuordnung gefunden für: {text(args.text)}")
        page(args, [need], "Einkaufsbedarf mit Lieblingsprodukten, bestes zuerst", _print_need)


def cmd_favorites_add(args):
    need = remote(
        args, "favorites.add", {"name": args.name, "aliases": args.alias or []},
    )
    if args.json:
        dump(need)
    else:
        print("Einkaufsbedarf angelegt.")
        _print_need(need)


def cmd_favorites_set(args):
    need = remote(
        args, "favorites.set", {"need": args.need, "name": args.name},
    )
    if args.json:
        dump(need)
    else:
        print("Einkaufsbedarf aktualisiert.")
        _print_need(need)


def cmd_favorites_remove(args):
    result = remote(args, "favorites.remove", {"need": args.need})
    if args.json:
        dump(result)
    else:
        print(f"Einkaufsbedarf entfernt: {text(result['id'])}; nicht wiederherstellbar.")


def cmd_favorites_alias_add(args):
    need = remote(
        args, "favorites.alias.add", {"need": args.need, "alias": args.alias},
    )
    if args.json:
        dump(need)
    else:
        print(f"Alias bei '{text(need['name'])}' hinterlegt: {text(args.alias)}")
        _print_need(need)


def cmd_favorites_alias_remove(args):
    need = remote(
        args, "favorites.alias.remove",
        {"need": args.need, "alias": args.alias},
    )
    if args.json:
        dump(need)
    else:
        print(f"Alias bei '{text(need['name'])}' entfernt: {text(args.alias)}")
        _print_need(need)


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
        print("Lieblingsprodukt hinzugefügt.")
        _print_product(product)


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
        print("Lieblingsprodukt aktualisiert.")
        _print_product(product)


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
        _print_need(need)


def cmd_favorites_product_remove(args):
    need = remote(
        args, "favorites.product.remove", {"need": args.need, "id": args.id},
    )
    if args.json:
        dump(need)
    else:
        print(f"Lieblingsprodukt {text(args.id)} bei '{text(need['name'])}' entfernt.")
        _print_need(need)


def _print_shopping_item(item: dict, as_json: bool, *, prefix: str = "") -> None:
    if as_json:
        dump(item)
        return
    marker = "[x]" if item["checked"] else "[ ]"
    line = f"{marker} {text(item['id'])}  {text(item['text'])}"
    if item.get("quantity"):
        line += f"  ({text(item['quantity'])})"
    if item.get("source"):
        line += f"  (aus {text(item['source'])})"
    print(prefix + line)
    if "favorite" in item:
        if item["favorite"] is None:
            print("    Kein zugeordneter Einkaufsbedarf.")
        else:
            print(f"    Einkaufsbedarf: {text(item['favorite']['name'])}")
            _print_favorite_products(item["favorite"])


def cmd_shopping_list(args):
    items = remote(args, "shopping.list", _store_arguments(args, {"pending": args.pending}))
    if not items and not args.json:
        print("Keine offenen Einkaufsposten." if args.pending else "Einkaufsliste ist leer.")
        return
    if args.pending and not args.json:
        print("Hinweis: Nur offene Einträge; ohne --pending auch abgehakte anzeigen.")
    page(args, items, "Einkaufsposten, älteste zuerst",
         lambda item: _print_shopping_item(item, False, prefix="  "))


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
        print(f"{len(items)} Zutat(en) aus '{text(args.slug)}' hinzugefügt.")
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

    def render(ingredient):
        print("  " + text(ingredient["scaled"]))
        if ingredient.get("warning"):
            print("    Hinweis: " + text(ingredient["warning"]))

    page(args, preview["ingredients"], "Zutaten in Rezeptreihenfolge", render)


def _shopping_state(args, operation: str, label: str):
    multiple = len(args.id) > 1
    item = remote(args, operation + ("_many" if multiple else ""),
                  {"ids": args.id} if multiple else {"id": args.id[0]})
    if args.json:
        dump(item)
    else:
        items = item["items"] if multiple else [item]
        print(f"{len(items)} Einkaufsposten {label}."
              + (f" {item['changed']} geändert, {item['unchanged']} bereits in diesem Zustand."
                 if multiple else ""))
        for saved in items:
            _print_shopping_item(saved, False, prefix="  ")


def cmd_shopping_check(args):
    _shopping_state(args, "shopping.check", "abgehakt")


def cmd_shopping_uncheck(args):
    _shopping_state(args, "shopping.uncheck", "offen")


def cmd_shopping_remove(args):
    multiple = len(args.id) > 1
    result = remote(args, "shopping.remove_many" if multiple else "shopping.remove",
                    {"ids": args.id} if multiple else {"id": args.id[0]})
    if args.json:
        dump(result)
    else:
        if multiple:
            print(f"{result['removed']} Einkaufsposten entfernt; "
                  f"{result['unchanged']} bereits entfernt. Nicht wiederherstellbar.")
            for item_id in result["ids"]:
                print(f"  ID: {text(item_id)}")
        else:
            print(f"Entfernt: {text(args.id[0])}; nicht wiederherstellbar.")


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
    add_paging(fp)
    fp.set_defaults(func=cmd_favorites_list)

    fp = fsub.add_parser("show", parents=[base],
                         help="Einen Einkaufsbedarf mit Produkten anzeigen.")
    fp.add_argument("need", help="Id oder exakter Name des Einkaufsbedarfs.")
    fp.add_argument("--store", help=store_filter_help)
    add_paging(fp, limit=False)
    fp.set_defaults(func=cmd_favorites_show)

    fp = fsub.add_parser("match", parents=[base],
                         help="Exakten Bedarfsnamen oder Alias zuordnen und Produktranking zeigen; keine Teiltextsuche.")
    fp.add_argument("text", help="Einkaufstext; Groß-/Kleinschreibung und Leerraum werden ignoriert, Mengen nicht.")
    fp.add_argument("--store", help=store_filter_help)
    add_paging(fp, limit=False)
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
    ep.add_argument("--store", help="Passende Lieblingsprodukte für diesen Laden mit anzeigen. "
                    "Ohne Angabe: nur die Einkaufsposten.")
    add_paging(ep)
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
    add_paging(ep)
    ep.set_defaults(func=cmd_shopping_preview_recipe)

    ep = esub.add_parser("check", parents=[base], help="Ein oder mehrere Einträge gemeinsam abhaken; gespeicherte Zustände ausgeben.")
    ep.add_argument("id", nargs="+", help="Eintrags-IDs aus shopping list. Bereits abgehakt bleibt unverändert. "
                    "Ungültige IDs verhindern die gesamte Änderung.")
    ep.set_defaults(func=cmd_shopping_check)

    ep = esub.add_parser("uncheck", parents=[base], help="Ein oder mehrere Einträge gemeinsam öffnen; gespeicherte Zustände ausgeben.")
    ep.add_argument("id", nargs="+", help="Eintrags-IDs aus shopping list. Bereits offen bleibt unverändert. "
                    "Ungültige IDs verhindern die gesamte Änderung.")
    ep.set_defaults(func=cmd_shopping_uncheck)

    ep = esub.add_parser("remove", parents=[base], help="Ein oder mehrere Einträge gemeinsam entfernen; nicht wiederherstellbar.")
    ep.add_argument("id", nargs="+", help="Eintrags-IDs aus shopping list; erneutes Entfernen bleibt ohne Wirkung. "
                    "Unbekannte IDs verhindern die gesamte Änderung.")
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
