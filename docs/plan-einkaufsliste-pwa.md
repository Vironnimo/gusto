# Umsetzungsplan: Einkaufsliste + PWA

## Ziel
Integrierte Einkaufsliste in Gusto. Zutaten eines Rezepts per Knopf (Web)
bzw. per CLI/Agent auf die Liste setzen. Unterwegs **offline** nutzbar als
**PWA**, die zu Hause im LAN mit dem Gusto-Server synchronisiert. Der Host bleibt die
einzige Quelle der Wahrheit. Datenfluss ist asymmetrisch: *Schreiben* zu Hause
(Agent/Web), *Lesen + Abhaken* unterwegs (offline).

## Prinzip für parallele Subagent-Arbeit (das Wichtigste)
Damit mehrere Subagents **gleichzeitig** arbeiten, ohne sich gegenseitig zu
überschreiben:
1. **Contract-first.** Vor jeder Parallel-Welle steht der Kontrakt fest
   (Datenmodell, Core-Signaturen, API-Form, CLI-Spec, Asset-Liste). Erst dann
   wird aufgefächert.
2. **Disjunkte Dateien.** Jeder Subagent besitzt klar abgegrenzte Dateien (siehe
   Tabellen). **Keine zwei Subagents editieren dieselbe Datei.**
3. **Isolation per git-Worktree.** Jeder Subagent läuft in eigenem Worktree
   (`isolation: "worktree"`). → **Voraussetzung: `git init`** (Projekt ist noch
   kein Repo).
4. **Integrations-Checkpoint** nach jeder Welle: zusammenführen, Tests
   (`browser_check` + CLI) grün, dann erst weiter.

**Legende:** 🔒 sequenziell (blockiert die nächste Welle) · ⚡ **MUSS an N
parallele Subagents** (gleichzeitig, getrennte Dateien).

---

## Voraussetzung 🔒 (einmalig)
`git init` + erster Commit des aktuellen Stands. Ohne Repo keine
Worktree-Isolation für die Subagents.

---

## Phase 0 — Kontrakt 🔒 (1 Agent / Haupt-Session, blockiert Phase 1)
Festlegen, als Stubs committen, BEVOR Phase 1 startet:

- **`data/shopping_list.json`-Schema (sync-fähig):**
  ```json
  { "items": [ {
      "id": "uuid", "text": "200 g Spaghetti", "quantity": "200 g",
      "checked": false, "source": "spaghetti-carbonara",
      "created_at": "2026-06-23T18:00:00.123Z",
      "updated_at": "2026-06-23T18:00:00.123Z",
      "deleted": false
  } ] }
  ```
  `updated_at` (UTC ISO) treibt later „letzter gewinnt"; `deleted` =
  Tombstone, damit Löschungen synchron propagieren.
- **Core-Signaturen** (nur Stubs + Docstrings): `parse_zutaten`,
  `shopping_load/save/add/add_rezept/list/toggle/remove/remove_done/clear/merge`.
- **Zutaten-Parsing-Regel:** Bullet-Items (`-`/`*`) unter `## Zutaten` bis zur
  nächsten `##`-Überschrift; v1 = ganze Zeile als `text`.
- **CLI-Spec:** `gusto shopping list|add|rezept|check|uncheck|remove|remove-done|clear`
  (alle mit `--json`).
- **Schon mit Blick auf Phase 2:** Merge-Regel = pro `id` neuestes
  `updated_at` gewinnt, Tombstones propagieren.

→ Ergebnis: ein Commit mit Stubs + Kontrakt. Daran hängen alle weiteren Agents.

---

## Phase 1 — Einkaufsliste als Feature ⚡ MUSS an 3 Subagents
Gegen den Phase-0-Kontrakt, gleichzeitig, getrennte Dateien:

