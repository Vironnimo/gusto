# Phase 1: Server-API und Core-Vertrag

**Goal:** Der laufende Gusto-Server stellt eine vollständige, anonyme,
versionierte Command-API, Health-Informationen und eine persistente
Änderungsrevision bereit.

## Required decisions

- `POST /api/v1/command` verwendet den in der Plan-README festgelegten
  Envelope aus Operation, Arguments und optionalen Base64-Attachments.
- Kein Token, keine Anmeldung, keine IP-Einschränkung.
- Lifecycle-Operationen sind nicht Teil dieser API.
- Core-Fachregeln bleiben unverändert und weiterhin allein in
  `gusto/core.py`.
- Die vorhandenen Markdown-/JSON-Formate ändern sich nicht; lediglich eine
  additive Änderungsrevision darf im Datenordner entstehen.

## Tasks

- Änderungsjournal in Core ergänzen — read:
  [.vorch/domain-maps/catalog.md, .vorch/domain-maps/shopping.md,
  .vorch/domain-maps/surfaces.md], files:
  [gusto/core.py, tests/test_recipes.py]
  - Erfolgreiche `_locked_mutation`-Operationen schreiben nach Freigabe der
    Fachlocks eine atomare, monotone Revision mit den betroffenen Ressourcen.
  - Parallele Mutationen dürfen keine Revision verlieren; ein eigener kurzer
    Event-Lock serialisiert nur das Journal.
  - Reads und fehlgeschlagene/idempotente No-op-Operationen erzeugen nach
    Möglichkeit kein irreführendes Fachereignis.
  - Core erhält eine nicht-mutierende Abfrage für Revision und Ressourcen.

- Tiefe Command-API implementieren — read:
  [.vorch/domain-maps/catalog.md, .vorch/domain-maps/shopping.md,
  .vorch/domain-maps/surfaces.md], files:
  [gusto/api.py, tests/test_api.py]
  - FastAPI-`APIRouter` mit `/api/v1/health`, `/api/v1/command` und
    `/api/v1/events`.
  - Jede vorhandene Fach-CLI-Fähigkeit erhält genau eine dokumentierte
    Operation und JSON-ready Ergebnisform.
  - `recipe.content.set` schreibt Inhalt über `core.update_recipe(...,
    content=...)` und erhält die H1-Invariante.
  - Anhänge werden streng auf Shape, dekodierte Größe und erlaubten
    Operationskontext geprüft, in einem Temp-Verzeichnis materialisiert und
    immer bereinigt.
  - Expected Core errors werden konsistent in 4xx plus
    `{"ok": false, "error": ...}` übersetzt.
  - SSE sendet initial die aktuelle Revision, danach nur neue Revisionen,
    Heartbeats zur Verbindungsstabilität und beendet sauber bei Disconnect.

- Router in die Web-App integrieren — read:
  [.vorch/domain-maps/surfaces.md], files: [gusto/web.py]
  - Router einmalig registrieren.
  - Bestehende `/api/shopping`- und `/api/favorites`-PWA-Endpunkte bleiben
    kompatibel.
  - Web-Routen rufen weiterhin Core direkt im selben Prozess auf.

## Dependencies

Keine Implementierungsabhängigkeit zwischen Core/API-Agent und CLI-Agent:
Request-/Response- und Operation-Namen sind in der Plan-README fest. Die
`gusto/web.py`-Integration erfolgt erst, wenn `gusto/api.py` importierbar ist.

## Agent ownership

- Server/API-Agent: `gusto/core.py`, `gusto/api.py`, `tests/test_api.py`,
  `tests/test_recipes.py`.
- Root-Agent: ausschließlich der Router-Glue in `gusto/web.py`.

## Done when

- Health liefert Version, Datenpfad und Revision aus einem isolierten Store.
- Jede Operation hat Happy-Path-, Invalid-Input- und Not-found-Abdeckung.
- Ein Datei-Anhang wird erfolgreich importiert und bei Erfolg wie Fehler
  temporär bereinigt.
- Mutationen erhöhen die Revision; Reads nicht.
- Zwei kurz aufeinanderfolgende/parallel abgeschlossene Mutationen erzeugen
  beobachtbar unterschiedliche Revisionen.
- SSE liefert eine nachträgliche Mutation ohne Browser-Polling aus.

