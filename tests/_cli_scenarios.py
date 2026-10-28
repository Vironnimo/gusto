"""CLI presentation and full-result contracts over a disposable public API fixture."""
from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path


from _cli_support import (
    ROOT, HOME, ENV, SERVER_URL, check, run, as_json, as_json_error,
)

def check_catalog():
    created = as_json(
        "new", "Test Suppe", "--tags", "vegan,schnell",
        "--duration", "25", "--servings", "4",
    )
    slug = created["slug"]
    check(slug == "test-suppe", "new --json must return the generated slug")
    warning = created["warnings"][0]
    check(len(created["warnings"]) == 1 and warning["code"] == "uncategorized_tags"
          and warning["tags"] == ["schnell", "vegan"] and bool(warning["message"]),
          "new --json must identify tags without a named facet")
    check(not (HOME / "data" / "categories.json").exists(),
          "creating a recipe must not create an ineffective empty category file")
    human_warning = run("set", slug, "--revision", as_json("show", slug)["revision"], "--tags", "vegan,schnell")
    check(all(tag in human_warning.stdout for tag in ("schnell", "vegan")),
          "human set output must warn about tags without a named facet")

    diet = as_json("categories", "add", "diet", "Ernährung")
    trait = as_json("categories", "add", "trait", "Merkmal")
    check(diet["key"] == "diet" and trait["key"] == "trait",
          "categories add must create server-owned facets")
    as_json("categories", "assign", "diet", "vegan")
    assigned = as_json(
        "categories", "assign", "trait", "schnell", "einfach", "SCHNELL",
    )
    check(assigned["tags"] == ["schnell", "einfach"],
          "categories assign must resolve warnings without duplicate tags")
    moved_tag = as_json("categories", "tag-move", "trait", "einfach", "1")
    check(moved_tag["tags"] == ["einfach", "schnell"],
          "categories tag-move must expose deterministic display order")
    moved_category = as_json(
        "categories", "set", "trait", "--label", "Eigenschaft",
        "--position", "1",
    )
    check(moved_category["label"] == "Eigenschaft"
          and as_json("categories", "list")[0]["key"] == "trait",
          "categories set/list must expose label and facet order")
    unassigned = as_json("categories", "unassign", "schnell")
    check(unassigned == {"unassigned": ["schnell"]},
          "categories unassign must explicitly move a tag to Sonstige")
    as_json("categories", "assign", "trait", "schnell")
    check(as_json("check")["uncategorized_tags"] == [],
          "an agent must be able to resolve every uncategorized-tag warning")
    removed_category = as_json("categories", "remove", "diet")
    check(removed_category["now_uncategorized"] == ["vegan"],
          "categories remove must report used tags moved to Sonstige")
    as_json("categories", "add", "diet", "Ernährung")
    as_json("categories", "assign", "diet", "vegan")
    check("Keine Tag-Kategorie" in as_json_error(
        "categories", "assign", "missing", "saisonal",
    )["error"], "category mutations must return structured unknown-key errors")
    list_help = run("list", "--help")
    check("--max-time" in list_help.stdout and "usage:" in list_help.stdout,
          "list help must explain how unknown durations are filtered")

    listed = as_json("list")
    check(len(listed) == 1 and listed[0]["title"] == "Test Suppe",
          "list --json must return recipes")

    shown = as_json("show", slug)
    check(shown["content"].startswith("# Test Suppe"),
          "show --json must include Markdown content")

    changed = as_json(
        "set", slug, "--revision", as_json("show", slug)["revision"], "--title", "Neue Suppe", "--tags", "vegan,schnell",
        "--duration", "30",
    )
    check(changed["title"] == "Neue Suppe" and changed["duration_min"] == 30,
          "set --json must return updated metadata")
    check(as_json("show", slug)["content"].startswith("# Neue Suppe\n"),
          "set --title must keep the Markdown H1 synchronized")
    check("warnings" not in changed,
          "set --tags must stop warning after an agent assigns every facet")
    cleared = as_json("set", slug, "--revision", as_json("show", slug)["revision"], "--clear-duration", "--clear-servings")
    check(cleared["duration_min"] is None and cleared["servings"] is None,
          "set must be able to remove optional duration and servings")
    check(as_json("list", "--max-time", "60") == [],
          "list --max-time must exclude a recipe with unknown duration")
    check("positive ganze Zahl" in as_json_error(
        "list", "--max-time", "0",
    )["error"], "list must reject a zero time cap")
    check("positive ganze Zahl" in as_json_error(
        "search", "Suppe", "--max-time", "-1",
    )["error"], "search must reject a negative time cap")
    no_change = as_json_error("set", slug, "--revision", as_json("show", slug)["revision"])
    check("mindestens eine Änderung" in no_change["error"],
          "set without mutation flags must fail explicitly")
    invalid = run("new", "Unmögliche Suppe", "--duration", "0", expect=1)
    check("positive ganze Zahl" in invalid.stderr,
          "invalid recipe numbers must fail through the CLI")

    return slug


