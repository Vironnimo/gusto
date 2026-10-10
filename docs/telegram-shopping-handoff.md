# Gusto-Einkaufsliste als Telegram-Checkliste

Gusto hält die gemeinsame Einkaufsliste; vBot stellt sie als antippbare
Telegram-Tastatur dar. Ein Tap auf einen Artikel ändert zunächst nur dessen
Häkchen in vBot. Erst „Fertig“ weckt den Agenten, der die sichtbaren Zustände
über die Gusto-CLI speichert. Telegram-Tastatur und Callback-Verarbeitung
gehören zur vBot-App, nicht zu Gusto.

Die operative Anleitung für einen Agenten mit vBots `channel_send` steht in
[der Telegram-Referenz des Skills](../skill/gusto/references/telegram.md).
Dieser Vertrag beschreibt die Daten und Zustandsübergänge an der App-Grenze.

## Dienst und Aufruf

Haushaltsaufgaben verwenden den installierten `gusto`-Befehl gegen den
ausgewählten laufenden Dienst. Vor der ersten Fachoperation erklärt derselbe
Aufruf mit `home --json` die Auswahl. `server_url`, `server_reachable` und
`server_data_path` beschreiben das Ziel normaler CLI-Aufrufe; `path` und die
lokalen Auflösungsfelder können bei einem entfernten Dienst davon abweichen.

`--server URL` beziehungsweise `GUSTO_URL` wählen einen anderen Dienst.
`GUSTO_HOME` wählt nur den lokalen Datenpfad für Server- und Recovery-Betrieb;
es leitet normale CLI-Befehle nicht auf einen anderen Datenspeicher um.
`python -m gusto` ist ein ausdrücklich gewählter Entwicklungsaufruf, kein
Fallback für einen fehlenden oder unerreichbaren Haushaltsdienst.

## Die Checkliste senden und abschließen

1. `gusto shopping list --json` liefert ein flaches Array aller sichtbaren
   Items, auch der bereits gehakten. Jede Zeile erhält einen Button mit
   führendem `⬜` bei `checked:false` oder `✅` bei `checked:true`.
2. Die Button-Daten lauten `chk:<id>`. Die ID wird unverändert aus dem Item
   übernommen. Artikeltext und eine optionale Produktempfehlung sind reine
   Beschriftung; der Agent leitet daraus keine ID ab.
3. Die letzte Zeile enthält `{"label":"Fertig ✅","data":"run:done"}`.
   Ohne diesen Button erreichen die visuellen Änderungen weder Agent noch
   Gusto. vBot übernimmt Versand und Telegram-Callback-Verarbeitung.
4. Artikel-Taps ändern nur das führende Glyph in der bestehenden Tastatur.
   Sie rufen keine Gusto-Befehle auf und wecken den Agenten nicht.
5. „Fertig“ schließt die Tastatur und liefert dem Agenten deren aktuelle
   Beschriftungen. Für jede `chk:`-Zeile setzt er den gewünschten Zustand:
   `✅` mit `gusto shopping check <id> --json`, `⬜` mit
   `gusto shopping uncheck <id> --json`. `run:done` ist kein Artikel.

Die Befehle setzen Zustände explizit; es gibt keinen CLI-`toggle`-Befehl.
Unabhängige Items können parallel geschrieben werden. Jede Antwort und jeder
Exitcode wird geprüft; erst erfolgreiche Speicherung wird als solche
bestätigt. Ein inzwischen entferntes oder unbekanntes Item ist ein Fehler.
In diesem Fall wird der aktuelle Stand gelesen und die Abweichung gemeldet,
statt den Artikel unter einer erfundenen ID neu anzulegen.

Eine verlorene Antwort kann `outcome_unknown:true` melden. Dann wird der Stand
auf demselben Dienst geprüft, bevor eine Mutation wiederholt wird. Explizite
`check`/`uncheck`-Aufrufe sind idempotent; Hinzufügen ist es nicht.

„Fertig“ entfernt keine Artikel. `shopping remove-done` folgt nur auf einen
zusätzlichen Wunsch, gekaufte Artikel zu entfernen. „Einkaufsliste leeren“
entspricht `shopping clear` und entfernt auch offene Items. Beide Operationen
haben keine CLI-Wiederherstellung.

Nach Hinzufügen, Entfernen oder erneutem Rezeptimport wird eine aktuelle
Tastatur gesendet. Ein erneuter Rezeptimport erzeugt neue IDs. Wenn die Liste
nach dem Schließen lesbar bleiben soll, stehen die Artikel zusätzlich im
ursprünglichen Nachrichtentext. Bei einem benannten Laden wird die Reihenfolge
gemäß [Einkaufstour-Referenz](../skill/gusto/references/shopping-trip.md) gewählt;
Gusto speichert keinen Laufweg.

## Gusto-Rückgaben

Alle folgenden Befehle akzeptieren `--json`:

| Kommando | JSON-Rückgabe und Wirkung |
|---|---|
| `shopping list` | Flaches Array aller nicht gelöschten Items, gehakte eingeschlossen. |
| `shopping list --pending` | Dasselbe Array, beschränkt auf `checked:false`. |
| `shopping check <id>` / `uncheck <id>` | Item mit dem gesetzten `checked`-Wert; unveränderter Zustand behält `updated_at`. |
| `shopping add "<text>" [--quantity M] [--source SLUG]` | Erzeugtes Item; `source` muss ein aktives Rezept sein und zählt für dessen Import-Sperre. |
| `shopping add-many "<text>" ...` | Atomar erzeugte Items als Array in Argumentreihenfolge. |
| `shopping add-recipe <slug>` | Array der erzeugten Items; lehnt leere Zutatenabschnitte und vorhandene sichtbare Items derselben Rezeptquelle ab; die Ablehnung nennt deren Anzahl. |
| `shopping remove <id>` | `{"id":"...","deleted":true}`; wiederholtes Entfernen desselben Tombstones ändert den Zeitstempel nicht. |
| `shopping remove-done` | `{"removed":N}`; entfernt alle sichtbaren gehakten Items. |
| `shopping clear` | `{"removed":N}`; entfernt alle sichtbaren Items. |

Ein erwarteter Befehlsfehler liefert `{"ok":false,"error":"..."}` auf
stdout mit Exitcode 1. Parserfehler verwenden Exitcode 2. Die vollständige
CLI-Referenz steht unter [skill/gusto/references/cli.md](../skill/gusto/references/cli.md).

Ein Item enthält folgende Felder:

```json
{
  "id": "a04381611b2740d5944abc58993c2643",
  "text": "200 g Spaghetti",
  "quantity": "",
  "checked": false,
  "source": "spaghetti-carbonara",
  "created_at": "2026-07-08T19:25:38.123Z",
  "updated_at": "2026-07-08T19:25:38.123Z",
  "deleted": false
}
```

IDs sind opake Strings. Server-Items haben gewöhnlich 32 Hexzeichen,
Browser-Items können Bindestriche enthalten. Die App verwendet den
zurückgegebenen Wert unverändert als Button-Anker und als CLI-Argument.
Archivieren eines Quellrezepts verändert bestehende Items nicht. Entfernen
setzt Tombstones für den [PWA-Sync](sync-kontrakt.md); sie erscheinen nicht in
`shopping list`.
