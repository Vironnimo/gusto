# Gusto

A small, **markdown-based recipe system** for self-hosting – made for a
Raspberry Pi on the local network, reachable from any device.

- **Recipes are plain Markdown files** in `recipes/` – directly readable and
  editable, no lock-in, no frontmatter.
- **Metadata** (tags, duration, servings, last cooked) live bundled in
  `data/recipes.json`, the cooking log in `data/log.json`.
- **Two equal surfaces over the same core** (`recipe/core.py`): a **CLI** (also
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

The CLI runs without any dependencies: `python -m recipe list`

## Start the web UI

```bash
recipe serve                 # http://0.0.0.0:8000 – reachable across the LAN
recipe serve --port 9000     # different port
```

Then open `http://<machine-or-pi>:8000` in the browser.

## CLI

```bash
recipe list                          # all recipes
recipe search "linsen kokos"         # full-text incl. ingredients in the body
recipe show spaghetti-carbonara
recipe new "Title" --tags a,b --duration 25 --servings 2
recipe set <slug> --servings 4       # change metadata
recipe cooked <slug>                 # cooked today -> log
recipe suggest --days 7              # suggestions for the next meal
recipe delete <slug>
recipe check                         # consistency index <-> .md
```

Every command takes `--json` for machine-readable output.

## Layout

```
recipes/            the recipes as .md (the heart, pure content)
data/recipes.json   metadata of all recipes
data/log.json       cooking log
recipe/core.py      all the logic
recipe/cli.py       the CLI
recipe/web.py       the FastAPI web app
recipe/templates/   Jinja2 templates
recipe/static/      CSS + JS
deploy/             systemd unit for the Raspberry Pi
scripts/            end-to-end tests of the web UI (Playwright)
skill/gusto/        skill for operating it via the CLI (for agents)
```

## On the Raspberry Pi (autostart)

1. Copy the project to e.g. `/home/pi/recipes`, create a venv,
   `pip install -e ".[web]"`.
2. Adjust `deploy/gusto.service` to your paths and install it:
   ```bash
   sudo cp deploy/gusto.service /etc/systemd/system/
   sudo systemctl enable --now gusto
   ```
3. Reachable at `http://<pi-hostname>.local:8000`.

With the environment variable `RECIPE_HOME` the data folder (`recipes/` +
`data/`) can be placed anywhere – handy for backing it up as its own git repo.

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
- [ ] Agent-managed recipe images
- [ ] Weekly plan
