"""Hermetic smoke checks for the agent-facing JSON CLI."""
from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
HOME = Path(tempfile.mkdtemp(prefix="gusto-cli-test-"))
ENV = {**os.environ, "GUSTO_HOME": os.fspath(HOME)}
checks = 0


def check(condition, message):
    global checks
    assert condition, message
    checks += 1


def run(*arguments, expect=0):
    result = subprocess.run(
        [sys.executable, "-m", "gusto", *arguments], cwd=ROOT, env=ENV,
        text=True, encoding="utf-8", capture_output=True,
    )
    check(result.returncode == expect,
          f"{' '.join(arguments)} returned {result.returncode}: {result.stderr}")
    return result


def as_json(*arguments):
    return json.loads(run(*arguments, "--json").stdout)


def as_json_error(*arguments):
    result = run(*arguments, "--json", expect=1)
    check(result.stderr == "", "JSON command errors must not emit plain stderr text")
    payload = json.loads(result.stdout)
    check(payload.get("ok") is False and isinstance(payload.get("error"), str),
          "JSON command errors must use the documented error object")
    return payload


def main():
    home = as_json("home")
    check(Path(home["path"]) == HOME and home["source"] == "environment",
          "home --json must explain the active GUSTO_HOME data directory")

    created = as_json(
        "new", "Test Suppe", "--tags", "vegan,schnell",
        "--duration", "25", "--servings", "4",
    )
    slug = created["slug"]
    check(slug == "test-suppe", "new --json must return the generated slug")
    check(created["warnings"] == [{
        "code": "uncategorized_tags",
        "message": "Tags ohne Kategorie (Facet „Sonstige“): schnell, vegan",
        "tags": ["schnell", "vegan"],
    }], "new --json must identify tags without a named facet")
    check(not (HOME / "data" / "categories.json").exists(),
          "creating a recipe must not create an ineffective empty category file")
    human_warning = run("set", slug, "--tags", "vegan,schnell")
    check("Warnung: Tags ohne Kategorie" in human_warning.stdout,
          "human set output must warn about tags without a named facet")
    list_help = run("list", "--help")
    check("ohne Dauerangabe werden ausgeschlossen"
          in " ".join(list_help.stdout.split()),
          "list help must explain how unknown durations are filtered")

    listed = as_json("list")
    check(len(listed) == 1 and listed[0]["title"] == "Test Suppe",
          "list --json must return recipes")

    shown = as_json("show", slug)
    check(shown["content"].startswith("# Test Suppe"),
          "show --json must include Markdown content")

    changed = as_json(
        "set", slug, "--title", "Neue Suppe", "--tags", "vegan,schnell",
        "--duration", "30",
    )
    check(changed["title"] == "Neue Suppe" and changed["duration_min"] == 30,
          "set --json must return updated metadata")
    check(changed["warnings"][0]["code"] == "uncategorized_tags",
          "set --tags --json must identify tags without a named facet")
    cleared = as_json("set", slug, "--clear-duration", "--clear-servings")
    check(cleared["duration_min"] is None and cleared["servings"] is None,
          "set must be able to remove optional duration and servings")
    check(as_json("list", "--max-time", "60") == [],
          "list --max-time must exclude a recipe with unknown duration")
    no_change = as_json_error("set", slug)
    check("mindestens eine Änderung" in no_change["error"],
          "set without mutation flags must fail explicitly")
    invalid = run("new", "Unmögliche Suppe", "--duration", "0", expect=1)
    check("positive ganze Zahl" in invalid.stderr,
          "invalid recipe numbers must fail through the CLI")

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
    check(as_json("log") == [{"date": "2026-07-13", "slug": slug}],
          "log --json must return the written entry")
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

    (HOME / "recipes" / f"{slug}.md").write_text(
        "# Neue Suppe\n\n## Zutaten\n\n- Wasser\n- Salz\n", encoding="utf-8",
    )
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
    check("--source SLUG" in normalized_add_help
          and "Herkunftsrezept" in normalized_add_help,
          "shopping add help must document sourced single items")
    clear_help = run("shopping", "clear", "--help")
    normalized_clear_help = " ".join(clear_help.stdout.split())
    check("gesamte Einkaufsliste" in normalized_clear_help
          and "offene und erledigte" in normalized_clear_help
          and "nicht per CLI wiederherstellbar" in normalized_clear_help,
          "shopping clear help must describe complete, irreversible removal")
    remove_done_help = run("shopping", "remove-done", "--help")
    check("abgehakten Eintraege"
          in " ".join(remove_done_help.stdout.split()),
          "shopping remove-done help must describe checked-only removal")
    imported = as_json("shopping", "add-recipe", slug)
    check([entry["text"] for entry in imported] == ["Wasser", "Salz"],
          "shopping add-recipe must return imported ingredients")
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

    consistency = as_json("check")
    check(consistency["recipe_count"] == 2
          and consistency["favorite_need_count"] == 1
          and not consistency["missing_files"],
          "check --json must expose consistency state")

    missing = as_json_error("show", "does-not-exist")
    check("Kein Rezept" in missing["error"],
          "domain errors under --json must be machine-readable")
    usage_error = run("show", "--json", expect=2)
    check(not usage_error.stdout and "usage:" in usage_error.stderr,
          "argparse usage errors must stay distinguishable on stderr with exit 2")

    deleted = as_json("delete", slug)
    check(deleted == {"slug": slug, "deleted": True},
          "delete --json must confirm deletion")

    refused_uninstall = as_json_error("uninstall", "--keep-data")
    check("Projekt-Checkout" in refused_uninstall["error"],
          "uninstall must refuse to delete a development checkout")

    print(f"OK - {checks} CLI checks passed (GUSTO_HOME={HOME})")


if __name__ == "__main__":
    main()
