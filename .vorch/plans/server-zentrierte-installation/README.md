# Plan: Server-zentriertes Gusto mit User-Installation und Update

**Größe:** Large

**Goal:** Gusto wird als dauerhaft laufender, benutzerbezogener Dienst
installiert; Browser und normale CLI-Befehle verwenden denselben serverseitigen
Core, offene Browser sehen Online-Änderungen automatisch, die Einkaufs-PWA
bleibt offline-fähig, und `gusto update` aktualisiert die Anwendung mit
Rollback-Möglichkeit.

## Context

Der heutige Installer legt eine virtuelle Umgebung, Instanz-Settings und unter
Windows einen PATH-Eintrag an, startet aber nicht die eigentliche
Webanwendung. Plattform-Autostart ist ein separater optionaler Schritt. Normale
CLI-Befehle importieren `gusto.core` im eigenen Prozess und schreiben direkt in
dieselben Markdown-/JSON-Dateien, die der Webserver bei späteren Requests neu
liest. Es gibt keine allgemeine Änderungssignalisierung an bereits geöffnete
Browser. Die Einkaufs-PWA ist die beabsichtigte Ausnahme: Sie hält eine
offline-fähige Replik mit Tombstones in `localStorage` und merged bei
Konnektivität mit dem Server.

Das Repository besitzt noch keine GitHub-Release-Automation und keine
öffentlichen Tags/Releases. `install.py` kann nur aus einem Checkout oder einem
bereits übertragenen Release-Bundle installieren. Die vorhandenen
systemd-/Task-Scheduler-Adapter registrieren den Server zwar, sind aber nicht
Teil eines einheitlichen Installationsvertrags.

## Confirmed requirements

- Browser-UI und normale CLI-Fachbefehle sprechen mit demselben laufenden
  Gusto-Server; der Core führt die Fachoperationen im Serverprozess aus.
- Normale CLI-Fachbefehle besitzen keinen stillen direkten
  Dateisystem-/Core-Fallback.
- Lokale Lifecycle- und Recovery-Befehle dürfen den Server umgehen:
  `serve`, `home`, `status`, `start`, `stop`, `restart`, `update`, `uninstall`
  sowie ein ausdrücklich angeforderter Offline-Check.
- Die Server- und CLI-API ist im lokalen Netz ohne Token, Anmeldung oder
  sonstige Authentifizierung erreichbar.
- Die Offline-Funktionalität der Einkaufs-PWA einschließlich lokaler
  Mutationen, Tombstones und Full-State-Merge bleibt erhalten.
- Unter Windows startet Gusto benutzerbezogen bei Anmeldung; es muss nicht vor
  der Anmeldung verfügbar sein.
- Installation, Autostart, Update und Deinstallation benötigen weder unter
  Windows noch unter Linux Administratorrechte.
- `gusto update` ist der normale Updateweg; ein erneuter Aufruf des
  Erstinstallationsskripts wird nicht als regulärer Update-Workflow
  dokumentiert.
- Windows und Linux erhalten je ein öffentlich abrufbares
  One-shot-Installationsskript.
- Der Gusto-Skill wird nicht durch den App-Installer installiert. Er enthält
  lediglich eine ausgelagerte Anleitung für die App-Installation.
- Die vorhandenen Markdown-/JSON-Daten und PWA-Sync-Daten bleiben kompatibel.
- MCP bleibt ausgeschlossen.

## Scope

### In

- Versionierte anonyme Command-API und Health-/Event-Endpunkte im Webserver.
- CLI-Clienttransport für alle vorhandenen Rezept-, Archiv-, Bild-, Log-,
  Such-, Vorschlags-, Favoriten- und Einkaufsbefehle.
- API-basierte Rezepttextbearbeitung und Dateiübertragung für Rezept- und
  Produktbilder.
- Explizite lokale Service-/Recovery-Befehle.
- Live-Aktualisierung geöffneter Browser über Server-Sent Events.
- Unverändertes Offline-First-Verhalten der Einkaufs-PWA mit zusätzlichem
  Online-Event-Trigger.
- Benutzerbezogener Windows-Task und Linux-`systemd --user`-Dienst.
- Side-by-side installierte Anwendungs-Versionen, atomarer Versionswechsel und
  Update-Rollback.
