# Phase 2: CLI als Server-Client

**Goal:** Alle normalen Fachbefehle verwenden die Server-API; lokale
Ausführung bleibt ausschließlich für Lifecycle und explizite Recovery.

## Required decisions

- Kein automatischer lokaler Core-/Dateisystem-Fallback.
- Server-Auflösung: `--server`, dann `GUSTO_URL`, dann Instanz-Settings, dann
  `http://127.0.0.1:8000`.
- Die äußeren Human- und `--json`-Verträge der bestehenden CLI bleiben
  kompatibel.
- `edit` ist ein lokaler Editor-Workflow über Server-GET/SET, nicht ein
  direkter Store-Editor.
- Für headless Agenten wird Rezeptinhalt über einen expliziten stdin-/Datei-
  fähigen CLI-Befehl gesetzt.

## Tasks

- HTTP-Clienttransport implementieren ⚡ *parallel zur Server-API* — read:
  [.vorch/domain-maps/surfaces.md], files:
  [gusto/client.py]
  - Standardbibliothek-HTTP mit definiertem Timeout und verständlichen
    Connection-/HTTP-/JSON-Fehlern.
  - Command-Envelope und Attachment-Encoding gemäß Plan-README.
  - Response-Unwrap für Erfolg sowie erwartete Fehler.
  - Health-Abfrage und Server-URL-Auflösung.
  - Keine Retry-Schleife für mutierende POSTs; eine nicht-mutierende
    Health-Abfrage darf begrenzt erneut versucht werden.

- CLI-Handler auf Remote-Ergebnisse umstellen — read:
  [.vorch/domain-maps/catalog.md, .vorch/domain-maps/shopping.md,
  .vorch/domain-maps/surfaces.md], files:
  [gusto/cli.py, tests/test_cli.py]
  - Vorhandene Parsernamen/Flags und JSON-Ergebnisformen erhalten.
  - `list`, `search`, `tags`, `show`, `new`, `cooked`, `log`, `suggest`,
    normales `check`, `set`, `delete`, Archiv, Bilder, Favoriten und Shopping
    mappen auf Command-Operationen.
  - `image add` und Produktbild-Flags lesen die Client-Datei und senden sie als
    Attachment.
  - `edit` lädt Inhalt, öffnet eine lokale Temp-Datei, sendet den geänderten
    Inhalt zurück und bereinigt die Temp-Datei.
  - Neuer agentenfähiger Inhaltsbefehl akzeptiert stdin oder `--file`, aber
    niemals beide.
  - Serverfehler behalten Exit 1; `--json` schreibt den bestehenden
    Fehler-Envelope auf stdout. Argparse bleibt Exit 2/stderr.
  - `home`, `serve`, Service-Lifecycle, `update`, `uninstall` und ein
    ausdrücklich markierter Offline-Check werden vor Remote-Dispatch lokal
    behandelt.
  - `home --json` zeigt Dateninstanz, Server-URL und Erreichbarkeit getrennt.

- Integrationsprüfung mit echtem isoliertem Uvicorn-Prozess — files:
  [tests/test_cli.py]
  - Test-Server mit `GUSTO_HOME` und zufälligem freien Loopback-Port.
  - Bestehende Befehlsmatrix läuft über HTTP.
  - Server gestoppt: Fachmutation scheitert strukturiert und Store-Snapshot
    bleibt bytegleich.
  - `home`, `serve`-Preflight und Offline-Check benötigen keinen Server.

## Dependencies

Der CLI-Agent arbeitet gegen den festgeschriebenen API-Vertrag. Die endgültige
Integration wird erst akzeptiert, wenn Phase 1 real erreichbar ist. Lifecycle-
Unterbefehle aus Phase 4 werden nach Abschluss des CLI-Agenten sequenziell in
`gusto/cli.py` ergänzt.

## Agent ownership

CLI-Agent exklusiv: `gusto/client.py`, `gusto/cli.py`, `tests/test_cli.py`.
Andere Agenten dürfen diese Dateien während dieser Phase nicht ändern.

## Done when

- Jeder bestehende Fachbefehl durchläuft in Tests einen echten HTTP-Server.
- Kein normaler Handler importiert oder ruft Persistenz-Core als Fallback auf.
- Human-Ausgabe und alle dokumentierten `--json`-Shapes bleiben stabil.
- Editieren, Inhaltssetzung und Bildimport funktionieren ohne direkte
  Store-Dateipfade.
- Ein nicht erreichbarer Server erzeugt weder Mutation noch Traceback.

