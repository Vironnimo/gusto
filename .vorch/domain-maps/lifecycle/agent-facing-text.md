# Lifecycle text reasons

This file records why the runtime sentences in the lifecycle modules and public bootstraps exist. Text cells give the sentence or an unmistakable start; substitutions such as paths and versions remain data. Reasons are inferred from the owning code and named contract tests, not claims of observed user incidents. Keep the row with the sentence when changing wording. CLI help/result formatting and shipped skill text have their own home in `cli/agent-facing-text.md`.

## Installer (`install.py`)

Evidence: target validation and `main`; `tests/test_paths.py`, `tests/test_packaging.py`, installer/repair cases in `tests/test_update.py`.

| Text | Reason |
|---|---|
| `Gusto als laufende Benutzer-App installieren.` | Defines installation success as a running per-user service, not merely copied files. |
| `App- und Datenpfad müssen getrennte, nicht verschachtelte Verzeichnisse sein.` | Prevents app removal/update from consuming the user's store through nested custom paths. |
| `App-Pfad ist kein Verzeichnis:` | Identifies the conflicting file instead of suggesting repair of an unrelated root. |
| `Der App-Pfad ist nicht leer und nicht als Gusto-Installation erkennbar:` | Stops claiming existing unrelated contents for first-install cleanup. |
| `mehrere Gusto-Wheels gefunden` | Avoids silently choosing a payload version from an ambiguous release folder. |
| `weder ein Gusto-Wheel noch ein vollständiger Projekt-Checkout gefunden` | Explains that the bootstrap payload/source is incomplete rather than a service problem. |
| `Version im Wheel-Namen nicht erkennbar:` | Rejects an unidentifiable payload before creating version directories. |
| `Projektversion ist nicht erkennbar` | Stops a checkout installation without a usable version identity. |
| `Bestehender fremder Symlink blockiert Gusto:` | Preserves another application's command link and names the collision. |
| `Bestehende Datei blockiert den Gusto-Befehl:` | Prevents overwriting an unrelated command file. |
| `Trockenlauf: Gusto {version} würde installiert.` | Distinguishes a non-mutating preview from a started service; plain dry-run case in packaging tests. |
| `Gusto {version} ist installiert und läuft.` | Reports real success only after registration and version/store-bound health verification. |
| `App:` | Exposes the selected application root, especially with a custom install path. |
| `Daten:` | Separates persistent household data from removable application files. |
| `Öffnen:` | Gives the configured local browser URL after installation or in the preview. |
| `Updates: gusto update` | Prevents using a second bootstrap run as the normal update path. |
| `Fehler:` | Marks plain-mode expected failure; JSON instead carries `ok:false,error`. |
| `Gusto braucht Python 3.10 oder neuer.` | Names the interpreter requirement before executing unsupported code. |
| `--port muss zwischen 1 und 65535 liegen.` | Makes an out-of-range numeric port an actionable installation error, including JSON mode. |
| `Der App-Root enthält nur einen Teil der Gusto-Installationsmarkierung;` | Refuses an ambiguous partially marked root rather than treating it as disposable. |
| `Die verwaltete Python-Runtime wurde unvollständig übergeben.` | Rejects partial bootstrap-to-installer runtime metadata before publishing state. |
| `Ein Windows-Release muss über install.ps1 mit seiner verifizierten Python-Runtime installiert werden.` | Directs callers to the bootstrap that supplies the verified private interpreter. |
| `Ungültige Python-Runtime:` | Keeps runtime validation failure in the installer's result/error contract. |
| `Gusto wird als normaler Benutzer und ohne sudo installiert.` | Prevents a root-owned installation from violating the per-user model. |
| `Gusto ist bereits installiert.` | Explains why an ordinary repeated installer call refuses the existing root. |
| `Für Updates 'gusto update' verwenden; für Reparatur den öffentlichen Installer mit -Repair/--repair starten.` | Separates update from repair and supplies the supported next operation. |
| `Der Installationszustand wurde zwischenzeitlich verändert.` | Identifies the ownership race detected after acquiring the lock; no stale cleanup is safe. |
| `Bitte Gusto erneut prüfen; für Updates 'gusto update' verwenden.` | Avoids blindly repeating first-install behavior against a concurrently created installation. |
| `Der App-Pfad wurde zwischenzeitlich belegt:` | Names a first-install directory that acquired foreign content after preflight. |
| `Es wurde nichts verändert.` | Records the effect of refusing that concurrent ownership change. |
| `Die Reparatur darf nicht von System-Python auf eine verwaltete Runtime umstellen.` | Prevents repair from silently changing the installation's interpreter ownership model. |
| `Die Python-Runtime des Reparaturpakets passt nicht zur installierten Runtime.` | Requires matching interpreter identity before replacing a broken private runtime. |
| `Die Python-Runtime ist beschädigt.` | Separates interpreter damage from an unhealthy Gusto service. |
| `Reparatur über das öffentliche install.ps1 erneut starten.` | Names the entry point capable of supplying the verified replacement interpreter. |
| `Die aktive Version {version} ist beschädigt, das Reparaturpaket enthält aber` | Gives both versions needed to choose a matching repair payload. |
| `Eine Reparatur darf kein verdecktes Update durchführen.` | Prevents treating a different payload as consent to change application version. |
| `Reparatur nicht möglich:` | Preserves the repair context while reporting the specific refusal. |
| `Die Runtime unter {runtime} ließ sich nicht vollständig entfernen.` | Prevents installing over a partly removed, possibly locked venv. |
| `Bitte den Gusto-Dienst beenden (gusto stop) und die Reparatur erneut starten.` | Supplies the bounded recovery action for a still-running locked runtime. |
| `Versionsordner existiert bereits:` | Refuses overwriting a first-install runtime whose ownership was not established. |
| `Installation oder Reparatur nicht möglich:` | Distinguishes shared-lock refusal from first-install failure cleanup. |
| `Installation fehlgeschlagen:` | Reports the terminal failure after cleanup attempts without claiming success. |

