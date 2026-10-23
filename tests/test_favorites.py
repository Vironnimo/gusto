"""Hermetic checks for shopping needs and ranked preferred products."""
from __future__ import annotations

import base64
import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch


sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import gusto  # noqa: E402, F401 - configure checkout temp before allocating data

HOME = Path(tempfile.mkdtemp(prefix="gusto-favorites-test-"))
os.environ["GUSTO_HOME"] = os.fspath(HOME)

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


def check_image_import_failures(source):
    """Partial copies and failed index writes must not silently orphan images."""
    recipe = core.add_recipe("Bildimport-Fehler")
    need = core.favorite_add_need("Bildimport-Fehler")
    product = core.favorite_add_product(need.id, "Alt", brand="Marke", image=source)
    scenarios = (
        ("recipe", lambda: core.add_recipe_image(recipe.slug, source),
         core.recipe_images_dir(recipe.slug), "_save_recipes_unlocked",
         lambda: core.get(recipe.slug).to_dict()),
        ("favorite-add", lambda: core.favorite_add_product(
            need.id, "Neu", brand="Marke", image=source),
         core.favorite_images_dir(), "_favorites_save_unlocked",
         lambda: core.favorite_get_need(need.id).to_dict()),
        ("favorite-replace", lambda: core.favorite_update_product(
            need.id, product.id, image=source),
         core.favorite_images_dir(), "_favorites_save_unlocked",
         lambda: core.favorite_get_need(need.id).to_dict()),
    )

    def partial_copy(_source, destination, *args, **kwargs):
        Path(destination).write_bytes(b"partial image")
        raise OSError("copy interrupted")

    original_unlink = Path.unlink
    for name, import_image, folder, save_name, snapshot in scenarios:
        for failed_step in ("copy", "save"):
            for cleanup_fails in (False, True):
                before_files = set(folder.iterdir()) if folder.exists() else set()
                before_state = snapshot()
                revision = core.change_state()["revision"]

                def reject_cleanup(path, *args, **kwargs):
                    if path.parent == folder and path not in before_files:
                        raise PermissionError("copied image busy")
                    return original_unlink(path, *args, **kwargs)

                failure = (
                    patch.object(core.shutil, "copy2", partial_copy)
                    if failed_step == "copy"
                    else patch.object(core, save_name, side_effect=OSError("index denied"))
                )
                with failure, patch.object(
                    Path, "unlink", reject_cleanup if cleanup_fails else original_unlink,
                ):
                    try:
                        import_image()
                    except Exception as error:
                        check(isinstance(error, core.PostMutationError)
                              if cleanup_fails else isinstance(error, OSError),
                              f"{name}/{failed_step}: report uncertainty only if cleanup failed")
                    else:
                        raise AssertionError(f"{name}/{failed_step}: injected failure ignored")
                leftovers = set(folder.iterdir()) - before_files
                check(snapshot() == before_state,
                      f"{name}/{failed_step}: a rejected import must preserve existing metadata")
                check(len(leftovers) == (1 if cleanup_fails else 0)
                      and core.change_state()["revision"] == revision + int(cleanup_fails),
                      f"{name}/{failed_step}: only retained partial copies may publish a change")
                for leftover in leftovers:
                    leftover.unlink()

        # The JSON save itself may succeed before a later cleanup raises.
        # Removing the copy then would break the already committed reference.
        before_files = set(folder.iterdir())
        revision = core.change_state()["revision"]
        original_save = getattr(core, save_name)

        def save_then_fail(records):
            original_save(records)
            raise OSError("failure after index commit")

        with patch.object(core, save_name, save_then_fail):
            try:
                import_image()
            except core.PostMutationError:
                pass
            else:
                raise AssertionError(f"{name}: post-commit failure must remain uncertain")
        copied = set(folder.iterdir()) - before_files
        state = snapshot()
        referenced = ({image["filename"] for image in state["images"]}
                      if name == "recipe" else
                      {item["image_filename"] for item in state["products"]})
        check(len(copied) == 1 and next(iter(copied)).name in referenced
              and core.change_state()["revision"] == revision + 1,
              f"{name}: a post-commit error must preserve the stored image reference")


