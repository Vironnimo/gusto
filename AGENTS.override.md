# Gusto — project context for Codex

Self-hosted, **markdown-based recipe system** for home use. Goal: it runs on a
Raspberry Pi and is reachable from any device on the local network. Built to
replace paper recipes and to enable e.g. meal suggestions based on the last few
days.

You work in this repository as a standalone agent with your normal tools and skills. You are not a managed node in the Vorch orchestrator system: do not invoke or impersonate its roles under `.opencode/agents/`, and perform investigation, implementation, testing, review, documentation, and git work yourself.

## Read at Session Start

Before doing anything else in every Session, read `.vorch/PROJECT.md` and `.vorch/GLOSSARY.md` completely. Use and maintain the Vorch knowledge base as a standalone agent whenever the work changes Project facts, contracts, terminology, or decisions. Immediately before Domain Map work, read `.vorch/workflows/domain-map-workflow.md` completely.

## Guiding principles (important!)

1. **Agent-first.** Every feature must be fully usable via the CLI, so agents
   can operate the app exactly like a human. This is the core of the project,
   not a nice-to-have. The actual goal: you ask an agent "what should I cook
   today?" / "what do I do with these ingredients?", and the agent uses the CLI
   to answer.
2. **One source of truth, several thin shells.** All logic lives in
   `gusto/core.py`. The CLI (`gusto/cli.py`) and web (`gusto/web.py`) are only
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
8. **Keep every agent guide and the skill in sync.** The usage documentation
   deliberately lives in the agent guides and under `skill/gusto/`. When the CLI
   or its behavior changes, update **every copy** immediately — none of the
   agent-facing references may go stale.

## Data model

| Location | Contents |
|----------|----------|
| `recipes/<slug>.md` | Pure recipe content (Markdown, starts with `# Title`). No frontmatter. |
| `images/<slug>/` | Recipe images copied into and owned by Gusto. |
| `data/recipes.json` | Recipe metadata including `images` and `cover_image_id`. |
| `data/categories.json` | Tag categories (facets): `{ "<key>": {"label", "tags": [...]} }`. Maps the flat tags to categories (order = display order). |
| `data/log.json` | Cooking log: `[{ "date": "YYYY-MM-DD", "slug": ... }]`. |

`gusto new` writes both the .md AND the index entry. If a .md is created by
hand, add the entry in `data/recipes.json` and run `gusto check`. When saving
from the web, the first line of the .md is always rewritten as `# {title}` (the
title is its own form field, not in the body).

Per recipe, tags stay a **flat list**; their category lives centrally in
`data/categories.json` (so recipe tags stay clean). Tag filters are **facets**:
`--tag`/`?tag=` can be repeated, **OR within** a category and **AND across**
categories. Tags without a category are reported by `gusto check` as
"unsorted"; then sort the tag into `categories.json`.

## Usage

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[web]"        # the pure CLI needs no dependencies
gusto serve                   # web on 0.0.0.0:8000 (across the LAN)
python -m gusto <command>     # CLI, if not installed
```

All commands understand `--json` (machine-readable, for agents). `GUSTO_HOME`
(env) relocates the full recipe store (`recipes/` + `images/` + `data/`), handy
on the Pi.

```
gusto list   [--tag T ...] [--max-time N]    Filter; --tag repeatable/comma-separated
gusto search "<terms>" [--match any|all] [--tag T ...]   Full-text (incl. ingredients)
gusto tags   [--all]                          Show tag categories (facets)
gusto show   <slug>                          Print a recipe (--json: incl. content)
gusto new    "<Title>" [--tags a,b] [--duration N] [--servings N]
gusto edit   <slug>                          Open the .md in the editor
gusto cooked <slug> [--date YYYY-MM-DD]      Record in the cooking log
gusto log    [--days N]                       Show the cooking log
gusto set    <slug> [--title ...] [--tags a,b] [--duration N] [--servings N]
gusto delete <slug>                           Delete a recipe
gusto suggest [--days N] [--limit N]          Candidates for the next meal
gusto check                                   Consistency index <-> .md (+ unsorted tags)
gusto serve  [--host H] [--port N]            Start the web UI (LAN)

gusto image list <slug>                       Show cover and gallery images
gusto image add <slug> <path> [--role R] [--caption TEXT] [--cover]
gusto image set <slug> <id> [--role R] [--caption TEXT]
gusto image cover <slug> <id>                 Select the top image
gusto image remove <slug> <id>                Delete one stored image