- Windows-Registrierung unter „Installierte Apps“ und Startmenü-Link zum
  lokalen Web-UI.
- Öffentliche PowerShell-/POSIX-Shell-Bootstrapper und GitHub-Release-Workflow.
- Vollständige Agent-, Skill-, README-, Release- und Projekt-Dokumentation.

### Out

- Authentifizierung, Tokens, Benutzerkonten oder Berechtigungsrollen.
- Systemweite Dienste, Start vor Benutzeranmeldung, `sudo` oder
  Administratorrechte.
- Ablösung der Markdown-/JSON-Speicherung durch eine Datenbank.
- Entfernen oder Vereinfachen des Offline-PWA-Merge-Vertrags.
- Automatisches Veröffentlichen eines echten GitHub-Releases ohne
  ausdrückliche Autorisierung zum Taggen/Pushen; der Workflow und die
  Release-Artefakte werden vorbereitet und lokal geprüft.
- Eine neue öffentliche Stabilitätsgarantie für die interne API außerhalb der
  gemeinsam ausgelieferten Server-/CLI-Version.

## Architecture decisions

### One authoritative runtime

Der installierte Gusto-Dienst besitzt den normalen Fachzugriff. Browser-Routen
und die Command-API rufen `gusto.core` im selben Serverprozess auf. Die CLI
serialisiert Befehlsparameter und Dateien zur API und formatiert die Antwort;
sie importiert für normale Operationen keinen lokalen Persistenzpfad.

Abgelehnt ist ein automatischer Offline-Fallback der CLI: Er würde erneut zwei
Produktionspfade schaffen, Änderungen am laufenden Dienst vorbeiführen und
Fehler als scheinbar erfolgreiche Writes in einer anderen Instanz tarnen.

### Command-oriented HTTP contract

Ein neues tiefes Modul `gusto/api.py` besitzt die komplette
maschinenlesbare, versionierte Command-Oberfläche:

- `GET /api/v1/health` liefert Version, Status, aktive Dateninstanz und aktuelle
  Änderungsrevision.
- `POST /api/v1/command` akzeptiert
  `{"operation": "...", "arguments": {...}, "attachments": [...]}`.
- Anhänge enthalten Name, Originaldateiname und Base64-Inhalt; der Server
  begrenzt und validiert sie, materialisiert sie nur temporär und übergibt den
  Pfad an die bestehenden Core-Operationen.
- Erfolg liefert `{"ok": true, "result": ...}`.
- Erwartete Fehler liefern `{"ok": false, "error": "..."}` mit geeignetem
  4xx-Status; unerwartete Fehler bleiben Serverfehler.
- Lifecycle-, Installations-, Update- und Uninstall-Operationen werden von
  diesem Endpunkt abgewiesen.

Die Operation-Namen bilden die vorhandenen CLI-Fähigkeiten ab:
`catalog.list`, `catalog.search`, `tags.list`, `recipe.show`,
`recipe.create`, `recipe.content.set`, `recipe.update`, `recipe.cooked`,
`log.list`, `suggest.list`, `check.run`, `recipe.archive`,
`archive.list|show|restore|purge`, `image.list|add|set|cover|remove`,
alle vorhandenen `favorites.*`- und `shopping.*`-Operationen.

Die Browser-Routen machen keine HTTP-Selbstaufrufe. Sie rufen im selben
Serverprozess weiterhin den Core auf; die Command-API ist der Transport für
externe maschinenlesbare Clients.

### Client discovery

`gusto/client.py` besitzt URL-Auflösung, HTTP, JSON-Fehlerabbildung,
Attachment-Encoding und Erreichbarkeitsfehler. Priorität:

1. explizites globales `--server`,
2. `GUSTO_URL`,
3. `server_url` aus der Instanzkonfiguration,
4. `http://127.0.0.1:8000`.

Die vorhandene `data_dir`-Konfiguration bleibt rückwärtskompatibel.
`gusto home --json` zeigt zusätzlich die gewählte Server-URL und den
Service-Status.

### Live changes without weakening offline behavior

