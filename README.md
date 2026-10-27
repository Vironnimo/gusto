# Gusto

A small, **markdown-based recipe system** for self-hosting on **Windows or
Linux**, reachable from any device on the local network. A Raspberry Pi is an
important Linux deployment target, not a separate or exclusive edition.

- **Recipes are plain Markdown files** in `recipes/` – portable, with no
  frontmatter. Edit running Gusto through the UI or CLI so metadata and
  connected clients stay in sync.
- **Metadata** (tags, duration, servings, last cooked, image references) live
  bundled in `data/recipes.json`; Gusto stores recipe images under `images/`,
  and the cooking log lives in `data/log.json`.
- **Preferred products** connect recurring free-text shopping items to exact
  learned aliases and a shared, manually ranked household selection. Product
  cards contain a name and brand, may also include an owned photo, store, and
  note, and remain visible in the shopping PWA while offline. On a phone, a
  product photo can be taken directly or selected from the photo library.
- **Recipe photos** can be captured or selected in the web UI and managed there
  as cover, result, ingredient, step, or gallery images. Browser uploads are
  resized to a 1920 px maximum edge, converted to WebP, and stripped of metadata.
- **One running service, several thin clients:** the browser UI and normal
  `gusto` commands use the same anonymous LAN server and the same
  `gusto/core/`. Server-sent change events keep open browser pages current.
  The shopping PWA remains usable offline and merges when it reconnects.

The UI and the data fields are German; the code is English.

## Installation

Installation is entirely per-user: no administrator or root rights, no
system-wide files and no token configuration. Windows needs no preinstalled
Python: the PowerShell bootstrap downloads Gusto's pinned, SHA-256-verified
app-private Python and creates each Gusto version in its own venv. Linux uses
an existing Python 3.10 or newer. The installer registers Gusto for the current
user, enables login autostart, starts the service immediately and verifies its
health.

On Windows, Gusto never takes over or removes an unrelated Scheduled Task that
happens to be named `Gusto`; such a name collision is reported and must be
resolved by the user.

### Windows (PowerShell)

```powershell
irm https://github.com/Vironnimo/gusto/releases/latest/download/install.ps1 | iex
```

### Linux

```bash
curl -fsSL https://github.com/Vironnimo/gusto/releases/latest/download/install.sh | bash
```

Afterwards open `http://<computer-name>:8000` from another device in the LAN
or `http://localhost:8000` locally. Normal updates are deliberately separate:

```bash
gusto update
```

A damaged downloaded Windows Python package stops the update before activation;
the active version remains selected. If a retained version's runtime marker is
unreadable or incomplete, Gusto skips managed Python cleanup to preserve the
interpreters it may need.

Running the installer again is rejected. Use its explicit `--repair` /
`-Repair` mode only to repair an existing managed installation. `gusto status`,
`gusto start`, `gusto stop`, and `gusto restart` control the current-user
service. Gusto does not start before login.

Service control and `update` use the managed installation containing the running
CLI, including custom app directories. From a checkout or unmanaged Python they
use the platform-default application path; `--app-root PATH` explicitly selects
another managed installation.

The HTTP API is intentionally anonymous on the trusted home LAN. There are no
tokens to distribute to the CLI or browser. Do not expose port 8000 directly
to the public internet.

Every managed runtime carries the matching Gusto agent skill. Registration in
an agent host remains explicit; for an existing default vBot installation:

```bash
gusto install-skill vbot --dry-run --json
gusto install-skill vbot --json
```

This installs to `~/.vbot/skills/gusto` and fully replaces an existing Gusto
skill. The app installer does not modify vBot automatically. After `gusto
update`, rerun `install-skill` to refresh vBot from the new active runtime.

Application and data stay separate:

- Windows application: `%LOCALAPPDATA%\Programs\Gusto`
- Linux application: `~/.local/opt/gusto`
- Windows data: `%LOCALAPPDATA%\Gusto`
- Linux data: `$XDG_DATA_HOME/gusto`, otherwise `~/.local/share/gusto`

The managed app root contains immutable version directories plus a small
current-version pointer. The data directory remains separate and survives
normal updates and app-only uninstall. `gusto home --json` reports the server,
service reachability and the server-owned data directory. `GUSTO_URL` or the
global `--server` option selects another Gusto server explicitly.
Server URLs with missing hosts, invalid ports or embedded whitespace or control
characters, and settings that are not UTF-8, are rejected before a request.

