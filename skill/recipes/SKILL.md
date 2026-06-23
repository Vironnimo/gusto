---
name: recipes
description: >-
  Operate the Küchenbuch recipe system from the command line to answer cooking
  questions and manage recipes. Trigger when the user (in the recipes repo)
  asks what to cook or eat ("was soll ich heute essen", "was koche ich heute",
  "what should I cook"), what to make with ingredients on hand ("was mache ich
  mit ...", "was kann ich mit diesen Zutaten kochen"), or wants to find, show,
  add, edit, or delete recipes, filter by tags/categories (Küche, Art,
  Ernährung, Eigenschaft; vegetarisch, vegan, halal …), manage the shopping
  list ("Einkaufsliste"), or record/read what was cooked ("gekocht", Logbuch).
  Drives the `recipe` CLI (`python -m recipe`), always with `--json` for
  parsing.
---

# Küchenbuch — Rezept-System per CLI

## Overview

The Küchenbuch is a self-hosted, markdown-based recipe system. Recipes are
plain `.md` files; all metadata lives in JSON. The `recipe` CLI is the single
interface — built so an agent operates it exactly like a human, and every
command supports `--json`. The system stays deliberately simple: the *reasoning*
behind a suggestion ("what should I cook?") comes from **you** combining `log`,
`suggest`, `search`, and `list` — not from a clever algorithm.

## When to Use

Use when, working in the recipes repo, the user wants to:

- Decide a meal — "was soll ich heute essen/kochen", "what should I cook",
  ideally without repeating the last days.
- Cook from ingredients on hand — "was mache ich mit Paprika und Hähnchen".
- Find, show, add, edit, or delete recipes.
- Filter by tags/categories (Küche, Art, Ernährung, Eigenschaft; vegetarisch,
  vegan, halal …).
- Manage the shopping list ("Einkaufsliste").
- Record what was cooked or read the cooking log ("Logbuch").

Do not use for:

- Developing the app itself (changing `core`/`web`/CLI code) — that is normal
  coding work; see the project `CLAUDE.md`.
- Anything involving MCP — the project forbids MCP entirely, by explicit user
  decision. Never propose or build it.

## Invocation

- Run `python -m recipe <command>` (or `recipe <command>` if `pip install -e .`
  was done). The pure CLI needs no dependencies.
- Append `--json` whenever you will parse the result. Without it, output is
  formatted for humans.
- `RECIPE_HOME` (env var) relocates the data folder (`recipes/` + `data/`);
  honor it if set (e.g. on the Pi).
- Read commands (`list`, `search`, `show`, `tags`, `log`, `suggest`, `check`)
  are safe to run freely. Treat `new`, `set`, `delete`, `cooked`, and `einkauf`
  writes as state changes.

## Core Workflows

### "Was soll ich heute essen?"

The CLI gives candidates; you provide judgment.

1. `python -m recipe log --days 7 --json` — what was cooked recently.
2. `python -m recipe suggest --json` — not cooked in the last 7 days,
   longest-ago first.
3. Recommend with **variety**: avoid repeating the recent cuisine/`tags`; honor
   any constraint the user named (time → `list --max-time 25`, diet →
   `--tag vegetarisch`).

Report 2–3 concrete picks, each with a one-line reason — not the raw list.

### "Was kann ich mit diesen Zutaten kochen?"

- `python -m recipe search "haehnchen paprika" --match any --json`
- `search` covers title, tags **and the full recipe body** (the ingredients in
  the `.md`). Use `--match all` to require every term, `any` for at-least-one.

### Filter by tags (facets)

- Discover the vocabulary first: `python -m recipe tags --json` (tags in use) or
  `--all` (every defined tag).
- Combine tags: `python -m recipe list --tag italienisch --tag pasta --json`.
  Rule: **OR within a category, AND across categories.** So `--tag italienisch
  --tag tuerkisch` = either cuisine; adding `--tag vegetarisch` narrows to the
  vegetarian ones. `--tag` is repeatable and comma-separated.

### Add a recipe

1. `python -m recipe new "Ofen-Lachs" --tags ofen,schnell --dauer 25 --portionen 2 --json`
   — writes both the `.md` (a template) and the index entry in one step.
2. Write the real content into `recipes/<slug>.md`: keep the first line
   `# Ofen-Lachs`, then `## Zutaten` (bullet list) and `## Zubereitung`
   (numbered). **No frontmatter.**
3. If you used a tag not yet in a category, add it under the right key in
   `data/categories.json`, then run `python -m recipe check` (it reports
   `unsortierte_tags`).

From Python you can do it atomically: `core.add_recipe(titel, tags=...,
inhalt=full_markdown)`.

### Edit, log, shopping list

- Metadata: `recipe set <slug> --tags a,b --dauer N` (`--tags` replaces the
  whole list). Body: rewrite `recipes/<slug>.md` (first line stays `# Titel`).
- Record a cook: `recipe cooked <slug>` (adds a log entry + bumps
  `zuletzt_gekocht`).
- Shopping list: `recipe einkauf rezept <slug>` (all ingredients of a recipe),
  `einkauf add "<text>"`, `einkauf list [--offen]`,
  `einkauf check|uncheck|remove <id>`, `einkauf clear`.

## Data Model (know it, don't fight it)

- `recipes/<slug>.md` — pure markdown, starts with `# Titel`. **Never add
  frontmatter.**
- `data/recipes.json` — metadata index: `slug, titel, tags[], dauer_minuten,
  portionen, zuletzt_gekocht`.
- `data/categories.json` — tag → category map: `{ key: { label, tags[] } }`
  (order = display order).
- `data/log.json`, `data/einkaufsliste.json` — cooking log / shopping list.

The `slug` links `.md` ↔ index. Prefer CLI commands over hand-editing JSON so
invariants hold; if you do hand-edit, run `recipe check` afterward.

## Supporting Files

- `references/cli.md`: full command + flag reference and the exact `--json`
  output shapes. Read it when you need a flag or field you don't remember.

## Common Pitfalls

1. Parsing human-formatted output — always pass `--json` when consuming a result.
2. Putting metadata or frontmatter into the `.md` — the body is content only;
   metadata belongs in `recipes.json`.
3. Hand-writing a `.md` with no index entry — use `recipe new` (writes both), or
   add the entry and run `recipe check`.
4. Assuming one tag = one filter — tags are facets: multiple `--tag`, OR within
   a category, AND across categories.
5. Forgetting to categorize a new tag — `recipe check` flags `unsortierte_tags`;
   add it to `categories.json`.
6. Proposing or building MCP — forbidden in this project.
7. Treating `suggest` as the final answer — it is a simple rule; you add the
   variety / diet / time reasoning.

## Output Contract

- Produce: the exact `recipe` commands you ran (or would run); for open
  questions like "what should I eat", a short reasoned recommendation, not a raw
  dump.
- Preserve: data integrity — markdown-only bodies, index and `.md` in sync, new
  tags categorized.
- Report: what changed (recipes created/edited, cooks logged, shopping items)
  and the slugs involved.
