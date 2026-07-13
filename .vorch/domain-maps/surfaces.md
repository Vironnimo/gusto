# Surfaces

The surfaces domain adapts Gusto's core capabilities to the agent-facing CLI and the server-rendered web application without owning catalog or shopping business rules.

## Overview

`gusto/cli.py` owns argument parsing, terminal presentation, JSON serialization, editor launching, and server startup. `gusto/web.py`, templates, and static assets own HTTP adaptation and browser presentation. Both import `gusto/core.py`; the stdlib-only core imports neither surface nor optional web dependencies.

## Terms

No cross-cutting terms for this domain are currently defined in `.vorch/GLOSSARY.md`.

### Thin shell

**Definition:** A surface that validates or converts transport input, delegates behavior to core, and formats the result. It must not create a second implementation of a domain rule.

## Interfaces

The installed application command is `gusto`, backed by `gusto.cli:main`; `python -m gusto` reaches the same entry point. Every command accepts `--json`, and the CLI converts expected core `ValueError` failures into non-zero command exits.

The web application object is `gusto.web:app`. It serves catalog, log, suggestion, shopping, and form pages; recipe media; shopping sync JSON; static assets; the root-scoped service worker; and a custom HTML 404. Entity URLs continue to use `/recipe/{slug}` because they address a recipe, not the application package.

Templates and static assets are package-relative under `gusto/templates/` and `gusto/static/`. The PWA manifest names the installed browser app Gusto and starts at `/shopping`.

## Packaging & Runtime

- `pyproject.toml` declares project and package `gusto`, a `gusto` console script, no default dependencies, and optional web dependencies under `.[web]`.
- `GUSTO_HOME` relocates the complete runtime store (`recipes/`, `images/`, and `data/`). Without it, the store is resolved beside the source package.
- `gusto serve` imports Uvicorn only when invoked and starts `gusto.web:app`. The systemd example runs the same module entry point from `/home/pi/gusto`.

## Conventions

- Add or change behavior in core and expose it through the CLI before adding web presentation. Keep the agent guides and `skill/gusto/` command reference synchronized with CLI changes.
- Preserve German product copy and English code identifiers.
- Starlette template calls pass the request as the first argument.
- Browser tests and PWA tests set `GUSTO_HOME` to throwaway stores before starting isolated servers; never point them at real data.

## Constraints & Gotchas

- Web imports require the optional dependency set, including form parsing support; the core and CLI must remain usable without it.
- The offline shopping client duplicates only client-side state transitions required for optimistic use; authoritative merge and persistence rules remain in core. Keep both timestamp and merge behaviors aligned.
- Run `tests/test_packaging.py` after package, dependency, command, or import changes and `tests/test_cli.py` after CLI changes. Any web-visible change also requires `scripts/browser_check.py`; shopping PWA behavior additionally requires `scripts/pwa_check.py`.