## Managed paths and runtime preparation (`gusto/runtime.py`)

Evidence: state/path/Python validators and install/repair/update preparation; `tests/test_service.py`, `tests/test_paths.py`, `tests/test_packaging.py` and `tests/test_update.py`.

| Text | Reason |
|---|---|
| `Ungültiger Versionspfad.` | Refuses a runtime path escaping the managed versions tree. |
| `Der App-Pfad ist zu breit für eine verwaltete Installation.` | Prevents claiming a filesystem root or home directory as app-owned. |
| `Ungültige Release-Version:` | Keeps version strings safe as one directory name and comparable as SemVer. |
| `{label} fehlt:` | Names the missing state/current/marker file, not a generic missing service. |
| `{label} ist nicht lesbar:` | Distinguishes corrupted/unreadable state from absence. |
| `{label} muss ein JSON-Objekt sein.` | Rejects a syntactically valid but structurally unusable state file. |
| `Unbekannte Version des Installationszustands.` | Refuses interpreting unsupported persisted schemas. |
| `Installationszustand enthält kein` | Names a missing required configuration field. |
| `Installationszustand enthält keinen gültigen Host.` | Stops malformed bind targets before producing service commands. |
| `Installationszustand enthält keinen gültigen Port.` | Stops invalid port types/ranges before rendering or probing the service. |
| `Installationszustand enthält keine gültige Python-Runtime.` | Makes malformed interpreter records a managed-state error. |
| `Aktiver Versionszeiger enthält keine Version.` | Refuses an active pointer without a runtime identity. |
| `Aktiver Versionszeiger verweist aus dem App-Root.` | Prevents selecting an unrelated executable directory through current state. |
| `Aktive Runtime fehlt:` | Names the missing version directory needed for repair. |
| `Ungültige Python-Version:` | Keeps interpreter versions suitable as managed directory names. |
| `Ungültiger Pfad der verwalteten Python-Runtime.` | Rejects interpreter-path escape from the managed Python tree. |
| `Ungültige SHA-256 der Python-Runtime.` | Requires usable integrity metadata before trusting a Python package. |
| `Python-Runtime-Quelle ist unvollständig:` | Names an extracted payload lacking its interpreter. |
| `Python-Runtime enthält einen symbolischen Link:` | Explains rejection of a payload link before copying external content. |
| `Python-Runtime-Quelle meldet nicht die erwartete Version/Architektur.` | Avoids marking a mismatched or wrong-architecture interpreter as verified. |
| `Vorhandene Python-Runtime ist nicht verifiziert:` | Refuses silently overwriting existing interpreter files outside explicit repair. |
| `Kopierte Python-Runtime hat die Prüfung nicht bestanden.` | Reports failed post-copy verification before publication. |
| `Installationszustand enthält keine Python-Runtime.` | Stops lifecycle work when no interpreter record exists. |
| `Installationszustand enthält eine ungültige Python-Runtime.` | Refuses malformed interpreter identity fields. |
| `Python-Runtime verweist aus dem App-Root.` | Prevents managed state from treating an external interpreter tree as app-owned. |
| `Verwaltete Python-Runtime ist nicht verwendbar.` | Separates a failed marker/executable probe from a Gusto version failure. |
| `System-Python der Installation ist nicht verwendbar.` | Names missing or app-nested system-Python state without choosing another interpreter silently. |
| `Unbekannte Python-Runtime-Art:` | Refuses unsupported interpreter ownership kinds. |
| `Virtuelle Umgebung konnte nicht erstellt werden` | Reports interpreter/venv creation failure before any wheel can be installed. |
| `Wheel-Installation fehlgeschlagen` | Distinguishes pip failure from the later installed-runtime verification and health checks. |
| `Import-/Web-Runtime-Prüfung fehlgeschlagen` | Prevents calling an installed-but-unusable runtime operational. |
| `Installation des Release-Wheels fehlgeschlagen:` | Keeps pip failure before activation distinct from rollback failure. |
| `Import-/Versionsprüfung der neuen Runtime fehlgeschlagen:` | Prevents activating a wheel that installed but cannot supply the expected package version. |
| `Web-Abhängigkeiten fehlen oder sind nicht importierbar: {', '.join(missing)}.` | Names missing web dependencies and distinguishes checkout installation from managed-app repair. |
| `Projekt-Checkout: python -m pip install -e ".[web]".` | Names missing web dependencies and distinguishes checkout installation from managed-app repair. |
| `Installierte App: den Installer derselben Gusto-Version im expliziten Reparaturmodus verwenden (--repair...` | Names missing web dependencies and distinguishes checkout installation from managed-app repair. |

