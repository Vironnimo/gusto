# Phase 5: Distribution, Dokumentation und Gesamtprüfung

**Goal:** Alle öffentlichen Verträge, Agent-Anleitungen und Release-Artefakte
beschreiben denselben server-zentrierten Installations-/Updatebetrieb und die
vollständige Testmatrix besteht.

## Required decisions

- Skill-Installation bleibt außerhalb des App-Installers.
- `SKILL.md` enthält nur den kurzen Installations-Einstieg; Details liegen in
  `references/installation.md`.
- Normale Agentenbefehle verwenden den Server; kein Checkout-Fallback.
- Offline-PWA bleibt ausdrücklich dokumentiert.
- Authentifizierung/Tokens werden nicht dokumentiert, weil sie nicht
  existieren.

## Tasks

- Öffentliche Projektdokumentation aktualisieren — read:
  [.vorch/domain-maps/surfaces.md, .vorch/domain-maps/catalog.md,
  .vorch/domain-maps/shopping.md], files:
  [README.md, CLAUDE.md]
  - Öffentliche Windows-/Linux-One-liner.
  - Definition einer betriebsbereiten Installation: installiert, User-
    Autostart registriert, gestartet, Health geprüft.
  - `gusto update` als einziger normaler Updateweg.
  - Server-zentrierte CLI, Server-unavailable-Fehler und lokale Lifecycle-
    Ausnahmen.
  - Offline-PWA als bewusste lokale Replik.
  - Neue stabile Installations-/Daten-/Wrapper-Pfade und Uninstall-Vertrag.

- Skill vollständig synchronisieren — files:
  [skill/gusto/SKILL.md, skill/gusto/references/cli.md,
  skill/gusto/references/installation.md]
  - Kurzer Abschnitt „App installieren“ verlinkt die neue Referenz.
  - Referenz enthält direkte Skript-URLs, Voraussetzungen, OS-Ergebnis,
    Health-Verifikation und `gusto update`.
  - Normale Workflows prüfen `home`/`status`, sprechen über die Server-API und
    fallen nie auf `python -m gusto` oder Store-Dateien zurück.
  - Rezeptinhalt nutzt neuen CLI-Inhaltsbefehl; Bildpfade werden vom Client
    hochgeladen.
  - Offline-PWA- und Telegram-Verträge bleiben erhalten.

- Projektwissen nach implementierter Realität aktualisieren — read:
  [.vorch/workflows/domain-map-workflow.md], files:
  [.vorch/PROJECT.md, .vorch/GLOSSARY.md,
  .vorch/domain-maps/surfaces.md, .vorch/domain-maps/catalog.md,
  .vorch/domain-maps/shopping.md]
  - Erst nach vollständiger Verifikation, nicht als Vorausbehauptung.
  - Begriffe `Gusto-Dienst` und `betriebsbereite Installation` präzise vom
    installierten Dateibaum und von der Offline-PWA-Replik abgrenzen.
  - Domain Maps auf API, Service-Lifecycle, Inhaltsupload und Events anpassen.
  - Diese Dateien bleiben ausschließlich im Besitz des Root-Agenten.

- Integrierte Testmatrix ausführen und Fixes dem ursprünglichen Dateiowner
  zuordnen — files: [nur fehlschlagende Owner-Dateien]
  - `python tests/test_packaging.py`
  - `python tests/test_shopping.py`
  - `python tests/test_favorites.py`
  - `python tests/test_merge.py`
  - `python tests/test_concurrency.py`
  - `python tests/test_recipes.py`
  - `python tests/test_cli.py`
  - `python tests/test_api.py`
  - `python tests/test_service.py`
  - `python tests/test_update.py`
  - `python tests/test_autostart.py`
  - `python tests/test_uninstall.py`
  - `python tests/test_paths.py`
  - `python scripts/browser_check.py`
  - `python scripts/pwa_check.py`
  - `python scripts/build_release.py`

- Release-Artefakt visuell/inhaltlich prüfen — files: [keine]
  - ZIP-Inhalt, Manifest, SHA-256 und Skill-Kopie vergleichen.
  - Windows-/Linux-Dry-run aus entpacktem Bundle.
  - Keine echten Nutzdaten, Settings, Tokens oder lokale absolute Pfade im
    Bundle.

## Dependencies

Dokumentation beschreibt nur implementierte und geprüfte Verträge; sie folgt
Phasen 1–4. Projektwissen wird unmittelbar vor Änderung gemäß
Domain-Map-Workflow erneut gelesen. Browser-/PWA-Gates laufen erst nach
Integration aller Server-/CLI-/Eventteile.

## Agent ownership

Root-Agent besitzt alle Dokumentations- und `.vorch`-Dateien. Subagents liefern
nur verifizierte Fakten und ändern diese Dateien nicht.

## Done when

- README, CLAUDE, Skill-Hauptdatei, CLI- und Installationsreferenz widersprechen
  sich in keinem Installations-, Server-, Update-, Offline- oder
  Deinstallationspunkt.
- Release-Bundle enthält eine bytegleiche vollständige Skill-Kopie.
- Alle genannten Tests bestehen gegen isolierte Daten.
- Browser- und PWA-Läufe erzeugen aktuelle Screenshots und verändern keine
  echten Daten.
- Der Plan kann anschließend als umgesetzt markiert werden; er wird gemäß
  Plan-Skill nicht gestaged oder committed.

