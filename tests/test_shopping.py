"""Hermetic tests for the shopping-list logic in gusto/core.py.

Runnable WITHOUT pytest:  python tests/test_shopping.py

IMPORTANT: GUSTO_HOME is set at the very top to a fresh temp directory BEFORE
gusto.core is imported or any function is called. Otherwise the real data
under recipes/ and data/ would be modified -- which is forbidden.
"""
import os
import sys
import tempfile

# --- Hermetic: GUSTO_HOME to a throwaway directory, BEFORE the import -------
os.environ["GUSTO_HOME"] = tempfile.mkdtemp(prefix="gusto-test-")

# Project root on the path so `gusto` is importable no matter where the script
# is started from.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from gusto import core  # noqa: E402

RECIPE_MD = (
    "# T\n"
    "\n"
    "## Zutaten\n"
    "\n"
    "- 200 g Spaghetti\n"
    "- 100 g Speck\n"
    "\n"
    "## Zubereitung\n"
    "\n"
    "1. kochen"
)

checks = 0


def check(cond, msg):
    global checks
    assert cond, msg
    checks += 1


def expect_valueerror(fn, *args, **kwargs):
    try:
        fn(*args, **kwargs)
    except ValueError:
        return
    raise AssertionError(f"expected ValueError from {fn.__name__}, none raised.")


