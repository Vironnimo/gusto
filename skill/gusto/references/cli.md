# Gusto CLI — Full Reference

All commands: `python -m gusto <command>` in a development checkout. Installed
releases use `gusto <command>` in a newly opened Windows console or
`~/.local/opt/gusto/bin/gusto <command>` on Linux. Every command accepts `--json`
for machine-readable output.

Expected command failures with `--json` return
`{ "ok": false, "error": "<German message>" }` on stdout and exit `1`.
`check --json` instead returns its full diagnostics with `ok:false` and exit
`1` when it finds hard integrity errors. Argument and usage errors from the
parser remain plain stderr and exit `2`.

## Commands

### Reading

- `home [--json]`
  Show the active store root and whether it came from `GUSTO_HOME`, the
  instance's `gusto.settings.json`, the normal platform user-data directory, or
  the compatibility fallback. JSON also includes `platform_default` and, when
  present, `settings_path`.
- `list [--tag T ...] [--max-time N] [--json]`
  List/filter recipes. `--tag` is repeatable **and** comma-separated
  (`--tag a --tag b` ≡ `--tag a,b`). `--max-time` caps `duration_min` and
  excludes recipes whose duration is unknown; the cap must be positive.
- `search "<query>" [--match any|all] [--tag T ...] [--max-time N] [--json]`
  Full-text over title, tags, **and the markdown body** (so ingredients match).
  `any` (default) = any term present; `all` = every term present.
- `show <slug> [--json]`
  Print a recipe. With `--json`, the object additionally carries `content` (the
  full markdown).
- `tags [--all] [--json]`
  Tag categories (facets). Default: only tags actually used; `--all`: every
  defined category/tag.
- `log [--days N] [--json]`
  Cooking log. Human output is newest-first and labels archived recipes. `N`
  must be positive.
- `suggest [--days N] [--limit N] [--json]`
  Recipes not cooked in the last positive N days (default 7), longest-ago
  first. `limit` must be nonnegative; `0` returns an empty array.
- `check [--json]`
  Full active/archive H1, file, image, cover, cross-reference, and
  preferred-product integrity plus organization warnings. Hard errors set
  `ok:false` and exit 1; uncategorized tags and unresolved historical log slugs
  are warnings and keep exit 0.

### Writing

- `new "<title>" [--tags a,b] [--duration N] [--servings N] [--edit] [--json]`
  Create the `.md` (a template) **and** the index entry. `--edit` opens
  `$EDITOR` (interactive — skip it when headless; write the file directly).
  Tags without a named category add a structured `warnings` entry to JSON and
  a warning line to human output; they are still stored in the `Sonstige` facet.
- `set <slug> [--title ...] [--tags a,b]
  [--duration N|--clear-duration] [--servings N|--clear-servings] [--json]`
  Update metadata. `--title` also rewrites the first Markdown H1. `--tags`
  **replaces** the whole list; the clear flags remove
  optional duration or serving values. At least one change is required. A
  `--tags` change uses the same uncategorized-tag warning contract as `new`.
- `edit <slug> [--json]`
  Open the `.md` in `$EDITOR`. Interactive; for a headless agent, first read
  `gusto home --json`, then rewrite `<path>/recipes/<slug>.md` directly. Never
  assume the checkout's `recipes/` directory is the active store. JSON waits
  until the editor exits and returns `slug`, absolute `path`, `editor`, and
  `exit_code`.
- `cooked <slug> [--date YYYY-MM-DD] [--json]`
  Add a log entry (default: today) and bump `last_cooked`. An explicit date
  must be a valid calendar date in that exact format and cannot be in the future.
- `delete <slug> [--json]`
  **Reversibly archive**, rather than destroy, the Markdown, full metadata, and
  all owned images. Log and shopping records stay in their own stores and
  resolve the archived slug. JSON returns `slug`, `archived:true`, and
  `archived_at`.

### Recipe archive (`archive`)

- `archive list [--json]` — list complete archived recipe metadata, newest
  archive first.
- `archive show <slug> [--json]` — print archived Markdown; JSON returns recipe
  metadata plus `archived_at` and `content`.
- `archive restore <slug> [--json]` — move the complete snapshot back to the
  active catalog. JSON is `{ "restored": true, "recipe": { ... } }`.