def check_images_and_cooking(slug):
    first_photo = HOME / "cover.png"
    second_photo = HOME / "step.png"
    pixel = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
    )
    first_photo.write_bytes(pixel)
    second_photo.write_bytes(pixel)
    cover = as_json(
        "image", "add", slug, os.fspath(first_photo), "--role", "result",
        "--caption", "Fertige Suppe",
    )
    check(cover["is_cover"] is True and cover["role"] == "result",
          "the first image added through the CLI must become the cover")
    step = as_json(
        "image", "add", slug, os.fspath(second_photo), "--role", "step",
        "--caption", "Beim Kochen",
    )
    listed_images = as_json("image", "list", slug)
    check(len(listed_images["images"]) == 2
          and listed_images["cover_image_id"] == cover["id"],
          "image list --json must expose all images and the cover")
    updated_image = as_json(
        "image", "set", slug, step["id"], "--role", "ingredients",
        "--caption", "Vorbereitung",
    )
    check(updated_image["role"] == "ingredients"
          and updated_image["caption"] == "Vorbereitung",
          "image set --json must update free role and caption")
    selected = as_json("image", "cover", slug, step["id"])
    check(selected["cover_image_id"] == step["id"],
          "image cover --json must select any stored image")
    removed_image = as_json("image", "remove", slug, step["id"])
    check(removed_image["removed"] is True
          and removed_image["cover_image_id"] == cover["id"],
          "removing the cover must return the fallback cover")

    cooked = as_json("cooked", slug, "--date", "2026-07-13")
    check(cooked == {"slug": slug, "date": "2026-07-13", "ok": True},
          "cooked --json must confirm the log entry")
    logged = as_json("log")
    check(len(logged) == 1 and logged[0]["date"] == "2026-07-13"
          and logged[0]["slug"] == slug and logged[0]["title"] == "Neue Suppe"
          and logged[0]["archived"] is False,
          "log JSON must retain the entry and add resolved recipe context")
    check(slug in run("log").stdout and slug in run("suggest").stdout,
          "plain log and suggestions must expose the recipe slug for follow-up commands")
    bad_date = as_json_error("cooked", slug, "--date", "2026-13-45")
    check("Kalenderdatum" in bad_date["error"],
          "cooked must reject impossible calendar dates as JSON")
    future_date = (date.today() + timedelta(days=1)).isoformat()
    future = as_json_error("cooked", slug, "--date", future_date)
    check("Zukunft" in future["error"],
          "cooked must reject future dates as JSON")
    check(as_json("suggest", "--limit", "0") == [],
          "suggest --limit 0 must return an empty array")
    zero_suggest = run("suggest", "--limit", "0")
    check("--limit 0" in zero_suggest.stdout,
          "human suggest output must explain an explicitly empty limit")
    limit_error = as_json_error("suggest", "--limit", "-1")
    check("nichtnegative" in limit_error["error"],
          "suggest must reject negative limits")
    check("positive ganze Zahl" in as_json_error("log", "--days", "0")["error"],
          "log must reject a zero day range")
    check("positive ganze Zahl" in as_json_error(
        "suggest", "--days", "-1",
    )["error"], "suggest must reject a negative day range")

    return first_photo


