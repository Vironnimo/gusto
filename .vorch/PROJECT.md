# Project Context

This is the **single source of truth for project-specific knowledge**. Every agent reads it before starting work. Only the Orchestrator updates it.

Remove sections that don't apply to your project. Keep entries short and factual — this is working notes for agents, not polished documentation.

## Project

Gusto is a self-hosted, German-language recipe system for home use on Windows
and Linux. A Raspberry Pi is the first Linux deployment target, not an
exclusive platform. Recipes remain portable Markdown files while metadata,
cooking history, and the shopping list live in JSON. Every user capability must
also be available through the JSON-capable CLI so an agent can operate the product.

## Architecture

Python 3.10+ with a stdlib-only Core and CLI HTTP client. FastAPI, Jinja2,
Markdown, Uvicorn, python-multipart, and Pillow form the always-installed
service/UI runtime. Core owns recipe, search, log, suggestion, shopping-list,
and sync behavior. The versioned anonymous LAN API exposes that Core; browser
and normal CLI commands are thin clients of the same running service with no
tokens and no local CLI fallback. A persisted revision journal drives
server-sent change events. The shopping PWA keeps its localStorage,
full-state last-writer-wins sync, tombstones, offline app shell, and offline
preferred-product display. Cross-process transaction locks serialize mutations
per catalog, favorites, or shopping source of truth while allowing independent
domains and reads to run in parallel.

## Conventions

Recipes contain no frontmatter. Metadata is linked to Markdown by the filename
slug; the metadata title and first Markdown H1 are one enforced contract.
Reversible recipe snapshots live under `archive/<slug>/`. Product copy and data
fields are German; code identifiers are English.
Writes to JSON are atomic. Features are implemented in Core, API, and CLI
before web, and every CLI command accepts `--json`. Complete Core mutations,
not only their final JSON replacement, hold the matching resource lock. Normal
domain commands require a reachable server; only explicit lifecycle/recovery
commands (`serve`, `home`, service control, update/uninstall, and
`check --offline`) operate locally. MCP is explicitly out of scope.

## Development

`scripts/build_release.py` creates stable public assets:
`gusto-release.zip`, its SHA-256 file, and `gusto-release.json`.
`.github/workflows/quality.yml` is the reusable PR/main/release gate: all script
checks run on Windows and Linux with Python 3.10 and the current feature
release, while real Chromium browser/PWA checks run on both operating systems.
`.github/workflows/release.yml` verifies version tags, builds the release once,
smoke-installs that exact artifact on fresh Windows and Linux runners, creates
build-provenance attestations, and only then publishes the assets. Public
`install.ps1`/`install.sh` bootstraps install the application
only; agent skills are delivered separately. Managed application roots use
versioned side-by-side runtimes and a current pointer under
`%LOCALAPPDATA%\Programs\Gusto` or `~/.local/opt/gusto`; data stays separate
under `%LOCALAPPDATA%\Gusto` or the XDG user-data directory. Installation is
current-user only, registers a limited Windows logon task or `systemd --user`,
starts immediately, and must pass health without admin/root. `gusto update` is
the sole normal update path and rolls back a failed activation; installer
reruns require explicit repair mode. Browser/PWA tests always use throwaway
data through `GUSTO_HOME`.

## Testing

Tests are standalone Python scripts rather than a pytest suite. Browser and PWA
checks start isolated servers and drive real Chromium through Playwright.
`python scripts/run_quality.py scripts|browser|all` is the shared
cross-platform entry point used locally and by CI.

Quality gates:

- `python tests/test_packaging.py`
- `python tests/test_api.py`
- `python tests/test_shopping.py`
- `python tests/test_favorites.py`
- `python tests/test_merge.py`
- `python tests/test_concurrency.py`
- `python tests/test_recipes.py`
- `python tests/test_cli.py`
- `python tests/test_service.py`
- `python tests/test_update.py`
- `python tests/test_autostart.py`
- `python tests/test_uninstall.py`
- `python tests/test_paths.py`
- `python tests/test_ci.py`
- `python scripts/browser_check.py`
- `python scripts/pwa_check.py`

## Context

- 2026-07-13: The application, Python package, CLI, module entry point, and
  runtime-home variable are consistently named Gusto (`gusto`, `python -m
  gusto`, and `GUSTO_HOME`).
- 2026-07-13: Fresh web installs now declare form support and pass an isolated
  installation smoke test.
- 2026-07-13: Shopping sync versions now use millisecond timestamps and advance
  monotonically per item. Existing second-precision data remains compatible;
  rapid add-then-check is covered end to end.
- 2026-07-13: Recipes may own multiple images stored by Gusto. Agents inspect
  images and add them through the CLI. One image can be selected as the cover;
  all images may carry a free role and caption and appear in the recipe gallery.
- 2026-07-15: Shared household shopping needs now map exact learned aliases to
  manually ranked preferred-product cards with optional owned photos, store,
  and notes. Shopping recommendations and photos remain readable offline;
  catalog changes intentionally require an online server or the CLI.
- 2026-07-15: Recipe and preferred-product forms now offer separate native
  camera and image-library actions. Recipe images are fully manageable in the
  web UI; all browser photo uploads are converted to metadata-free WebP with a
  1920 px maximum edge, while CLI image imports remain unchanged.
- 2026-07-17: Built wheels include all templates, static assets, manifest, and
  PWA icons. `install.py` is the shared Windows/Linux installer; platform
  autostart is a separate optional systemd or Windows logon adapter.
- 2026-07-17: Normal installs store data in the operating system's per-user
  directory. `GUSTO_HOME` remains the explicit override, existing checkout data
  has a non-destructive fallback, and `gusto home --json` explains the result.