A development checkout's `gusto.settings.json` selects the shared `gusto-dev`
data path for a locally started service. It does not select the CLI's remote
server: without `--server` or `GUSTO_URL`, checkout commands target
`http://127.0.0.1:8000`. Installed commands use their runtime's configured URL,
including the port chosen during installation.

### Development checkout

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# Linux:   source .venv/bin/activate
python -m pip install -e ".[web,test,dev]"
git config core.hooksPath .githooks
```

For isolated development, set `GUSTO_HOME` to a throwaway directory under `.tmp/`,
start `python -m gusto serve --port <free-port>`, and pass
`--server http://127.0.0.1:<free-port>` on checkout CLI calls. Verify
`home --json` reports that directory as `server_data_path` before writing.
The CLI HTTP client itself uses only the standard library.

## Service and web UI

A normal installation already has a running service. Open
`http://<machine>:8000` in the browser or inspect it locally:

```bash
gusto status
gusto home --json
```

`gusto serve` remains the foreground recovery/development command. It is not
the normal installed-app startup path. All normal recipe, favorites, log and
shopping commands fail clearly when the selected server is unavailable; they
never fall back to editing local files. `gusto check --offline` is the explicit
exception for diagnosing a stopped service against the locally configured
store.

The browser receives server-sent change notifications after CLI or other
browser writes. Ordinary pages reload from the server; the shopping page
performs its existing state merge. The service worker still caches the PWA
shell and shopping state for offline use, and `/api/` stays network-only.

## CLI

```bash
gusto list                          # all recipes
gusto list --max-time 30            # only recipes with a known duration <= 30
gusto home                          # server, service and active data directory
gusto search "linsen kokos"         # full-text incl. ingredients in the body
gusto tags --all                    # all tag facets and configured tags
gusto categories assign diet vegan # classify a tag through the service
gusto categories add season "Saison" # create another ordered facet
gusto show spaghetti-carbonara
gusto new "Title" --tags a,b --duration 25 --servings 2
gusto show <slug> --json            # obtain the current revision
gusto content set <slug> --file recipe.md --revision <revision>
gusto set <slug> --title "New title" --revision <revision>
gusto set <slug> --servings 4 --revision <revision>
gusto set <slug> --clear-servings --revision <revision>
gusto shopping preview-recipe <slug> --servings 4 --json
gusto shopping add-recipe <slug> --servings 4 --revision <recipe_revision>
gusto backup create --file backup.zip
gusto backup preview --file backup.zip --json
gusto backup restore --file backup.zip --token <token>
gusto cooked <slug>                 # cooked today -> log
gusto suggest --days 7              # suggestions for the next meal
gusto delete <slug>                 # reversibly archive Markdown + metadata + images
gusto archive list
gusto archive show <slug>
gusto archive restore <slug>
gusto archive purge <slug> --yes    # permanent; refused while shopping items refer to it
gusto check                         # full data integrity; hard errors exit 1
gusto image list <slug>             # cover and gallery images
gusto image add <slug> <path> --role result --caption "Serviert" --cover
gusto image set <slug> <id> --role step --caption "Nach dem Anbraten"
gusto image cover <slug> <id>       # select the top image
gusto image remove <slug> <id>
gusto favorites match "200 g Spaghetti"  # show the ranked household choice
gusto favorites match "Marmelade" --store REWE  # only what we buy at REWE
gusto favorites add "Spaghetti" --alias "200 g Spaghetti"
gusto favorites product-add Spaghetti "De Cecco n. 12" --brand "De Cecco" --image photo.png
gusto favorites product-move Spaghetti <id> 1
gusto shopping add "Parmesan" --source spaghetti-carbonara
gusto shopping add-many "Milch" "Brot" "6 Eier"  # one atomic group
gusto shopping remove-done         # remove every checked item
gusto shopping clear               # empty the complete visible list
gusto update                        # verified release update + rollback on failure
gusto install-skill vbot            # install/replace ~/.vbot/skills/gusto
gusto status                        # current-user service status
gusto uninstall                     # interactive: app only or app + data
```

The recipe edit examples are alternatives: read `show --json` again after each
change and use its current `revision`. A stale revision is rejected; merge any
concurrent changes before retrying. For adjusted shopping portions, review the
preview warnings and use its `recipe_revision`. Review backup effects before
restoring with the preview's `token`; restore creates a safety backup. Archives
are limited to 64 MiB compressed, 128 MiB unpacked and 32 MiB per file.

