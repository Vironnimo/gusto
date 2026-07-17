"""Hermetic smoke checks for the agent-facing JSON CLI."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import base64
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


def main():
    created = as_json(
        "new", "Test Suppe", "--tags", "vegan,schnell",
        "--duration", "25", "--servings", "4",
    )
    slug = created["slug"]
    check(slug == "test-suppe", "new --json must return the generated slug")

    listed = as_json("list")
    check(len(listed) == 1 and listed[0]["title"] == "Test Suppe",
          "list --json must return recipes")

    shown = as_json("show", slug)
    check(shown["content"].startswith("# Test Suppe"),
          "show --json must include Markdown content")

    changed = as_json("set", slug, "--title", "Neue Suppe", "--duration", "30")
    check(changed["title"] == "Neue Suppe" and changed["duration_min"] == 30,
          "set --json must return updated metadata")
    cleared = as_json("set", slug, "--clear-duration", "--clear-servings")
    check(cleared["duration_min"] is None and cleared["servings"] is None,
          "set must be able to remove optional duration and servings")
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

    item = as_json("shopping", "add", "Milch", "--quantity", "1 L")
    check(item["text"] == "Milch" and item["quantity"] == "1 L",
          "shopping add --json must return the new item")
    checked = as_json("shopping", "check", item["id"])
    check(checked["checked"] is True, "shopping check --json must set checked")
    check(as_json("shopping", "list", "--pending") == [],
          "shopping list --pending --json must hide checked items")

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
    fallback = as_json(
        "favorites", "product-add", need["id"], "Pizza-Kit",
        "--brand", "Knack & Back",
    )
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
    check(consistency["recipe_count"] == 1
          and consistency["favorite_need_count"] == 1
          and not consistency["missing_files"],
          "check --json must expose consistency state")

    missing = run("show", "does-not-exist", "--json", expect=1)
    check("Kein Rezept" in missing.stderr, "CLI errors must be written to stderr")

    deleted = as_json("delete", slug)
    check(deleted == {"slug": slug, "deleted": True},
          "delete --json must confirm deletion")

    print(f"OK - {checks} CLI checks passed (GUSTO_HOME={HOME})")


if __name__ == "__main__":
    main()