- `archive purge <slug> --yes [--json]` — permanently destroy the snapshot.
  The explicit flag is mandatory. Purge refuses while any visible shopping
  items still reference the slug. JSON is `{ "slug": "...", "purged": true }`.

### Recipe images (`image`)

- `image list <slug> [--json]` — list every image and the selected cover.
- `image add <slug> <path> [--role R] [--caption TEXT] [--cover] [--json]` —
  copy an image into Gusto. The first image automatically becomes the cover.
- `image set <slug> <id> [--role R] [--caption TEXT] [--json]` — update free-form
  purpose and/or caption.
- `image cover <slug> <id> [--json]` — select an existing image as the top image.
- `image remove <slug> <id> [--json]` — delete the stored file and metadata; if
  it was the cover, the first remaining image becomes the new cover.

### Preferred products (`favorites`)

- `favorites list [--json]` — all shared household shopping needs and their
  already-ranked `products` arrays.
- `favorites show <need> [--json]` — one need by id or exact canonical name.
- `favorites match "<shopping text>" [--json]` — the need whose canonical name
  or explicit alias matches after case/whitespace normalization; `null` when
  there is no match. Quantities, punctuation, and words are never guessed.
- `favorites add "<name>" [--alias TEXT ...] [--json]` — create a need with
  optional repeatable exact aliases.
- `favorites set <need> --name "<name>" [--json]` — rename a need. The old name
  remains an alias so existing shopping formulations keep matching.
- `favorites remove <need> [--json]` — remove the need, all product cards, and
  their owned images.
- `favorites alias-add <need> "<text>" [--json]` / `favorites alias-remove
  <need> "<text>" [--json]` — maintain deterministic aliases. A name or alias
  cannot belong to two needs. Re-adding the same alias to its current need is
  an idempotent retry; creating an already existing need remains an error.
- `favorites product-add <need> "<name>" --brand B [--store S] [--note N]
  [--image PATH] [--json]` — append a product at the end of the ranking; images
  are validated and copied into Gusto.
- `favorites product-set <need> <id> [--name N] [--brand B] [--store S]
  [--note N] [--image PATH] [--remove-image] [--json]` — update a product or
  replace/remove its owned image.
- `favorites product-move <need> <id> <position> [--json]` — move to a one-based
  position in the manual household ranking.
- `favorites product-remove <need> <id> [--json]` — remove one product and its
  owned image.

### Shopping list (`shopping`)

- `shopping list [--pending] [--json]` — a **flat array** of items (checked ones
  included, marked `checked:true`); `--pending` = only unchecked.
- `shopping add "<text>" [--quantity M] [--source SLUG] [--json]` — returns the
  created item. `--source` must name an existing recipe, records its slug on the
  item, and makes that visible item participate in the recipe import guard.
- `shopping add-many "<text>" ... [--json]` — add a non-empty free-text group
  in one transaction; returns the array of created items in argument order.
- `shopping add-recipe <slug> [--json]` — add all non-empty parsed ingredients
  of a recipe (`source = slug`); returns the array of created items. It fails
  clearly when none can be parsed or while any visible items from that recipe
  remain, reporting their count and avoiding accidental repeated imports
  without merging quantities.
- `shopping check <id> [--json]` / `shopping uncheck <id> [--json]` — return the
  item. Repeating the requested state does not advance `updated_at`; an unknown
  `id` follows the JSON error contract above.
- `shopping remove <id> [--json]` — tombstone (sync-safe; never hard-deleted).
  Repeating the same removal is a no-op that preserves `updated_at`.
- `shopping remove-done [--json]` — tombstone all checked visible items; returns
  `{ "removed": N }`.
- `shopping clear [--json]` — tombstone every visible item, open or checked;
  returns `{ "removed": N }`. The complete visible list is empty afterward.
  Neither bulk removal has a CLI restore operation.

Mutations sharing the catalog, favorites, or shopping store are serialized
across Gusto processes. Independent adds, image imports, and item-specific
checks may run in parallel without lost updates. Calls with dependencies or
ordering semantics must stay sequential. Direct recipe/category file edits and
interactive `edit` bypass these locks.

### Server

- `serve [--host H] [--port N] [--reload] [--json]` — start the web UI
  (default `0.0.0.0:8000`, reachable across the LAN); port must be 1–65535.
  JSON emits one `{status:"starting", host, port, url, reload}` object before
  the server begins its long-running work.