Every command takes `--json` for machine-readable output; normal remote
commands also accept global `--server URL`, with `GUSTO_URL` as the environment
equivalent. Expected command
failures then return `{ "ok": false, "error": "..." }` on stdout with exit
code 1. `check --json` returns the full server diagnostics with `ok: false` and
exit 1 instead; `edit --json` downloads to a temporary editor file and uploads
the final content. `serve --json`
validates the web runtime first and emits a startup object only when that
validation succeeds. Parser/usage errors remain on stderr with exit code 2.

A lost reply to a mutation can report `outcome_unknown:true`; inspect the
affected state on the same server before repeating that change. Read-only
queries can be repeated. Headless recipe replacement uses `content set --file`
(UTF-8, optional BOM, or UTF-16 with BOM) or `--stdin` (UTF-8). Input files
remain in place after upload. Interactive edit failures retain a recovery copy
and report its `recovery_path`.

`cooked --date` accepts only a valid `YYYY-MM-DD` no later than today.
`--max-time`, `log --days`, and `suggest --days` must be positive;
`suggest --limit` is nonnegative and `0` returns no candidates. `set` requires
at least one change. `new` and `set --tags` warn when tags have no named facet;
they remain stored under `Sonstige` until an agent resolves them with
`categories assign`. Category definition, ordering, assignment and removal all
go through `gusto categories ...`, never direct JSON edits. `--max-time`
excludes recipes whose duration is unknown. `shopping add --source` accepts
only an active recipe,
and the sourced item participates in the same duplicate-import guard as
`shopping add-recipe`. A rejected recipe import reports the number of visible
sourced items. `shopping remove-done` removes checked items; `shopping clear`
empties the complete visible list. Both use sync-safe tombstones and have no
CLI restore operation.

Recipe deletion is deliberately reversible by default. `delete` moves the
recipe's Markdown, complete metadata, and owned images together into
`archive/<slug>/`; log entries and visible shopping items retain their slug and
link to the archived view. `archive restore` reverses the move. Only
`archive purge --yes` destroys the snapshot, and it refuses while visible
shopping items still refer to that recipe.

## Layout

```
recipes/            the recipes as .md (the heart, pure content)
images/<slug>/      recipe images copied into and owned by Gusto
archive/<slug>/     reversible recipe snapshots (Markdown + metadata + images)
images/_favorites/  preferred-product images copied into and owned by Gusto
data/recipes.json   metadata of all recipes
data/categories.json tag categories, assignment and display order
data/favorites.json shared shopping needs, exact aliases, ranked products
data/log.json       cooking log
gusto/core/      all the logic
gusto/api.py       anonymous versioned command API + change events
gusto/client.py    standard-library HTTP client
gusto/cli.py       CLI assembly; _cli_*.py own remote/local command groups
gusto/service.py   current-user service registration and control
gusto/update.py    verified side-by-side update with rollback
gusto/uninstall.py safe managed-runtime removal lifecycle
gusto/skill_install.py local version-matched agent-skill delivery
gusto/web.py       the FastAPI web app and API host
gusto/templates/   Jinja2 templates
gusto/static/      CSS + JS
install.py          managed local installer implementation
install.ps1         public Windows bootstrap
install.sh          public Linux bootstrap
gusto.settings.json development instance data selection
deploy/             platform user-autostart compatibility helpers
scripts/build_release.py  build direct, manifest-hashed public release assets
scripts/run_quality.py    one cross-platform entry point for every quality gate
scripts/ci_smoke_install.py  release install/lifecycle smoke test
scripts/            end-to-end tests of the web UI (Playwright)
skill/gusto/        self-contained agent operating guide
```

## User autostart

Autostart is part of a successful installation:

- Windows registers a limited-privilege Scheduled Task for the current user's
  logon and an entry under the current user's Installed Apps.
- Linux writes `~/.config/systemd/user/gusto.service` and enables it with
  `systemctl --user enable --now`.

Both start the service immediately and restart it after failures. Neither
requires administrator rights, neither runs before user login, and uninstall
removes the integration. The scripts under `deploy/` remain compatibility
entry points for existing source-based setups; new systems use the public
one-shot installer.

## Uninstall

Run the installed command without options for a human-readable choice:

```bash
gusto uninstall
```

