"""Hermetic tests for the full-state sync core.shopping_merge.

Runnable WITHOUT pytest:  python tests/test_merge.py

Covers the merge rule from docs/sync-kontrakt.md ("last writer wins" per id +
tombstones, full state).

IMPORTANT: GUSTO_HOME is set at the very top to a fresh temp directory BEFORE
gusto.core is imported or any function is called. Otherwise the real data
under recipes/ and data/ would be modified -- which is forbidden.
"""
import os
import json
import sys
import tempfile
from datetime import datetime, timezone
from unittest.mock import patch

# Project root on the path so `gusto` is importable no matter where the script
# is started from.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import gusto  # noqa: E402 - does not load Core or household data

# --- Hermetic: GUSTO_HOME to a throwaway directory, BEFORE the import -------
os.environ["GUSTO_HOME"] = tempfile.mkdtemp(prefix="gusto-merge-test-")


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


def expect_valueerror(value) -> None:
    global checks
    try:
        core.shopping_merge(value)
    except ValueError:
        checks += 1
        return
    raise AssertionError(f"expected ValueError for invalid remote state: {value!r}")


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


class SameMillisecond(datetime):
    @classmethod
    def now(cls, tz=None):
        return cls(2026, 9, 22, 12, 0, 0, 123500, tzinfo=timezone.utc)


with patch.object(core, "datetime", SameMillisecond):
    reset_local([item("rapid", "2026-09-22T12:00:00.123Z")])
    changed = core.shopping_toggle("rapid", checked=True)
    check(changed.updated_at == "2026-09-22T12:00:00.124Z",
          "a mutation in the same wall-clock millisecond must get a new version")
    removed_before = changed.updated_at
    core.shopping_remove("rapid")
    removed = by_id(core.shopping_load())["rapid"]
    check(core._version_key(removed.updated_at) > core._version_key(removed_before),
          "a subsequent removal in that millisecond must advance again")


# --- 11) robustness: unknown/missing fields in the remote dict --------------
reset_local([])
# Unknown fields are ignored; quantity/source remain optional. Status and
# version fields are required so an incomplete payload cannot revive an item.
result = core.shopping_merge([
    {"id": "x", "text": "Brot", "updated_at": "2026-06-23T18:00:00Z",
     "created_at": "2026-06-23T18:00:00Z", "checked": False, "deleted": False,
     "extra_field": "ignore me"},
])
m = by_id(result)
check(m["x"].text == "Brot", "robust: known fields taken over")
check(m["x"].quantity == "" and m["x"].source is None,
      "robust: optional fields fall back to defaults")


# --- 12) malformed remote state is rejected before it reaches persistence --
expect_valueerror("not-a-list")
expect_valueerror(["not-an-object"])
expect_valueerror([{}])
expect_valueerror([item([], "2026-06-23T18:00:00Z")])
expect_valueerror([item("x", 123)])
for required_field in ("checked", "deleted", "created_at", "updated_at"):
    incomplete = item("incomplete", "2026-06-23T18:00:00Z")
    del incomplete[required_field]
    expect_valueerror([incomplete])
check("incomplete" not in by_id(core.shopping_load()),
      "missing status/version fields must leave the persisted state untouched")


core.add_recipe("Syncquelle", content="# Syncquelle\n", slug="syncquelle")

# --- 13) taken-over items need non-empty text -------------------------------
reset_local([item("a", "2026-06-23T18:00:00Z", text="lokal")])
expect_valueerror([item("bad", "2026-06-23T19:00:00Z", text="   ")])
loaded = by_id(core.shopping_load())
check(loaded["a"].text == "lokal" and "bad" not in loaded,
      "a rejected empty-text item must not be persisted")


# --- 14) taken-over visible items need a known source -----------------------
reset_local([])
result = core.shopping_merge([
    item("ok", "2026-06-23T18:00:00Z", text="Mehl", source="syncquelle"),
])
check(by_id(result)["ok"].source == "syncquelle",
      "a taken-over item with a known recipe source is accepted")
