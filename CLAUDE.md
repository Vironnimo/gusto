# Gusto — project context for Claude

Self-hosted, **markdown-based recipe system** for home use. Goal: it runs on a
Raspberry Pi and is reachable from any device on the local network. Built to
replace paper recipes and to enable e.g. meal suggestions based on the last few
days.

## Guiding principles (important!)

1. **Agent-first.** Every feature must be fully usable via the CLI, so agents
   can operate the app exactly like a human. This is the core of the project,
   not a nice-to-have. The actual goal: you ask an agent "what should I cook
   today?" / "what do I do with these ingredients?", and the agent uses the CLI
   to answer.
2. **One source of truth, several thin shells.** All logic lives in
   `recipe/core.py`. The CLI (`recipe/cli.py`) and web (`recipe/web.py`) are only
   shells around it. **Never** build a feature only in the web UI — always in
   core + CLI first. Every CLI command understands `--json`.
3. **Recipes are pure Markdown files. NO frontmatter.** Metadata lives
   separately in `data/recipes.json`, linked via the `slug` (= filename).
4. **NO MCP. Ever.** Explicit, final decision by the user. Do not propose it, do
   not build it.
5. **Ask first, then build.** Do not implement new features / larger steps
   without an explicit go. Present the concept → ask → only then code.
6. **Verify in a real browser.** Check web changes with `scripts/browser_check.py`
   (Playwright) against a throwaway copy of the data and produce screenshots.
   Tests must never modify the real data.
7. **Show it live, don't just describe it.** When something runs, start the
   server yourself and give a clickable link — don't just explain how to start it.
8. **Usage docs exist twice — keep the skill and CLAUDE.md in sync.** The usage
   documentation deliberately lives in two places: here (sections "Usage" /
   "Typical tasks") AND in the skill under `skill/gusto/`. When the CLI or its
   behavior changes, update **both** immediately — the skill must never go stale.

## Data model

| Location | Contents |
|----------|----------|
| `recipes/<slug>.md` | Pure recipe content (Markdown, starts with `# Title`). No frontmatter. |
| `data/recipes.json` | Metadata of all recipes: `slug`, `title`, `tags`, `duration_min`, `servings`, `last_cooked`. |
| `data/categories.json` | Tag categories (facets): `{ "<key>": {"label", "tags": [...]} }`. Maps the flat tags to categories (order = display order). |
| `data/log.json` | Cooking log: `[{ "date": "YYYY-MM-DD", "slug": ... }]`. |

`recipe new` writes both the .md AND the index entry. If a .md is created by
hand, add the entry in `data/recipes.json` and run `recipe check`. When saving
from the web, the first line of the .md is always rewritten as `# {title}` (the
title is its own form field, not in the body).

Per recipe, tags stay a **flat list**; their category lives centrally in
`data/categories.json` (so recipe tags stay clean). Tag filters are **facets**:
`--tag`/`?tag=` can be repeated, **OR within** a category and **AND across**
categories. Tags without a category are reported by `recipe check` as
"unsorted"; then sort the tag into `categories.json`.

## Usage

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[web]"        # the pure CLI needs no dependencies
recipe serve                   # web on 0.0.0.0:8000 (across the LAN)
python -m recipe <command>     # CLI, if not installed
```

All commands understand `--json` (machine-readable, for agents). `RECIPE_HOME`
(env) relocates the data folder (`recipes/` + `data/`), handy on the Pi.

```
recipe list   [--tag T ...] [--max-time N]    Filter; --tag repeatable/comma-separated
recipe search "<terms>" [--match any|all] [--tag T ...]   Full-text (incl. ingredients)
recipe tags   [--all]                          Show tag categories (facets)
recipe show   <slug>                          Print a recipe (--json: incl. content)
recipe new    "<Title>" [--tags a,b] [--duration N] [--servings N]
recipe edit   <slug>                          Open the .md in the editor
recipe cooked <slug> [--date YYYY-MM-DD]      Record in the cooking log
recipe log    [--days N]                       Show the cooking log
recipe set    <slug> [--title ...] [--tags a,b] [--duration N] [--servings N]
recipe delete <slug>                           Delete a recipe
recipe suggest [--days N] [--limit N]          Candidates for the next meal
recipe check                                   Consistency index <-> .md (+ unsorted tags)
recipe serve  [--host H] [--port N]            Start the web UI (LAN)