def main():
    # Safety net: we really work inside the temp directory.
    check(str(core.project_root()).startswith(tempfile.gettempdir()),
          "GUSTO_HOME does not point into the temp directory -- abort.")

    # --- parse_ingredients --------------------------------------------------
    check(core.parse_ingredients(RECIPE_MD) == ["200 g Spaghetti", "100 g Speck"],
          "parse_ingredients does not read the two ingredients correctly.")
    check(core.parse_ingredients("# Only title\n\nNo section here.") == [],
          "parse_ingredients without an ingredient section must return [].")
    # Stops at the next heading, skips empty bullets, '*' counts.
    mixed = ("## Zutaten\n"
             "- Mehl\n"
             "* Zucker\n"
             "-   \n"            # empty bullet -> skipped
             "\n"
             "## Zubereitung\n"
             "- do not count\n")
    check(core.parse_ingredients(mixed) == ["Mehl", "Zucker"],
          "parse_ingredients: empty bullets/second section/'*' handled wrong.")

    # Create a recipe (via the public core path, in the temp home).
    core.add_recipe("T", content=RECIPE_MD, slug="t")

    # --- load/save round-trip on an empty list ------------------------------
    check(core.shopping_load() == [], "Fresh shopping list must be empty.")
    check(core.shopping_list() == [], "Empty list -> shopping_list() == [].")

    # --- shopping_add -------------------------------------------------------
    a = core.shopping_add("Milch", quantity="1 L")
    check(a.id and a.created_at and a.updated_at,
          "shopping_add must set id/created_at/updated_at.")
    check(a.checked is False and a.deleted is False,
          "New item: checked and deleted must be False.")
    check(a.text == "Milch" and a.quantity == "1 L" and a.source is None,
          "shopping_add does not take over text/quantity/source correctly.")
    check(len(core.shopping_load()) == 1, "After add there must be exactly 1 item.")

    # load/save round-trip: stored values == read-back values.
    loaded = core.shopping_load()[0]
    check(loaded.to_dict() == a.to_dict(),
          "load/save round-trip changes the item.")

    # --- shopping_add_recipe ------------------------------------------------
    new = core.shopping_add_recipe("t")
    check([i.text for i in new] == ["200 g Spaghetti", "100 g Speck"],
          "shopping_add_recipe returns the wrong ingredients.")
    check(all(i.source == "t" for i in new),
          "shopping_add_recipe must set source=slug.")
    check(len(core.shopping_load()) == 3, "Expected 3 items total (1 + 2).")
    expect_valueerror(core.shopping_add_recipe, "t")
    check(len(core.shopping_load()) == 3,
          "re-importing a recipe with visible items must not add duplicates")
    expect_valueerror(core.shopping_add_recipe, "does-not-exist")
    core.add_recipe("Leer", content="# Leer\n\n## Zubereitung\n\n1. Test\n", slug="leer")
    expect_valueerror(core.shopping_add_recipe, "leer")

    # --- shopping_list: order by created_at ---------------------------------
    texts = [i.text for i in core.shopping_list()]
    check(texts == ["Milch", "200 g Spaghetti", "100 g Speck"],
          "shopping_list must sort by insertion order (created_at).")

    # --- shopping_toggle ----------------------------------------------------
    t1 = core.shopping_toggle(a.id)                # toggle -> True
    check(t1.checked is True, "toggle(None) must flip from False to True.")
    t2 = core.shopping_toggle(a.id)                # toggle -> False
    check(t2.checked is False, "toggle(None) again must go back to False.")
    t3 = core.shopping_toggle(a.id, checked=True)  # set explicitly
    check(t3.checked is True, "toggle(checked=True) must set True.")
    unchanged_timestamp = t3.updated_at
    t4 = core.shopping_toggle(a.id, checked=True)
    check(t4.updated_at == unchanged_timestamp,
          "idempotent check must not advance updated_at")
    expect_valueerror(core.shopping_toggle, "unknown-id")

    # --- shopping_list filter: done -----------------------------------------
    check([i.text for i in core.shopping_list(include_done=False)]
          == ["200 g Spaghetti", "100 g Speck"],
          "include_done=False must hide done items.")
    check(len(core.shopping_list(include_done=True)) == 3,
          "include_done=True must show all (non-deleted) items.")

    # --- shopping_remove: tombstone -----------------------------------------
    spaghetti = next(i for i in core.shopping_load() if i.text == "200 g Spaghetti")
    core.shopping_remove(spaghetti.id)
    removed = next(i for i in core.shopping_load() if i.id == spaghetti.id)
    check(removed.deleted is True,
          "shopping_remove must mark the item as a tombstone.")
    # The tombstone is present in load() but not in list() (default).
    check(any(i.id == spaghetti.id for i in core.shopping_load()),
          "Tombstone must still appear in shopping_load().")
    check(not any(i.id == spaghetti.id for i in core.shopping_list()),
          "Tombstone must NOT appear in shopping_list() (default).")
    check(any(i.id == spaghetti.id
              for i in core.shopping_list(include_deleted=True)),
          "include_deleted=True must show tombstones.")
    removed_timestamp = removed.updated_at
    core.shopping_remove(spaghetti.id)
    removed_again = next(i for i in core.shopping_load() if i.id == spaghetti.id)
    check(removed_again.updated_at == removed_timestamp,
          "idempotent remove must not advance a tombstone timestamp")
    # Tombstones can no longer be toggled.
    expect_valueerror(core.shopping_toggle, spaghetti.id)
    expect_valueerror(core.shopping_remove, "unknown-id")

    # --- shopping_clear_done ------------------------------------------------
    # Currently done + non-tombstone: only "Milch" (a). Speck is open,
    # Spaghetti is already a tombstone.
    count = core.shopping_clear_done()
    check(count == 1, f"clear_done should remove exactly 1 item, was {count}.")
    milch = next(i for i in core.shopping_load() if i.id == a.id)
    check(milch.deleted is True,
          "clear_done must turn done items into tombstones.")
    # Calling again removes nothing more.
    check(core.shopping_clear_done() == 0,
          "clear_done with no done items must return 0.")
    # Exactly the open Speck item stays visible.
    visible = core.shopping_list()
    check([i.text for i in visible] == ["100 g Speck"],
          "After clear_done only the open Speck item may be visible.")

    # --- shopping_add_many --------------------------------------------------
    batch = core.shopping_add_many(["Brot", "6 Eier", "200 g Spaghetti"])
    check([item.text for item in batch] == ["Brot", "6 Eier", "200 g Spaghetti"],
          "shopping_add_many must preserve all requested entries and their order.")
    check([item.text for item in core.shopping_list()][-3:]
          == ["Brot", "6 Eier", "200 g Spaghetti"],
          "shopping_add_many must persist the complete group.")
    expect_valueerror(core.shopping_add_many, [])
    expect_valueerror(core.shopping_add_many, ["Brot", "  "])

    # --- sourced single add -------------------------------------------------
    sourced_recipe = core.add_recipe(
        "Einzelzutat", content="# Einzelzutat\n\n## Zutaten\n\n- Pfeffer\n",
        slug="einzelzutat",
    )
    sourced = core.shopping_add("Pfeffer", source=sourced_recipe.slug)
    check(sourced.source == sourced_recipe.slug,
          "a sourced single item must retain its recipe slug")
    count_before_invalid_source = len(core.shopping_load())
    expect_valueerror(core.shopping_add, "Salz", source="does-not-exist")
    check(len(core.shopping_load()) == count_before_invalid_source,
          "an unknown source recipe must not mutate the shopping list")
    expect_valueerror(core.shopping_add_recipe, sourced_recipe.slug)
    core.shopping_remove(sourced.id)
    imported_after_remove = core.shopping_add_recipe(sourced_recipe.slug)
    check([item.text for item in imported_after_remove] == ["Pfeffer"],
          "removing the sourced item must allow a fresh recipe import")

    print(f"OK - {checks} checks passed (GUSTO_HOME={core.project_root()})")


if __name__ == "__main__":
    main()
