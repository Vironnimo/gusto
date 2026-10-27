# Backup agent-facing text

Core backup errors are passed through the service and CLI. Each sentence identifies a failed precondition or the recovery state; paths and counts are data. Validation occurs before replacing household data except for a reported rollback failure.

| Text | Reason |
|---|---|
| `Das Datenverzeichnis ist ein symbolischer Link.` | Refuses to traverse a redirected data directory. |
| `Symbolischer Link im Datenbestand:` | Names the redirected household path that cannot be copied safely. |
| `Ungültiger Pfad im Datenbestand:` | Names a file/directory type conflict before replacement. |
| `Nicht unterstützte Datei im Datenbestand:` | Names content that would otherwise be omitted or deleted by restore. |
| `Der Datenbestand überschreitet die Backup-Grenzen.` | Reports that the household snapshot exceeds bounded processing. |
| `Dateinamen im Datenbestand kollidieren auf anderen Systemen.` | Refuses a snapshot that cannot be restored portably. |
| `Das Backup überschreitet die Größenbegrenzung.` | Reports the compressed archive limit. |
| `Das Backup konnte nicht verifiziert werden.` | Refuses to return a snapshot whose own manifest does not match. |
| `Das Backup enthält zu viele Dateien.` | Reports the bounded file count. |
| `Das Backup enthält doppelte Dateinamen.` | Refuses ambiguous paths, including case-insensitive collisions. |
| `Die Backup-Dateiliste fehlt.` | Identifies an archive without its required manifest. |
| `Ungültiger Backup-Pfad:` | Names an unowned, nonportable or escaping entry. |
| `Eine Backup-Datei ist zu groß oder verschlüsselt.` | Refuses entries that cannot be safely validated. |
| `Das entpackte Backup ist zu groß.` | Reports the aggregate extraction bound. |
| `Das Backup-Format wird nicht unterstützt.` | Refuses an unknown manifest version or shape. |
| `Die Backup-Dateiliste stimmt nicht mit dem Archiv überein.` | Rejects omitted or unlisted payload files. |
| `Die Backup-Dateiliste ist ungültig.` | Rejects malformed or duplicate manifest records. |
| `Die Größe der Backup-Datei stimmt nicht:` | Names a file whose recorded length is wrong. |
| `Die Prüfsumme der Backup-Datei stimmt nicht:` | Names a file whose content fails verification. |
| `Das Backup ist beschädigt oder nicht lesbar.` | Distinguishes malformed ZIP data from a valid snapshot. |
| `Der Datenbestand im Backup konnte nicht geprüft werden.` | Reports that the isolated validator did not complete. |
| `Der Datenbestand im Backup ist ungültig.` | Refuses data that the owner validators cannot load. |
| `Der Datenbestand im Backup ist inkonsistent:` | Lists owner-check codes that prevent a complete restore. |
| `Die Shopping-Generationsdatei ist ein symbolischer Link.` | Refuses to bind a preview to redirected generation data. |
| `Ungültige Sicherungskennung.` | Refuses unsafe safety-copy names. |
| `Sicherung nicht gefunden.` | Reports the requested safety copy is unavailable. |
| `Das Backup oder der Datenbestand hat sich seit der Vorschau geändert.` | Refuses replacement when the preview no longer describes its inputs. |
| `Erstelle eine neue Vorschau.` | Gives the safe next step after the preview becomes stale. |
| `Der Sicherungsordner ist ein symbolischer Link.` | Refuses to write the automatic safety copy outside its owner. |
| `Die Sicherung vor der Wiederherstellung konnte nicht verifiziert werden.` | Stops replacement without a complete safety copy. |
| `Die gespeicherte Sicherung konnte nicht verifiziert werden.` | Stops replacement when the saved safety copy differs from prepared bytes. |
| `Wiederherstellung unvollständig; vorherige Dateien liegen unter` | Names the retained recovery directory when rollback itself fails. |
