# Surfaces

The surfaces domain adapts Gusto's core capabilities to the agent-facing CLI and the server-rendered web application without owning catalog or shopping business rules.

## Overview

`gusto/cli.py` owns argument parsing, terminal presentation, JSON serialization, editor launching, and server startup. `gusto/web.py`, templates, and static assets own HTTP adaptation and browser presentation. Both import `gusto/core.py`; the stdlib-only core imports neither surface nor optional web dependencies.

## Terms

GLOSSARY → Deployment-Ziel distinguishes an important runtime target from the
general installation and supported-platform contracts owned here.

### Thin shell

**Definition:** A surface that validates or converts transport input, delegates behavior to core, and formats the result. It must not create a second implementation of a domain rule.

## Interfaces

The installed application command is `gusto`, backed by `gusto.cli:main`; `python -m gusto` reaches the same entry point. Every command accepts `--json`, and the CLI converts expected core `ValueError` failures into non-zero command exits.

`gusto home` reports the active store root, its resolution source, and the
normal platform default. It also reports the instance `gusto.settings.json`
when present. Installation and deployment tooling uses this contract instead
of inferring data paths from the checkout.

`gusto set` can remove optional duration or serving metadata with
`--clear-duration` and `--clear-servings`; these are mutually exclusive with
setting the corresponding value.

The web application object is `gusto.web:app`. It serves catalog, log, suggestion, shopping, preferred-product management, and form pages; recipe and product media; shopping/favorite JSON; static assets; the root-scoped service worker; and a custom HTML 404. Entity URLs continue to use `/recipe/{slug}` because they address a recipe, not the application package.

Browser photo forms use two explicit file controls: native outward-facing
camera capture and image-library selection. Pillow in the web extra normalizes
uploads before handing their temporary paths to the same core operations used
by the CLI; JavaScript only adds selection previews and mutual exclusion.

Templates and static assets are package-relative under `gusto/templates/` and `gusto/static/`. The PWA manifest names the installed browser app Gusto and starts at `/shopping`.

At widths up to 720px, the web surface uses a fixed bottom primary navigation while the masthead retains the brand. Catalog filters and the shopping add form become compact disclosure panels; the recipe page exposes a direct jump to its content and keeps edit/delete actions in a secondary disclosure. Desktop keeps the conventional header navigation and visible catalog filters.

## Packaging & Runtime

- `pyproject.toml` declares project and package `gusto`, the normal `gusto`
  Console script, and the Windows-only deployment surface
  `gusto-autostart` as a GUI script. The latter prepends `serve`, delegates to
  the same CLI parser/handler, redirects stdout and stderr (which may be `None`
  under `pythonw.exe`) to `<data-root>/gusto-autostart.log`, and preserves the
  process exit code. The package has no default dependencies, optional web
  dependencies under `.[web]`, and includes the templates, CSS/JavaScript,
  manifest, and PWA icons required by an installed web app.
- `scripts/build_release.py` builds a transferable ZIP containing the regular
  wheel, standalone installer, and both deployment adapters. A target installs
  without the private repository or GitHub credentials.
- Normal application runtimes live under `%LOCALAPPDATA%\Programs\Gusto` on
  Windows and `~/.local/opt/gusto` on Linux, separate from both the source
  checkout and the data store. `install.py --venv` overrides this location.
  Windows installation also adds the runtime's `Scripts` directory to the user
  `PATH`; a new console can invoke `gusto` directly.
- Normal stores live under `%LOCALAPPDATA%\Gusto` on Windows and
  `$XDG_DATA_HOME/gusto` or `~/.local/share/gusto` on Linux. `GUSTO_HOME` is the
  highest-precedence explicit override. Each runtime otherwise reads its own
  `gusto.settings.json`; relative `data_dir` values resolve beside the platform
  default. The checkout selects `gusto-dev`, while installed settings select the
  production store. Without settings, an existing legacy checkout remains a
  compatibility fallback.
- `gusto serve` imports Uvicorn only when invoked and starts `gusto.web:app`.
  Both platform deployment helpers run the installed module from the normal
  per-user application directory.
- `install.py` installs from either the wheel beside it in a release bundle or
  a complete source checkout. It creates a virtual environment only when the
  selected runtime has no Python executable, so upgrades reuse rather than
  rewrite an existing Windows environment. It copies existing checkout data
  only when the platform store is empty and never removes the original.
  `deploy/install-systemd.sh` optionally adds Linux autostart;
  `deploy/install-windows-task.ps1` optionally adds Windows logon autostart.
  The Windows adapter schedules the GUI launcher by its exact path in the
  selected installed runtime, never through `PATH` or an intermediate
  PowerShell action. Re-running it stops an active task before updating the
  runtime (so Windows does not lock an in-use launcher), migrates the task,
  removes the obsolete generated `start-gusto.ps1`, and starts the new action.
  If migration fails after the old task was stopped, the adapter makes a
  best-effort restart. These adapters do not define application capabilities or
  platform support.

## Conventions

- Add or change behavior in core and expose it through the CLI before adding web presentation. Keep the agent guides and `skill/gusto/` command reference synchronized with CLI changes.
- Preserve German product copy and English code identifiers.
- Product-catalog changes have a server-rendered online path; the shopping
  client only caches and presents the last successful catalog offline.
- Starlette template calls pass the request as the first argument.
- Browser tests and PWA tests set `GUSTO_HOME` to throwaway stores before starting isolated servers; never point them at real data.

## Constraints & Gotchas

- Web imports require the optional dependency set, including form parsing support; the core and CLI must remain usable without it.
- Packaging tests build and inspect a real wheel so editable installs cannot
  conceal missing web runtime assets.
- `tests/test_paths.py` locks the Windows/Linux platform defaults,
  per-instance settings, `GUSTO_HOME` precedence, and legacy-store transition.
  Do not remove the legacy fallback without an explicit data migration path.
- Web photo processing additionally requires Pillow from the `web` extra. It
  accepts at most one of the camera/library controls, limits input to 25 MB,
  applies orientation, resizes to a 1920 px maximum edge, and stores WebP without
  source metadata.
- Catalog filter markup is open by default so desktop and no-JavaScript use stay visible; `app.js` closes it only on an initial mobile load without selected tags. The mobile masthead must remain in a higher stacking context than main content so its fixed navigation cannot be covered by recipe cards.
- Shopping items persist their recipe source as a slug. `gusto.web` supplies a slug-to-title presentation map to both the server fallback and offline client so the visible list uses recipe titles without changing the sync contract.
- The offline shopping client duplicates only client-side state transitions required for optimistic use; authoritative merge and persistence rules remain in core. Keep both timestamp and merge behaviors aligned.
- The same client performs deterministic name/alias lookup for presentation;
  keep its case/whitespace normalization aligned with core and never add fuzzy
  matching only in JavaScript.
- Run `tests/test_packaging.py` after package, dependency, command, or import changes and `tests/test_cli.py` after CLI changes. Any web-visible change also requires `scripts/browser_check.py`; shopping PWA behavior additionally requires `scripts/pwa_check.py`.
- Run `tests/test_autostart.py` after changing the GUI launcher or Windows
  task contract. On Windows, the packaging test installs a real wheel under a
  path containing spaces and verifies PE subsystem 3 (Console) for `gusto.exe`
  and subsystem 2 (GUI) for `gusto-autostart.exe`.
