# Gusto

A small, **markdown-based recipe system** for self-hosting on **Windows or
Linux**, reachable from any device on the local network. A Raspberry Pi is an
important Linux deployment target, not a separate or exclusive edition.

- **Recipes are plain Markdown files** in `recipes/` – directly readable and
  editable, no lock-in, no frontmatter.
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
- **Two equal surfaces over the same core** (`gusto/core.py`): a **CLI** (also
  for agents; guide in [CLAUDE.md](CLAUDE.md) and as a skill under
  `skill/gusto/`) and a polished **web UI**. No feature exists in only one of them.

The UI and the data fields are German; the code is English.

## Installation

Gusto needs Python 3.10 or newer. A transferable release archive contains a
regular Python wheel, the standalone installer, the optional autostart
adapters, and the complete `skill/gusto/` agent skill. The target computer
needs neither the private repository nor GitHub credentials.

Build the archive once in a development checkout:

```powershell
py -3 scripts/build_release.py
```

Copy `dist/gusto-<version>-release.zip` to the Windows or Linux target and
extract it. Run the following commands inside that extracted folder.

### Windows

```powershell
py -3 install.py
# Open a new PowerShell after installation:
gusto serve
```

### Linux

```bash
python3 install.py
"$HOME/.local/opt/gusto/bin/gusto" serve
```

Then open `http://<computer-name>:8000` from another device in the LAN, or
`http://localhost:8000` on the same computer. `python install.py --dry-run`
shows the planned paths without changing anything; `--cli-only` omits the web
dependencies. The same installer can run directly from a complete source
checkout, but the release archive is the normal deployment artifact.
On Windows the installer adds its command directory to the user `PATH`, so a
new console can invoke `gusto` directly.

### Agent skill

The extracted release contains the self-contained, generic skill under
`skill/gusto/`. Agent hosts import that complete folder according to their own
skill convention. `install.py` installs only the Gusto application and
deliberately does not mutate agent-host configuration.

Application and data stay separate:

- Windows application: `%LOCALAPPDATA%\Programs\Gusto`
- Linux application: `~/.local/opt/gusto`
- Windows data: `%LOCALAPPDATA%\Gusto`
- Linux data: `$XDG_DATA_HOME/gusto`, otherwise `~/.local/share/gusto`

Every instance owns a `gusto.settings.json` beside its application runtime. The
installer writes the production data path into the installed application
directory. The development checkout carries its own settings file with
`"data_dir": "gusto-dev"`; relative names resolve beside the normal platform
data directory. Development therefore uses `%LOCALAPPDATA%\gusto-dev` on
Windows or `~/.local/share/gusto-dev` on Linux without a different start command.

`gusto home` (`--json` for agents) shows the active location and why it was
chosen. `GUSTO_HOME` still overrides it for a portable store or server setup.
When the installer runs directly from a source checkout, existing checkout data
is copied into an empty user directory without deleting the original. Release
archives contain application code and the agent skill, but never existing user
data; user data is transferred separately.

### Development checkout

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# Linux:   source .venv/bin/activate
python -m pip install -e ".[web]"
```

The checkout's `gusto.settings.json` keeps manual development isolated from
production data. The pure CLI has no third-party dependencies.

## Start the web UI

```bash
gusto serve                 # http://0.0.0.0:8000 – reachable across the LAN
gusto serve --port 9000     # different port
```

Then open `http://<machine>:8000` in the browser.
Before reporting a successful start, `serve` validates every optional web
dependency. If a CLI-only installation is used, rerun `python install.py`
without `--cli-only`; in a development checkout use
`python -m pip install -e ".[web]"`. With `--json`, a missing dependency is a
structured command error and no `starting` object or traceback is emitted.

## CLI

```bash
gusto list                          # all recipes
gusto list --max-time 30            # only recipes with a known duration <= 30
gusto home                          # active user-data directory
gusto search "linsen kokos"         # full-text incl. ingredients in the body
gusto show spaghetti-carbonara
gusto new "Title" --tags a,b --duration 25 --servings 2
gusto set <slug> --title "New title" # changes metadata and Markdown H1 together
gusto set <slug> --servings 4       # change metadata
gusto set <slug> --clear-servings   # remove optional metadata again
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
gusto favorites add "Spaghetti" --alias "200 g Spaghetti"
gusto favorites product-add Spaghetti "De Cecco n. 12" --brand "De Cecco" --image photo.png
gusto favorites product-move Spaghetti <id> 1
gusto shopping add "Parmesan" --source spaghetti-carbonara
gusto shopping add-many "Milch" "Brot" "6 Eier"  # one atomic group
gusto shopping remove-done         # remove every checked item
gusto shopping clear               # empty the complete visible list
gusto uninstall                     # interactive: app only or app + data
```

