"""Hermetic checks for shopping needs and ranked preferred products."""
from __future__ import annotations

import base64
import os
import sys
import tempfile
from pathlib import Path


HOME = Path(tempfile.mkdtemp(prefix="gusto-favorites-test-"))
os.environ["GUSTO_HOME"] = os.fspath(HOME)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from gusto import core  # noqa: E402


checks = 0


def check(condition, message):
    global checks
    assert condition, message
    checks += 1


def expect_valueerror(function, *args, **kwargs):
    try:
        function(*args, **kwargs)
    except ValueError:
        return
    raise AssertionError(f"expected ValueError from {function.__name__}")


def main():
    check(core.favorites_load() == [], "a fresh catalog must be empty")

    need = core.favorite_add_need(
        "Pizzateig", aliases=["1 Rolle Pizzateig", "  Pizzateig  "],
    )
    check(need.aliases == ["1 Rolle Pizzateig"],
          "canonical-name duplicates must not be stored as aliases")
    check(core.favorite_match("  PIZZATEIG  ").id == need.id,
          "matching must ignore case and surrounding whitespace")
    check(core.favorite_match("1   Rolle Pizzateig").id == need.id,
          "matching must collapse whitespace")
    check(core.normalize_shopping_text("WEISSE  BOHNEN")
          == core.normalize_shopping_text("weiße Bohnen"),
          "server matching must normalize German sharp-s like the browser")
    check(core.favorite_match("400 g Pizzateig") is None,
          "matching must not guess quantities or substrings")
    expect_valueerror(core.favorite_add_need, "1 Rolle Pizzateig")

    core.favorite_add_alias(need.id, "Frischer Pizzateig")
    unchanged = core.favorite_add_alias(need.id, "  FRISCHER   PIZZATEIG ")
    check(unchanged.aliases == ["1 Rolle Pizzateig", "Frischer Pizzateig"],
          "repeating an alias on the same need must be an idempotent retry")
    expect_valueerror(core.favorite_add_need, "Anderer Teig", ["Frischer Pizzateig"])
    expect_valueerror(core.favorite_add_need, "Pizzateig")
    renamed = core.favorite_update_need(need.id, "Pizza-Fertigteig")
    check("Pizzateig" in renamed.aliases and core.favorite_match("Pizzateig").id == need.id,
          "renaming must retain the old canonical name as an alias")

    pixel = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
    )
    first_image = HOME / "first.png"
    replacement_image = HOME / "replacement.png"
    first_image.write_bytes(pixel)
    replacement_image.write_bytes(pixel)

    expect_valueerror(core.favorite_add_product, need.id, "Produkt ohne Marke")

    first = core.favorite_add_product(
        need.id, "Frischer Pizzateig 400 g", brand="Tante Fanny",
        store="REWE", note="Wird besonders knusprig", image=first_image,
    )
    second = core.favorite_add_product(need.id, "Pizza-Kit", brand="Knack & Back")
    check(core.favorite_image_path(first.image_filename).is_file(),
          "product images must be copied into Gusto storage")
    check([product.id for product in core.favorite_get_need(need.id).products]
          == [first.id, second.id], "insertion order must be the preference order")

    moved = core.favorite_move_product(need.id, second.id, 1)
    check([product.id for product in moved.products] == [second.id, first.id],
          "products must move to an explicit one-based rank")
    expect_valueerror(core.favorite_move_product, need.id, first.id, 3)

    old_filename = first.image_filename
    updated = core.favorite_update_product(
        need.id, first.id, note="Unser Favorit", image=replacement_image,
    )
    check(updated.note == "Unser Favorit" and updated.image_filename != old_filename,
          "product details and images must be replaceable")
    check(not core.favorite_image_path(old_filename).exists()
          and core.favorite_image_path(updated.image_filename).is_file(),
          "replacing an image must remove the old owned file")

    cleared = core.favorite_update_product(need.id, first.id, remove_image=True)
    check(not cleared.image_filename,
          "an existing product image must be removable")
    check(not core.favorite_image_path(updated.image_filename).exists(),
          "removing image metadata must remove the owned file")

    consistent = core.check()
    check(not consistent["duplicate_favorite_aliases"]
          and not consistent["missing_favorite_image_files"]
          and not consistent["orphaned_favorite_image_files"],
          "a catalog written through core must be consistent")

    remaining = core.favorite_remove_product(need.id, second.id)
    check([product.id for product in remaining.products] == [first.id],
          "removing a product must preserve the remaining ranking")
    expect_valueerror(core.favorite_remove_product, need.id, second.id)
    removed = core.favorite_remove_need(need.id)
    check(removed.id == need.id and core.favorites_load() == [],
          "removing a need must remove its whole catalog entry")

    print(f"OK - {checks} favorite-product checks passed (GUSTO_HOME={HOME})")


if __name__ == "__main__":
    main()