### Installed application lifecycle

- `uninstall [--keep-data | --delete-data [--yes]] [--dry-run] [--json]` —
  remove a managed installed Gusto runtime. A bare interactive call offers
  **app only**, **app + all data**, or **cancel** and shows the exact runtime and
  active data paths. Data deletion requires the exact interactive confirmation
  `DATEN LÖSCHEN`; non-interactive callers must use `--delete-data --yes`.
  `--keep-data` removes platform autostart and the exact Windows PATH entry but
  preserves recipes, images, shopping data, and cooking history. `--dry-run`
  changes nothing. JSON reports `status`, `application_path`, `data_path`,
  `delete_data`, cleanup booleans, and (when scheduled) `log_path`. The command
  refuses source checkouts, system Python, and unsafe/unrecognized deletion
  roots; do not invoke it without an explicit user request.

## JSON shapes

Recipe (returned by `list`, `search`, `new`, `set` — array or single object):

```json
{
  "slug": "spaghetti-carbonara",
  "title": "Spaghetti Carbonara",
  "tags": ["pasta", "italienisch", "schnell"],
  "duration_min": 25,
  "servings": 2,
  "last_cooked": "2026-06-21",
  "images": [
    {"id": "a04381611b2740d5944abc58993c2643", "filename": "a04381611b2740d5944abc58993c2643.png", "role": "result", "caption": "Serviert", "created_at": "2026-07-13T18:00:00Z"}
  ],
  "cover_image_id": "a04381611b2740d5944abc58993c2643"
}
```

When `new` or `set --tags` stores tags without a named category, its otherwise
unchanged recipe object additionally contains:

```json
{
  "warnings": [{
    "code": "uncategorized_tags",
    "message": "Tags ohne Kategorie (Facet „Sonstige“): schnell",
    "tags": ["schnell"]
  }]
}
```

`show --json` adds `"content": "<full markdown of the .md>"`.

`delete --json`:

```json
{"slug": "spaghetti-carbonara", "archived": true, "archived_at": "2026-07-23T12:00:00.000Z"}
```

`archive list --json` returns recipe objects with an additive `archived_at`;
`archive show --json` additionally adds `content`. Restore and purge use the
wrapper shapes documented under the archive commands above.

`tags --json`:

```json
[ { "key": "cuisine", "label": "Küche", "tags": ["italienisch", "indisch"] } ]
```

`check --json`:

```json
{
  "ok": true,
  "recipe_count": 3,
  "archive_count": 1,
  "duplicate_recipe_slugs": [],
  "orphaned_files": [],
  "missing_files": [],
  "title_mismatches": [],
  "uncategorized_tags": [],
  "orphaned_image_folders": [],
  "orphaned_image_files": [],
  "missing_image_files": [],
  "invalid_cover_images": [],
  "invalid_archive_entries": [],
  "orphaned_archive_root_files": [],
  "stale_archive_transactions": [],
  "missing_archive_files": [],
  "archived_title_mismatches": [],
  "missing_archive_image_files": [],
  "orphaned_archive_image_files": [],
  "invalid_archive_cover_images": [],
  "active_archive_conflicts": [],
  "unresolved_shopping_sources": [],
  "unresolved_log_references": [],
  "favorite_need_count": 1,
  "duplicate_favorite_aliases": [],
  "orphaned_favorite_image_files": [],
  "missing_favorite_image_files": [],
  "errors": [],
  "warnings": []
}
```

- `orphaned_files`: a `.md` with no index entry.
- `missing_files`: an index entry with no `.md`.
- `title_mismatches`: active metadata title and first Markdown H1 disagree.
- `uncategorized_tags`: used tags not in any category.
- The image fields report folders without recipes, files without metadata,
  missing referenced files, and cover ids that do not point to an image.
- The favorite fields report ambiguous aliases and product-image files that do
  not match the shared preference catalog.
- Archive fields apply the same file/H1/image/cover rules to snapshots, detect
  interrupted moves and active/archive slug collisions, and verify visible
  shopping sources. Historical unresolved log slugs are warnings because the
  cooking log is append-only.
- `errors` and `warnings` contain `{code, items}` diagnostics. `ok` is false
  exactly when `errors` is non-empty; JSON is still the complete object and the
  process exits 1.