## User service (`gusto/service.py`)

Evidence: health waiters and platform integration; `tests/test_service.py` and `tests/test_autostart.py`.

| Text | Reason |
|---|---|
| `Health-Antwort meldet nicht ok=true` | Prevents accepting a reachable but unready/unrecognized server as activation success. |
| `Health-Antwort stammt von Version` | Names actual/expected versions when a stale service answers on the target port. |
| `Health-Antwort verwendet Datenpfad` | Prevents accepting a server for another household store. |
| `Gusto ist unter {url} erreichbar, erfüllt aber nicht die Erwartung:` | Distinguishes identity mismatch from a retryable connection wait. |
| `Gusto wurde unter {url} nicht erreichbar:` | Names the endpoint and last failed response after the health deadline. |
| `Der bisherige Gusto-Prozess unter {url} wurde nicht beendet.` | Refuses starting over a health endpoint that never went down. |
| `systemd-Werte dürfen keine Steuerzeichen enthalten.` | Prevents state values from injecting additional unit lines. |
| `Befehl fehlgeschlagen (` | Carries platform-command exit/detail into the managed service failure contract. |
| `Autostart-Launcher fehlt:` | Prevents registering a task whose versioned launcher is absent. |
| `Eine fremde geplante Aufgabe namens Gusto blockiert die Installation.` | Preserves an unrelated same-name Scheduled Task instead of replacing it. |
| `Die bisherige Gusto-Aufgabe konnte nicht beendet werden.` | Stops registration when the old task did not stop within its deadline. |
| `Eine fremde geplante Aufgabe namens Gusto wird nicht entfernt.` | Prevents uninstall/cleanup from removing a task not owned by Gusto. |
| `Die laufende Gusto-Aufgabe konnte nicht beendet werden.` | Prevents reporting task removal while the old process remains running. |
| `User-Autostart wird nur unter Windows und Linux unterstützt.` | Refuses unsupported platform integration explicitly. |
| `Linux-User-Dienst konnte nicht vollständig deaktiviert werden:` | Reports partial disable failure even if unit-file cleanup succeeded. |
| `Unbekannte Service-Aktion:` | Refuses unsupported operations at the module boundary. |
| `Eine fremde geplante Aufgabe namens Gusto blockiert die Service-Steuerung.` | Protects unrelated same-name tasks during status/start/stop/restart. |
| `Gusto konnte nicht beendet werden.` | Reports the bounded task-stop failure before restart continues. |
| `Service-Steuerung wird nur unter Windows und Linux unterstützt.` | Separates unsupported local control from network service availability. |
| `Service-Status konnte nicht gelesen werden:` | Avoids converting a systemctl failure into a false stopped/running state. |

