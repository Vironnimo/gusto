# Storage: change journal

Format, compaction, replay and recovery of `data/changes.json`. The contract that every changed transaction records one revision lives in `storage.md` -> Interfaces.

## Format

`{"revision": N, "resources": [...], "events": [{"revision", "resources"}, ...], "compacted_revision": M, "compacted_resources": [...]}`. Top-level `revision`/`resources` repeat the latest event. A missing file means revision 0 with no events.

Loading validates: `revision` and `compacted_revision` non-negative integers (not booleans), `resources` and `compacted_resources` lists of strings, `events` a list; otherwise `ValueError` (`Ungültiges Gusto-Änderungsjournal`). Individual malformed events are skipped by replay.

## Writing

`_record_change(resources)` keeps only `catalog`, `log`, `favorites`, `shopping` (sorted); with none left it returns the current state without writing. Under the `events` lock it appends `revision + 1`, keeps the newest 256 events (`_CHANGE_HISTORY_LIMIT`), moves dropped events into `compacted_revision` (last dropped) and the union `compacted_resources`, and writes with `track_mutation=False`.

Event resources per transaction default to its locks; overrides: archive purge publishes only `catalog`, sourced shopping add, ingredient import and merges publish only `shopping`. Only cooking publishes `log` (together with `catalog`).

## Replay

`change_events(after)` rejects negative or non-integer values. When `after < compacted_revision` it first yields one synthetic event at `compacted_revision` with `compacted_resources`, then retained events above the cursor. If nothing is retained but `revision > after`, it yields one synthetic current event. `after >= revision` yields nothing. The SSE layer sends the current state instead when a client's `after` exceeds the current revision (`service.md`).

Legacy migration (in memory, persisted with the next write): a journal without compacted fields whose first retained event is above revision 1 is treated as compacted up to that point with all four resources, forcing a broad refresh.

## Recovery

A corrupt journal breaks health, page rendering and the outcome of every mutation (`storage.md` -> Constraints & Gotchas); `check` does not report it. Repair on the server host by restoring valid JSON or deleting the file: the revision restarts at 0, and clients holding a higher revision receive the current state and continue.

## Tests

`tests/test_api.py` (`check_compacted_event_replay`, `check_event_stream`, `check_post_mutation_failures`), `tests/test_recipes.py` (parallel monotonic revisions, silent no-op merge).