recipe shopping list [--pending]                  Show the shopping list
recipe shopping add "<text>" [--quantity M]        Add an entry
recipe shopping add-recipe <slug>                   All ingredients of a recipe -> list
recipe shopping check|uncheck <id>              Check / uncheck an entry
recipe shopping remove <id>                     Remove an entry (tombstone)
recipe shopping clear                           Remove done (checked) entries
```

## Typical tasks (agent)

**"What should I eat today?"**
1. `recipe log --days 7 --json` → what was cooked recently.
2. `recipe suggest --json` → what hasn't been cooked for a while.
3. Decide with variety (not pasta three times in a row); honor time/diet via
   `--max-time` / `--tag`. `suggest` is deliberately simple — the "intelligence"
   comes from the agent combining `log`, `search` and `list`.

**"What can I make with these ingredients?"**
- `recipe search "haehnchen paprika" --match any --json` — also searches the
  ingredients in the text of the `.md` files.

**Filter by tags (facets):** `recipe list --tag italienisch --tag pizza --tag
vegetarisch --json` — OR within a category, AND across categories.

**Digitize a recipe:** `recipe new "Title" --tags … --duration …`, then write the
content into `recipes/<slug>.md` (from code: `core.add_recipe(…, content=…)`).

**Shopping list as a Telegram checklist:** the tappable checkboxes are rendered
by the Telegram client app (inline-keyboard + `callback_query` — not Gusto).
Gusto only supplies data via the CLI: render from `recipe shopping list --json`
(a flat array; ⬜/✅ from `checked`), a tap toggles via `recipe shopping
check|uncheck <id>` (no `toggle` — decide from `checked`), add via `recipe
shopping add`, clear done via `recipe shopping clear`. Client-side contract:
[docs/telegram-shopping-handoff.md](docs/telegram-shopping-handoff.md).

## Important files

- `recipe/core.py` — all logic (load/save, search, log, suggestions)
- `recipe/cli.py` — the CLI
- `recipe/web.py` — FastAPI app (server-rendered, Jinja2)
- `recipe/templates/`, `recipe/static/` — UI + CSS/JS
- `scripts/browser_check.py` — end-to-end browser test (Playwright)
- `deploy/gusto.service` — systemd unit for the Pi
- `skill/gusto/` — skill (SKILL.md + `references/cli.md`) for operating the
  system via the CLI; mirrors "Usage" / "Typical tasks" — **keep in sync** (principle 8).

## Commits

- Conventional format: `<type>(<scope>): <what>` — lowercase, ≤72 chars, no
  trailing period. Types: `feat` `fix` `docs` `refactor` `perf` `test` `chore`.
  Breaking change → `!` (e.g. `feat(core)!: …`).
- One logical unit per commit; never batch unrelated changes; never commit
  broken code.
- When you finish a task, commit it (the user may also ask you to commit
  mid-way); you don't need to wait to be asked.

## Tech / pitfalls

- Python (stdlib-only core/CLI). Web: FastAPI + uvicorn + Jinja2 + markdown +
  `python-multipart` (forms). Tests: Playwright.
- Starlette ≥1.3: the signature is `TemplateResponse(request, "name.html", {...})`
  — `request` MUST be the first argument.
- Windows console (cp1252): stdout in CLI/tests is switched to UTF-8, otherwise
  characters like "✓" break.

## Design

"Gusto": warm cookbook editorial, deliberately not a dashboard. Cream paper
with fine grain, paprika red as accent, herb green. Fraunces (display serif) +
Hanken Grotesk (text). Numbered steps with large serif numerals, cards with a
staggered fade-in. UI and data fields are German.

## Status & roadmap

**Done:** data model, core, CLI, web UI (list/search incl. **live search** while
typing, **tag facets**: multi-select grouped by category, OR within / AND across
categories — `data/categories.json`, `recipe tags`), recipe view,
create/edit/delete, "cooked today", suggestions, log, 404 page, Pi deployment
(systemd), browser test. **Shopping list** (core/CLI/web, `recipe shopping …`)
incl. offline-capable **PWA**: service worker (app-shell cache, offline fallback)
+ full-state sync via "last writer wins" + tombstones. Sync contract:
[docs/sync-kontrakt.md](docs/sync-kontrakt.md). Tests: `scripts/browser_check.py`
(web incl. no-JS fallback) + `scripts/pwa_check.py` (offline, two-device merge,
service worker).

**In progress:**
- **Telegram shopping checklist** (tap-to-check on the phone) — Gusto side is
  ready (`recipe shopping …`, verified); the Telegram client (inline-keyboard +
  `callback_query`) is built in the agent app. Contract:
  [docs/telegram-shopping-handoff.md](docs/telegram-shopping-handoff.md).

**Under discussion / planned:**
- Agent-managed recipe images.
- Weekly plan.
