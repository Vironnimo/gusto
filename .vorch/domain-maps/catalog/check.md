# Catalog: consistency check

Contract, coverage, extension and repair paths of `checks.check()`.

## Contract

`checks.check()` (API `check.run`; CLI `check`; `check --offline` runs Core in the CLI process against its own data root) returns counts (`recipe_count`, `archive_count`, `favorite_need_count`), one list per diagnostic, `errors` and `warnings` as `[{code, items}]` for non-empty lists, and `ok` = no errors. The CLI exits 1 when `ok` is false, also with `--json`.

- Warnings, which never affect `ok`: `uncategorized_tags`, `unresolved_log_references`. Every other diagnostic is a hard error.
- Read-only: never writes, never repairs, takes no lock. It can observe a concurrent multi-file mutation mid-way; re-run before acting on a finding.
- Robust: loader failures (index, categories, log, favorites, shopping, change journal) and unreadable Markdown or directories become messages in `invalid_data_files`, and the remaining diagnostics still run. The journal check uses the same read-only loader as health and events (`storage/change-journal.md`); it does not reset or repair `data/changes.json`. Not checked: settings, lock files.

## Coverage

- Active recipes: duplicate slugs, Markdown without index entry and the reverse (a directory named `<slug>.md` does not count), H1 title drift.
- Active images: missing referenced files, unreferenced files, image folders without an active recipe (except `_favorites`), cover ids that are missing while images exist or name no stored image.
- Archive: invalid entries, files directly under `archive/`, stale transactions, missing Markdown, title drift, image files, covers.
- Cross-store: slugs both active and archived, visible shopping sources and log slugs naming no known recipe.
- Favorites: names or aliases shared by several needs after normalization, missing or unreferenced product image files.

## Adding a diagnostic

Add the list to the result and classify it in `hard_error_fields` or `warning_fields` (an unclassified list never reaches `errors`, `warnings` or `ok`). Add its German label in `cmd_check` in `gusto/cli.py`, document the JSON shape in `skill/gusto/references/cli.md`, and cover it in `tests/test_recipes.py` or the owning domain's test.

## Repair paths through the service

From Core behavior:
- `title_mismatches`: `set <slug> --title "<title>"` re-applies the title and rewrites the H1.
- `missing_files`: `content set <slug>` recreates the Markdown.
- `invalid_cover_images` on a recipe with images: `image cover <slug> <id>`.
- `missing_image_files`: `image remove <slug> <id>` drops the metadata.
- `uncategorized_tags`: `categories assign <key> <tag> ...`.

Orphaned Markdown, image files or folders, duplicate slugs, invalid or stale archive entries, active/archive conflicts and invalid data files have no service command; inspect them on the server host. `new` refuses to adopt an orphaned slug.
