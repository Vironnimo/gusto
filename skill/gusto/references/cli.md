# Gusto CLI — Full Reference

All commands: `python -m gusto <command>` in a development checkout. Installed
release executables live under `%LOCALAPPDATA%\Programs\Gusto\Scripts\gusto.exe`
on Windows or `~/.local/opt/gusto/bin/gusto` on Linux. Every command accepts
`--json` for machine-readable output.

## Commands

### Reading

- `home [--json]`
  Show the active store root and whether it came from `GUSTO_HOME`, the normal
  platform user-data directory, or the compatibility fallback for an existing
  checkout. JSON also includes `platform_default`.
- `list [--tag T ...] [--max-time N] [--json]`
  List/filter recipes. `--tag` is repeatable **and** comma-separated
  (`--tag a --tag b` ≡ `--tag a,b`). `--max-time` caps `duration_min`.
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
  Cooking log. Human output is newest-first.
- `suggest [--days N] [--limit N] [--json]`
  Recipes not cooked in the last N days (default 7), longest-ago first.
- `check [--json]`
  Consistency of index ↔ `.md` files, recipe image metadata ↔ stored files,
  preferred-product aliases/images, plus tags not assigned to a category.

### Writing

- `new "<title>" [--tags a,b] [--duration N] [--servings N] [--edit] [--json]`
  Create the `.md` (a template) **and** the index entry. `--edit` opens
  `$EDITOR` (interactive — skip it when headless; write the file directly).
- `set <slug> [--title ...] [--tags a,b]
  [--duration N|--clear-duration] [--servings N|--clear-servings] [--json]`
  Update metadata. `--tags` **replaces** the whole list; the clear flags remove
  optional duration or serving values.
- `edit <slug>`
  Open the `.md` in `$EDITOR`. Interactive; for a headless agent, rewrite
  `recipes/<slug>.md` directly instead.
- `cooked <slug> [--date YYYY-MM-DD] [--json]`
  Add a log entry (default: today) and bump `last_cooked`.
- `delete <slug> [--json]`
  Remove the `.md`, index entry, and all stored images. Log history is kept.

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
  cannot belong to two needs.
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
- `shopping add "<text>" [--quantity M] [--json]` — returns the created item.
- `shopping add-recipe <slug> [--json]` — add all ingredients of a recipe
  (`source = slug`); returns the array of created items.
- `shopping check <id> [--json]` / `shopping uncheck <id> [--json]` — return the
  updated item; an unknown `id` prints to stderr and exits `1`.
- `shopping remove <id> [--json]` — tombstone (sync-safe; never hard-deleted).
- `shopping clear [--json]` — tombstone all checked items; returns `{ "removed": N }`.

### Server

- `serve [--host H] [--port N] [--reload]` — start the web UI
  (default `0.0.0.0:8000`, reachable across the LAN).

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

`show --json` adds `"content": "<full markdown of the .md>"`.

`tags --json`:

```json
[ { "key": "cuisine", "label": "Küche", "tags": ["italienisch", "indisch"] } ]
```

`check --json`:

```json
{
  "recipe_count": 3,
  "orphaned_files": [],
  "missing_files": [],
  "uncategorized_tags": [],
  "orphaned_image_folders": [],
  "orphaned_image_files": [],
  "missing_image_files": [],
  "invalid_cover_images": [],
  "favorite_need_count": 1,
  "duplicate_favorite_aliases": [],
  "orphaned_favorite_image_files": [],
  "missing_favorite_image_files": []
}
```

- `orphaned_files`: a `.md` with no index entry.
- `missing_files`: an index entry with no `.md`.
- `uncategorized_tags`: used tags not in any category.
- The image fields report folders without recipes, files without metadata,
  missing referenced files, and cover ids that do not point to an image.
- The favorite fields report ambiguous aliases and product-image files that do
  not match the shared preference catalog.

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
`add-recipe` an array; `clear` returns `{ "removed": N }`. Rendering the list as
a tappable **Telegram checklist** (inline-keyboard + `callback_query` in the
client app): see `docs/telegram-shopping-handoff.md`.

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
  it under the right key in `categories.json` to make it filterable as a facet.

## Data files

Resolve the root with `gusto home --json`. Normal installs use
`%LOCALAPPDATA%\Gusto` on Windows or `$XDG_DATA_HOME/gusto` /
`~/.local/share/gusto` on Linux; `GUSTO_HOME` is the explicit override.

| File | Content |
|---|---|
| `recipes/<slug>.md` | Pure markdown. First line `# Title`, then `## Zutaten` (bullets) and `## Zubereitung` (numbered). **No frontmatter.** |
| `images/<slug>/` | Recipe images copied into and owned by Gusto. |
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
narrow with `list --max-time 25 --json` first.