## Updates (`gusto/update.py`)

Evidence: manifest and payload validation, update lock, activation/rollback branches; `tests/test_update.py`.

| Text | Reason |
|---|---|
| `Release-URLs müssen HTTPS- oder lokale Fixture-Pfade sein.` | Refuses remote plaintext downloads while allowing hermetic fixtures. |
| `Release-Download fehlgeschlagen (` | Identifies the failing release location after bounded download retries. |
| `Release-Manifest ist nicht lesbar:` | Separates transport/decoding failure from absence of a newer version. |
| `Release-Manifest hat ein unbekanntes Format.` | Refuses unsupported manifest schemas. |
| `Release-Manifest enthält keine Version.` | Requires the version used for comparison and runtime selection. |
| `Release-Manifest enthält keine Assets.` | Stops a payload-free update manifest before preparation. |
| `Release-Manifest enthält Asset` | Names the missing required release component. |
| `Release-Asset` | Identifies invalid asset names or checksums before resolving/downloading them. |
| `Windows-Python-Asset ist unvollständig.` | Requires version/layout information needed to install the interpreter package. |
| `SHA-256-Prüfung für` | Reports expected/actual integrity mismatch and refuses executing the payload. |
| `Unsicherer ZIP-Eintrag:` | Rejects traversal/absolute archive entries before extraction. |
| `Symlink im Python-Paket ist nicht erlaubt:` | Prevents a payload link from escaping the extracted tree. |
| `ZIP-Eintrag verlässt das Ziel:` | Rejects resolved extraction targets outside the destination. |
| `Python-Runtime-Paket enthält tools/python.exe nicht.` | Names the required interpreter payload missing from an otherwise valid archive. |
| `Die Gusto-Update-Sperre ist nicht initialisiert.` | Distinguishes a short incomplete lock initialization from another operation holding the lock. |
| `Ein anderes Gusto-Update läuft bereits.` | Reports `update_locked` rather than starting competing preparation or activation. |
| `Release-Manifest enthält kein Wheel.` | Refuses update preparation without its Gusto payload. |
| `Installationszustand enthält keine Python-Runtime.` | Refuses choosing a replacement interpreter without the installation's ownership state. |
| `Release enthält keine Windows-Python-Runtime.` | Identifies the missing component required by a managed-Python installation. |
| `Das Release ersetzt dieselbe Python-Version mit anderem Inhalt;` | Prevents replacing an interpreter still referenced by retained runtimes under the same identity. |
| `Die installierte Python-Runtime ist beschädigt; bitte den öffentlichen Installer im Reparaturmodus starten.` | Directs damaged-interpreter recovery to the matching installer repair path. |
| `Update-Aktivierung fehlgeschlagen und Rollback ist fehlgeschlagen:` | Does not promise restored service when both activation and recovery failed. |
| `Update-Aktivierung fehlgeschlagen; vorige Version wurde wiederhergestellt:` | Reports a failed update with verified recovery, not successful activation of the new version. |

## Uninstall (`gusto/uninstall.py`)

Evidence: discovery, target safety, integration removal and helper scheduling; `tests/test_uninstall.py`.