expect_valueerror([
    item("bad", "2026-06-23T18:00:00Z", text="Mehl",
         source="unbekanntes-rezept"),
])
check("bad" not in by_id(core.shopping_load()),
      "an unknown source must not reach the shopping file")


# --- 15) tombstones keep their source; stale losing versions are tolerated --
reset_local([])
result = core.shopping_merge([
    item("tomb", "2026-06-23T18:00:00Z", text="Alt",
         source="weggepurgt", deleted=True),
])
m = by_id(result)
check(m["tomb"].deleted is True and m["tomb"].source == "weggepurgt",
      "tombstones keep their deliberately unvalidated source")

reset_local([item("a", "2026-06-23T19:00:00Z", text="neu", deleted=True)])
result = core.shopping_merge([
    item("a", "2026-06-23T18:00:00Z", text="alt", source="weggepurgt"),
])
m = by_id(result)
check(m["a"].deleted is True and m["a"].text == "neu",
      "a losing stale version may still reference a purged recipe")


# --- 16) read-only merges do not rewrite the ever-growing full state -------
unchanged = item("same", "2026-06-23T18:00:00Z", deleted=True)
reset_local([unchanged])
core.shopping_path().write_text(json.dumps({"items": [unchanged]}), encoding="utf-8")
before_bytes = core.shopping_path().read_bytes()
before_revision = core.change_state()["revision"]
for remote in ([], [unchanged], [item("same", "2026-06-23T17:00:00Z")]):
    result = core.shopping_merge(remote)
    check(result[0].to_dict() == unchanged,
          "empty, identical and stale sync states must return the stored version")
check(core.shopping_path().read_bytes() == before_bytes
      and core.change_state()["revision"] == before_revision,
      "a no-op sync must preserve the existing file and must not publish an event")
core.shopping_path().unlink()
check(core.shopping_merge([]) == [] and not core.shopping_path().exists()
      and core.change_state()["revision"] == before_revision,
      "an empty first sync must not create a shopping store or a change event")


# --- 17) extreme timestamps cannot crash or poison future mutations -------
for overflowed_utc in ("0001-01-01T00:00:00+01:00",
                       "9999-12-31T23:59:59-01:00"):
    check(core._parse_iso(overflowed_utc) is None,
          "out-of-range UTC conversion must be treated like malformed legacy time")
    reset_local([item("legacy", overflowed_utc)])
    result = core.shopping_merge([item("legacy", "2026-06-23T18:00:00Z")])
    check(result[0].updated_at == "2026-06-23T18:00:00Z",
          "a valid version must repair a legacy timestamp outside the UTC range")

reset_local([item("unchanged", "2026-06-23T18:00:00Z")])
before_bytes = core.shopping_path().read_bytes()
before_revision = core.change_state()["revision"]
for final_instant in ("9999-12-31T23:59:59.999Z",
                      "9999-12-31T23:59:59.999999Z"):
    expect_valueerror([item("first", "2026-06-23T19:00:00Z"),
                       item("unwritable", final_instant)])
check(core.shopping_path().read_bytes() == before_bytes
      and core.change_state()["revision"] == before_revision,
      "a non-advanceable version must reject the entire merge without a partial write")

# Existing bad data must fail as a validation error rather than OverflowError;
# even earlier in-memory removals in the same operation remain unpersisted.
reset_local([item("normal", "2026-06-23T18:00:00Z"),
             item("unwritable", "9999-12-31T23:59:59.999Z")])
before_bytes = core.shopping_path().read_bytes()
try:
    core.shopping_clear()
except ValueError as error:
    check("Zeitstempel" in str(error), "an unwritable version needs a clear validation error")
else:
    raise AssertionError("an unadvanceable stored version must be rejected")
check(core.shopping_path().read_bytes() == before_bytes,
      "an invalid stored version must not partially clear the shopping list")


print(f"OK - {checks} checks passed (test_merge.py)")
