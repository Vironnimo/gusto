# Catalog: archive lifecycle

Snapshot format, the archive/restore/purge transactions and recovery from interrupted ones.

## Snapshot format

`archive/<slug>/recipe.md`, `metadata.json` as `{"version": 1, "archived_at": "<UTC ISO>", "recipe": <full index entry>}`, and an optional `images/` holding the whole former `images/<slug>/` folder, including unreferenced files. The loader requires version 1, a metadata slug equal to the folder name and a parseable `archived_at`. Listings skip dot-folders and sort newest first.

## Operations

- Archive (`archive_recipe`, compatibility name `delete_recipe`): requires the active entry and its Markdown file and refuses an existing snapshot. It builds `archive/.<slug>.archive.<random>/` (counted as a change), moves `recipe.md` and the image folder in, writes `metadata.json`, renames the folder to `archive/<slug>`, then saves the index without the entry (commit point). Earlier failures attempt to move the files back and remove the folder; rollback failure remains an uncertain outcome (`catalog.md` -> Conventions).
- Restore: requires the snapshot, no active entry and no active Markdown or image folder for the slug. It renames the snapshot to `archive/.<slug>.restore.<hex>`, moves Markdown and images back, appends the archived entry to the index (commit point; `last_cooked` is the value at archiving), then deletes the transaction folder. Earlier failures attempt to move everything back; cleanup failure after the commit leaves the restored recipe active and reports an uncertain outcome.
- Purge: holds `catalog` and `shopping` locks and refuses while any visible shopping item has the slug as source (the error reports the count). It renames the snapshot to `archive/.<slug>.purge.<hex>` (counted as a change) and deletes it recursively. It publishes only `catalog`. Irreversible; the API requires `yes: true`.

## Interrupted transactions

Any dot-folder left under `archive/` is reported by `check` as `stale_archive_transactions` (hard error). Nothing cleans it automatically and no service command removes it. Inspect it on the server host before deleting: an interrupted `.archive.` or `.restore.` folder can hold the only copy of the recipe Markdown, images or metadata (accompanying `missing_files` or `orphaned_*` findings show where the rest is). A `.purge.` folder is a snapshot already hidden from the archive; deleting it completes the purge. Act only while no mutation is running.

## Tests

`tests/test_recipes.py` (archive, restore, purge, interrupted purge, transaction failure matrix), `tests/test_concurrency.py` (sync during restore).