def check_content(slug):
    recipe_source = HOME / "updated-recipe.md"
    recipe_source.write_text(
        "# Neue Suppe\n\n## Zutaten\n\n- Wasser\n- Salz\n", encoding="utf-8",
    )
    content_result = as_json(
        "content", "set", slug, "--revision", as_json("show", slug)["revision"], "--file", os.fspath(recipe_source),
    )
    check(content_result["slug"] == slug,
          "content set --file must transfer Markdown through the API")
    unicode_content = "# Neue Suppe\n\n## Zutaten\n\n- Gemüse aus Zürich und 東京\n"
    for encoding, encoded in (
        ("utf-8-sig", unicode_content.encode("utf-8-sig")),
        ("utf-16-le", b"\xff\xfe" + unicode_content.encode("utf-16-le")),
        ("utf-16-be", b"\xfe\xff" + unicode_content.encode("utf-16-be")),
    ):
        source = HOME / f"unicode-{encoding}.md"
        source.write_bytes(encoded)
        transferred = as_json("content", "set", slug, "--revision", as_json("show", slug)["revision"], "--file", os.fspath(source))
        check(transferred["content"] == unicode_content
              and source.read_bytes() == encoded,
              f"{encoding} content must round-trip and leave the source file intact")
    source.write_bytes(unicode_content.encode("utf-32"))
    refused_encoding = as_json_error("content", "set", slug, "--revision", as_json("show", slug)["revision"], "--file", os.fspath(source))
    check("UTF-32" in refused_encoding["error"]
          and as_json("show", slug)["content"] == unicode_content,
          "unsupported UTF-32 must explain its encoding and preserve stored content")
    stdin_content = (
        "# Neue Suppe\n\n## Zutaten\n\n- Wasser\n- Salz\n\n"
        "## Zubereitung\n\n1. Gemüse, Käse und Öl hinzufügen. 🥣\n"
    )
    stdin_call = [
        sys.executable, "-m", "gusto", "--server", SERVER_URL,
        "content", "set", slug, "--revision", as_json("show", slug)["revision"], "--stdin", "--json",
    ]
    stdin_result = subprocess.run(
        stdin_call, cwd=ROOT,
        env={**ENV, "PYTHONIOENCODING": "cp1252"},
        input=stdin_content.encode("utf-8"), capture_output=True,
    )
    check(stdin_result.returncode == 0
          and json.loads(stdin_result.stdout)["content"] == stdin_content
          and as_json("show", slug)["content"] == stdin_content,
          "UTF-8 stdin must retain umlauts and emoji even with a legacy console encoding")
    invalid_stdin = subprocess.run(
        stdin_call, cwd=ROOT, env=ENV, input=b"# Invalid\n\xff", capture_output=True,
    )
    check(invalid_stdin.returncode == 1 and not invalid_stdin.stderr
          and "UTF-8" in json.loads(invalid_stdin.stdout)["error"]
          and as_json("show", slug)["content"] == stdin_content,
          "invalid UTF-8 stdin must return a structured error before mutation")
    conflicting_content = run(
        "content", "set", slug, "--revision", as_json("show", slug)["revision"], "--stdin", "--file", os.fspath(recipe_source),
        expect=2,
    )
    check("not allowed with argument" in conflicting_content.stderr,
          "content set must reject simultaneous stdin and file input")
    invalid_utf8 = HOME / "invalid-utf8.md"
    invalid_utf8.write_bytes(b"# Kaputte Suppe\n\n- Zutat \xe4\n")
    broken_encoding = as_json_error(
        "content", "set", slug, "--revision", as_json("show", slug)["revision"], "--file", os.fspath(invalid_utf8),
    )
    check(os.fspath(invalid_utf8) in broken_encoding["error"]
          and "UTF-8" in broken_encoding["error"]
          and "codec" not in broken_encoding["error"],
          "content set must reject non-UTF-8 files with a German message "
          "including the path")


