# Gusto

A small, **markdown-based recipe system** for self-hosting – made for a
Raspberry Pi on the local network, reachable from any device.

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

### Raspberry Pi (empfohlen)

Nach dem Klonen reicht ein Befehl:

```bash
./deploy/install.sh
```

Der Installer prüft Python 3.10+, legt `.venv` an, installiert die Web-UI,
erzeugt die systemd-Unit mit dem **tatsächlichen Benutzer und Projektpfad** und
startet Gusto. Er wird ohne `sudo` aufgerufen und fragt nur für die systemd-
Schritte danach. Danach ist Gusto unter
`http://<pi-hostname>.local:8000` erreichbar und startet beim Booten mit.

Optional kann der Datenbestand getrennt vom Code liegen:

```bash
./deploy/install.sh --data-dir /home/meinname/gusto-daten
./deploy/install.sh --dry-run       # nur Pfade und systemd-Unit anzeigen
```

### Manuell / Entwicklung

```bash
python -m venv .venv
# Windows:        .venv\Scripts\activate
# Linux/macOS/Pi: source .venv/bin/activate
python -m pip install -e ".[web]"  # Web-UI; ohne [web] reicht es für die CLI
```

The CLI runs without any dependencies: `python -m gusto list`

## Start the web UI

```bash
gusto serve                 # http://0.0.0.0:8000 – reachable across the LAN
gusto serve --port 9000     # different port
```

Then open `http://<machine-or-pi>:8000` in the browser.

## CLI

```bash
gusto list                          # all recipes
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
deploy/             one-command Pi installer + systemd template
scripts/            end-to-end tests of the web UI (Playwright)
skill/gusto/        skill for operating it via the CLI (for agents)
```

## Betrieb auf dem Raspberry Pi

`deploy/install.sh` richtet den Autostart ein. Die Datei
`deploy/gusto.service` ist die dafür verwendete Vorlage und wird vom Installer
mit Benutzer, Projektpfad, Python-Pfad und `GUSTO_HOME` befüllt; sie soll nicht
unverändert nach `/etc/systemd/system/` kopiert werden. Für eine Installation
ohne Autostart gibt es `./deploy/install.sh --no-service`.

Mit `GUSTO_HOME` bzw. `--data-dir` kann der Rezeptbestand (`recipes/` +
`images/` + `data/`) getrennt vom Code liegen – praktisch für Backups als
eigenes Git-Repo.

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
```

## Roadmap

- [x] Data model + core + CLI
- [x] Web UI (FastAPI, responsive & nice)
- [x] Shopping list & PWA (offline + sync)
- [x] Shared preferred products (exact aliases, ranking, photos, offline view)
- [x] Multiple stored images per recipe (cover + gallery, CLI + web camera/library)
- [ ] Weekly plan
