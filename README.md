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
regular Python wheel, the standalone installer, and the optional autostart
adapters. The target computer needs neither the private repository nor GitHub
credentials.

Build the archive once in a development checkout:

```powershell
py -3 scripts/build_release.py
```

Copy `dist/gusto-<version>-release.zip` to the Windows or Linux target and
extract it. Run the following commands inside that extracted folder.

### Windows

```powershell
py -3 install.py
& "$env:LOCALAPPDATA\Programs\Gusto\Scripts\gusto.exe" serve
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

Application and data stay separate:

- Windows application: `%LOCALAPPDATA%\Programs\Gusto`
- Linux application: `~/.local/opt/gusto`
- Windows data: `%LOCALAPPDATA%\Gusto`
- Linux data: `$XDG_DATA_HOME/gusto`, otherwise `~/.local/share/gusto`

`gusto home` (`--json` for agents) shows the active location and why it was
chosen. `GUSTO_HOME` still overrides it for a portable store or server setup.
When the installer runs directly from a source checkout, existing checkout data
is copied into an empty user directory without deleting the original. Release
archives intentionally contain application code only; existing user data is
transferred separately.

### Development checkout

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# Linux:   source .venv/bin/activate
python -m pip install -e ".[web]"
```

Editable installs intentionally keep existing checkout data visible through the
legacy fallback. The pure CLI has no third-party dependencies.

## Start the web UI

```bash
gusto serve                 # http://0.0.0.0:8000 – reachable across the LAN
gusto serve --port 9000     # different port
```

Then open `http://<machine>:8000` in the browser.

## CLI

```bash
gusto list                          # all recipes
gusto home                          # active user-data directory
gusto search "linsen kokos"         # full-text incl. ingredients in the body
gusto show spaghetti-carbonara
gusto new "Title" --tags a,b --duration 25 --servings 2
gusto set <slug> --servings 4       # change metadata
gusto set <slug> --clear-servings   # remove optional metadata again
gusto cooked <slug>                 # cooked today -> log
gusto suggest --days 7              # suggestions for the next meal
gusto delete <slug>
gusto check                         # consistency index <-> .md
gusto image list <slug>             # cover and gallery images
gusto image add <slug> <path> --role result --caption "Serviert" --cover
gusto image set <slug> <id> --role step --caption "Nach dem Anbraten"
gusto image cover <slug> <id>       # select the top image
gusto image remove <slug> <id>
gusto favorites match "200 g Spaghetti"  # show the ranked household choice
gusto favorites add "Spaghetti" --alias "200 g Spaghetti"
gusto favorites product-add Spaghetti "De Cecco n. 12" --brand "De Cecco" --image photo.png
gusto favorites product-move Spaghetti <id> 1
```

Every command takes `--json` for machine-readable output.

## Layout

```
recipes/            the recipes as .md (the heart, pure content)
images/<slug>/      recipe images copied into and owned by Gusto
images/_favorites/  preferred-product images copied into and owned by Gusto
data/recipes.json   metadata of all recipes
data/favorites.json shared shopping needs, exact aliases, ranked products
data/log.json       cooking log
gusto/core.py      all the logic
gusto/cli.py       the CLI
gusto/web.py       the FastAPI web app
gusto/templates/   Jinja2 templates
gusto/static/      CSS + JS
install.py          cross-platform Windows/Linux installer
deploy/             optional Linux systemd + Windows logon autostart
scripts/build_release.py  build the transferable release archive
scripts/            end-to-end tests of the web UI (Playwright)
skill/gusto/        skill for operating it via the CLI (for agents)
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

Both helpers call the same bundled installer first, use the normal per-user
application directory, and then add only the platform-specific background-start
mechanism. `--install-dir` on Linux and `-InstallDir` on Windows override the
application location.

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
python tests/test_packaging.py     # fresh-install dependency declaration
python tests/test_paths.py         # Windows/Linux data-directory contract
```

## Roadmap

- [x] Data model + core + CLI
- [x] Web UI (FastAPI, responsive & nice)
- [x] Shopping list & PWA (offline + sync)
- [x] Shared preferred products (exact aliases, ranking, photos, offline view)
- [x] Multiple stored images per recipe (cover + gallery, CLI + web camera/library)
- [ ] Weekly plan