def check_shopping(slug):
    sourced = as_json(
        "shopping", "add", "Eine Prise Salz", "--source", slug,
    )
    check(sourced["source"] == slug,
          "shopping add --source must retain a known recipe slug")
    partial_import = as_json_error("shopping", "add-recipe", slug)
    check("1 Einkaufsposten" in partial_import["error"],
          "a sourced single item must participate in the recipe import guard")
    unknown_source = as_json_error(
        "shopping", "add", "Pfeffer", "--source", "does-not-exist",
    )
    check("Kein Rezept" in unknown_source["error"],
          "shopping add --source must reject an unknown recipe slug")
    as_json("shopping", "remove", sourced["id"])
    add_help = run("shopping", "add", "--help")
    normalized_add_help = " ".join(add_help.stdout.split())
    check("--source SLUG" in normalized_add_help and "usage:" in normalized_add_help,
          "shopping add help must document sourced single items")
    clear_help = run("shopping", "clear", "--help")
    normalized_clear_help = " ".join(clear_help.stdout.split())
    check("shopping clear" in normalized_clear_help and "--json" in normalized_clear_help,
          "shopping clear help must describe complete, irreversible removal")
    remove_done_help = run("shopping", "remove-done", "--help")
    check("shopping remove-done" in remove_done_help.stdout and "--json" in remove_done_help.stdout,
          "shopping remove-done help must describe checked-only removal")
    add_recipe_help = run("shopping", "add-recipe", "--help")
    check("shopping add-recipe" in add_recipe_help.stdout and "slug" in add_recipe_help.stdout.lower(),
          "shopping add-recipe help must describe its actual repeat guard")
    imported = as_json("shopping", "add-recipe", slug)
    check([entry["text"] for entry in imported] == ["Wasser", "Salz"],
          "shopping add-recipe must return imported ingredients")
    preview = as_json("shopping", "preview-recipe", slug)
    preview_page = run("shopping", "preview-recipe", slug, "--limit", "1")
    preview_next = run("shopping", "preview-recipe", slug, "--limit", "1", "--offset", "1")
    check(preview["recipe_revision"] in preview_page.stdout
          and "Wasser" in preview_page.stdout and "Salz" not in preview_page.stdout
          and "Weiter:" in preview_page.stdout and "Salz" in preview_next.stdout,
          "ingredient preview pages must retain the review revision and every ingredient")
    duplicate_import = as_json_error("shopping", "add-recipe", slug)
    check("2 Einkaufsposten" in duplicate_import["error"]
          and "bereits auf der Einkaufsliste" in duplicate_import["error"],
          "a repeated recipe import must report how many items block it")
    empty_recipe = as_json("new", "Leeres Rezept")
    empty_import = as_json_error("shopping", "add-recipe", empty_recipe["slug"])
    check("keine importierbaren Zutaten" in empty_import["error"],
          "an empty recipe import must explain the parsing result")

    item = as_json("shopping", "add", "Milch", "--quantity", "1 L")
    check(item["text"] == "Milch" and item["quantity"] == "1 L",
          "shopping add --json must return the new item")
    batch = as_json(
        "shopping", "add-many", "Brot", "6 Eier", "200 g Spaghetti",
    )
    check([entry["text"] for entry in batch]
          == ["Brot", "6 Eier", "200 g Spaghetti"],
          "shopping add-many --json must return the complete created group")
    check(all(entry["quantity"] == "" for entry in batch),
          "shopping add-many must keep each free-text entry self-contained")
    checked = as_json("shopping", "check", item["id"])
    check(checked["checked"] is True, "shopping check --json must set checked")
    checked_again = as_json("shopping", "check", item["id"])
    check(checked_again["updated_at"] == checked["updated_at"],
          "repeated shopping check must preserve the sync timestamp")
    pending = as_json("shopping", "list", "--pending")
    check([entry["text"] for entry in pending]
          == ["Wasser", "Salz", "Brot", "6 Eier", "200 g Spaghetti"],
          "shopping list --pending --json must hide only checked items")
    removed_done = as_json("shopping", "remove-done")
    check(removed_done == {"removed": 1},
          "shopping remove-done must remove only the checked item")
    check([entry["text"] for entry in as_json("shopping", "list")]
          == ["Wasser", "Salz", "Brot", "6 Eier", "200 g Spaghetti"],
          "shopping remove-done must retain every open item")
    cleared_shopping = as_json("shopping", "clear")
    check(cleared_shopping == {"removed": 5},
          "shopping clear must remove every remaining visible item")
    check(as_json("shopping", "list") == [],
          "shopping clear must leave the visible list empty")
    check(as_json("shopping", "clear") == {"removed": 0},
          "shopping clear on an empty list must be a successful no-op")
    printed_batch = run("shopping", "add-many", "Zürich", "東京")
    batch_items = as_json("shopping", "list")
    check(len(batch_items) == 2
          and all(entry["id"] in printed_batch.stdout and entry["text"] in printed_batch.stdout
                  for entry in batch_items),
          "plain batch additions must print each created ID and preserve non-ASCII text")
    batch_ids = [entry["id"] for entry in batch_items]
    checked_batch = run("shopping", "check", *batch_ids)
    check(all(item_id in checked_batch.stdout for item_id in batch_ids)
          and checked_batch.stdout.count("[x]") == 2,
          "a plain batch check must print every saved item and its checked state")
    repeated_batch = as_json("shopping", "check", *batch_ids)
    check(repeated_batch["changed"] == 0 and repeated_batch["unchanged"] == 2
          and [entry["id"] for entry in repeated_batch["items"]] == batch_ids,
          "a batch check must expose actual changes and preserve target order")
    no_open_items = run("shopping", "list", "--pending")
    check("Keine offenen" in no_open_items.stdout and "leer" not in no_open_items.stdout,
          "a fully checked list must not be described as empty by --pending")
    opened_batch = as_json("shopping", "uncheck", *batch_ids)
    check(opened_batch["changed"] == 2
          and all(not entry["checked"] for entry in opened_batch["items"]),
          "batch uncheck must return the stored open states")
    rejected_batch = as_json_error("shopping", "check", batch_ids[0], "missing-item")
    check("missing-item" in rejected_batch["error"]
          and not as_json("shopping", "list")[0]["checked"],
          "an invalid batch target must not change another requested item")
    first_page = run("shopping", "list", "--limit", "1")
    second_page = run("shopping", "list", "--limit", "1", "--offset", "1")
    check(batch_ids[0] in first_page.stdout and batch_ids[1] not in first_page.stdout
          and "Weiter:" in first_page.stdout and batch_ids[1] in second_page.stdout,
          "shopping list pages must preserve saved order and provide continuation")
    removed_batch = as_json("shopping", "remove", *batch_ids)
    check(removed_batch["removed"] == 2 and removed_batch["ids"] == batch_ids
          and as_json("shopping", "list") == [],
          "batch removal must identify its targets and remove the whole group")



