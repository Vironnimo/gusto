"""Hermetic tests for the shopping-list logic in gusto/core/shopping.py.

Runnable WITHOUT pytest:  python tests/test_shopping.py

IMPORTANT: GUSTO_HOME is set at the very top to a fresh temp directory BEFORE
gusto.core is imported or any function is called. Otherwise the real data
under recipes/ and data/ would be modified -- which is forbidden.
"""
import os
import json
import sys
import tempfile
from pathlib import Path

# Project root on the path so `gusto` is importable no matter where the script
# is started from.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import gusto  # noqa: E402, F401 - does not load Core or household data

# --- Hermetic: GUSTO_HOME to a throwaway directory, BEFORE the import -------
os.environ["GUSTO_HOME"] = tempfile.mkdtemp(prefix="gusto-test-")


import gusto.core.catalog.archive as core_archive  # noqa: E402
import gusto.core.catalog.recipes as core_recipes  # noqa: E402
import gusto.core.checks as core_checks  # noqa: E402
import gusto.core.errors as core_errors  # noqa: E402
import gusto.core.shopping as core_shopping  # noqa: E402
import gusto.core.storage as core_storage  # noqa: E402

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
    check(core_storage.project_root().is_relative_to(Path(tempfile.gettempdir()).resolve()),
          "GUSTO_HOME does not point into the temp directory -- abort.")

    # --- parse_ingredients --------------------------------------------------
    check(core_shopping.parse_ingredients(RECIPE_MD) == ["200 g Spaghetti", "100 g Speck"],
          "parse_ingredients does not read the two ingredients correctly.")
    check(core_shopping.parse_ingredients("# Only title\n\nNo section here.") == [],
          "parse_ingredients without an ingredient section must return [].")
    # Stops at the next heading, skips empty bullets, '*' counts.
    mixed = ("## Zutaten\n"
             "- Mehl\n"
             "* Zucker\n"
             "-   \n"            # empty bullet -> skipped
             "\n"
             "## Zubereitung\n"
             "- do not count\n")
    check(core_shopping.parse_ingredients(mixed) == ["Mehl", "Zucker"],
          "parse_ingredients: empty bullets/second section/'*' handled wrong.")
    formatted = (
        "## Zutaten\n**Für die Sauce**\n- Tomaten\n---\n* * *\n- - -\n"
        "* **Basilikum**\n***optional***\n## Zubereitung\n- Nicht importieren\n"
    )
    check(core_shopping.parse_ingredients(formatted) == ["Tomaten", "**Basilikum**"],
          "bold notes and Markdown separators must not become shopping items.")

    # Create a recipe (via the public core path, in the temp home).
    core_recipes.add_recipe("T", content=RECIPE_MD, slug="t")

    # --- load/save round-trip on an empty list ------------------------------
    check(core_shopping.shopping_load() == [], "Fresh shopping list must be empty.")
    check(core_shopping.shopping_list() == [], "Empty list -> shopping_list() == [].")

    # --- corrupted file shapes ----------------------------------------------
    # A wrong JSON root must fail with a German ValueError instead of a raw
    # AttributeError, and check() must report it as an integrity error.
    core_storage.shopping_path().write_text("[]", encoding="utf-8")
    expect_valueerror(core_shopping.shopping_load)
    corrupted = core_checks.check()
    check(any("shopping_list.json" in message
              for message in corrupted["invalid_data_files"]),
          "corrupted shopping data must be reported as an integrity error.")
    check(corrupted["ok"] is False,
          "corrupted shopping data must be a hard error.")
    for broken_item in (
        {"text": "Milch"},
        {"id": "x", "text": "Milch", "source": {}},
        {"id": "x", "text": "Milch", "checked": "false"},
        {"id": "x", "text": "Milch", "created_at": None},
    ):
        core_storage.shopping_path().write_text(
            json.dumps({"items": [broken_item]}), encoding="utf-8")
        expect_valueerror(core_shopping.shopping_load)
        corrupted = core_checks.check()
        check(corrupted["ok"] is False
              and any("shopping_list.json" in message
                      for message in corrupted["invalid_data_files"]),
              "broken shopping entries must produce a named diagnostic instead of crashing")
    core_storage.shopping_path().write_text(
        '{"items": [{"id": "legacy", "text": "Milch"}]}', encoding="utf-8")
    legacy = core_shopping.shopping_load()[0]
    check(legacy.checked is False and legacy.deleted is False
          and legacy.created_at == "" and legacy.source is None,
          "omitted optional fields in older stored items must keep their defaults")
    os.remove(core_storage.shopping_path())
    check(core_shopping.shopping_load() == [],
          "a removed shopping file must load as an empty list.")

    # --- shopping_add -------------------------------------------------------
    a = core_shopping.shopping_add("Milch", quantity="1 L")
    check(a.id and a.created_at and a.updated_at,
          "shopping_add must set id/created_at/updated_at.")
    check(a.checked is False and a.deleted is False,
          "New item: checked and deleted must be False.")
    check(a.text == "Milch" and a.quantity == "1 L" and a.source is None,
          "shopping_add does not take over text/quantity/source correctly.")
    check(len(core_shopping.shopping_load()) == 1, "After add there must be exactly 1 item.")

    # load/save round-trip: stored values == read-back values.
    loaded = core_shopping.shopping_load()[0]
    check(loaded.to_dict() == a.to_dict(),
          "load/save round-trip changes the item.")

    # --- shopping_add_recipe ------------------------------------------------
    new = core_shopping.shopping_add_recipe("t")
    check([i.text for i in new] == ["200 g Spaghetti", "100 g Speck"],
          "shopping_add_recipe returns the wrong ingredients.")
    check(all(i.source == "t" for i in new),
          "shopping_add_recipe must set source=slug.")
    check(len(core_shopping.shopping_load()) == 3, "Expected 3 items total (1 + 2).")
    expect_valueerror(core_shopping.shopping_add_recipe, "t")
    check(len(core_shopping.shopping_load()) == 3,
          "re-importing a recipe with visible items must not add duplicates")
    expect_valueerror(core_shopping.shopping_add_recipe, "does-not-exist")
    core_recipes.add_recipe("Leer", content="# Leer\n\n## Zubereitung\n\n1. Test\n", slug="leer")
    expect_valueerror(core_shopping.shopping_add_recipe, "leer")

    # --- shopping_list: order by created_at ---------------------------------
    texts = [i.text for i in core_shopping.shopping_list()]
    check(texts == ["Milch", "200 g Spaghetti", "100 g Speck"],
          "shopping_list must sort by insertion order (created_at).")

    # --- shopping_toggle ----------------------------------------------------
    t1 = core_shopping.shopping_toggle(a.id)                # toggle -> True
    check(t1.checked is True, "toggle(None) must flip from False to True.")
    t2 = core_shopping.shopping_toggle(a.id)                # toggle -> False
    check(t2.checked is False, "toggle(None) again must go back to False.")
    t3 = core_shopping.shopping_toggle(a.id, checked=True)  # set explicitly
    check(t3.checked is True, "toggle(checked=True) must set True.")
    unchanged_timestamp = t3.updated_at
    t4 = core_shopping.shopping_toggle(a.id, checked=True)
    check(t4.updated_at == unchanged_timestamp,
          "idempotent check must not advance updated_at")
    expect_valueerror(core_shopping.shopping_toggle, "unknown-id")

    # --- shopping_list filter: done -----------------------------------------
    check([i.text for i in core_shopping.shopping_list(include_done=False)]
          == ["200 g Spaghetti", "100 g Speck"],
          "include_done=False must hide done items.")
    check(len(core_shopping.shopping_list(include_done=True)) == 3,
          "include_done=True must show all (non-deleted) items.")

    # --- shopping_remove: tombstone -----------------------------------------
    spaghetti = next(i for i in core_shopping.shopping_load() if i.text == "200 g Spaghetti")
    core_shopping.shopping_remove(spaghetti.id)
    removed = next(i for i in core_shopping.shopping_load() if i.id == spaghetti.id)
    check(removed.deleted is True,
          "shopping_remove must mark the item as a tombstone.")
    # The tombstone is present in load() but not in list() (default).
    check(any(i.id == spaghetti.id for i in core_shopping.shopping_load()),
          "Tombstone must still appear in shopping_load().")
    check(not any(i.id == spaghetti.id for i in core_shopping.shopping_list()),
          "Tombstone must NOT appear in shopping_list() (default).")
    check(any(i.id == spaghetti.id
              for i in core_shopping.shopping_list(include_deleted=True)),
          "include_deleted=True must show tombstones.")
    removed_timestamp = removed.updated_at
    core_shopping.shopping_remove(spaghetti.id)
    removed_again = next(i for i in core_shopping.shopping_load() if i.id == spaghetti.id)
    check(removed_again.updated_at == removed_timestamp,
          "idempotent remove must not advance a tombstone timestamp")
    # Tombstones can no longer be toggled.
    expect_valueerror(core_shopping.shopping_toggle, spaghetti.id)
    expect_valueerror(core_shopping.shopping_remove, "unknown-id")

    # --- shopping_remove_done -----------------------------------------------
    # Currently done + non-tombstone: only "Milch" (a). Speck is open,
    # Spaghetti is already a tombstone.
    count = core_shopping.shopping_remove_done()
    check(count == 1, f"remove_done should remove exactly 1 item, was {count}.")
    milch = next(i for i in core_shopping.shopping_load() if i.id == a.id)
    check(milch.deleted is True,
          "remove_done must turn done items into tombstones.")
    # Calling again removes nothing more.
    check(core_shopping.shopping_remove_done() == 0,
          "remove_done with no done items must return 0.")
    # Exactly the open Speck item stays visible.
    visible = core_shopping.shopping_list()
    check([i.text for i in visible] == ["100 g Speck"],
          "After remove_done only the open Speck item may be visible.")

    # --- shopping_add_many --------------------------------------------------
    batch = core_shopping.shopping_add_many(["Brot", "6 Eier", "200 g Spaghetti"])
    check([item.text for item in batch] == ["Brot", "6 Eier", "200 g Spaghetti"],
          "shopping_add_many must preserve all requested entries and their order.")
    check([item.text for item in core_shopping.shopping_list()][-3:]
          == ["Brot", "6 Eier", "200 g Spaghetti"],
          "shopping_add_many must persist the complete group.")
    expect_valueerror(core_shopping.shopping_add_many, [])
    expect_valueerror(core_shopping.shopping_add_many, ["Brot", "  "])

    # --- shopping_clear -----------------------------------------------------
    # The visible list contains open Speck plus all three batch items.
    check(core_shopping.shopping_clear() == 4,
          "shopping_clear must remove every visible item in one operation.")
    check(core_shopping.shopping_list() == [],
          "shopping_clear must leave no visible shopping items.")
    check(core_shopping.shopping_clear() == 0,
          "shopping_clear on an empty visible list must return 0.")

    # Explicit multi-item state changes validate the whole selection and save once.
    first, second, third = core_shopping.shopping_add_many(["Äpfel", "Tee", "Reis"])
    core_shopping.shopping_toggle(first.id, checked=True)
    first_before = next(item for item in core_shopping.shopping_load() if item.id == first.id)
    before_revision = core_storage.change_state()["revision"]
    result = core_shopping.shopping_check_many([second.id, first.id, second.id], True)
    check([item.id for item in result["items"]] == [second.id, first.id]
          and all(item.checked for item in result["items"])
          and (result["changed"], result["unchanged"]) == (1, 1),
          "batch check must return distinct targets in request order with actual transition counts")
    check(result["items"][0].updated_at > second.updated_at
          and result["items"][1].updated_at == first_before.updated_at
          and core_storage.change_state()["revision"] == before_revision + 1,
          "batch check must timestamp only changed items and publish one event")
    before_bytes = core_storage.shopping_path().read_bytes()
    before_revision = core_storage.change_state()["revision"]
    unchanged = core_shopping.shopping_check_many([first.id, second.id], True)
    check((unchanged["changed"], unchanged["unchanged"]) == (0, 2)
          and core_storage.shopping_path().read_bytes() == before_bytes
          and core_storage.change_state()["revision"] == before_revision,
          "repeated batch check must preserve stored bytes and revision")
    for target_ids in ([third.id, "missing"], [], [third.id, ""], [third.id, None], third.id):
        expect_valueerror(core_shopping.shopping_check_many, target_ids, True)
        expect_valueerror(core_shopping.shopping_remove_many, target_ids)
    expect_valueerror(core_shopping.shopping_check_many, [third.id], "false")
    check(core_storage.shopping_path().read_bytes() == before_bytes
          and core_storage.change_state()["revision"] == before_revision,
          "invalid batch arguments or a later missing target must reject every requested change")
    unchecked = core_shopping.shopping_check_many([first.id, second.id], False)
    check((unchecked["changed"], unchecked["unchanged"]) == (2, 0)
          and all(not item.checked for item in unchecked["items"])
          and core_storage.change_state()["revision"] == before_revision + 1,
          "batch uncheck must clear every target in a single transaction")
    before_revision = core_storage.change_state()["revision"]
    removed_many = core_shopping.shopping_remove_many([second.id, first.id, second.id])
    check(removed_many == {"removed": 2, "unchanged": 0, "ids": [second.id, first.id]}
          and core_storage.change_state()["revision"] == before_revision + 1,
          "batch remove must deduplicate targets and publish one event for all tombstones")
    before_bytes = core_storage.shopping_path().read_bytes()
    before_revision = core_storage.change_state()["revision"]
    expect_valueerror(core_shopping.shopping_check_many, [third.id, first.id], True)
    expect_valueerror(core_shopping.shopping_remove_many, [third.id, "missing"])
    repeated = core_shopping.shopping_remove_many([first.id, second.id])
    check(repeated == {"removed": 0, "unchanged": 2, "ids": [first.id, second.id]}
          and core_storage.shopping_path().read_bytes() == before_bytes
          and core_storage.change_state()["revision"] == before_revision,
          "tombstones reject batch checks, accept repeated removals, and cause no writes")
    removed_rest = core_shopping.shopping_remove_many([first.id, third.id])
    check((removed_rest["removed"], removed_rest["unchanged"]) == (1, 1)
          and core_shopping.shopping_list() == [],
          "mixed batch removal must count only the new tombstone and preserve existing ones")

    # --- sourced single add -------------------------------------------------
    sourced_recipe = core_recipes.add_recipe(
        "Einzelzutat", content="# Einzelzutat\n\n## Zutaten\n\n- Pfeffer\n",
        slug="einzelzutat",
    )
    sourced = core_shopping.shopping_add("Pfeffer", source=sourced_recipe.slug)
    check(sourced.source == sourced_recipe.slug,
          "a sourced single item must retain its recipe slug")
    count_before_invalid_source = len(core_shopping.shopping_load())
    expect_valueerror(core_shopping.shopping_add, "Salz", source="does-not-exist")
    check(len(core_shopping.shopping_load()) == count_before_invalid_source,
          "an unknown source recipe must not mutate the shopping list")
    expect_valueerror(core_shopping.shopping_add_recipe, sourced_recipe.slug)
    core_shopping.shopping_remove(sourced.id)
    imported_after_remove = core_shopping.shopping_add_recipe(sourced_recipe.slug)
    check([item.text for item in imported_after_remove] == ["Pfeffer"],
          "removing the sourced item must allow a fresh recipe import")
    core_archive.archive_recipe(sourced_recipe.slug)
    before_bytes = core_storage.shopping_path().read_bytes()
    before_revision = core_storage.change_state()["revision"]
    expect_valueerror(core_shopping.shopping_add, "Salz", source=sourced_recipe.slug)
    expect_valueerror(core_shopping.shopping_add_recipe, sourced_recipe.slug)
    check(core_storage.shopping_path().read_bytes() == before_bytes
          and core_storage.change_state()["revision"] == before_revision,
          "an archived source must reject new attribution and ingredient import atomically")
    remote = imported_after_remove[0].to_dict()
    remote.update(quantity="1 Bund", updated_at="2099-01-01T00:00:00Z")
    synced = core_shopping.shopping_merge([remote])
    check(any(item.id == remote["id"] and item.source == sourced_recipe.slug
              and item.quantity == "1 Bund" for item in synced),
          "sync must preserve and update an existing item's archived source")

    # --- single-line hygiene -------------------------------------------------
    # Newlines in free text would persist as store dirt (matching normalizes
    # whitespace anyway), so the write paths must reject them.
    count_before_newline_probe = len(core_shopping.shopping_load())
    for multiline in ("a\nb", "a\rb"):
        expect_valueerror(core_shopping.shopping_add, multiline)
    expect_valueerror(core_shopping.shopping_add, "Reis", quantity="1\nkg")
    expect_valueerror(core_shopping.shopping_add_many, ["ok", "a\nb"])
    check(len(core_shopping.shopping_load()) == count_before_newline_probe,
          "rejected multiline texts must not mutate the shopping list.")

    # --- shopping_merge: status fields are required, not defaulted ----------
    full_item = {
        "id": "a" * 32, "text": "Probe-Item", "quantity": "",
        "checked": False, "deleted": False, "source": None,
        "created_at": "2026-09-18T10:00:00Z",
        "updated_at": "2026-09-18T10:00:00Z",
    }
    merged = core_shopping.shopping_merge([dict(full_item)])
    check(next(item for item in merged if item.id == full_item["id"]).text
          == "Probe-Item",
          "a complete valid item must merge unchanged.")
    before_bytes = core_storage.shopping_path().read_bytes()
    before_revision = core_storage.change_state()["revision"]
    for broken in (
        # Verified live: a minimal item was persisted with empty timestamps
        # and an implicit deleted=False, contradicting the HTTP-400 contract.
        {"id": "b" * 32, "text": "Probe-Item"},
        {"id": "b" * 32, "text": "Probe-Item", "checked": False, "deleted": False,
         "created_at": "", "updated_at": "2026-09-18T10:00:00Z"},
        {"id": "b" * 32, "text": "Probe-Item", "checked": False, "deleted": False,
         "created_at": "2026-09-18T10:00:00Z"},
    ):
        try:
            core_shopping.shopping_merge([dict(broken)])
            raise AssertionError(
                f"missing status fields must be rejected: {broken!r}")
        except ValueError as error:
            check(bool(str(error)), "an invalid item must explain the rejection")
    check(core_storage.shopping_path().read_bytes() == before_bytes
          and core_storage.change_state()["revision"] == before_revision,
          "a rejected minimal item must preserve all stored items and revisions")

    # Portion previews and imports share one calculation and one recipe token.
    portion_content = (
        "# Portionen\n\n## Zutaten\n"
        "- 200 g Mehl\n- 1,5 l Milch\n- 1/3 TL Salz\n- 1 1/2 Eier\n"
        "- ½ Zwiebel\n- 1½ kg Karotten\n- 2-4 EL Öl\n- 2 Biskuit\n"
        "- 2 x 400 g Tomaten\n- 1 Dose (400 g) Bohnen\n"
        "- 2 Eigelb + 1 Ei\n- Salz nach Geschmack\n"
        "- 3,5% Milch\n- 2er-Pack Joghurt\n- 1.000 g Mehl\n"
        "- 200g Mehl\n- 0.5 l Milch\n"
    )
    core_recipes.add_recipe("Portionen", content=portion_content, slug="portionen", servings=4)
    preview = core_shopping.shopping_preview_recipe("portionen", 6)
    check((preview["source_servings"], preview["target_servings"], preview["factor"])
          == (4, 6, 1.5), "preview must identify both serving counts and the factor")
    originals = [line["original"] for line in preview["ingredients"]]
    scaled = [line["scaled"] for line in preview["ingredients"]]
    check(scaled == ["300 g Mehl", "2,25 l Milch", "0,5 TL Salz", "2,25 Eier",
                     "0,75 Zwiebel", "2,25 kg Karotten", "3-6 EL Öl", "3 Biskuit",
                     "2 x 400 g Tomaten", "1 Dose (400 g) Bohnen",
                     "2 Eigelb + 1 Ei", "Salz nach Geschmack", "3,5% Milch",
                     "2er-Pack Joghurt", "1.000 g Mehl", "300g Mehl", "0,75 l Milch"],
          "preview must scale clear quantities exactly and preserve ambiguous lines")
    check(all(not line["warning"] for line in preview["ingredients"][:8])
          and all(line["warning"] for line in preview["ingredients"][8:15])
          and all(not line["warning"] for line in preview["ingredients"][15:]),
          "each ambiguous or unquantified line must be identified")
    check(originals == core_shopping.parse_ingredients(portion_content),
          "preview must retain the original wording for review")
    third = core_shopping.shopping_preview_recipe("portionen", 3)
    check(third["ingredients"][2]["scaled"] == "0,25 TL Salz",
          "fractional ingredients must not be rounded")
    half = core_shopping.shopping_preview_recipe("portionen", 2)
    check(half["ingredients"][2]["scaled"] == "1/6 TL Salz",
          "non-terminating quantities must remain exact fractions")
    for invalid in (0, -1, True, 1.5, "4"):
        expect_valueerror(core_shopping.shopping_preview_recipe, "portionen", invalid)
    expect_valueerror(core_shopping.shopping_add_recipe, "portionen", 6)
    no_baseline = core_shopping.shopping_preview_recipe("t")
    check(no_baseline["source_servings"] is None and no_baseline["target_servings"] is None
          and no_baseline["factor"] == 1,
          "a recipe without servings remains previewable unscaled")
    expect_valueerror(core_shopping.shopping_preview_recipe, "t", 4)

    core_recipes.update_recipe("portionen", content=portion_content + "\nExtra\n")
    before_bytes = core_storage.shopping_path().read_bytes()
    before_revision = core_storage.change_state()["revision"]
    try:
        core_shopping.shopping_add_recipe("portionen", 6, preview["recipe_revision"])
    except core_errors.RevisionConflictError as error:
        check(bool(error.current_revision), "a stale preview identifies the current recipe revision")
    else:
        raise AssertionError("stale ingredient preview must be rejected")
    check(core_storage.shopping_path().read_bytes() == before_bytes
          and core_storage.change_state()["revision"] == before_revision,
          "a stale preview must not change the shopping list or publish an event")
    preview = core_shopping.shopping_preview_recipe("portionen", 6)
    core_recipes.update_recipe("portionen", servings=5)
    expect_valueerror(core_shopping.shopping_add_recipe, "portionen", 6,
                      preview["recipe_revision"])
    preview = core_shopping.shopping_preview_recipe("portionen", 6)
    imported = core_shopping.shopping_add_recipe("portionen", 6, preview["recipe_revision"])
    check([item.text for item in imported] == [line["scaled"] for line in preview["ingredients"]]
          and all(item.source == "portionen" for item in imported),
          "import must use exactly the reviewed lines and retain their source")
    expect_valueerror(core_shopping.shopping_add_recipe, "portionen", 6,
                      preview["recipe_revision"])

    print(f"OK - {checks} checks passed (GUSTO_HOME={core_storage.project_root()})")


if __name__ == "__main__":
    main()
