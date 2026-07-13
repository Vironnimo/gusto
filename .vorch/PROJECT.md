# Project Context

This is the **single source of truth for project-specific knowledge**. Every agent reads it before starting work. Only the Orchestrator updates it.

Remove sections that don't apply to your project. Keep entries short and factual — this is working notes for agents, not polished documentation.

## Project

Gusto is a self-hosted, German-language recipe system for home use on a
Raspberry Pi. Recipes remain portable Markdown files while metadata, cooking
history, and the shopping list live in JSON. Every user capability must also be
available through the JSON-capable CLI so an agent can operate the product.

## Architecture

Python 3.10+ with a stdlib-only core and CLI. FastAPI, Jinja2, Markdown,
Uvicorn, and python-multipart form the optional web surface. Core owns recipe,
search, log, suggestion, shopping-list, and sync behavior; CLI and web are thin
shells. The shopping PWA uses localStorage plus full-state last-writer-wins sync
and tombstones.

## Conventions

Recipes contain no frontmatter. Metadata is linked to Markdown by the filename
slug. Product copy and data fields are German; code identifiers are English.
Writes to JSON are atomic. Features are implemented in core and CLI before web,
and every CLI command accepts `--json`. MCP is explicitly out of scope.

## Development

Create a virtual environment and install `-e ".[web]"`; run `gusto serve` for
the LAN web app. `GUSTO_HOME` relocates the recipes and data directories. Web
browser tests always use throwaway data through this environment variable.

## Testing

Tests are standalone Python scripts rather than a pytest suite. Browser and PWA
checks start isolated servers and drive real Chromium through Playwright.

Quality gates:

- `python tests/test_packaging.py`
- `python tests/test_shopping.py`
- `python tests/test_merge.py`
- `python tests/test_recipes.py`
- `python tests/test_cli.py`
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

## Domain Maps

Domain-specific documentation lives in `.vorch/domain-maps/`. A **domain** is any module or subsystem that has its own folder or clear boundary in the codebase — a chunk of code that has a distinct responsibility and that agents need context about before touching it. This includes technical modules (`hooks`, `tools`, `storage`), infrastructure modules (`server`, `channel`), and business modules (`auth`, `payments`). Size doesn't matter — what matters is that working on it without context risks misunderstanding its interfaces or conventions.

The Orchestrator uses `.vorch/workflows/domain-map-workflow.md` when creating, auditing, or updating domain maps.

**When working on a domain: read its domain map.** Your task will list which maps are relevant — treat that as a starting point, not a ceiling. Read additional maps if you need them.

| Domain map | Domain | What it covers |
|---|---|---|
| `catalog.md` | Catalog | Recipes, metadata, images, tag facets, cooking history, suggestions, and consistency checks. |
| `shopping.md` | Shopping | Shopping-list persistence, ingredient import, tombstones, offline behavior, and full-state sync. |
| `surfaces.md` | Surfaces | CLI, web, packaging, runtime entry points, templates, static assets, and verification boundaries. |