gusto shopping list [--pending]                  Show the shopping list
gusto shopping add "<text>" [--quantity M]        Add an entry
gusto shopping add-recipe <slug>                   All ingredients of a recipe -> list
gusto shopping check|uncheck <id>              Check / uncheck an entry
gusto shopping remove <id>                     Remove an entry (tombstone)
gusto shopping clear                           Remove done (checked) entries
```

## Typical tasks (agent)

**"What should I eat today?"**
1. `gusto log --days 7 --json` → what was cooked recently.
2. `gusto suggest --json` → what hasn't been cooked for a while.
3. Decide with variety (not pasta three times in a row); honor time/diet via
   `--max-time` / `--tag`. `suggest` is deliberately simple — the "intelligence"
   comes from the agent combining `log`, `search` and `list`.

**"What can I make with these ingredients?"**
- `gusto search "haehnchen paprika" --match any --json` — also searches the
  ingredients in the text of the `.md` files.

**Filter by tags (facets):** `gusto list --tag italienisch --tag pizza --tag
vegetarisch --json` — OR within a category, AND across categories.

**Transfer a recipe from images:** inspect the supplied images yourself, create
or update the recipe content, then attach every useful image with `gusto image
add <slug> <path> --role <purpose> --caption "…"`. Gusto copies and owns the
files. Recipes can have any number of images; the first is the default cover,
and `--cover` or `gusto image cover` selects a different top image. Roles such
as `result`, `ingredients`, or `step` are descriptive and remain open-ended.

**Shopping list as a Telegram checklist:** the agent posts the list via vBot's
`channel_send` tool (inline-keyboard) — one `chk:<id>` button per item (leading
⬜/✅ from `checked`) plus a final `run:done` "Fertig" button. Item taps flip the
glyph visually (vBot's checklist extension — no Gusto write); the **Fertig** tap
wakes the agent with the current button state, which then syncs Gusto (`gusto
shopping check|uncheck <id>` per item; no `toggle` — decide from the ⬜/✅ glyph)
and confirms in chat. "Fertig" saves the checked-state (add `gusto shopping
clear` if it should also remove bought items). Full round-trip in the skill
(`skill/gusto/SKILL.md`); client history: [docs/telegram-shopping-handoff.md](docs/telegram-shopping-handoff.md).

## Important files

- `gusto/core.py` — all logic (load/save, search, log, suggestions)
- `gusto/cli.py` — the CLI
- `gusto/web.py` — FastAPI app (server-rendered, Jinja2)
- `gusto/templates/`, `gusto/static/` — UI + CSS/JS
- `scripts/browser_check.py` — end-to-end browser test (Playwright)
- `deploy/gusto.service` — systemd unit for the Pi
- `skill/gusto/` — skill (SKILL.md + `references/cli.md`) for operating the
  system via the CLI; mirrors "Usage" / "Typical tasks" in the agent guides — **keep every copy in sync** (principle 8).

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
categories — `data/categories.json`, `gusto tags`), recipe view,
create/edit/delete, "cooked today", suggestions, log, 404 page, Pi deployment
(systemd), browser test, and **multiple stored recipe images** with a selected
cover, gallery, free role/caption, and full agent control through `gusto image
…`. **Shopping list** (core/CLI/web, `gusto shopping …`)
incl. offline-capable **PWA**: service worker (app-shell cache, offline fallback)
+ full-state sync via "last writer wins" + tombstones. Sync contract:
[docs/sync-kontrakt.md](docs/sync-kontrakt.md). Tests: `scripts/browser_check.py`
(web incl. no-JS fallback) + `scripts/pwa_check.py` (offline, two-device merge,
service worker).

**In progress:**
- **Telegram shopping checklist** (tap-to-check on the phone) — Gusto side is
  ready (`gusto shopping …`, verified); the Telegram client (inline-keyboard +
  `callback_query`) is built in the agent app. Contract:
  [docs/telegram-shopping-handoff.md](docs/telegram-shopping-handoff.md).
  Fallback if Telegram gets too fiddly: the existing **PWA over HTTPS in the LAN**
  (DuckDNS + Caddy, no app code needed) — ready-to-grab guide in
  [docs/https-pwa-option.md](docs/https-pwa-option.md).

**Under discussion / planned:**
- Weekly plan.