Every command takes `--json` for machine-readable output. Expected command
failures then return `{ "ok": false, "error": "..." }` on stdout with exit
code 1. `check --json` returns the full diagnostics with `ok: false` and exit 1
instead; `edit --json` reports the completed editor process. `serve --json`
validates the web runtime first and emits a startup object only when that
validation succeeds. Parser/usage errors remain on stderr with exit code 2.

`cooked --date` accepts only a valid `YYYY-MM-DD` no later than today.
`--max-time`, `log --days`, and `suggest --days` must be positive;
`suggest --limit` is nonnegative and `0` returns no candidates. `set` requires
at least one change. `new` and `set --tags` warn when tags have no named facet;
they remain stored under `Sonstige`. `--max-time` excludes recipes whose
duration is unknown. `shopping add --source` accepts only an existing recipe,
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
data/favorites.json shared shopping needs, exact aliases, ranked products
data/log.json       cooking log
gusto/core.py      all the logic
gusto/cli.py       the CLI
gusto/uninstall.py safe installed-runtime removal lifecycle
gusto/web.py       the FastAPI web app
gusto/templates/   Jinja2 templates
gusto/static/      CSS + JS
install.py          cross-platform Windows/Linux installer
gusto.settings.json development instance data selection
deploy/             optional Linux systemd + Windows logon autostart
scripts/build_release.py  build the transferable release archive
scripts/            end-to-end tests of the web UI (Playwright)
skill/gusto/        self-contained CLI skill, also shipped in the release ZIP
```

## Optional autostart

Installation and autostart are deliberately separate. Gusto works normally
without either helper.

Linux systems with systemd, including a Raspberry Pi:

```bash
./deploy/install-systemd.sh
./deploy/install-systemd.sh --data-dir /srv/gusto --port 9000
```

Windows can start Gusto when the current user signs in:

```powershell
powershell -ExecutionPolicy Bypass -File .\deploy\install-windows-task.ps1
```

The Windows task calls the installed `gusto-autostart.exe` GUI launcher
directly, so sign-in does not open a console window. The normal `gusto serve`
command remains a Console application with its usual output. Autostart stdout,
stderr, and startup failures are written to
`%LOCALAPPDATA%\Gusto\gusto-autostart.log`; Task Scheduler also retains the
process result. Rerunning the adapter stops the active task before updating its
runtime, replaces an existing PowerShell-based task action, removes its obsolete
`start-gusto.ps1`, and restarts the task, so an existing installation does not
need to be recreated and no in-use Windows launcher blocks the update.

Both helpers call the same bundled installer first, use the normal per-user
application directory, and then add only the platform-specific background-start
mechanism. `--install-dir` on Linux and `-InstallDir` on Windows override the
application location.

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

```bash
python scripts/browser_check.py    # starts a server against throwaway data and
                                   # clicks through the web UI in a real browser
python scripts/pwa_check.py        # offline / sync / service worker
python tests/test_shopping.py      # core shopping-list logic
python tests/test_favorites.py     # aliases, rankings, product cards/images
python tests/test_merge.py         # full-state sync merge rule
python tests/test_recipes.py       # recipe/search/log/suggestion core logic
python tests/test_cli.py           # agent-facing JSON CLI
python tests/test_autostart.py     # windowless Windows launcher and exit codes
python tests/test_uninstall.py     # safe app/data removal lifecycle
python tests/test_packaging.py     # fresh-install dependency declaration
python tests/test_paths.py         # Windows/Linux data-directory contract
```

## Roadmap

- [x] Data model + core + CLI
- [x] Web UI (FastAPI, responsive & nice)
- [x] Shopping list & PWA (offline + sync)
- [x] Shared preferred products (exact aliases, ranking, photos, offline view)
- [x] Multiple stored images per recipe (cover + gallery, CLI + web camera/library)
- [x] Reversible recipe archive (complete snapshots, restore, guarded purge)
- [ ] Weekly plan
