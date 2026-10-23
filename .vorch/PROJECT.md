# Project Context

## Project

Gusto is a self-hosted, German-language recipe system for one household on Windows and Linux, reachable from every device on the local network. It replaces paper recipes and lets an agent answer questions such as "what should I cook today?" or "what can I make with these ingredients?" through the CLI. Recipes are portable Markdown files; metadata, cooking log, shopping list and preferred products live in JSON next to them.

One per-user Gusto service owns the data store and serves the browser UI, the shopping PWA and the anonymous LAN command API. The `gusto` CLI and the browser are thin clients of that running service; agents operate Gusto through the CLI with `--json`, guided by the shipped skill in `skill/gusto/`.

## Architecture

**Stack:** Python 3.10+ (CI runs 3.10 and 3.14). Core (`gusto/core/`) and the CLI HTTP client use only the standard library. The service adds FastAPI, Uvicorn, Jinja2, Markdown, python-multipart and Pillow (`pyproject.toml` extra `web`; unpinned). Browser code is plain JavaScript and CSS without a build step, framework or TypeScript. Windows releases carry a pinned, hash-verified app-private CPython; Linux uses the system Python.

**Layers:**
```
gusto/core/catalog/  active recipes/cooking, facets/search, snapshots/archive and recipe images
gusto/core/shopping.py, favorites.py  shopping state/sync and household preferred products
gusto/core/storage.py, checks.py     persistence/transactions/revisions and read-only diagnostics
gusto/api.py         versioned anonymous command API and change events over Core
gusto/web.py         FastAPI host: mounts the API, serves templates/, static/ and the PWA
gusto/client.py      stdlib HTTP client the CLI uses to reach the service
gusto/cli.py         CLI assembly, dispatch and autostart; private _cli_*.py units own command groups
                     domain commands only through client; lifecycle and recovery commands local
gusto/runtime.py     installed paths/state, Python and package preparation/retention, readiness probes
gusto/service.py     per-user autostart, service control and health verification
gusto/update.py      verified-release orchestration and rollback; install.py bootstraps install/repair
gusto/uninstall.py, skill_install.py  removal and delivery of the bundled agent skill
skill/gusto/         the agent skill shipped in every runtime
scripts/, tests/     developer harness: quality gates, browser checks, release build, Vorch tooling
```

Each owning module's top-level functions are its interface; names with a leading underscore are internal. Core package markers export no compatibility facade: API, web, local recovery commands and tests import the actual owner. Shared domain records have a public owner; image-file validation and timestamp rules are private Core units; storage owns the one mutable transaction context and fault-injection seams. The dependency graph has no cycles. Soft limit: about 1,000 lines per source file. CLI command registrations and handlers live together in focused private units behind the substantive `cli.py` entry points.

Split an oversized source file into focused internal units behind the owning module and its public contract: a large file costs context on every inspection and edit, and cohesion alone does not justify a multi-thousand-line file. Avoid arbitrary chunks, method-binding tables or compressed formatting; a split follows the units a typical edit would read on their own.

**Agent-first:** every user capability exists in Core, the API and the CLI (with `--json`) before or together with the web UI; never build a feature only in the browser. When CLI, service or installation behavior changes, the shipped skill (`skill/gusto/`) changes in the same commit (`cli.md`).

**One service, thin clients:** normal CLI domain commands require a reachable server and never fall back to local Core or file access; only the explicit lifecycle and recovery commands run locally (`service.md`, `cli.md`).

**Persistence:** only Core reads and writes the data store. JSON writes are atomic, and every Core mutation holds the cross-process lock of its source of truth (catalog, favorites or shopping) for its whole duration (`storage.md`). Clients, agents and tests never edit the store's Markdown or JSON files directly.

**Recipes:** a recipe is pure Markdown without frontmatter, starting with `# Title`; its metadata lives in `data/recipes.json`, linked by the slug (`catalog.md`).

## Domain Maps

How to read the maps: `AGENTS.md` -> Load context for the task.

| Map | Domain | Covers |
|---|---|---|
| catalog.md | `gusto/core/catalog/`, `gusto/core/checks.py` | Recipes, archive, images, tag facets, cooking log, suggestions, `check` diagnostics |
| shopping.md | `gusto/core/shopping.py`, `gusto/core/favorites.py`, `gusto/static/shopping-client.js`, `gusto/static/sw.js` | Shopping list, preferred products, tombstones, full-state sync, offline PWA |
| storage.md | `gusto/core/storage.py`, `gusto/_temp.py`, `gusto.settings.json` | Data-directory selection, mutation locks, atomic writes, change revisions, temp-file policy |
| service.md | `gusto/api.py`, `gusto/client.py` | Versioned command API, change-event stream, HTTP client, uncertain outcomes |
| cli.md | `gusto/cli.py`, `gusto/_cli_*.py`, `gusto/__main__.py`, `skill/gusto/` | Command surface, `--json` contract, exit codes, server selection, the shipped agent skill |
| web.md | `gusto/web.py`, `gusto/templates/`, `gusto/static/` | FastAPI host, server-rendered UI, no-JS fallback, photo uploads, design |
| lifecycle.md | `install.py`, `install.ps1`, `install.sh`, `gusto/runtime.py`, `gusto/service.py`, `gusto/update.py`, `gusto/uninstall.py`, `gusto/skill_install.py`, `deploy/`, `scripts/build_release.py` | Per-user install, autostart, update and rollback, uninstall, skill install, release assets |
| development.md | `scripts/`, `tests/`, `.githooks/`, `.github/workflows/quality.yml`, `pyproject.toml` | Quality gates, test isolation, commit hook, push check, CI, worktrees |

