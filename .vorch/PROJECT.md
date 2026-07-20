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

Python 3.10+ with a stdlib-only core and CLI. FastAPI, Jinja2, Markdown,
Uvicorn, and python-multipart form the optional web surface. Core owns recipe,
search, log, suggestion, shopping-list, and sync behavior; CLI and web are thin
shells. The shopping PWA uses localStorage plus full-state last-writer-wins sync
and tombstones. Shared preferred products are a separate server-owned shopping
catalog mirrored read-only into browser localStorage for offline display.

## Conventions

Recipes contain no frontmatter. Metadata is linked to Markdown by the filename
slug. Product copy and data fields are German; code identifiers are English.
Writes to JSON are atomic. Features are implemented in core and CLI before web,
and every CLI command accepts `--json`. MCP is explicitly out of scope.

## Development

`scripts/build_release.py` creates a transferable ZIP containing a regular
wheel, standalone `install.py`, and both optional autostart adapters; targets
need no repository access. Normal application installs use
`%LOCALAPPDATA%\Programs\Gusto` on Windows or `~/.local/opt/gusto` on Linux; an
editable `-e ".[web]"` checkout install remains available for development.
Stores remain separate under `%LOCALAPPDATA%\Gusto` or the XDG user-data
directory. Every source or installed runtime owns an adjacent
`gusto.settings.json`; the checkout selects the platform-data sibling
`gusto-dev`, while the installer records the production data path beside the
installed runtime. `GUSTO_HOME` overrides instance settings for tests and
explicit portable stores. Browser tests always use throwaway data through
`GUSTO_HOME`. Linux systemd and Windows logon autostart use the installed
runtime and are optional deployment helpers under `deploy/`. Windows installs
add the runtime command directory to the user `PATH`; new consoles can invoke
`gusto` directly.

## Testing

Tests are standalone Python scripts rather than a pytest suite. Browser and PWA
checks start isolated servers and drive real Chromium through Playwright.

Quality gates:

- `python tests/test_packaging.py`
- `python tests/test_shopping.py`
- `python tests/test_favorites.py`
- `python tests/test_merge.py`
- `python tests/test_recipes.py`
- `python tests/test_cli.py`
- `python tests/test_autostart.py`
- `python tests/test_uninstall.py`
- `python tests/test_paths.py`
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

## Domain Maps

Domain-specific documentation lives in `.vorch/domain-maps/`. A **domain** is any module or subsystem that has its own folder or clear boundary in the codebase — a chunk of code that has a distinct responsibility and that agents need context about before touching it. This includes technical modules (`hooks`, `tools`, `storage`), infrastructure modules (`server`, `channel`), and business modules (`auth`, `payments`). Size doesn't matter — what matters is that working on it without context risks misunderstanding its interfaces or conventions.

The Orchestrator uses `.vorch/workflows/domain-map-workflow.md` when creating, auditing, or updating domain maps.

**When working on a domain: read its domain map.** Your task will list which maps are relevant — treat that as a starting point, not a ceiling. Read additional maps if you need them.

| Domain map | Domain | What it covers |
|---|---|---|
| `catalog.md` | Catalog | Recipes, metadata, images, tag facets, cooking history, suggestions, and consistency checks. |
| `shopping.md` | Shopping | Shopping-list persistence, ingredient import, preferred products, tombstones, offline behavior, and full-state sync. |
| `surfaces.md` | Surfaces | CLI, web, packaging, runtime entry points, templates, static assets, and verification boundaries. |
