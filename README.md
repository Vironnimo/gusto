# Gusto

Ein kleines, **markdown-basiertes Rezept-System** zum Selbsthosten – gedacht
für einen Raspberry Pi im lokalen Netzwerk, erreichbar von jedem Gerät.

- **Rezepte sind reine Markdown-Dateien** in `recipes/` – direkt les- und
  editierbar, kein Lock-in, kein Frontmatter.
- **Metadaten** (Tags, Dauer, Portionen, zuletzt gekocht) liegen gebündelt in
  `data/recipes.json`, das Koch-Logbuch in `data/log.json`.
- **Zwei gleichwertige Oberflächen über demselben Kern** (`recipe/core.py`):
  eine **CLI** (auch für Agents; Anleitung in [CLAUDE.md](CLAUDE.md) und als
  Skill unter `skill/gusto/`) und eine schöne **Web-UI**. Es gibt kein
  Feature, das nur die eine kann.

## Installation

```bash
python -m venv .venv
# Windows:        .venv\Scripts\activate
# Linux/macOS/Pi: source .venv/bin/activate
pip install -e ".[web]"     # mit Web-UI; ohne [web] reicht für die reine CLI
```

Die CLI läuft auch ganz ohne Abhängigkeiten: `python -m recipe list`

## Web-Oberfläche starten

```bash
recipe serve                 # http://0.0.0.0:8000 – im ganzen LAN erreichbar
recipe serve --port 9000     # anderer Port
```

Dann im Browser `http://<rechner-oder-pi>:8000` öffnen.

## CLI

```bash
recipe list                          # alle Rezepte
recipe search "linsen kokos"         # Volltext inkl. Zutaten im Text
recipe show spaghetti-carbonara
recipe new "Titel" --tags a,b --dauer 25 --portionen 2
recipe set <slug> --portionen 4      # Metadaten ändern
recipe cooked <slug>                 # heute gekocht -> Logbuch
recipe suggest --days 7              # Vorschläge fürs nächste Essen
recipe delete <slug>
recipe check                         # Konsistenz Index <-> .md
```

An **jedem** Befehl liefert `--json` eine maschinenlesbare Ausgabe.

## Aufbau

```
recipes/            Die Rezepte als .md (das Herzstück, reiner Inhalt)
data/recipes.json   Metadaten aller Rezepte
data/log.json       Koch-Logbuch
recipe/core.py      die gesamte Logik
recipe/cli.py       die CLI
recipe/web.py       die FastAPI-Web-App
recipe/templates/   Jinja2-Templates
recipe/static/      CSS + JS
deploy/             systemd-Unit für den Raspberry Pi
scripts/            End-to-End-Test der Web-UI (Playwright)
skill/gusto/      Skill zur Bedienung per CLI (für Agents)
```

## Auf dem Raspberry Pi (Autostart)

1. Projekt nach z.B. `/home/pi/recipes` kopieren, venv anlegen,
   `pip install -e ".[web]"`.
2. `deploy/gusto.service` an deine Pfade anpassen und einrichten:
   ```bash
   sudo cp deploy/gusto.service /etc/systemd/system/
   sudo systemctl enable --now gusto
   ```
3. Erreichbar unter `http://<pi-hostname>.local:8000`.

Mit der Umgebungsvariable `RECIPE_HOME` lässt sich der Datenordner
(`recipes/` + `data/`) frei platzieren – praktisch, um ihn z.B. als eigenes
git-Repo zu sichern.

## Tests

```bash
python scripts/browser_check.py    # startet einen Server gegen Wegwerf-Daten und
                                   # klickt die Web-UI im echten Browser durch
```

## Roadmap

- [x] Datenmodell + Core + CLI
- [x] Web-UI (FastAPI, responsiv & schön)
- [ ] Agent-verwaltete Rezeptbilder
- [ ] Einkaufsliste & Wochenplan
