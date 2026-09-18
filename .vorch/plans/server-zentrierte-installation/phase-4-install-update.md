# Phase 4: User-Installation, Autostart und `gusto update`

**Goal:** Öffentliche One-shot-Skripte installieren Gusto ohne Adminrechte als
laufenden User-Dienst; `gusto update` wechselt geprüft und rollback-fähig auf
das neueste Release.

## Required decisions

- Keine systemweite Installation, kein `sudo`, kein Administratorprozess.
- Windows startet bei Benutzeranmeldung; Linux verwendet `systemd --user`.
- Installation und Update sind getrennte User-Flows.
- Updates sind side-by-side; die aktive Runtime wird nicht in place
  überschrieben.
- GitHub Latest Release liefert ein Manifest, ein Release-ZIP und dessen
  SHA-256.
- Reale GitHub-Veröffentlichung erfolgt erst nach gesondert autorisiertem
  Tag/Push; Tests verwenden lokale Fixtures.

## Tasks

- Verwalteten Runtime-/Service-Lifecycle implementieren ⚡ *parallel zu Phase
  1/2* — read: [.vorch/domain-maps/surfaces.md], files:
  [gusto/service.py, tests/test_service.py]
  - App-Root, Versionsordner, `current.json` und `install-state.json` sicher
    auflösen und validieren.
  - Windows Task Scheduler: Current User, AtLogOn, sofortiger Start,
    Restart-on-failure, keine Elevation.
  - Linux: `~/.config/systemd/user/gusto.service`,
    `systemctl --user daemon-reload/enable --now`.
  - Plattformneutrale `status/start/stop/restart`-Ergebnisse mit JSON-ready
    Status.
  - Health-Wait mit Timeout gegen die konfigurierte Server-URL.
  - Dry-run und injizierbare Runner für hermetische Tests.

- Release-Download und atomaren Updater implementieren — files:
  [gusto/update.py, tests/test_update.py]
  - Manifest/Archiv laden, SHA-256 vor Extraktion prüfen, Pfadtraversal beim
    ZIP-Entpacken verhindern.
  - Semantische Versionsangabe validieren; `--check` verändert nichts.
  - Neue Venv unter `versions/<version>` erstellen und Wheel samt Web-Extra
    installieren.
  - Settings/Datenpfad übernehmen, Import-/CLI-Smoke-Test ausführen.
  - Dienst kurz stoppen, Pointer/Task/Unit umschalten, starten und Health
    prüfen.
  - Bei Fehler vorige Version vollständig wieder aktivieren; Fehler und
    Rollbackstatus maschinenlesbar melden.
  - Aktuelle plus vorige Version behalten; ältere verifizierte Versionen
    bereinigen.

- Öffentliche Bootstrapper und interne Installation umbauen — files:
  [install.ps1, install.sh, install.py, deploy/gusto.service,
  deploy/install-systemd.sh, deploy/install-windows-task.ps1,
  tests/test_packaging.py, tests/test_autostart.py, tests/test_paths.py]
  - PowerShell und POSIX-Shell laden Latest-Manifest/ZIP/Checksumme in einen
    sicheren Temp-Ordner und rufen den gebündelten Installer.
  - Erstinstallation erstellt Version, stabile Wrapper, Instanzzustand,
    OS-Integration, startet und health-checkt.
  - Windows registriert HKCU „Installed Apps“, exakten PATH-Eintrag und
    Startmenü-URL; alles wird vom Uninstaller exakt rückgängig gemacht.
  - Linux schreibt ausschließlich in User-Verzeichnisse.
  - Bestehende flache Installation wird erkannt, Datenpfad übernommen und
    erst nach erfolgreichem Wechsel entfernt.
  - `--dry-run`, alternative Install-/Datenpfade und lokale Release-URL für
    Tests bleiben verfügbar.

- Uninstall an verwaltete App-Wurzel anpassen — files:
  [gusto/uninstall.py, tests/test_uninstall.py]
  - Gesamte versionierte App-Wurzel statt nur `sys.prefix` erkennen.
  - User-Task/-Unit stoppen und entfernen.
  - Windows HKCU-Uninstall, Startmenü und exakten PATH-Eintrag entfernen.
  - Standard bewahrt Daten; bestehende starke Datenlöschbestätigung bleibt.
  - Legacy-Flat-Venv bleibt sicher deinstallierbar.

- Release-Erzeugung und Veröffentlichung vorbereiten — files:
  [scripts/build_release.py, .github/workflows/release.yml]
  - Bundle enthält Wheel, `install.py`, beide öffentlichen Bootstrapper,
    User-Service-Adapter und vollständigen Skill.
  - Build erzeugt Release-Manifest und SHA-256.
  - Tag-Workflow führt alle nicht-browserabhängigen Gates aus, baut Artefakte
    und hängt stabile Assetnamen an ein GitHub Release.

- Lifecycle-Befehle nach Abschluss des CLI-Agenten integrieren — files:
  [gusto/cli.py, tests/test_cli.py]
  - `status/start/stop/restart/update` samt `--json`, `update --check` und
    Dry-run/Fehlerverträgen.
  - Diese Befehle werden immer lokal dispatcht.
  - Integration erfolgt sequenziell durch Root oder nach expliziter Übergabe
    vom CLI-Agenten, nie parallel.

## Dependencies

Service-/Updater-Module und Bootstrapper können unabhängig von Phase 1/2
entwickelt werden. Der Health-Check nutzt den festgelegten
`/api/v1/health`-Vertrag. CLI-Parserintegration wartet auf Abschluss von
Phase 2.

## Agent ownership

Installer-Agent exklusiv bis zur Übergabe:
`gusto/service.py`, `gusto/update.py`, `gusto/uninstall.py`, `install.py`,
`install.ps1`, `install.sh`, `deploy/*`, `scripts/build_release.py`,
`.github/workflows/release.yml`, `tests/test_service.py`,
`tests/test_update.py`, `tests/test_packaging.py`, `tests/test_autostart.py`,
`tests/test_uninstall.py`, `tests/test_paths.py`.

Root/CLI-Agent integrieren erst danach die neuen Befehle in `gusto/cli.py`.

## Done when

- Beide Bootstrapper bestehen hermetische Erstinstallations- und
  Wiederholungsfehler-Tests ohne Elevation.
- User-Dienst läuft nach Installation und Health ist erreichbar.
- Update auf eine neue Fixture-Version erhält Daten und aktiviert die neue
  Runtime.
- Erzwungener Health-Fehler rollt Pointer und Dienst auf die alte Version
  zurück.
- `update --check` ist read-only; normaler Updateweg ist `gusto update`.
- Uninstall entfernt exakt App und Integration, standardmäßig nicht die Daten.