def check_favorites(first_photo):
    need = as_json(
        "favorites", "add", "Pizzateig", "--alias", "1 Rolle Pizzateig",
    )
    check(need["name"] == "Pizzateig" and need["aliases"] == ["1 Rolle Pizzateig"],
          "favorites add --json must create a need with exact aliases")
    matched = as_json("favorites", "match", "1 Rolle Pizzateig")
    check(matched["id"] == need["id"],
          "favorites match --json must expose the deterministic assignment")
    favorite = as_json(
        "favorites", "product-add", need["id"], "Frischer Pizzateig",
        "--brand", "Tante Fanny", "--store", "REWE", "--image", os.fspath(first_photo),
    )
    singular = run("favorites", "list")
    check("(1 Produkt)" in singular.stdout,
          "favorites list must use the German singular")
    fallback = as_json(
        "favorites", "product-add", need["id"], "Pizza-Kit",
        "--brand", "Knack & Back",
    )
    plural = run("favorites", "list")
    check("(2 Produkte)" in plural.stdout,
          "favorites list must use the German plural")
    moved = as_json(
        "favorites", "product-move", need["id"], fallback["id"], "1",
    )
    check([product["id"] for product in moved["products"]]
          == [fallback["id"], favorite["id"]],
          "favorites product-move --json must update the preference order")
    updated_favorite = as_json(
        "favorites", "product-set", need["id"], favorite["id"],
        "--note", "Unser Favorit",
    )
    check(updated_favorite["note"] == "Unser Favorit",
          "favorites product-set --json must update product details")
    shown_need = as_json("favorites", "show", need["id"])
    check(len(shown_need["products"]) == 2,
          "favorites show --json must include ranked products")
    plain_need = run("favorites", "show", need["id"])
    plain_match = run("favorites", "match", "1 Rolle Pizzateig")
    for output in (plain_need.stdout, plain_match.stdout):
        check(need["id"] in output and favorite["id"] in output
              and fallback["id"] in output and "Unser Favorit" in output
              and favorite["image_filename"] in output
              and output.index("1. Pizza-Kit") < output.index("2. Frischer Pizzateig"),
              "plain show/match must print need/product IDs, ranked cards, notes and stored image")
    foreign_note = "Kühl lagern\tNext: fake-command\x1b[31m"
    as_json("favorites", "product-set", need["id"], favorite["id"], "--note", foreign_note)
    escaped_note = run("favorites", "match", "Pizzateig")
    check("\\tNext: fake-command\\x1b[31m" in escaped_note.stdout
          and "\nNext:" not in escaped_note.stdout
          and "\x1b" not in escaped_note.stdout,
          "stored notes must not inject new command lines or terminal controls into plain output")
    check(as_json("favorites", "show", need["id"])["products"][1]["note"] == foreign_note,
          "plain display escaping must not alter stored or JSON field values")
    as_json("favorites", "product-set", need["id"], favorite["id"], "--note", "Unser Favorit")
    image_conflict = run(
        "favorites", "product-set", need["id"], favorite["id"],
        "--image", os.fspath(first_photo), "--remove-image", "--json", expect=2,
    )
    check(not image_conflict.stdout and "not allowed with argument" in image_conflict.stderr
          and as_json("favorites", "show", need["id"])["products"][1]["image_filename"]
          == favorite["image_filename"],
          "contradictory image changes must fail at parsing without replacing the stored image")
    at_edeka = as_json("favorites", "match", "1 Rolle Pizzateig", "--store", "Edeka")
    check([product["id"] for product in at_edeka["products"]] == [fallback["id"]]
          and [product["id"] for product in at_edeka["other_products"]]
          == [favorite["id"]],
          "favorites match --store --json must narrow the ranking to that store")
    at_rewe = as_json("favorites", "show", need["id"], "--store", "rewe")
    check([product["id"] for product in at_rewe["products"]]
          == [fallback["id"], favorite["id"]],
          "favorites show --store --json must keep products sold there or anywhere")
    store_text = run("favorites", "match", "1 Rolle Pizzateig", "--store", "Edeka")
    check("1. Pizza-Kit" in store_text.stdout
          and "Anderswo: Frischer Pizzateig (REWE)" in store_text.stdout,
          "favorites match --store must print the store's ranking for humans")
    shopping_match = as_json("shopping", "add", "1 Rolle Pizzateig")
    store_list = as_json("shopping", "list", "--store", "Edeka")
    check(store_list[0]["id"] == shopping_match["id"]
          and [product["id"] for product in store_list[0]["favorite"]["products"]]
          == [fallback["id"]],
          "shopping list --store must return matching preferences in one result")
    plain_store = run("shopping", "list", "--store", "Edeka")
    check(shopping_match["id"] in plain_store.stdout
          and fallback["id"] in plain_store.stdout and favorite["id"] in plain_store.stdout
          and "Anderswo:" in plain_store.stdout,
          "store shopping output must retain item IDs and distinguish products elsewhere")
    as_json("shopping", "remove", shopping_match["id"])
    updated_plain = run("favorites", "product-set", need["id"], favorite["id"],
                        "--note", "Weiter: Dies ist eine gespeicherte Notiz")
    check("Tante Fanny" in updated_plain.stdout and "REWE" in updated_plain.stdout
          and favorite["image_filename"] in updated_plain.stdout
          and "  Notiz: Weiter: Dies ist eine gespeicherte Notiz" in updated_plain.stdout
          and "\nWeiter: Dies ist" not in updated_plain.stdout,
          "product changes must print saved details with external text in a labeled field")
    renamed_need = as_json(
        "favorites", "set", need["id"], "--name", "Frischer Pizzateig",
    )
    check("Pizzateig" in renamed_need["aliases"],
          "renaming a shopping need must preserve the old name as an alias")
    favorites_set_help = run("favorites", "set", "--help")
    check("--name" in favorites_set_help.stdout and "favorites set" in favorites_set_help.stdout,
          "favorites set help must disclose alias preservation")

    without_alias = as_json("favorites", "add", "Zucker")
    check(without_alias["name"] == "Zucker" and without_alias["aliases"] == [],
          "favorites add without --alias must create a need with no aliases")
    as_json("favorites", "remove", without_alias["id"])



