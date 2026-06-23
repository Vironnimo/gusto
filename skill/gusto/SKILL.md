---
name: gusto
description: Operate the Gusto recipe system through its `recipe` CLI. Use to answer "what should I cook?" and "what can I make with these ingredients?", to find, show, add, edit or delete recipes, filter recipes by tag categories, manage the shopping list, and record or read the cooking log. Drive the `recipe` CLI and pass `--json` whenever you parse output.
---

# Gusto — operating the recipe system via the CLI

The `recipe` CLI is the only interface you need; every command takes `--json`
for machine-readable output. `suggest` is intentionally dumb — the judgment for
"what should I cook?" is yours, from combining `log`, `suggest`, `search` and
`list`. (Recipe content and tag values are German; commands and JSON keys are
English.)

## Invocation

- `python -m recipe <command> --json` (or `recipe <command>` if installed).
- Side-effect-free: `list`, `search`, `show`, `tags`, `log`, `suggest`, `check`.
- Change state: `new`, `set`, `delete`, `cooked`, `shopping …`.

## Workflows

**"What should I cook?"**
1. `recipe log --days 7 --json` — what was cooked recently.
2. `recipe suggest --json` — not cooked lately, longest-ago first.
3. Choose with variety (don't repeat the recent cuisine); honor any constraint
   given: time `--max-time 25`, diet `--tag vegetarisch`. Answer with 2–3
   concrete picks, one reason each — not the raw list.

**"What can I make with X?"**
- `recipe search "haehnchen paprika" --match any --json` — searches title, tags
  and the recipe body (ingredients). `--match all` requires every term.

**Filter by tags (facets)**
- `recipe tags --json` lists the categories and their tags.
- `recipe list --tag italienisch --tag pasta --json` — OR within a category,
  AND across categories. `--tag` repeats and is comma-separated.

**Add a recipe**
- `recipe new "<title>" --tags a,b --duration 25 --servings 2 --json` writes the
  `.md` (a template) and the index entry.
- Then write the body into `recipes/<slug>.md`: first line `# <title>`, then a
  `## Zutaten` bullet list and a `## Zubereitung` numbered list. No frontmatter.
  (From Python in one step: `core.add_recipe(title, tags=..., content=md)`.)
- Used a new tag? add it under a category in `data/categories.json`;
  `recipe check` reports uncategorized tags.

**Edit · log · shopping**
- `recipe set <slug> --tags a,b --duration N` (`--tags` replaces the list);
  edit the body by rewriting `recipes/<slug>.md`.
- `recipe cooked <slug>` — record a cook (updates the log + `last_cooked`).
- `recipe shopping add-recipe <slug>` (all ingredients), `shopping add "<text>"`,
  `shopping list [--pending]`, `shopping check|uncheck|remove <id>`,
  `shopping clear`.

## Data model

- `recipes/<slug>.md` — recipe content, no frontmatter.
- `data/recipes.json` — metadata: `slug, title, tags[], duration_min, servings, last_cooked`.
- `data/categories.json` — `{ key: { label, tags[] } }` (order = display order); defines the facets.
- `data/log.json`, `data/shopping_list.json`.

The `slug` links `.md` ↔ index. Prefer the CLI over hand-editing JSON; after a
manual edit run `recipe check`.

## Pitfalls

- Parsing output? always `--json`.
- The `.md` holds content only — never put metadata/frontmatter in it.
- Don't hand-write a `.md` without an index entry — use `recipe new`, or add the
  entry and run `recipe check`.
- Tags are facets: multiple `--tag`, OR within a category, AND across.
- Never propose or build MCP — forbidden in this project.

## Full reference

`references/cli.md` — every command, flag and the exact `--json` shapes.