Der Core pflegt nach erfolgreichen Mutationen eine atomare,
ressourcenbezogene Änderungsrevision. `GET /api/v1/events` streamt Änderungen
als Server-Sent Events. `app.js` verbindet sich online mit diesem Stream.
Normale Seiten laden sich bei einer relevanten Änderung neu; die Einkaufsseite
stößt stattdessen ihren bestehenden Full-State-Sync an. Offline bleibt
`shopping-client.js` vollständig lokal und synchronisiert erst bei
Wiederverbindung. `/api/` und der SSE-Stream bleiben im Service Worker
network-only.

### Side-by-side runtime and rollback

Die verwaltete App-Wurzel enthält stabile Integrationsdateien und versionierte
virtuelle Umgebungen:

```text
<app-root>/
  install-state.json
  current.json
  bin/
    gusto.cmd | gusto
  versions/
    <version>/
      <venv>
      gusto.settings.json
```

Ein Update installiert und prüft zuerst eine neue Version, stoppt erst für den
atomaren Umschaltpunkt den User-Dienst, stellt Task/Unit und `current.json` auf
die neue Version, startet und prüft `/api/v1/health`. Bei Fehler werden
Pointer und Dienstaktion auf die vorige Version zurückgestellt. Mindestens die
aktuelle und die vorherige Version bleiben für Rollback erhalten.

### User-only OS integration

- Windows: Task Scheduler mit `AtLogOn` für den aktuellen Benutzer,
  unmittelbarer Start nach Installation, Restart-on-failure, keine Elevation;
  HKCU-Uninstall-Eintrag, exakter Benutzer-PATH-Eintrag und Startmenü-URL.
- Linux: Unit unter `~/.config/systemd/user/gusto.service`,
  `systemctl --user enable --now`, kein `/etc`, kein `sudo`, Start erst mit der
  Benutzer-Session.

## Milestones

| Milestone | Observable result |
|---|---|
| M1 Server contract | Health- und Command-API führen alle vorhandenen Fachoperationen im Serverprozess aus. |
| M2 CLI cutover | Normale CLI-Befehle funktionieren gegen einen Server und scheitern ohne Server strukturiert ohne Write. |
| M3 Live + offline | CLI-/Web-Änderungen erscheinen online automatisch; die PWA arbeitet und merged weiterhin offline. |
| M4 Managed lifecycle | One-shot-Install installiert und startet User-Autostart; `gusto update` wechselt geprüft und rollback-fähig die Version. |
| M5 Distribution | Release-Bundle, Workflow, Skill und Dokumentation bilden denselben Vertrag ab; alle Gates bestehen. |

## Phase files

1. [Phase 1 – Server-API und Core-Vertrag](phase-1-server-api.md)
2. [Phase 2 – CLI als Server-Client](phase-2-cli-client.md)
3. [Phase 3 – Live-UI und Offline-PWA](phase-3-live-pwa.md)
4. [Phase 4 – User-Installation und Update](phase-4-install-update.md)
5. [Phase 5 – Distribution, Dokumentation und Gesamtprüfung](phase-5-release-docs-verification.md)

## Parallel implementation ownership

Nach Festschreiben dieses Plans können drei Subagents gleichzeitig arbeiten:

- **Server/API-Agent:** ausschließlich `gusto/core.py`, neues `gusto/api.py`,
  `tests/test_api.py` und serververtragsspezifische Ergänzungen in
  `tests/test_recipes.py`.
- **CLI-Agent:** ausschließlich neues `gusto/client.py`, `gusto/cli.py` und
  `tests/test_cli.py`; er implementiert gegen den in diesem Plan festgelegten
  API-Vertrag und berührt weder Core noch Web.
- **Installer-Agent:** ausschließlich neue Lifecycle-/Updater-Module,
  Installationsskripte, Deployment-Adapter, Release-Build/Workflow und deren
  Tests; er berührt zunächst nicht `gusto/cli.py`.
- **Root-Agent:** besitzt `gusto/web.py`, Templates/Static/PWA,
  Integrations-Glue, Dokumentation, gemeinsame Verifikation und alle
  sequenziellen Konfliktauflösungen.