| Text | Reason |
|---|---|
| `Deinstallation wird nur unter Windows und Linux unterstützt.` | Refuses unsupported platform cleanup. |
| `Ein Projekt-Checkout wird nicht mit 'gusto uninstall' gelöscht.` | Prevents deletion of the coding workspace as if it were an installed app. |
| `Der Befehl ist nur für eine installierte Gusto-Runtime gedacht.` | Explains the required execution context after checkout refusal. |
| `Gusto läuft nicht aus der aktiven verwalteten Version.` | Prevents a retained old runtime from uninstalling another active version. |
| `Die verwaltete Installation kann nicht verifiziert werden:` | Wraps state corruption in the uninstall/JSON failure contract. |
| `Gusto läuft nicht aus einer aktuellen verwalteten Installation.` | Refuses destructive discovery without the managed marker pair. |
| `Gusto läuft nicht aus einer eigenständigen verwalteten Installation.` | Rejects a package imported from outside the executing installed runtime. |
| `Die Installationsmarkierung fehlt:` | Names missing ownership evidence before deletion. |
| `Aus Sicherheitsgründen wird nichts gelöscht.` | Records that ownership/venv/launcher preflight refusal has no deletion effect. |
| `'{runtime}' ist keine eindeutig erkennbare virtuelle Umgebung.` | Requires the venv marker before claiming the runtime tree. |
| `Der erwartete Gusto-Launcher fehlt:` | Requires the stable installed command as additional ownership evidence. |
| `Der ermittelte Installationspfad ist zu breit zum Löschen.` | Refuses root/home application deletion. |
| `Der Datenordner liegt innerhalb der Installation und kann beim Entfernen der App nicht erhalten bleiben.` | Prevents claiming keep-data while deleting its containing app tree. |
| `Der Datenpfad ist zu breit oder enthält die Installation:` | Refuses a destructive data target encompassing unrelated paths or the app. |
| `'{data}' sieht nicht wie ein Gusto-Datenordner aus;` | Requires expected data subdirectories before deleting an existing separate store. |
| `powershell.exe für die Autostart-Bereinigung fehlt.` | Identifies unavailable Windows cleanup tooling before subsequent removal steps. |
| `systemctl für die Autostart-Bereinigung fehlt.` | Identifies unavailable Linux cleanup tooling. |
| `Windows-Autostart konnte nicht entfernt werden:` | Preserves the platform failure so uninstall reports partial cleanup instead of scheduling deletion as success. |
| `Linux-Autostart konnte nicht entfernt werden:` | Reports failed disable/reload, including when the unit file was already removed. |
| `Eine fremde geplante Aufgabe namens Gusto wird nicht entfernt.` | Preserves a same-name task not owned by Gusto. |
| `Die laufende Gusto-Aufgabe konnte nicht beendet werden.` | Refuses to claim service cleanup while the task remains active. |
| `Kein unabhängiger Python-Interpreter für die Selbstlöschung gefunden.` | Prevents deleting the interpreter needed by the detached Linux removal helper. |
| `Python für die Selbstlöschung fehlt:` | Reports the missing independent helper interpreter. |
| `powershell.exe für die Selbstlöschung fehlt.` | Reports missing helper tooling without claiming deletion was scheduled. |
| `Deinstallation bei` | Names the failed step and underlying cause so a caller can assess partial effects. |
| `Bereits entfernt:` | Preserves knowledge of successful earlier integration removals. |
| `Der fehlgeschlagene Schritt kann teilweise ausgeführt sein.` | Avoids a false all-or-nothing or rollback claim for the failed step. |
| `Die Dateilöschung wurde nicht bestätigt; prüfe die Installation und die angegebenen Pfade vor einem weiteren Versuch.` | Stops blind repetition of destructive work after ambiguous helper launch/cleanup failure. |

## Skill delivery (`gusto/skill_install.py`)

Evidence: bundled-source resolution, source/host validation and publish rollback; `tests/test_skill_install.py` and wheel cases in `tests/test_packaging.py`.

