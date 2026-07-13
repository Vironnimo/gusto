"""Hermetic tests for the full-state sync core.shopping_merge.

Runnable WITHOUT pytest:  python tests/test_merge.py

Covers the merge rule from docs/sync-kontrakt.md ("last writer wins" per id +
tombstones, full state).

IMPORTANT: GUSTO_HOME is set at the very top to a fresh temp directory BEFORE
gusto.core is imported or any function is called. Otherwise the real data
under recipes/ and data/ would be modified -- which is forbidden.
"""
import os
import sys
import tempfile

# --- Hermetic: GUSTO_HOME to a throwaway directory, BEFORE the import -------
os.environ["GUSTO_HOME"] = tempfile.mkdtemp(prefix="gusto-merge-test-")

# Project root on the path so `gusto` is importable no matter where the script
# is started from.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from gusto import core  # noqa: E402

checks = 0


def check(cond, msg):
    global checks
    assert cond, msg
    checks += 1


def item(id, updated_at, *, text="x", checked=False, deleted=False,
         created_at="2026-06-23T18:00:00Z", quantity="", source=None) -> dict:
    """A raw item dict (as it would come from the client)."""
    return {
        "id": id, "text": text, "quantity": quantity, "checked": checked,
        "source": source, "created_at": created_at,
        "updated_at": updated_at, "deleted": deleted,
    }


def reset_local(items: list[dict]) -> None:
    """Set the local state freshly (via shopping_save)."""
    core.shopping_save([core.ShoppingItem.from_dict(d) for d in items])


def by_id(items: list[core.ShoppingItem]) -> dict[str, core.ShoppingItem]:
    return {i.id: i for i in items}


# --- 1) empty local + remote items -> taken over ----------------------------
reset_local([])
result = core.shopping_merge([item("a", "2026-06-23T18:00:00Z", text="Milch")])
m = by_id(result)
check(len(result) == 1, "empty local: exactly 1 item after merge")
check("a" in m and m["a"].text == "Milch", "empty local: remote item taken over")

# Return type is ShoppingItem (not dict).
check(isinstance(result[0], core.ShoppingItem), "return consists of ShoppingItem")


# --- 2) same id, remote newer (larger updated_at) -> remote wins ------------
reset_local([item("a", "2026-06-23T18:00:00Z", text="old", checked=False)])
result = core.shopping_merge([item("a", "2026-06-23T19:00:00Z", text="new", checked=True)])
m = by_id(result)
check(len(result) == 1, "remote newer: still 1 item")
check(m["a"].text == "new" and m["a"].checked is True, "remote newer: remote wins")


# --- 3) same id, remote older -> local stays --------------------------------
reset_local([item("a", "2026-06-23T19:00:00Z", text="local-new")])
result = core.shopping_merge([item("a", "2026-06-23T18:00:00Z", text="remote-old")])
m = by_id(result)
check(m["a"].text == "local-new", "remote older: local version stays")


# --- 4) tie on updated_at -> local stays ------------------------------------
reset_local([item("a", "2026-06-23T18:00:00Z", text="local")])
result = core.shopping_merge([item("a", "2026-06-23T18:00:00Z", text="remote")])
m = by_id(result)
check(m["a"].text == "local", "tie: local version stays")


# --- 5) remote tombstone newer -> item becomes tombstone --------------------
reset_local([item("a", "2026-06-23T18:00:00Z", text="here", deleted=False)])
result = core.shopping_merge([item("a", "2026-06-23T19:00:00Z", deleted=True)])
m = by_id(result)
check(m["a"].deleted is True, "remote tombstone newer: item becomes tombstone")
# The tombstone is part of the return (not filtered out).
check(len(result) == 1, "tombstone stays in the return")

# Counter-check: local tombstone, remote older+alive -> tombstone stays.
reset_local([item("a", "2026-06-23T19:00:00Z", deleted=True)])
result = core.shopping_merge([item("a", "2026-06-23T18:00:00Z", deleted=False)])
m = by_id(result)
check(m["a"].deleted is True, "local tombstone newer: stays deleted")


# --- 6) local-only id is kept -----------------------------------------------
reset_local([item("local-only", "2026-06-23T18:00:00Z", text="local only")])
result = core.shopping_merge([item("remote-only", "2026-06-23T18:00:00Z", text="remote only")])
m = by_id(result)
check("local-only" in m, "local-only id is kept")
check("remote-only" in m, "remote-only id is taken over")
check(len(result) == 2, "union of both ids in the result")


# --- 7) return contains tombstones (mixed state) ----------------------------
reset_local([
    item("open", "2026-06-23T18:00:00Z"),
    item("dead", "2026-06-23T18:00:00Z", deleted=True),
])
result = core.shopping_merge([])
m = by_id(result)
check("dead" in m and m["dead"].deleted is True,
      "empty remote state: local tombstones stay in the return")
check(len(result) == 2, "empty remote state: full local state returned")


# --- 8) result is persisted (shopping_load after merge) ---------------------
reset_local([item("a", "2026-06-23T18:00:00Z", text="old")])
core.shopping_merge([
    item("a", "2026-06-23T19:00:00Z", text="new"),
    item("b", "2026-06-23T19:00:00Z", text="fresh", deleted=True),
])
loaded = by_id(core.shopping_load())
check(loaded["a"].text == "new", "persisted: merged change on disk")
check("b" in loaded and loaded["b"].deleted is True,
      "persisted: new remote tombstone on disk")
check(len(loaded) == 2, "persisted: all ids incl. tombstone on disk")


# --- 9) mixed precision: milliseconds are compared as timestamps -----------
reset_local([item("a", "2026-06-23T18:00:00Z", text="second precision")])
result = core.shopping_merge([
    item("a", "2026-06-23T18:00:00.001Z", text="one millisecond newer"),
])
m = by_id(result)
check(m["a"].text == "one millisecond newer",
      "millisecond timestamp must beat an old same-second timestamp")


# --- 10) a rapid local mutation always advances the version ----------------
reset_local([item("rapid", "2099-01-01T00:00:00.000Z", checked=False)])
changed = core.shopping_toggle("rapid", checked=True)
check(changed.updated_at == "2099-01-01T00:00:00.001Z",
      "rapid mutation must advance at least one millisecond")
check(changed.checked is True, "rapid mutation must preserve the actual change")


# --- 11) robustness: unknown/missing fields in the remote dict --------------
reset_local([])
# 'extra_field' is unknown and must be ignored; missing fields -> defaults from
# ShoppingItem (from_dict).
result = core.shopping_merge([
    {"id": "x", "text": "Brot", "updated_at": "2026-06-23T18:00:00Z",
     "extra_field": "ignore me"},
])
m = by_id(result)
check(m["x"].text == "Brot", "robust: known fields taken over")
check(m["x"].quantity == "" and m["x"].checked is False and m["x"].deleted is False,
      "robust: missing fields fall back to defaults")


print(f"OK - {checks} checks passed (test_merge.py)")