- 2026-07-17: Optional recipe duration/servings can be removed in web and CLI;
  recipe titles and numeric metadata are validated in core. Malformed shopping
  sync states are rejected with HTTP 400 before persistence.
- 2026-07-19: Private-repository deployment uses a transferable release ZIP
  with a wheel and standalone installer. Application runtimes live outside the
  source checkout; bundled Windows/systemd adapters point at that installed
  runtime, while recipe and shopping data remain in the platform data store.
- 2026-07-19: Each runtime reads its own `gusto.settings.json` for the data
  directory. The checkout uses the separate `gusto-dev` store; installers write
  the production store into the installed runtime. `GUSTO_HOME` remains the
  highest-precedence override for isolated tests.
- 2026-07-19: Windows installation persists Gusto's command directory in the
  user `PATH`, making `gusto` directly available to newly opened consoles.
- 2026-07-20: Windows logon autostart directly invokes the installed
  `gusto-autostart` GUI launcher instead of PowerShell or the Console CLI. It
  delegates to the normal `serve` path, preserves exit codes, records output in
  the active data store, and the adapter stops then migrates/restarts existing
  scheduled tasks before updating in-use Windows launchers.
- 2026-07-20: `gusto uninstall` removes only a verified managed installed
  runtime, its autostart integration, and its exact Windows PATH entry by
  default while preserving the separately stored recipes and household data.
  Permanent data removal is a distinct confirmed choice; a one-shot external
  helper performs self-deletion after the CLI exits.
- 2026-07-20: Transferable release ZIPs include the complete, validated
  `skill/gusto/` agent skill alongside the application artifacts. `install.py`
  remains application-only because skill discovery and installation are owned
  by the selected agent host.
- 2026-07-21: Agent-visible mutations use cross-process catalog, favorites, and
  shopping transaction locks on Windows and POSIX, preventing lost updates
  from parallel tool calls without globally serializing independent domains.
  `gusto shopping add-many` adds a free-text group in one transaction; unique
  temporary files preserve atomic JSON replacement under contention.
- 2026-07-22: CLI command failures under `--json` now return structured JSON on
  stdout with exit 1, while argparse usage failures remain stderr/exit 2.
  Cooking dates and suggestion limits are boundary-validated; shopping state
  retries preserve sync timestamps, and recipe ingredient imports reject empty
  or already-visible same-recipe imports. Recipe mutations warn immediately
  about tags without named facets; duration caps explicitly exclude unknown
  durations. Sourced single shopping adds validate their recipe and participate
  in the import guard, whose rejection now reports the blocking count; CLI docs
  flag that cleared tombstones have no restore command. Release version is
  0.1.2.
- 2026-07-23: Shopping removal commands now describe their scope directly:
  `gusto shopping clear` atomically tombstones the complete visible list, while
  `gusto shopping remove-done` removes only checked entries. Core, CLI,
  server-rendered web, offline PWA, Telegram handoff, and the shipped Gusto
  skill share this contract.
- 2026-07-23: Recipe `delete` now means reversible archive. Markdown, complete
  metadata, and owned images move together under `archive/<slug>/`; CLI and web
  expose list/show/restore and separately confirmed purge. Log and shopping
  remain separate sources of truth whose visible references resolve active or
  archived slugs; purge refuses visible shopping dependencies.
- 2026-07-23: Catalog title updates rewrite the Markdown H1 in the same Core
  transaction. `check` now distinguishes hard integrity errors (full
  diagnostics plus exit 1) from warnings and covers active/archive titles,
  files, images, covers, interrupted moves, collisions, shopping sources,
  favorite data, and historical log references. Time/day ranges must be
  positive, ports stay within 1–65535, and `edit`/`serve` now honor `--json`.
- 2026-07-27: Gusto 0.1.5 centralizes normal browser and CLI behavior on one
  anonymous LAN service/API. CLI business commands have no local fallback;
  explicit recovery/lifecycle commands remain local. Persisted change
  revisions and SSE keep online browser pages current while shopping PWA
  offline operation and merge behavior remain intact.
- 2026-07-27: Public Windows/Linux one-shot bootstraps install versioned
  per-user runtimes, register login autostart, start immediately, and require a
  healthy service without admin rights. `gusto update` verifies public release
  assets, switches side-by-side, and rolls back unhealthy activation.
  Installer reruns are repair-only; app installation never installs the agent
  skill.
- 2026-07-27: Reusable CI runs every script contract on Windows/Linux at Python
  3.10 and 3.14 plus real Chromium/PWA suites on both systems. Tagged releases
  build once, smoke-install the exact artifact on both systems, retain failure
  diagnostics, attest provenance, and grant write authority only to the final
  publish job.

## Domain Maps

Domain-specific documentation lives in `.vorch/domain-maps/`. A **domain** is any module or subsystem that has its own folder or clear boundary in the codebase — a chunk of code that has a distinct responsibility and that agents need context about before touching it. This includes technical modules (`hooks`, `tools`, `storage`), infrastructure modules (`server`, `channel`), and business modules (`auth`, `payments`). Size doesn't matter — what matters is that working on it without context risks misunderstanding its interfaces or conventions.

The Orchestrator uses `.vorch/workflows/domain-map-workflow.md` when creating, auditing, or updating domain maps.

**When working on a domain: read its domain map.** Your task will list which maps are relevant — treat that as a starting point, not a ceiling. Read additional maps if you need them.

| Domain map | Domain | What it covers |
|---|---|---|
| `catalog.md` | Catalog | Recipes, metadata, images, tag facets, cooking history, suggestions, and consistency checks. |
| `shopping.md` | Shopping | Shopping-list persistence, ingredient import, preferred products, tombstones, offline behavior, and full-state sync. |
| `surfaces.md` | Surfaces | CLI, web, packaging, runtime entry points, templates, static assets, and verification boundaries. |