`log --json`: `[ { "date": "2026-06-21", "slug": "spaghetti-carbonara" } ]`

Shopping-list item:

```json
{
  "id": "a04381611b2740d5944abc58993c2643", "text": "200 g Spaghetti",
  "quantity": "", "checked": false, "source": "spaghetti-carbonara",
  "created_at": "2026-06-23T18:00:00.123Z", "updated_at": "2026-06-23T18:00:00.123Z",
  "deleted": false
}
```

`id` is 32-hex, stable and unique. `list`/`list --pending` return a **flat JSON
array** (no `{items}` wrapper); `check`/`uncheck`/`add` return a single item;
`add-recipe` an array; `remove-done` and `clear` return `{ "removed": N }`.
Rendering the list as a tappable **Telegram checklist** (inline-keyboard +
`callback_query` in the client app): see `docs/telegram-shopping-handoff.md`.

Shopping need returned by `favorites list|show|match`:

```json
{
  "id": "need-id",
  "name": "Pizzateig",
  "aliases": ["1 Rolle Pizzateig"],
  "products": [
    {
      "id": "product-id",
      "name": "Frischer Pizzateig 400 g",
      "brand": "Tante Fanny",
      "store": "REWE",
      "note": "Wird besonders knusprig",
      "image_filename": "owned-image.png",
      "created_at": "2026-07-15T12:00:00.000Z"
    }
  ],
  "created_at": "2026-07-15T12:00:00.000Z"
}
```

The `products` array order is the preference order. `favorites match --json`
prints one such object or `null`; it does not create an alias automatically.

## Tag facets in detail

- Each recipe's `tags` is a **flat list**. The tag → category mapping is central
  in `data/categories.json`.
- Filtering: **OR within a category, AND across categories.** Selected tags with
  no category form one shared "Sonstige" group (OR among themselves).
- Examples:
  - `--tag italienisch --tag indisch` → Italian **or** Indian (both in Küche).
  - `--tag italienisch --tag pasta` → Italian **and** a pasta dish (Küche + Art).
  - `--tag italienisch --tag pasta --tag vegetarisch` → Italian, pasta, **and**
    vegetarian.
- A new tag still stores fine but shows up in `check` as `uncategorized_tags`; add
  it under the right key in `categories.json` to assign it to a named facet.
  Until then it remains filterable in the shared `Sonstige` facet. `new` and
  `set --tags` surface the condition immediately through `warnings`.

## Data files

Resolve the root with `gusto home --json`. Each runtime owns an adjacent
`gusto.settings.json`; the checkout selects the platform data sibling
`gusto-dev`, while installed releases select their production store. A relative
`data_dir` is resolved beside the platform default. `GUSTO_HOME` is the explicit
override used by isolated runs and tests.

| File | Content |
|---|---|
| `recipes/<slug>.md` | Pure markdown. First line `# Title`, then `## Zutaten` (bullets) and `## Zubereitung` (numbered). **No frontmatter.** |
| `images/<slug>/` | Recipe images copied into and owned by Gusto. |
| `archive/<slug>/` | Reversible snapshot: `recipe.md`, `metadata.json`, and owned `images/`. |
| `images/_favorites/` | Preferred-product images copied into and owned by Gusto. |
| `data/recipes.json` | Metadata array — the index, including images and selected cover. |
| `data/categories.json` | `{ key: { label, tags[] } }`; order = display order. |
| `data/log.json` | `[ { date, slug } ]`. |
| `data/shopping_list.json` | `{ items: [ … ] }` (includes tombstones). |
| `data/favorites.json` | `{ needs: [ … ] }` with exact aliases and ranked products. |

## Worked example — "what should I cook?"

```
$ python -m gusto log --days 7 --json
[ { "date": "2026-06-21", "slug": "spaghetti-carbonara" },
  { "date": "2026-06-18", "slug": "rotes-linsen-dal" } ]

$ python -m gusto suggest --json
[ { "slug": "ofengemuese-feta", "title": "Ofengemüse mit Feta",
    "tags": ["vegetarisch", "ofen", "einfach"], "last_cooked": null } ]
```

Reasoning: pasta (carbonara) was just on, so steer away from it — e.g.
*Ofengemüse mit Feta* (vegetarian, never cooked). If the user said "schnell",
narrow with `list --max-time 25 --json` first; recipes without a duration are
excluded from that result.
