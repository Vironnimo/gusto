# Catalog

The catalog domain owns active and archived recipes, their metadata and images, tag facets, cooking history, suggestions, and the `check` consistency diagnostics.

## Overview

Catalog owners live in `gusto/core/catalog/`: `recipes.py` owns active metadata, Markdown and cooking; `categories.py` owns facet configuration; `search.py` combines facet and recipe queries; `snapshots.py` validates archived reads and active/archive references; `archive.py` orchestrates archive, restore and purge transactions; `images.py` owns recipe image transactions. `gusto/core/checks.py` owns cross-store diagnostics. Snapshot reads remain independent of archive transactions so shopping source validation and purge can share them without a dependency cycle. `gusto/api.py` exposes these owners to the CLI, and `gusto/web.py` calls them directly; surfaces only translate input and present results (`service.md`, `cli.md`, `web.md`). Data-root selection, locks, atomic writes and change events are `storage.md`. Shopping consumes catalog lookups and recipe Markdown to import ingredients and resolve item sources. Repository decisions apply: recipes are pure Markdown without frontmatter, and every catalog capability goes through Core, API and CLI with `--json` before web (PROJECT.md).

## Terms

Shared: GLOSSARY -> Recipe archive (deletion means reversible archive).

### Facet

A tag category used as one filter dimension. Filtering and presentation rules are in Interfaces.

### Cover image

The recipe image shown on cards and at the top of the detail page. Selection and fallback rules are in Interfaces.

## Data Model

- `recipes/<slug>.md`: pure Markdown beginning with `# <title>`; no metadata, no frontmatter.
- `data/recipes.json`: list of entries with slug, title, flat tag list, optional `duration_min` and `servings`, `last_cooked`, `images` (id, filename, role, caption, created_at) and `cover_image_id`.
- `data/categories.json`: `{ "<key>": {"label", "tags": [...]} }`. Object order is facet display order, list order is tag display order. Strict: an invalid key, an unknown field or a tag in two categories (case-insensitive) rejects the whole file. Missing file means no categories.
- `images/<slug>/<image-id><ext>`: Gusto-owned copies; their metadata exists only in the index.
- `archive/<slug>/`: reversible snapshot (`catalog/archive.md`).
- `data/log.json`: list of `{date: "YYYY-MM-DD", slug}`, kept sorted by date and append-only: no Core operation removes entries, archive and purge do not rewrite them. `last_cooked` only moves forward.

The slug joins Markdown, metadata, images, archive, log entries, shopping sources and URLs; title edits never rename it. Generated slugs transliterate `ä ö ü ß`, lowercase, replace other characters with `-`, are cut to 150 characters, fall back to `recipe`, and prefix Windows device names with `recipe-`. Explicit slugs must be portable file names of at most 150 characters (no path separators or reserved characters, no leading dot, no trailing dot or space, no Windows device name) and may not be `_favorites`. The 150 bound keeps the atomic writer's temporary file name within Windows limits. Creation refuses a slug that is active, archived, or already has Markdown or an image folder, even without an index entry; diagnose such files with `check` first.

## Interfaces

