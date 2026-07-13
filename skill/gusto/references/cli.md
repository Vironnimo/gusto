# Gusto CLI — Full Reference

All commands: `python -m recipe <command>` (or `recipe <command>` after
`pip install -e .`). Every command accepts `--json` for machine-readable output.

## Commands

### Reading

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
  Consistency of index ↔ `.md` files, plus tags not assigned to a category.

### Writing

- `new "<title>" [--tags a,b] [--duration N] [--servings N] [--edit] [--json]`
  Create the `.md` (a template) **and** the index entry. `--edit` opens
  `$EDITOR` (interactive — skip it when headless; write the file directly).
- `set <slug> [--title ...] [--tags a,b] [--duration N] [--servings N] [--json]`
  Update metadata. `--tags` **replaces** the whole list.
- `edit <slug>`
  Open the `.md` in `$EDITOR`. Interactive; for a headless agent, rewrite
  `recipes/<slug>.md` directly instead.
- `cooked <slug> [--date YYYY-MM-DD] [--json]`
  Add a log entry (default: today) and bump `last_cooked`.
- `delete <slug> [--json]`
  Remove the `.md` + index entry. Log history is kept.

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
  "last_cooked": "2026-06-21"
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
  "uncategorized_tags": []
}
```

- `orphaned_files`: a `.md` with no index entry.
- `missing_files`: an index entry with no `.md`.
- `uncategorized_tags`: used tags not in any category.

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

| File | Content |
|---|---|
| `recipes/<slug>.md` | Pure markdown. First line `# Title`, then `## Zutaten` (bullets) and `## Zubereitung` (numbered). **No frontmatter.** |
| `data/recipes.json` | Metadata array — the index. |
| `data/categories.json` | `{ key: { label, tags[] } }`; order = display order. |
| `data/log.json` | `[ { date, slug } ]`. |
| `data/shopping_list.json` | `{ items: [ … ] }` (includes tombstones). |

## Worked example — "what should I cook?"

```
$ python -m recipe log --days 7 --json
[ { "date": "2026-06-21", "slug": "spaghetti-carbonara" },
  { "date": "2026-06-18", "slug": "rotes-linsen-dal" } ]

$ python -m recipe suggest --json
[ { "slug": "ofengemuese-feta", "title": "Ofengemüse mit Feta",
    "tags": ["vegetarisch", "ofen", "einfach"], "last_cooked": null } ]
```

Reasoning: pasta (carbonara) was just on, so steer away from it — e.g.
*Ofengemüse mit Feta* (vegetarian, never cooked). If the user said "schnell",
narrow with `list --max-time 25 --json` first.