The safe default removes the application, autostart integration, and Gusto's
exact Windows `PATH` entry while preserving recipes, images, shopping data, and
the cooking log. Deleting the store is a separate destructive choice and shows
its exact path before requiring `DATEN LÖSCHEN`.

Agents and scripts use an explicit, non-interactive scope:

```bash
gusto uninstall --keep-data --json
gusto uninstall --delete-data --yes --json
gusto uninstall --delete-data --dry-run --json
```

The command only accepts a managed installed virtual environment; it refuses a
source checkout, system Python, user home, filesystem root, temporary root, or
an unrecognized data directory. On Windows a one-shot windowless helper removes
the active runtime after `gusto.exe` exits. Its completion or failure is written
to the temporary log path returned by the command.

## Tests

All checkout scratch files live in the ignored `.tmp/` directory. Direct test
and script calls configure Python and child-process temp automatically; release
builds also keep intermediate build trees there. Browser runs retain their
throwaway data and screenshots together in `.tmp/browser-*/` (PWA: `.tmp/pwa-*/`).
For ad-hoc tools, set `TMPDIR`, `TEMP`, and `TMP` to the absolute repository
`.tmp` path and save their outputs there. Close running tests/preview servers
and recover any retained editor copies before clearing this directory.
Installed packages without a source checkout keep the caller's temp policy;
atomic replacement files remain beside their targets on the same filesystem.

```bash
python scripts/run_quality.py scripts  # every standalone contract/core test
python scripts/run_quality.py browser  # Chromium UI + offline PWA
python scripts/run_quality.py all      # complete local quality gate

python scripts/browser_check.py    # starts a server against throwaway data and
                                   # clicks through the web UI in a real browser
python scripts/pwa_check.py        # offline / sync / service worker
python tests/test_api.py           # anonymous command API + SSE journal
python tests/test_shopping.py      # core shopping-list logic
python tests/test_favorites.py     # aliases, rankings, product cards/images
python tests/test_merge.py         # full-state sync merge rule
python tests/test_recipes.py       # recipe/search/log/suggestion core logic
python tests/test_cli.py           # remote CLI and local recovery commands
python tests/test_skill_install.py # vBot skill install and full replacement
python tests/test_service.py       # per-user service lifecycle
python tests/test_update.py        # verified update and rollback
python tests/test_autostart.py     # windowless Windows launcher and exit codes
python tests/test_uninstall.py     # safe app/data removal lifecycle
python tests/test_packaging.py     # fresh-install dependency declaration
python tests/test_paths.py         # Windows/Linux data-directory contract
python tests/test_temp.py          # repository temp and child-process isolation
python tests/test_ci.py            # CI matrix, permissions and release gates
```

`.github/workflows/quality.yml` runs automatically for pull requests and pushes
to `main`. The script suite covers Python 3.10 (the supported minimum) and the
current Python release on both Ubuntu and Windows. Real Chromium browser and PWA
checks also run on both operating systems. External actions are pinned to exact
commits and receive weekly Dependabot update PRs; failed browser jobs retain
their throwaway data and screenshots for seven days.

## Publishing a release

Keep `pyproject.toml` and `gusto/__init__.py` on the same version, then push the
matching `v<version>` tag. The release workflow first reuses the complete normal
CI, then builds the release once. That exact immutable workflow artifact is
installed on fresh Ubuntu and Windows runners. The smoke test verifies the
one-shot bootstrap, per-user service, health contract, browser UI/PWA shell,
remote CLI mutation, restart, `gusto update --check`, and data-preserving
uninstall. Only after both installations pass does the final job create build
provenance attestations and publish the stable manifest, Wheel, installer,
Windows Python runtime, and one-shot scripts. There is no outer Gusto release
ZIP. Write, OIDC, and attestation permissions exist only in that final job.

The public installation URLs become usable after the first successful tagged
release; publishing the tag is intentionally not part of a local install/build.

## Roadmap

- [x] Data model + core + CLI
- [x] Web UI (FastAPI, responsive & nice)
- [x] Shopping list & PWA (offline + sync)
- [x] Shared preferred products (exact aliases, ranking, photos, offline view)
- [x] Multiple stored images per recipe (cover + gallery, CLI + web camera/library)
- [x] Reversible recipe archive (complete snapshots, restore, guarded purge)
- [x] Managed user service, shared CLI/browser API, live events, and offline PWA
- [x] Public no-admin installers and verified `gusto update` with rollback
- [ ] Weekly plan