- Callers receive `Recipe`, `ArchivedRecipe` and `RecipeImage` objects or JSON-ready dicts. `recipe_references()` maps every active or archived slug to `{slug, title, archived}` for other stores; an active recipe wins.
- Title and H1 are one Core-enforced contract. Titles are non-empty, single-line and at most 150 characters after trimming. Creating or updating a title or body normalizes line endings to LF and replaces a first line of bare `#` or `#` plus whitespace with `# <title>`. Otherwise it prepends the title and a blank line to a non-empty body; empty content becomes only the title and a newline. Re-applying the same title repairs drift. A title-only update needs the Markdown file. Surfaces may rebuild the H1 themselves but are not the source of truth.
- Body replacement is `update_recipe(content=...)`, reached through API `recipe.content.set` by CLI `edit` and `content set`. `new` without content writes a template with `## Zutaten` and `## Zubereitung`.
- Duration, servings, time caps and day windows must be positive integers; the suggestion limit must be non-negative (0 returns nothing). `update_recipe` has explicit clear flags; setting and clearing the same value together is rejected.
- Recipe tags are trimmed single-line strings, deduplicated case-insensitively keeping the first spelling; an empty list clears them. Create and tag-changing updates through the API add `warnings: [{code: "uncategorized_tags", message, tags}]` for tags without a named facet; the recipe is saved anyway and the tags share the runtime `Sonstige` facet until `categories assign` maps them.
- Category mutations validate keys (`[a-z0-9][a-z0-9_-]*`; `other` and `__other__` are reserved for `Sonstige`), labels (non-empty, single line) and tags. Assigning moves a tag out of any other category and adopts the existing category spelling, else the first catalog spelling, else the given one. Positions are 1-based. Removing or unassigning never changes recipe tags; removal reports `now_uncategorized`.
- Facet listing (`tag_groups`): in used mode only used tags, case-insensitively deduplicated; named categories in configured order and spelling, empty ones dropped, then one `other`/`Sonstige` group of unassigned tags in first catalog spelling, sorted case-insensitively. The all mode lists defined categories only. Listing never rewrites metadata.
- Search: selected tags are ORed within a facet and ANDed across facets; selected tags without a category form one shared OR group. A time cap excludes recipes with unknown duration; query terms are whitespace-split, lowercased substrings matched against title and tags first and against the Markdown body only for terms metadata did not settle (ALL may combine metadata and body hits). `match` accepts only `any` or `all`.
- Cooking: `recipes.log_cooked` requires an active recipe and an exact `YYYY-MM-DD` calendar date no later than today (default today), and returns the committed date. The API uses that date in its acknowledgement, including across midnight. Every call appends an entry, even for the same slug and date; callers must not retry an uncertain outcome blindly. `load_log(days)` includes the cutoff date (`today - days`); windows beyond the calendar include all history.
- Suggestions: active recipes not cooked within `days` (default 7), ordered by oldest `last_cooked`, never-cooked first. Deliberately simple; meal intelligence belongs to the calling agent combining log, search and list.
- Images: Core copies a local file (the API materializes uploads, `service.md`). It accepts `.jpg`, `.jpeg`, `.png`, `.webp`, `.gif` after extension and header/dimension validation without optional libraries; role defaults to `gallery`; role and caption are single-line and open-ended (`result`, `ingredients`, `step`). The first image or an explicit cover becomes the cover; removing the cover promotes the first remaining image; removing the last image deletes the empty folder. At runtime a missing or stale stored cover selection falls back to the first image, but `check` treats images without a valid stored `cover_image_id` as an error. Upload size limits and browser photo normalization are transport and web rules (`cli.md`, `service.md`, `web.md`).
- Shopping uses `get`, `Recipe.content()` and `recipe_references()`; ingredient parsing under `## Zutaten` belongs to `shopping.md`.

## Conventions

- Every catalog mutation is one storage transaction holding the `catalog` lock across read-modify-write; cooking also holds `log`, purge also `shopping` (`storage.md` -> Interfaces). Never call a locked Core function from inside another.
- In multi-file mutations (create, body or title update, cooking, archive, restore, image add) the `data/recipes.json` save is the commit point. Write owned files first and the index last. A failure before the index commit attempts to roll back the files; only a completed rollback is a clean rejection without a change event. A failed rollback, or a failure after the index commit, surfaces as `PostMutationError`; committed index references keep their files. Remaining changes publish an event unless the journal write itself fails (`storage.md`).
- A failed image import removes its partial copy; failing to remove that copy counts as a storage change (uncertain outcome), not as a clean rejection.
- All catalog files, including `data/categories.json`, are server-owned. Clients use the service; direct edits bypass validation, locking and change events.
- `images/_favorites/` belongs to shopping; recipe image diagnostics ignore it and no slug may claim it.
- `delete` (Core `delete_recipe`, API `recipe.archive`) means reversible archive; only `archive purge` with explicit confirmation destroys a snapshot.
- `check` is read-only, never repairs and takes no lock; integrity errors make `ok` false and the CLI exit 1, while `uncategorized_tags` and `unresolved_log_references` are warnings.

## Constraints & Gotchas

- Separate clients may create recipes, record cooking, replace content or import images concurrently without lost index, log or image metadata; the final order follows lock acquisition.
- Archiving requires the Markdown file; repair a missing file with `content set` first. Restore rejects an active recipe or existing active files with the same slug.
- Cooking, ingredient import and new source attribution require an active recipe. Log entries and visible shopping items are not part of a snapshot; they keep the slug and resolve through `recipe_references()`. A purge can leave raw-slug log history (`unresolved_log_references` warning).
- Search reads Markdown files from disk; ingredient text participates in full-text results.

## Tests

`tests/test_recipes.py` owns the Core catalog contracts in independently seeded behavior functions: creation, search/facets, updates, images and import failures, cooking, archive, diagnostics, journal events and the transaction failure matrix. Cooking boundaries use a fixed date to protect repeated appends, the inclusive cutoff and suggestion order. Category corruption is checked through the public loader and diagnostics. API and CLI exposure: `tests/test_api.py`, `tests/test_cli.py`; web-visible behavior: `scripts/browser_check.py` against throwaway data; contention: `tests/test_concurrency.py`.

## References

Read these only when your task matches - not by default.

- Changing archive, restore or purge, or repairing an interrupted archive transaction -> `catalog/archive.md`
- Changing `check`, adding a diagnostic, or interpreting or repairing a finding -> `catalog/check.md`
- Changing catalog transaction events or diagnosing journal failures -> `storage/change-journal.md`