| Text | Reason |
|---|---|
| `Der zu dieser Gusto-Version gehörende Agent-Skill fehlt in der Runtime.` | Identifies a broken runtime payload rather than choosing instructions from another version. |
| `Repariere oder aktualisiere die Gusto-Installation.` | Gives the supported way to obtain a complete bundled payload. |
| `Der gebündelte Gusto-Skill ist unvollständig:` | Names a missing required instruction file before publishing a partial skill. |
| `Der gebündelte Gusto-Skill enthält einen unsicheren symbolischen Link:` | Refuses source links that could copy unrelated content into an agent host. |
| `vBot-Standardverzeichnis nicht gefunden:` | Identifies an absent host instead of silently initializing one. |
| `Installiere oder initialisiere vBot zuerst.` | Assigns host setup to the user/host, not Gusto's skill copier. |
| `vBot-Standardverzeichnis ist kein Ordner:` | Refuses an invalid host path without overwriting its contents. |
| `Nicht unterstützter Agent-Host` | Names the rejected host before selecting any destination. |
| `Unterstützt:` | Gives valid host choices from the same delivery registry the CLI uses. |
| `Gebündelter Gusto-Skill nicht gefunden:` | Names the missing explicitly selected payload directory. |
| `Skill-Ziel existiert, ist aber kein Ordner:` | Prevents replacement of an unrelated destination file. |
| `Gusto-Skill konnte nicht installiert werden:` | Reports a copy/publish failure with its underlying cause. |
| `der bisherige Skill blieb zur Wiederherstellung unter` | Gives the backup's real path when automatic restore failed. |

## Public bootstraps (`install.ps1`, `install.sh`)

Evidence: bootstrap argument parsing, manifest checks, hashing and extraction; `tests/test_packaging.py`. Installer/validator sentences reused above have the same reasons; the rows below identify bootstrap-specific forms.

| Text | Reason |
|---|---|
| `Fehler: --release-base braucht eine URL.` | Refuses a missing source argument rather than using an accidental next token. |
| `Fehler: $1 braucht einen Wert.` | Names the bootstrap option lacking its required value. |
| `Gusto installieren: ./install.sh` | Shows the install-root/data-root call shape. |
| `Danach erfolgen Updates ausschließlich mit: gusto update` | Prevents installer reruns as normal updates. |
| `Fehler: unbekannte Option` | Reports unsupported bootstrap syntax before downloads/install. |
| `Fehler: Gusto wird als normaler Benutzer und ohne sudo installiert.` | Refuses root invocation before acquiring payloads. |
| `Fehler: Python 3 wurde nicht gefunden.` | Names the Linux prerequisite the bootstrap cannot supply. |
| `Fehler: Gusto braucht Python 3.10 oder neuer.` | Refuses an unsupported Linux interpreter. |
| `Fehler: Release-URLs müssen HTTPS oder lokale Pfade sein.` | Refuses insecure remote release URLs while keeping fixture paths usable. |
| `Release-URLs müssen HTTPS, file:// oder lokale Pfade sein.` | Gives the equivalent accepted-source rule in PowerShell. |
| `Fehler: unbekanntes Release-Manifest.` | Stops Linux bootstrap on an unsupported manifest schema. |
| `Fehler: Release-Manifest enthält keine Assets.` | Refuses a manifest without payload descriptors. |
| `Fehler: Manifest-Asset` | Names a missing/invalid Linux payload descriptor before download. |
| `Fehler: Release-Manifest enthält keine gültigen Installer-Assets.` | Propagates failed manifest extraction instead of running a partial payload. |
| `Fehler: SHA-256-Prüfung für` | Refuses Linux payloads whose bytes differ from the manifest. |
| `Manifest-Asset '$Label' ist ungültig.` | Names a malformed PowerShell payload descriptor. |
| `SHA-256-Prüfung für '$Label' fehlgeschlagen.` | Refuses a mismatched PowerShell payload before execution. |
| `Das Release-Manifest ist unvollständig oder unbekannt.` | Refuses missing/invalid schema or release version in PowerShell. |
| `Die Windows-Python-Runtime im Manifest ist ungültig.` | Requires the interpreter version/layout needed for extraction. |
| `Unsicherer Python-Paketeintrag:` | Refuses traversal/drive-like extraction targets. |
| `Das Python-Runtime-Paket enthält tools/python.exe nicht.` | Identifies the missing executable needed to run the installer. |
| `Die Gusto-Installation ist mit Status` | Carries installer failure back through PowerShell without closing the caller's shell. |