def main():
    check(core.favorites_load() == [], "a fresh catalog must be empty")

    # Corrupted favorites data must raise a German ValueError and check() must
    # report it as a hard error instead of crashing.
    core.favorites_path().parent.mkdir(parents=True, exist_ok=True)
    core.favorites_path().write_text("[]", encoding="utf-8")
    expect_valueerror(core.favorites_load)
    corrupted = core.check()
    check(any("favorites.json" in message
              for message in corrupted["invalid_data_files"]),
          "corrupted favorites data must be reported by the integrity check")
    check(corrupted["ok"] is False,
          "corrupted favorites data must be a hard error")
    for broken_need in (
        {"name": "Milch"},
        {"id": "x", "name": "Milch", "aliases": None},
        {"id": "x", "name": "Milch", "aliases": [5]},
        {"id": "x", "name": "Milch", "products": None},
        {"id": "x", "name": "Milch", "products": [None]},
        {"id": "x", "name": "Milch", "products": [{"name": "Produkt"}]},
    ):
        core.favorites_path().write_text(
            json.dumps({"needs": [broken_need]}), encoding="utf-8")
        expect_valueerror(core.favorites_load)
        corrupted = core.check()
        check(corrupted["ok"] is False
              and any("favorites.json" in message
                      for message in corrupted["invalid_data_files"]),
              "broken favorite entries must return a named diagnostic instead of crashing")
    core.favorites_path().write_text(json.dumps({"needs": [{
        "id": "legacy", "name": "Milch",
        "products": [{"id": "legacy-product", "name": "Produkt"}],
    }]}), encoding="utf-8")
    legacy = core.favorites_load()[0]
    check(legacy.aliases == [] and legacy.created_at == ""
          and legacy.products[0].brand == "" and legacy.products[0].image_filename == "",
          "omitted optional fields in older favorites must keep their defaults")
    os.remove(core.favorites_path())
    check(core.favorites_load() == [],
          "a removed favorites file must load as an empty catalog")

    for alias in ("Hafer\ndrink", "Hafer\rdrink"):
        revision = core.change_state()["revision"]
        expect_valueerror(core.favorite_add_need, "Hafermilch", ["Hafer", alias])
        check(core.favorites_load() == []
              and core.change_state()["revision"] == revision,
              "a multiline initial alias must reject the whole need before persistence")

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
    for invalid_position in (True, "1", 1.5, 0):
        expect_valueerror(
            core.favorite_move_product, need.id, first.id, invalid_position,
        )

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

    everywhere = core.favorite_add_product(need.id, "Hausmarke", brand="Ja!")
    several = core.favorite_add_product(
        need.id, "Bio-Teig", brand="Alnatura", store=" Edeka ,  dm ",
    )
    ranking = core.favorite_get_need(need.id)
    at_rewe = core.favorite_for_store(ranking, "  rewe ")
    check(at_rewe["store"] == "rewe"
          and [p["id"] for p in at_rewe["products"]]
          == [second.id, first.id, everywhere.id]
          and [p["id"] for p in at_rewe["other_products"]] == [several.id],
          "a store view must keep the ranking of products sold there or anywhere")
    at_dm = core.favorite_for_store(ranking, "DM")
    check([p["id"] for p in at_dm["products"]]
          == [second.id, everywhere.id, several.id]
          and [p["id"] for p in at_dm["other_products"]] == [first.id],
          "comma-separated stores must each match exactly, ignoring case")
    check([p["id"] for p in core.favorite_for_store(ranking, "Aldi")["products"]]
          == [second.id, everywhere.id],
          "an unknown store must only offer products without a store")
    check(core.favorite_for_store(ranking, "REW")["other_products"][0]["id"] == first.id,
          "store matching must not use substrings")
    expect_valueerror(core.favorite_for_store, ranking, "   ")
    check(len(core.favorite_get_need(need.id).products) == 4,
          "a store view must not change the stored ranking")
    core.favorite_remove_product(need.id, everywhere.id)
    core.favorite_remove_product(need.id, several.id)

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

    # A file cleanup failure must report the already persisted effect and
    # still notify browser clients, instead of looking like a rejected write.
    failure_need = core.favorite_add_need("Aufräumfehler")
    failure_product = core.favorite_add_product(
        failure_need.id, "Produkt", brand="Marke", image=first_image,
    )
    revision = core.change_state()["revision"]
    with patch.object(core, "_favorites_save_unlocked",
                      side_effect=OSError("write denied")):
        try:
            core.favorite_update_product(failure_need.id, failure_product.id,
                                         note="Nicht gespeichert")
        except OSError as error:
            check(str(error) == "write denied",
                  "a pre-save failure must retain its original error")
        else:
            raise AssertionError("the injected pre-save failure must propagate")
    check(core.favorite_get_need(failure_need.id).products[0].note == ""
          and core.change_state()["revision"] == revision,
          "a failure before persistence must not publish or claim a change")

    original_unlink = Path.unlink
    image_to_remove = core.favorite_image_path(failure_product.image_filename)

    def locked_image(path, *args, **kwargs):
        if path == image_to_remove:
            raise PermissionError("image busy")
        return original_unlink(path, *args, **kwargs)

    with patch.object(Path, "unlink", locked_image):
        try:
            core.favorite_remove_product(failure_need.id, failure_product.id)
        except core.PostMutationError as error:
            check(isinstance(error.__cause__, PermissionError)
                  and "gespeichert" in str(error),
                  "a failed cleanup must preserve its cause and report the saved effect")
        else:
            raise AssertionError("a failed post-save cleanup must not report success")
    check(core.favorite_get_need(failure_need.id).products == []
          and image_to_remove.exists(),
          "the failed cleanup must not pretend to undo the saved product removal")
    check(core.change_state()["revision"] == revision + 1
          and core.change_events(revision)[-1]["resources"] == ["favorites"],
          "a saved removal must publish a favorites event despite failed image cleanup")
    image_to_remove.unlink()

    failure_product = core.favorite_add_product(
        failure_need.id, "Produkt", brand="Marke", image=first_image,
    )
    image_to_remove = core.favorite_image_path(failure_product.image_filename)
    with patch.object(Path, "unlink", locked_image), patch.object(
        core, "_record_change", side_effect=OSError("journal unavailable"),
    ):
        try:
            core.favorite_remove_product(failure_need.id, failure_product.id)
        except core.PostMutationError as error:
            check(isinstance(error.__cause__, PermissionError)
                  and "image busy" in str(error)
                  and "journal unavailable" in str(error),
                  "a journal failure must preserve the original post-save failure too")
        else:
            raise AssertionError("two post-save failures must remain an uncertain outcome")
    check(core.favorite_get_need(failure_need.id).products == [],
          "a failed event write must not hide the already saved business effect")
    image_to_remove.unlink()
    core.favorite_remove_need(failure_need.id)

    check_image_import_failures(first_image)

    print(f"OK - {checks} favorite-product checks passed (GUSTO_HOME={HOME})")


if __name__ == "__main__":
    main()
