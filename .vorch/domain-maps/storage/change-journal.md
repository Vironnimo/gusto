# Storage: change journal

Format, compaction, replay and recovery of `data/changes.json`. The contract that every changed transaction records one revision lives in `storage.md` -> Interfaces.

## Format

`{"revision": N, "resources": [...], "events": [{"revision", "resources"}, ...], "compacted_revision": M, "compacted_resources": [...]}`. Top-level `revision`/`resources` repeat the latest event. A missing file means revision 0 with no events.

Loading validates: `revision` and `compacted_revision` non-negative integers (not booleans), compaction no newer than the current revision, `resources` and `compacted_resources` lists of strings, and `events` a list of objects. Each event has an integer revision (not boolean), strictly newer than its predecessor or the compacted boundary and no newer than the current revision, plus a list of resource strings. Invalid UTF-8, JSON or shapes raise `ValueError` (`Ungültiges Gusto-Änderungsjournal`); malformed events are rejected before replay or compaction.

## Writing

`_record_change(resources)` keeps only `catalog`, `log`, `favorites`, `shopping` (sorted); with none left it returns the current state without writing. Under the `events` lock it appends `revision + 1`, keeps the newest 256 events (`_CHANGE_HISTORY_LIMIT`), moves dropped events into `compacted_revision` (last dropped) and the union `compacted_resources`, and writes with `track_mutation=False`.

Event resources per transaction default to its locks; overrides: archive purge publishes only `catalog`, sourced shopping add, ingredient import and merges publish only `shopping`. Only cooking publishes `log` (together with `catalog`).

## Replay

`change_events(after)` rejects negative or non-integer values. When `after < compacted_revision` it first yields one synthetic event at `compacted_revision` with `compacted_resources`, then retained events above the cursor. If nothing is retained but `revision > after`, it yields one synthetic current event. `after >= revision` yields nothing. The SSE layer sends the current state instead when a client's `after` exceeds the current revision (`service.md`).

Legacy migration (in memory, persisted with the next write): a journal without compacted fields whose first retained event is above revision 1 is treated as compacted up to that point with all four resources, forcing a broad refresh.

## Recovery

A corrupt journal breaks health, page rendering and the outcome of every mutation that writes (`storage.md` -> Constraints & Gotchas). `check` reports the path and loader error in `invalid_data_files`, keeps the remaining diagnostics available, and never repairs or resets the journal. On the server host, prefer restoring a valid journal. Deleting an unrecoverable file deliberately resets the revision to 0; clients holding a higher revision receive the current state and continue. Run repairs only while no service or other Core writer uses that store.

## Tests

`tests/test_api.py` (`check_compacted_event_replay`, `check_event_stream`, `check_post_mutation_failures`), `tests/test_recipes.py` (parallel monotonic revisions, silent no-op merge, invalid journal encoding/JSON/events diagnosed without repair).
