# Gusto — project context for Codex

Self-hosted, **markdown-based recipe system** for home use on Windows and Linux,
reachable from any device on the local network. A Raspberry Pi is the first
Linux deployment target, not an exclusive platform. Built to replace paper
recipes and to enable e.g. meal suggestions based on the last few days.

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
6. **Verify in a real browser through the local `playwright-cli` skill.** Never
   use the internal Codex in-app browser or the Codex Browser plugin for this
   project; it is unstable in this workspace and can crash the Codex app. Use
   `.agents/skills/playwright-cli/SKILL.md` for all browser interaction and
   visual verification. Run `scripts/browser_check.py` and
   `scripts/pwa_check.py` where relevant, always against a throwaway copy of
   the data, and produce screenshots. Tests must never modify the real data.
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
| `archive/<slug>/` | Reversible snapshot with Markdown, full metadata, and owned images. |
| `images/_favorites/` | Preferred-product images copied into and owned by Gusto. |
| `data/recipes.json` | Recipe metadata including `images` and `cover_image_id`. |
| `data/categories.json` | Tag categories (facets): `{ "<key>": {"label", "tags": [...]} }`. Maps the flat tags to categories (order = display order). |
| `data/favorites.json` | Shared shopping needs, exact aliases, and manually ranked preferred products. |
| `data/log.json` | Cooking log: `[{ "date": "YYYY-MM-DD", "slug": ... }]`. |

`gusto new` writes both the .md AND the index entry. `gusto set --title` updates
the metadata title and Markdown H1 together. If a .md is created by
hand, add the entry in `data/recipes.json` and run `gusto check`. When saving
from the web, the first line of the .md is always rewritten as `# {title}` (the
title is its own form field, not in the body).

Per recipe, tags stay a **flat list**; their category lives centrally in
`data/categories.json` (so recipe tags stay clean). Tag filters are **facets**:
`--tag`/`?tag=` can be repeated, **OR within** a category and **AND across**
categories. Tags without a category are reported by `gusto check` as
"unsorted" and share the `Sonstige` facet; `new` and `set --tags` also warn
immediately. Then sort the tag into `categories.json`.

## Usage

```bash
python scripts/build_release.py # transferable ZIP incl. skill; no repo access
python install.py              # install release/source into the user app dir
python install.py --cli-only   # the pure CLI needs no dependencies
# Windows (new console after install): gusto serve
# Linux:   ~/.local/opt/gusto/bin/gusto serve
python -m gusto <command>      # CLI during development
```

All commands understand `--json` (machine-readable, for agents). `edit --json`
returns the editor result after the editor exits; `serve --json` emits one
startup object before the long-running server takes over. Expected command
failures write `{ "ok": false, "error": "..." }` to stdout and exit 1.
`check --json` instead returns its full diagnostics with `ok: false` and exit 1
on hard integrity errors. Argparse usage errors remain plain stderr with exit 2. Each runtime
uses its adjacent `gusto.settings.json`: the checkout selects the platform data
sibling `gusto-dev`, while the installer writes the production data path beside
the installed app. `GUSTO_HOME` overrides settings for isolated tests or an
explicit portable store. `gusto home --json` reports the active path, source,
platform default, and settings file.