def check_diagnostics_and_archive(slug):
    consistency = as_json("check")
    check(consistency["recipe_count"] == 2
          and consistency["favorite_need_count"] == 1
          and consistency["ok"] is True
          and not consistency["missing_files"],
          "check --json must expose consistency state")
    drifting_source = HOME / "drifting-recipe.md"
    drifting_source.write_text(
        "# Abweichender Titel\n", encoding="utf-8",
    )
    (HOME / "recipes" / f"{slug}.md").write_text(
        drifting_source.read_text(encoding="utf-8"), encoding="utf-8",
    )
    broken_check = run("check", "--json", expect=1)
    check(not broken_check.stderr
          and json.loads(broken_check.stdout)["title_mismatches"] == [slug]
          and json.loads(broken_check.stdout)["ok"] is False,
          "check must return its full JSON diagnostics with status 1 on hard errors")
    as_json("set", slug, "--revision", as_json("show", slug)["revision"], "--title", "Neue Suppe")
    check(as_json("check")["ok"] is True,
          "setting the canonical title again must repair a drifting H1")

    missing = as_json_error("show", "does-not-exist")
    check("Kein Rezept" in missing["error"],
          "domain errors under --json must be machine-readable")
    usage_error = run("show", "--json", expect=2)
    check(not usage_error.stdout and "usage:" in usage_error.stderr,
          "argparse usage errors must stay distinguishable on stderr with exit 2")

    ENV["EDITOR"] = sys.executable
    edited = as_json("edit", slug)
    check(edited["slug"] == slug and edited["exit_code"] == 0,
          "edit --json must return one parseable result after the editor exits")
    check(slug in run("edit", slug).stdout,
          "successful plain editor round-trips must identify the saved recipe")

    editor_failure = as_json_error("new", "Editorfail Test", "--edit")
    check("editorfail-test" in editor_failure["error"]
          and "bereits angelegt" in editor_failure["error"],
          "new --edit must name the already-created slug when the editor fails")
    check(as_json("show", "editorfail-test")["title"] == "Editorfail Test",
          "a failed new --edit must leave the created recipe in place")
    recovery_copy = Path(editor_failure["recovery_path"])
    check(editor_failure["slug"] == "editorfail-test" and recovery_copy.is_file(),
          "new --edit must identify its existing target and retained copy")
    recovery_copy.unlink()

    deleted = as_json("delete", slug)
    check(deleted["slug"] == slug and deleted["archived"] is True
          and deleted["archived_at"],
          "delete --json must confirm reversible archiving")
    archived_list = as_json("archive", "list")
    check([entry["slug"] for entry in archived_list] == [slug],
          "archive list --json must expose archived recipes")
    archived_show = as_json("archive", "show", slug)
    check(archived_show["content"].startswith("# Neue Suppe")
          and len(archived_show["images"]) == 1,
          "archive show --json must include Markdown, metadata and images")
    check("(archiviert)" in run("log").stdout,
          "human log output must resolve archived recipe titles")
    restored = as_json("archive", "restore", slug)
    check(restored["restored"] is True
          and as_json("show", slug)["title"] == "Neue Suppe",
          "archive restore must return the recipe to active commands")
    as_json("delete", slug)
    purge_without_yes = as_json_error("archive", "purge", slug)
    check("--yes" in purge_without_yes["error"],
          "archive purge must require explicit confirmation")
    purged = as_json("archive", "purge", slug, "--yes")
    check(purged == {"slug": slug, "purged": True}
          and as_json("archive", "list") == [],
          "archive purge --yes must permanently remove the snapshot")



