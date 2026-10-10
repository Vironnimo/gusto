# Sync-Kontrakt der Einkaufs-PWA

Dieser Vertrag beschreibt den bestehenden Abgleich zwischen Einkaufs-PWA und
Gusto-Dienst. Die Fachlogik liegt in `gusto/core.py`, die Browser-Endpunkte in
`gusto/web.py`, der Offline-Client in `gusto/static/shopping-client.js` und die
Cache-Regeln in `gusto/static/sw.js`. Normale CLI-Aufrufe verwenden die
versionierte Command-API; sie schreiben keinen Browser-Sync-Stand direkt.

## Daten und Identität

`data/shopping_list.json` auf dem Server und der localStorage-Key
`gusto.shopping` enthalten jeweils `{ "items": [...] }`, einschließlich
Tombstones. Ein Eintrag entspricht `core.ShoppingItem.to_dict()`:

```json
{
  "id": "a04381611b2740d5944abc58993c2643",
  "text": "200 g Spaghetti",
  "quantity": "",
  "checked": false,
  "source": "spaghetti-carbonara",
  "created_at": "2026-06-23T18:00:00.123Z",
  "updated_at": "2026-06-23T18:00:00.124Z",
  "deleted": false
}
```

Eine `id` ist ein opaker, stabiler String. Der Server erzeugt UUID-Hexwerte,
der Browser UUIDs mit Bindestrichen beziehungsweise einen Fallback bei fehlender
WebCrypto-Unterstützung. Clients übernehmen vorhandene IDs unverändert;
Abgleich und CLI-Befehle benutzen den exakten Wert.

`source` ist ein Rezept-Slug oder `null`. Aktive und archivierte Quellen werden
separat für die Anzeige in Titel, URL und Lifecycle-Status aufgelöst. Diese
Präsentationsdaten gehören nicht zum Sync-Schema. Archivieren verändert weder
den Slug noch die Einkaufsitems. Ein unbekannter Slug erscheint als „nicht
gefunden“. Beim Übernehmen einer sichtbaren Remote-Version muss deren gesetzte
Quelle ein aktives oder archiviertes Rezept sein. Tombstones behalten ihre
Quelle auch nach einem endgültigen Purge.

## Versionen und Merge

Neue Zeitstempel sind UTC-Werte mit Millisekunden und literalem `Z`:
`YYYY-MM-DDTHH:MM:SS.sssZ`. Bestehende sekundengenaue Werte bleiben gültig.
Server und Browser vergleichen geparste Zeitwerte. Gültige Zeitwerte gewinnen
gegen fehlerhafte Altwerte; nur zwei fehlerhafte Werte werden lexikografisch
verglichen.

Jede Zustandsänderung eines Items erhält ein `updated_at`, das mindestens
1 ms nach dessen vorherigem Wert liegt. Das gilt auch für mehrere Änderungen
innerhalb derselben Millisekunde. Ein wiederholtes explizites CLI-`check` oder
`uncheck` auf den bereits gesetzten Zustand sowie das erneute Entfernen eines
Tombstones ändern weder Zeitstempel noch Änderungsrevision.

Der Abgleich überträgt den vollständigen Stand und führt ihn pro `id` zusammen:

- Ein nur auf einer Seite vorhandener Eintrag bleibt erhalten.
- Bei derselben ID gewinnt das spätere `updated_at`. Bei Gleichstand bleibt
  der Stand der empfangenden Seite erhalten.
- `deleted:true` ist eine normale Version und verbreitet eine Entfernung an
  andere Geräte. Fehlende Einträge bedeuten keine Entfernung.

Der Server speichert und liefert den Gesamtstand einschließlich Tombstones.
Die Mutation hält den Shopping-Lock über Laden, Merge und atomarem Speichern;
bei sichtbaren Rezeptquellen wird zusätzlich der Katalog gesperrt. Ein Merge
ohne Zustandsänderung erzeugt keine Änderungsrevision.

## Browser-API

- `GET /api/shopping` liefert `200 {"items":[...]}` mit allen Server-Einträgen
  einschließlich Tombstones.
- `POST /api/shopping/sync` nimmt `{"items":[...]}` entgegen und liefert
  `200 {"items":[...]}` mit dem gemergten Gesamtstand. Ein fehlendes oder
  `null` gesetztes `items` wird wie `[]` behandelt und liest damit den
  Serverstand ein.
- Ungültiges JSON, ein Body ohne Objektform, ein anderes `items`-Format oder
  ungültige Item-Felder werden mit HTTP 400 abgewiesen, bevor gespeichert wird.
  `id` und `text` müssen nichtleere Strings sein; `checked` und `deleted`
  müssen als Booleans vorliegen, beide Zeitstempelfelder als nichtleere
  Strings. `quantity` ist ein String, `source` ein String oder `null`.