Alle Agenten lesen vor Beginn `.vorch/PROJECT.md`, `.vorch/GLOSSARY.md`,
`.vorch/domain-maps/surfaces.md` und zusätzlich die in ihrer Phase genannten
Domain Maps. Sie sind nicht allein im Worktree, revertieren keine fremden
Änderungen und melden notwendige Scope-Abweichungen vor dem Schreiben.

## Risks and mitigations

- **Die anonyme API kann im LAN destruktive Operationen ausführen.** Das ist
  eine ausdrücklich bestätigte Produktentscheidung. Dokumentation und
  Endpunktnamen dürfen keine Authentifizierung suggerieren.
- **CLI-Refactor kann bestehende JSON-Ausgaben brechen.** API-Ergebnisse werden
  gegen jeden bestehenden `tests/test_cli.py`-Vertrag geprüft; `--json` bleibt
  außen unverändert, obwohl der Transport innen neu ist.
- **Live-Events können Reload-/Sync-Schleifen erzeugen.** Events tragen
  Revision und Ressource; Clients ignorieren bereits verarbeitete Revisionen,
  und reine Reads erzeugen nie Events.
- **PWA-Offline-Änderungen dürfen nicht durch Push überschrieben werden.** Die
  Einkaufsseite verwendet auf Event ausschließlich den vorhandenen
  Merge-Endpunkt und niemals blindes Server-Replace.
- **Update kann eine aktive Windows-Runtime sperren.** Neue Versionen werden
  side-by-side installiert; der laufende CLI-/Server-Baum wird nicht in place
  überschrieben.
- **Legacy-Installationen liegen als flaches Venv vor.** Der erste neue
  Installer/Updater erkennt das bestehende Layout, übernimmt Datenpfad und
  Task/PATH, prüft die neue Version und entfernt die alte Runtime erst nach
  erfolgreichem Umschalten.
- **Release-Tests dürfen nicht vom echten GitHub-Stand abhängen.** Download,
  Manifest und Checksumme sind injizierbar und werden gegen lokale
  temporäre HTTP-/Datei-Fixtures getestet.
- **User-systemd läuft nicht vor Login.** Das entspricht der bestätigten
  Anforderung; es wird kein Linger oder systemweiter Dienst eingerichtet.

## New dependencies

Keine Produktionsabhängigkeiten. HTTP-Client, Base64-Anhänge, Checksummen,
Archivverarbeitung, Server-Sent Events und Prozesssteuerung verwenden
Python-Standardbibliothek beziehungsweise bereits vorhandenes
FastAPI/Starlette/Uvicorn. Der explizite `test`-Extra deklariert Playwright und
den von aktuellen Starlette-TestClients benötigten `httpx2`-Adapter für
reproduzierbare Release-Gates.

## Done when

- Alle vorhandenen CLI-Fachbefehle erreichen nachweislich den Server-Core.
- Ohne laufenden Server liefert ein Fachbefehl Exit 1 und bei `--json`
  `{"ok": false, "error": "..."}`, ohne Dateien zu verändern.
- Lifecycle-Befehle funktionieren ohne erreichbare Command-API.
- Rezepttext und Bilder können vollständig über die CLI/API übertragen werden;
  Agenten müssen für normale Workflows keine Store-Datei direkt schreiben.
- Eine CLI- oder Browser-Mutation aktualisiert relevante bereits geöffnete
  Browser innerhalb eines definierten kurzen Zeitfensters.
- Die PWA besteht weiterhin Offline-, Reconnect-, Tombstone- und
  Zwei-Geräte-Merge-Prüfungen.
- Windows- und Linux-Installationsskript installieren aus einem lokalen
  Release-Fixture ohne Adminrechte, registrieren User-Autostart, starten den
  Server und bestehen den Health-Check.
- `gusto update` prüft Manifest/Checksumme, schaltet auf eine neue Version und
  rollt bei fehlendem Health-Check auf die vorige zurück.
- Uninstall entfernt App-Versionen und OS-Integration, bewahrt Daten
  standardmäßig und löscht sie nur nach bestehender expliziter Bestätigung.
- Release-ZIP enthält Wheel, Installer, Bootstrapper, Deployment-Dateien,
  Manifestgrundlage und vollständigen Skill.
- Alle projektspezifischen Unit-/Packaging-/Browser-/PWA-Gates bestehen gegen
  Wegwerf-Daten.