```
gusto list   [--tag T ...] [--max-time N]    Positive cap; unknown durations fail it
gusto search "<terms>" [--match any|all] [--tag T ...] [--max-time N]  Full-text
gusto tags   [--all]                          Show tag categories (facets)
gusto home                                    Show active data directory
gusto show   <slug>                          Print a recipe (--json: incl. content)
gusto new    "<Title>" [--tags a,b] [--duration N] [--servings N]  Warns on unsorted tags
gusto edit   <slug>                          Open the .md in the editor
gusto cooked <slug> [--date YYYY-MM-DD]      Record a valid, non-future date
gusto log    [--days N]                       Show the log; N is positive
gusto set    <slug> [--title ...] [--tags a,b] [--duration N|--clear-duration] [--servings N|--clear-servings]  Title also rewrites H1
gusto delete <slug>                           Reversibly archive recipe + owned files
gusto archive list|show <slug>
gusto archive restore <slug>
gusto archive purge <slug> --yes              Permanently delete one snapshot
gusto suggest [--days N] [--limit N]          Positive days; nonnegative limit
gusto check                                   Full integrity check; hard errors exit 1
gusto serve  [--host H] [--port 1..65535]     Start the web UI (LAN)
gusto uninstall [--keep-data|--delete-data --yes] [--dry-run]  Remove installed app

gusto image list <slug>                       Show cover and gallery images
gusto image add <slug> <path> [--role R] [--caption TEXT] [--cover]
gusto image set <slug> <id> [--role R] [--caption TEXT]
gusto image cover <slug> <id>                 Select the top image
gusto image remove <slug> <id>                Delete one stored image

gusto favorites list|show <need>              List shared shopping needs / one ranking
gusto favorites match "<text>"                 Match only an exact known name or alias
gusto favorites add "<name>" [--alias TEXT ...]
gusto favorites set <need> --name N            Rename; old name becomes an alias
gusto favorites remove <need>
gusto favorites alias-add|alias-remove <need> "<text>"
gusto favorites product-add <need> "<name>" --brand B [--store S] [--note N] [--image PATH]
gusto favorites product-set <need> <id> [...] [--remove-image]
gusto favorites product-move <need> <id> <position>
gusto favorites product-remove <need> <id>

gusto shopping list [--pending]                  Show the shopping list
gusto shopping add "<text>" [--quantity M] [--source slug]  Add, optionally attributed
gusto shopping add-many "<text>" ...              Add several entries atomically
gusto shopping add-recipe <slug>                   Import while no visible sourced items remain
gusto shopping check|uncheck <id>              Check / uncheck an entry
gusto shopping remove <id>                     Remove an entry (tombstone)
gusto shopping remove-done                    Remove all checked entries
gusto shopping clear                          Empty the complete visible list
```

**Parallel agent calls:** Read-only commands may always run in parallel. Gusto
serializes mutations that share the catalog, favorites, or shopping source of
truth, so independent adds, image imports, and checkbox updates do not lose
data. Prefer one `shopping add-many` call for a known group. Keep dependent or
order-sensitive calls sequential (`new` before `image add`, `favorites add`
before `product-add`, and `product-move` / `image cover` / `shopping clear` /
`shopping remove-done` relative to mutations they order or remove). Direct
Markdown/category edits and the interactive `edit` command are outside these
Core locks and must not race another write to the same files.

Explicit `shopping check`/`uncheck` and repeated removal of the same tombstone
are idempotent without advancing `updated_at`. `shopping add-recipe` rejects an
empty ingredient section and refuses a repeat while any visible item from that
recipe remains; the error reports their count. `shopping add --source <slug>`
accepts only an existing recipe and its item participates in the same guard. It
never merges same-looking ingredients or guesses quantities. `shopping
remove-done` tombstones all checked items; `shopping clear` tombstones every
visible item, whether open or checked. Both operations sync through tombstones
and have no CLI restore.

`gusto delete` means **archive**, not destruction. It moves the recipe Markdown,
full metadata, and every owned recipe image together under `archive/<slug>/`.
Log history and visible shopping items stay in their own stores and resolve the
slug to the archived recipe. `archive restore` reverses the operation. Only
`archive purge <slug> --yes` destroys the snapshot; purge refuses while visible
shopping items still reference that recipe.

## Typical tasks (agent)

**"What should I eat today?"**
1. `gusto log --days 7 --json` → what was cooked recently.
2. `gusto suggest --json` → what hasn't been cooked for a while.
3. Decide with variety (not pasta three times in a row); honor time/diet via
   `--max-time` / `--tag`. `suggest` is deliberately simple — the "intelligence"
   comes from the agent combining `log`, `search` and `list`. A time cap excludes
   recipes whose duration is unknown.

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
Human users can manage the same images in the web UI, with separate actions to
take a photo using the outward-facing camera or choose an existing image.
Browser uploads are normalized to metadata-free WebP with a 1920 px maximum
edge; CLI imports remain unchanged and dependency-free.

