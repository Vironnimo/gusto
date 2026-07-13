# Gusto

A small, **markdown-based recipe system** for self-hosting – made for a
Raspberry Pi on the local network, reachable from any device.

- **Recipes are plain Markdown files** in `recipes/` – directly readable and
  editable, no lock-in, no frontmatter.
- **Metadata** (tags, duration, servings, last cooked, image references) live
  bundled in `data/recipes.json`; Gusto stores recipe images under `images/`,
  and the cooking log lives in `data/log.json`.
- **Two equal surfaces over the same core** (`gusto/core.py`): a **CLI** (also
  for agents; guide in [CLAUDE.md](CLAUDE.md) and as a skill under
  `skill/gusto/`) and a polished **web UI**. No feature exists in only one of them.

The UI and the data fields are German; the code is English.

## Installation

```bash
python -m venv .venv
# Windows:        .venv\Scripts\activate
# Linux/macOS/Pi: source .venv/bin/activate
pip install -e ".[web]"     # with web UI; without [web] the pure CLI is enough
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
gusto cooked <slug>                 # cooked today -> log
gusto suggest --days 7              # suggestions for the next meal
gusto delete <slug>
gusto check                         # consistency index <-> .md
gusto image list <slug>             # cover and gallery images
gusto image add <slug> <path> --role result --caption "Serviert" --cover
gusto image set <slug> <id> --role step --caption "Nach dem Anbraten"
gusto image cover <slug> <id>       # select the top image
gusto image remove <slug> <id>
```

Every command takes `--json` for machine-readable output.

## Layout

```
recipes/            the recipes as .md (the heart, pure content)
images/<slug>/      recipe images copied into and owned by Gusto
data/recipes.json   metadata of all recipes
data/log.json       cooking log
gusto/core.py      all the logic
gusto/cli.py       the CLI
gusto/web.py       the FastAPI web app
gusto/templates/   Jinja2 templates
gusto/static/      CSS + JS
deploy/             systemd unit for the Raspberry Pi
scripts/            end-to-end tests of the web UI (Playwright)
skill/gusto/        skill for operating it via the CLI (for agents)
```

## On the Raspberry Pi (autostart)

1. Copy the project to e.g. `/home/pi/gusto`, create a venv,
   `pip install -e ".[web]"`.
2. Adjust `deploy/gusto.service` to your paths and install it:
   ```bash
   sudo cp deploy/gusto.service /etc/systemd/system/
   sudo systemctl enable --now gusto
   ```
3. Reachable at `http://<pi-hostname>.local:8000`.

With the environment variable `GUSTO_HOME` the recipe store (`recipes/` +
`images/` + `data/`) can be placed anywhere – handy for backing it up as its
own git repo.

## Tests

```bash
python scripts/browser_check.py    # starts a server against throwaway data and
                                   # clicks through the web UI in a real browser
python scripts/pwa_check.py        # offline / sync / service worker
python tests/test_shopping.py      # core shopping-list logic
python tests/test_merge.py         # full-state sync merge rule
python tests/test_recipes.py       # recipe/search/log/suggestion core logic
python tests/test_cli.py           # agent-facing JSON CLI
python tests/test_packaging.py     # fresh-install dependency declaration
```

## Roadmap

- [x] Data model + core + CLI
- [x] Web UI (FastAPI, responsive & nice)
- [x] Shopping list & PWA (offline + sync)
- [x] Multiple stored images per recipe (cover + gallery, agent-managed via CLI)
- [ ] Weekly plan
