# Küchenbuch — Projektkontext für Claude

Selbstgehostetes, **markdown-basiertes Rezept-System** für den Heimgebrauch.
Ziel: läuft auf einem Raspberry Pi und ist im lokalen Netz von jedem Gerät
erreichbar. Entstanden, um Papier-Rezepte abzulösen und z.B. Essensvorschläge
auf Basis der letzten Tage zu ermöglichen.

## Leitprinzipien (wichtig!)

1. **Agent-First.** Jedes Feature muss vollständig per CLI nutzbar sein, damit
   Agents die App genauso bedienen können wie ein Mensch. Das ist der Kern des
   Projekts, kein Nice-to-have. Eigentliches Ziel: man fragt einen Agent „was
   soll ich heute essen?" / „was mach ich mit diesen Zutaten?", und der Agent
   nutzt die CLI, um zu antworten.
2. **Eine Quelle der Wahrheit, mehrere dünne Hüllen.** Alle Logik lebt in
   `recipe/core.py`. CLI (`recipe/cli.py`) und Web (`recipe/web.py`) sind nur
   Hüllen darum. **Niemals** ein Feature nur in der Web-UI bauen — immer zuerst
   im Core + CLI. Jedes CLI-Kommando versteht `--json`.
3. **Rezepte sind reine Markdown-Dateien. KEIN Frontmatter.** Metadaten leben
   getrennt in `data/recipes.json`, verbunden über den `slug` (= Dateiname).
4. **KEIN MCP. Niemals.** Ausdrückliche, endgültige Entscheidung des Nutzers.
   Nicht vorschlagen, nicht bauen.
5. **Erst fragen, dann bauen.** Neue Features / größere Schritte NICHT ohne
   ausdrückliches Go umsetzen. Konzept vorstellen → Rückfrage → dann erst Code.
6. **Im echten Browser verifizieren.** Web-Änderungen mit
   `scripts/browser_check.py` (Playwright) gegen eine Wegwerf-Kopie der Daten
   prüfen und Screenshots erzeugen. Tests dürfen die echten Daten nie verändern.
7. **Live zeigen, nicht nur beschreiben.** Wenn etwas läuft, den Server selbst
   starten und einen anklickbaren Link geben — nicht nur erklären, wie man ihn
   startet.

## Datenmodell

| Ort | Inhalt |
|-----|--------|
| `recipes/<slug>.md` | Reiner Rezept-Inhalt (Markdown, beginnt mit `# Titel`). Kein Frontmatter. |
| `data/recipes.json` | Metadaten aller Rezepte: `slug`, `titel`, `tags`, `dauer_minuten`, `portionen`, `zuletzt_gekocht`. |
| `data/log.json` | Koch-Logbuch: `[{ "datum": "YYYY-MM-DD", "slug": ... }]`. |

`recipe new` schreibt .md UND Index-Eintrag. Wird eine .md von Hand angelegt,
den Eintrag in `data/recipes.json` ergänzen und `recipe check` laufen lassen.
Beim Speichern aus dem Web wird die erste Zeile der .md immer als `# {titel}`
neu geschrieben (der Titel ist ein eigenes Formularfeld, nicht im Body).

## Bedienung

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[web]"        # die reine CLI braucht keine Abhängigkeiten
recipe serve                   # Web auf 0.0.0.0:8000 (im ganzen LAN)
python -m recipe <befehl>      # CLI, falls nicht installiert
```

CLI: `list, search, show, new, set, edit, cooked, log, suggest, delete, check,
serve` — alle mit `--json`. `RECIPE_HOME` (Env) verschiebt den Datenordner
(`recipes/` + `data/`), praktisch auf dem Pi.

## Wichtige Dateien

- `recipe/core.py` — gesamte Logik (laden/speichern, Suche, Logbuch, Vorschläge)
- `recipe/cli.py` — die CLI
- `recipe/web.py` — FastAPI-App (server-gerendert, Jinja2)
- `recipe/templates/`, `recipe/static/` — UI + CSS/JS
- `scripts/browser_check.py` — End-to-End-Browsertest (Playwright)
- `deploy/kuechenbuch.service` — systemd-Unit für den Pi
- `AGENTS.md` — Kurz-Bedienungsanleitung speziell für Agents

## Technik / Stolperfallen

- Python (stdlib-only Core/CLI). Web: FastAPI + uvicorn + Jinja2 + markdown +
  `python-multipart` (Formulare). Tests: Playwright.
- Starlette ≥1.3: Signatur ist `TemplateResponse(request, "name.html", {...})`
  — `request` MUSS das erste Argument sein.
- Windows-Konsole (cp1252): stdout in CLI/Tests auf UTF-8 umgestellt, sonst
  brechen Zeichen wie „✓".

## Design

„Küchenbuch": warmes Kochbuch-Editorial, bewusst kein Dashboard. Cremefarbenes
Papier mit feiner Körnung, Paprika-Rot als Akzent, Kräuter-Grün. Fraunces
(Display-Serife) + Hanken Grotesk (Text). Nummerierte Schritte mit großen
Serifenziffern, Karten mit gestaffeltem Einblenden. UI und Datenfelder deutsch.

## Stand & Roadmap

**Fertig:** Datenmodell, Core, CLI, Web-UI (Liste/Suche/Tag-Filter, Rezept-
ansicht, anlegen/bearbeiten/löschen, „heute gekocht", Vorschläge, Logbuch),
404-Seite, Pi-Deployment (systemd), Browsertest.

**In Diskussion / geplant:**
- **Einkaufsliste + PWA** — ENTSCHIEDEN: offline-fähige **PWA** (kein App Store,
  eine Codebasis), die zu Hause im LAN mit dem Pi synct; WireGuard nur optionaler
  Bonus für Live-Sync unterwegs. Sync ist konfliktarm (pro Eintrag „letzter
  gewinnt" + Tombstones), weil der Datenfluss asymmetrisch ist (schreiben zu
  Hause, abhaken unterwegs). Detaillierter Umsetzungsplan mit erzwungener
  Subagent-Parallelisierung: **[docs/plan-einkaufsliste-pwa.md](docs/plan-einkaufsliste-pwa.md)**.
  Wird in 2 Wellen à 3 parallele Subagents gebaut (Contract-first, disjunkte
  Dateien, git-Worktrees).
- Agent-verwaltete Rezeptbilder.
- Wochenplan.