**Preferred product for a shopping item:** run `gusto favorites match "<shopping
text>" --json`. A result is the shared household shopping need; its `products`
array is already ordered from most preferred to fallback. Matching only folds
case and whitespace: never infer quantities or substrings. To teach a known
formulation use `favorites alias-add`; create and rank product cards through
`favorites product-add|product-set|product-move|product-remove`. Product images
are copied into Gusto and may be shown offline by the shopping PWA.

**Shopping list as a Telegram checklist:** the agent posts the list via vBot's
`channel_send` tool (inline-keyboard) — one `chk:<id>` button per item (leading
⬜/✅ from `checked`) plus a final `run:done` "Fertig" button. Item taps flip the
glyph visually (vBot's checklist extension — no Gusto write); the **Fertig** tap
wakes the agent with the current button state, which then syncs Gusto (`gusto
shopping check|uncheck <id>` per item; no `toggle` — decide from the ⬜/✅ glyph)
and confirms in chat. "Fertig" saves the checked-state (add `gusto shopping
remove-done` only if it should also remove bought items). A request to empty
the entire list maps to one `gusto shopping clear` call. Neither removal has a
CLI restore.
Full round-trip in the skill
(`skill/gusto/SKILL.md`); client history: [docs/telegram-shopping-handoff.md](docs/telegram-shopping-handoff.md).

## Important files

- `gusto/core.py` — all logic (load/save, search, log, suggestions)
- `gusto/cli.py` — the CLI
- `gusto/web.py` — FastAPI app (server-rendered, Jinja2)
- `gusto/templates/`, `gusto/static/` — UI + CSS/JS
- `scripts/browser_check.py` — end-to-end browser test (Playwright)
- `install.py` — cross-platform Windows/Linux installation
- `gusto.settings.json` — development instance data selection (`gusto-dev`)
- `scripts/build_release.py` — builds the transferable wheel + installer + skill ZIP
- `deploy/install-systemd.sh`, `deploy/install-windows-task.ps1` — optional platform autostart
- `skill/gusto/` — self-contained generic skill (`SKILL.md` and
  `references/cli.md`) for operating the system via the CLI; it is shipped in
  every release ZIP and mirrors "Usage" / "Typical tasks" in the agent guides
  — **keep every copy in sync** (principle 8).

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
  `python-multipart` (forms) + Pillow (photo normalization). Tests: Playwright.
- Starlette ≥1.3: the signature is `TemplateResponse(request, "name.html", {...})`
  — `request` MUST be the first argument.
- Windows console (cp1252): stdout/stderr in CLI/tests are switched to UTF-8, otherwise
  characters like "✓" break.
- Every source or installed runtime owns instance settings for its data path.
  Relative names resolve beside the platform data default. The checkout uses
  `gusto-dev`; installed production uses the normal platform data directory;
  `GUSTO_HOME` always wins explicitly.
- Windows installs add Gusto's command directory to the user `PATH`; already
  open consoles must be reopened before `gusto` resolves directly.

## Design

"Gusto": warm cookbook editorial, deliberately not a dashboard. Cream paper
with fine grain, paprika red as accent, herb green. Fraunces (display serif) +
Hanken Grotesk (text). Numbered steps with large serif numerals, cards with a
staggered fade-in. UI and data fields are German.

## Status & roadmap

**Done:** data model, core, CLI, web UI (list/search incl. **live search** while
typing, **tag facets**: multi-select grouped by category, OR within / AND across
categories — `data/categories.json`, `gusto tags`), recipe view,
create/edit/reversible archive/restore/purge, "cooked today", suggestions, log,
404 page, optional Linux
systemd and Windows logon deployment, browser test, and **multiple stored recipe images** with a selected
cover, gallery, free role/caption, full agent control through `gusto image …`,
and direct camera/library management in the web UI. **Shopping list**
(core/CLI/web, `gusto shopping …`) with shared
**preferred products** (`gusto favorites …`): exact learned aliases, manually
ranked product cards, optional owned photos/store/notes, a mobile bottom sheet,
central management with direct camera/library photos, no-JS fallback, and
offline-readable recommendations;
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