## Conventions

**Language:** product UI, CLI messages and persisted data fields are German; code identifiers, comments and all project documentation are English.

**Errors:** CLI commands report expected failures under `--json` as `{"ok": false, "error": "..."}` on stdout with exit 1; usage errors stay on stderr with exit 2. A mutation whose result is unknown reports `outcome_unknown: true` and is never retried automatically (`cli.md`, `service.md`).

**Temporary files:** scratch files in a checkout belong under `.tmp/`, never in the system temp directory or new `.testdata*` folders. Importing `gusto` from a checkout points Python's temp directory and the `TMPDIR`, `TEMP` and `TMP` of child processes there; a standalone test or script imports `gusto` before it allocates temp files (`storage.md`).

**Line endings:** `.gitattributes` stores text files with LF; PowerShell scripts check out with CRLF.

## Development

**Setup** (Windows paths; on Linux use `.venv/bin/python`):
```bash
python -m venv .venv
.venv/Scripts/python -m pip install -e ".[web,test,dev]"
.venv/Scripts/python -m playwright install chromium
git config core.hooksPath .githooks   # once per clone; worktrees share it
```

**Run:** `python -m gusto serve` starts the foreground development server, by default on port 8000, the installed household service's port; the checkout's CLI also targets `http://127.0.0.1:8000` unless `--server` says otherwise, so development uses another port and passes `--server`. The checkout's `gusto.settings.json` selects the `gusto-dev` store under the per-user data directory, which every checkout of this repository shares; `GUSTO_HOME` overrides the store. The installed production service owns its own store and is never used for development or tests. Starting the app to check a change, in the browser or through the CLI: read `.vorch/workflows/live-testing-workflow.md`.

**Worktrees:** `python scripts/worktree.py create|list|merge|delete <task-name>`. `create` prints the absolute path and a port block of its own and sets up the worktree's own `.venv`; `merge` runs no tests and moves `main` only once the merge commit passed the commit hook. Never link a folder of another checkout into a worktree: Git for Windows' `git worktree remove` follows junctions and deletes the target's contents. Conflicts and gotchas: `.vorch/workflows/worktree-workflow.md`.

**Push:** Read `.vorch/workflows/push-workflow.md` when the user asks to push. A push is finished only when the CI run it starts is green.

**Release:** Read `.vorch/workflows/release-workflow.md` when the user requests a release.

## Testing

Tests are standalone Python scripts, not a pytest suite: each `tests/test_<owner>.py` runs its checks top to bottom, prints `OK - <n> ... checks passed` and exits non-zero on the first failure. `scripts/browser_check.py` and `scripts/pwa_check.py` drive real Chromium and collect their failures. Run gates through `scripts/run_quality.py`: it isolates each gate's home, user configuration and default store under `.tmp/`, preserves the Playwright browser cache, records durations, and stops a gate and its child processes after 180 seconds. Live servers also use throwaway stores.

Before writing or changing a test, read `development/testing.md`: what to assert, how to wait, and what the gates isolate.

**Running tests and checks:** Call the tools directly; what to test: `AGENTS.md` -> Testing.
```bash
.venv/Scripts/python scripts/run_quality.py tests/test_recipes.py  # one isolated gate
.venv/Scripts/python scripts/run_quality.py scripts               # every script gate
.venv/Scripts/python scripts/run_quality.py browser               # Chromium browser and PWA checks
.venv/Scripts/python scripts/run_quality.py all                   # the complete suite
.venv/Scripts/python -m ruff check .                              # lint
python scripts/domain_maps.py --check                            # map structure and routing
```
The complete suite is `python scripts/run_quality.py all`; the push check and CI run it.

**Commit hook:** `.githooks/pre-commit` and `.githooks/pre-merge-commit` run `scripts/commit_check.py` on every commit and merge commit, in every checkout: `ruff check` on staged Python files, domain-map checks, and a refusal of AI tool identities; no tests. It blocks when the checkout's `.venv` lacks the ruff version `pyproject.toml` pins and prints the install command. Details: `development.md` -> Commit hook.

**CI:** `.github/workflows/quality.yml` runs on pull requests and pushes to `main`: lint, domain-map checks and the commit-authorship history, every script gate on Windows and Linux with Python 3.10 and 3.14, and the Chromium browser and PWA checks on both systems. `release.yml` reuses it before building a release.

## Context

- Change events are refresh notifications shared by Core, API, browser and PWA; their persistence, replay and recovery live in `storage.md` and `storage/change-journal.md`.
- Target platforms: Windows and Linux, installed per user without admin or root. A Raspberry Pi is the first Linux deployment target, not an exclusive platform.
- No MCP, ever: explicit, final user decision. Do not propose or build an MCP server; agents use the CLI.
- Ask first, then build: new features and larger steps start with a concept the user approves (`AGENTS.md` -> Working with the user).
- 2026-07-23: removing a recipe means reversible archive; only an explicitly confirmed purge destroys it (`catalog.md`).
- 2026-07-27: one central service with thin clients replaced local CLI access; `gusto update` is the only normal update path, installer reruns are repair-only (`lifecycle.md`).
- 2026-09-23: store walking routes are deliberately not persisted; agents order a shopping list per trip (`shopping.md`).
- In progress: Telegram shopping checklist through vBot's `channel_send` `checklist`. Gusto's side is ready; the live phone round-trip waits for vBot (`docs/telegram-shopping-handoff.md`). Alternative without Telegram: the PWA over HTTPS in the LAN (`docs/https-pwa-option.md`).
- Under discussion: a weekly meal plan.
- Repository audits with evidence and known platform limits: `docs/audit-*.md`.
