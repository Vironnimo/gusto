# Gusto CLI — Full Reference

All commands: `python -m recipe <command>` (or `recipe <command>` after
`pip install -e .`). Every command accepts `--json` for machine-readable output.

## Commands

### Reading

- `list [--tag T ...] [--max-time N] [--json]`
  List/filter recipes. `--tag` is repeatable **and** comma-separated
  (`--tag a --tag b` ≡ `--tag a,b`). `--max-time` caps `dauer_minuten`.
- `search "<query>" [--match any|all] [--tag T ...] [--max-time N] [--json]`
  Full-text over title, tags, **and the markdown body** (so ingredients match).
  `any` (default) = any term present; `all` = every term present.
- `show <slug> [--json]`
  Print a recipe. With `--json`, the object additionally carries `inhalt` (the
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

- `new "<titel>" [--tags a,b] [--dauer N] [--portionen N] [--edit] [--json]`
  Create the `.md` (a template) **and** the index entry. `--edit` opens
  `$EDITOR` (interactive — skip it when headless; write the file directly).
- `set <slug> [--titel ...] [--tags a,b] [--dauer N] [--portionen N] [--json]`
  Update metadata. `--tags` **replaces** the whole list.
- `edit <slug>`
  Open the `.md` in `$EDITOR`. Interactive; for a headless agent, rewrite
  `recipes/<slug>.md` directly instead.
- `cooked <slug> [--date YYYY-MM-DD] [--json]`
  Add a log entry (default: today) and bump `zuletzt_gekocht`.
- `delete <slug> [--json]`
  Remove the `.md` + index entry. Log history is kept.

### Shopping list (`einkauf`)

- `einkauf list [--offen] [--json]` — `--offen` = only unchecked items.
- `einkauf add "<text>" [--menge M] [--json]`
- `einkauf rezept <slug> [--json]` — add all ingredients of a recipe
  (`quelle = slug`).
- `einkauf check <id> [--json]` / `einkauf uncheck <id> [--json]`
- `einkauf remove <id> [--json]` — tombstone (sync-safe; never hard-deleted).
- `einkauf clear [--json]` — tombstone all checked items.

### Server

- `serve [--host H] [--port N] [--reload]` — start the web UI
  (default `0.0.0.0:8000`, reachable across the LAN).

## JSON shapes

Recipe (returned by `list`, `search`, `new`, `set` — array or single object):

```json
{
  "slug": "spaghetti-carbonara",
  "titel": "Spaghetti Carbonara",
  "tags": ["pasta", "italienisch", "schnell"],
  "dauer_minuten": 25,
  "portionen": 2,
  "zuletzt_gekocht": "2026-06-21"
}
```

`show --json` adds `"inhalt": "<full markdown of the .md>"`.

`tags --json`:

```json
[ { "key": "kueche", "label": "Küche", "tags": ["italienisch", "indisch"] } ]
```

`check --json`:

```json
{
  "anzahl_rezepte": 3,
  "verwaiste_dateien": [],
  "fehlende_dateien": [],
  "unsortierte_tags": []
}
```

- `verwaiste_dateien`: a `.md` with no index entry.
- `fehlende_dateien`: an index entry with no `.md`.
- `unsortierte_tags`: used tags not in any category.

`log --json`: `[ { "datum": "2026-06-21", "slug": "spaghetti-carbonara" } ]`

Shopping-list item:

```json
{
  "id": "ab12cd…", "text": "200 g Spaghetti", "menge": "",
  "checked": false, "quelle": "spaghetti-carbonara",
  "erstellt_am": "2026-06-23T18:00:00Z", "geaendert_am": "2026-06-23T18:00:00Z",
  "geloescht": false
}
```

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
- A new tag still stores fine but shows up in `check` as `unsortierte_tags`; add
  it under the right key in `categories.json` to make it filterable as a facet.

## Data files

| File | Content |
|---|---|
| `recipes/<slug>.md` | Pure markdown. First line `# Titel`, then `## Zutaten` (bullets) and `## Zubereitung` (numbered). **No frontmatter.** |
| `data/recipes.json` | Metadata array — the index. |
| `data/categories.json` | `{ key: { label, tags[] } }`; order = display order. |
| `data/log.json` | `[ { datum, slug } ]`. |
| `data/einkaufsliste.json` | `{ items: [ … ] }` (includes tombstones). |

## Worked example — "Was soll ich heute essen?"

```
$ python -m recipe log --days 7 --json
[ { "datum": "2026-06-21", "slug": "spaghetti-carbonara" },
  { "datum": "2026-06-18", "slug": "rotes-linsen-dal" } ]

$ python -m recipe suggest --json
[ { "slug": "ofengemuese-feta", "titel": "Ofengemüse mit Feta",
    "tags": ["vegetarisch", "ofen", "einfach"], "zuletzt_gekocht": null } ]
```

Reasoning: pasta (carbonara) was just on, so steer away from it — e.g.
*Ofengemüse mit Feta* (vegetarian, never cooked). If the user said "schnell",
narrow with `list --max-time 25 --json` first.