| Subagent | Auftrag | Besitzt (exklusiv) |
|---|---|---|
| **1A · Core** | `parse_zutaten` + alle `shopping_*`-Funktionen (außer `merge`) implementieren; Tests | `gusto/core.py` (Einkauf-Abschnitt), `tests/test_shopping.py` |
| **1B · CLI** | `gusto shopping …`-Subcommands gegen den Kontrakt, alle mit `--json` | `gusto/cli.py` (Einkauf-Abschnitt) |
| **1C · Web** | Routen + Listen-Seite + „Zutaten auf die Liste"-Knopf am Rezept + Styles | `gusto/web.py` (Einkauf-Routen), `gusto/templates/shopping.html`, `gusto/templates/recipe.html` (nur der Knopf), `gusto/static/style.css` (Einkauf-Styles) |

Hinweis: 1B/1C schreiben gegen die **Signaturen** und laufen erst nach dem Merge
mit 1A grün — genau dafür ist Contract-first da.

### Checkpoint 1 🔒
Zusammenführen → `python -m gusto shopping …` prüfen, `browser_check.py` um die
Einkaufs-Flows erweitern (Rezept→Liste, Häkchen, Clear). Grün? → weiter.

---

## Phase 2 — PWA + Offline + Sync

### Phase 2.0 — Sync-Kontrakt 🔒 (kurz, 1 Agent)
- `shopping_merge(remote_items)` finalisieren (letzter gewinnt pro `id` +
  Tombstones).
- **API-Form:** `GET /api/shopping` → `{items}`; `POST /api/shopping/sync`
  (Body `{items}`) → gemergte `{items}` zurück. Voll-State-Sync (bei der
  Listengröße völlig ausreichend, kein Delta-Protokoll nötig).
- **Client-Store-Schema** (localStorage reicht bei der Größe; IndexedDB
  optional) + Verhalten: optimistisch lokal abhaken, Sync bei `online`-Event
  und App-Start.
- **App-Shell-Asset-Liste** (was der Service-Worker cachen muss) — damit 2B und
  2C dieselben Pfade kennen.

### Phase 2.1 ⚡ MUSS an 3 Subagents
| Subagent | Auftrag | Besitzt (exklusiv) |
|---|---|---|
| **2A · Sync-Server** | `shopping_merge` + `GET /api/shopping` + `POST /api/shopping/sync` | `gusto/core.py` (merge), `gusto/web.py` (API-Abschnitt) |
| **2B · PWA-Schale** | `manifest.webmanifest`, Icons, Service-Worker (App-Shell cachen, Offline-Fallback), Registrierung | `gusto/static/manifest.webmanifest`, `gusto/static/sw.js`, `gusto/static/icons/*`, `gusto/templates/base.html` (manifest-Link + SW-Registrierung + theme-color) |
| **2C · Offline-Client** | Lokaler Store, optimistische Häkchen offline, Sync-Logik gegen die API | `gusto/static/shopping-client.js`, `gusto/templates/shopping.html` (Client-Anbindung) |

2A & 2C arbeiten gegen den 2.0-API-Kontrakt; 2B ist davon unabhängig
(braucht nur die Asset-Liste).

### Checkpoint 2 🔒
Zusammenführen → Playwright-Offline-Test: Liste online laden →
`context.set_offline(True)` → Häkchen setzen → wieder online → Sync prüfen.
Zusätzlich: Manifest + SW-Registrierung da, Zwei-Geräte-Merge simulieren
(zwei Browser-Kontexte). Grün? → fertig.

---

## Bewusst NICHT parallel (sequenziell)
`git init` · Phase 0 & 2.0 (Kontrakte = Linchpins) · beide Checkpoints
(Integration + Test).

## Subagent-Briefing (Vorlage, je Subagent)
1. Pfad zu **diesem Plan** + `CLAUDE.md` lesen.
2. **Nur die eigene Tabellen-Zeile** anfassen (exklusiver Datei-Besitz).
3. Auf dem **Kontrakt-Commit** aufsetzen, gegen die fixen Signaturen arbeiten.
4. Eigene Tests schreiben. Iron Rule beachten: kein Web-only-Feature, jedes
   CLI-Kommando kann `--json`.

## Ablauf in einem Satz
`git init` → 🔒 Phase 0 → ⚡ Phase 1 (3 parallel) → 🔒 Checkpoint 1 →
🔒 Phase 2.0 → ⚡ Phase 2.1 (3 parallel) → 🔒 Checkpoint 2. Zwei Parallel-Wellen
à 3 Subagents.