Diese Endpunkte liefern JSON und keine HTML-Redirects. Die normale CLI ruft
für einzelne Fachoperationen `POST /api/v1/command` auf.

## Offline-Client

Ohne JavaScript funktionieren die Serverformulare unter `/shopping` online.
Mit JavaScript blendet der Client `#shop-server` aus und `#shop-client` ein.
Er zeigt sichtbare Items nach `created_at` sortiert, getrennt nach offen und
erledigt. Die HTML- und JavaScript-Ansichten verwenden dieselben `shop-*`
CSS-Klassen.

Hinzufügen, Abhaken, Wiederöffnen und Entfernen schreiben zuerst localStorage,
rendern die Ansicht und stoßen `sync()` an. Neue Browser-Items haben
`source:null`. „Erledigte entfernen“ setzt Tombstones für alle sichtbaren
gehakten Items, „Einkaufsliste leeren“ für alle sichtbaren Items. Beide
Entfernungen haben keine CLI-Wiederherstellung.

Ein Sync lädt alle lokalen Items einschließlich Tombstones hoch. Seine Antwort
wird in den zum Antwortzeitpunkt aktuellen lokalen Stand gemergt. Sie ersetzt
ihn nicht blind: Änderungen während einer laufenden Anfrage bleiben erhalten.
Ändert sich die Liste während eines Syncs, folgt ein weiterer Sync.

Sync läuft beim Start, nach lokalen Änderungen, beim `online`-Ereignis und bei
einem `shopping`-Änderungsereignis des Dienstes. Offline bleiben Änderungen
lokal. Bei Netzwerk- oder Serverfehlern bleibt der lokale Stand erhalten;
online versucht der Client bis zu fünf Wiederholungen mit wachsendem Abstand.
Erfolg oder eine neue Online-Verbindung setzt dieses Wiederholungsbudget
zurück.

Online-Änderungen kommen über `/api/v1/events`. Ein `favorites`-Ereignis lädt
den Präferenzkatalog neu; ein `catalog`-Ereignis aktualisiert Rezeptquellen,
ohne die Seite neu zu laden oder lokale Einkaufsänderungen zu verwerfen.

## Lieblingsprodukte

Lieblingsprodukte sind ein separater, servergeführter Bestand.
`GET /api/favorites` liefert `{ "needs": [...] }` mit Einkaufsbedarfen,
exakten Aliassen und geordneten Produktkarten. Der Browser ersetzt bei Erfolg
seine Kopie unter `gusto.favorites`; bei einem Fehler bleibt die letzte Kopie
offline lesbar. Dieser Bestand hat weder Offline-Merge noch Offline-Mutationen.

Änderungen und Fotos laufen online über den Dienst. Browser-Fotos werden in
metadatenfreies WebP mit maximal 1.920 Pixel Kantenlänge umgewandelt. Die
Bild-URL einer Produktkarte enthält einen beim Ersetzen wechselnden Dateinamen,
damit der Offline-Cache kein vorheriges Foto unter derselben URL zeigt.

## Service Worker und App-Shell

`gusto/templates/base.html` registriert `/sw.js` mit Geltungsbereich `/`.
Das Manifest startet die installierte App unter `/shopping`. Der Service
Worker bearbeitet nur same-origin GET-Anfragen:

- Navigation: zuerst Netzwerk, bei Netzwerkfehler die gecachte Einkaufsseite,
  ersatzweise die gecachte Startseite.
- `/api/`, aktive und archivierte Rezeptbilder sowie andere nicht-statische
  HTML-Abrufe: ausschließlich Netzwerk. Dazu gehören Live-Suche und der Abruf
  aktueller Rezeptquellen.
- `/static/` und `/media/favorite/`: zuerst der aktive Cache, danach Netzwerk.
  Erfolgreiche Antworten werden nur ohne HTML-Content-Type gespeichert.

Beim Installieren lädt der Worker die App-Shell mit `cache:"reload"`, damit
der HTTP-Cache keine alten Assets einschleust. Beim Aktivieren entfernt er
vorherige Cache-Versionen. Bei Änderungen an gecachten Assets oder der
Offline-Shell wird die Cache-Version erhöht.

Die App-Shell enthält:

```text
/shopping
/
/static/style.css
/static/app.js
/static/photo-input.js
/static/shopping-client.js
/static/manifest.webmanifest
/static/icons/icon-192.png
/static/icons/icon-512.png
```

Cross-origin Fonts werden nicht precached; offline greifen die Font-Fallbacks.

## Verifikation

`tests/test_merge.py` prüft Versionen, Tombstones und den Core-Merge,
`tests/test_api.py` API-Validierung und Änderungsrevisionen.
`scripts/pwa_check.py` prüft Offline-Nutzung, Änderungen während eines Syncs,
mehrere Geräte, Live-Ereignisse, Bilder und Service Worker in Chromium mit
isolierten Testdaten. `scripts/browser_check.py` prüft auch den No-JS-Fallback.
