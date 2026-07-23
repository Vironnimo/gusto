# Sync-Kontrakt — Phase 2 (Einkaufsliste-PWA)

Linchpin für die zweite Parallel-Welle (2A/2B/2C). Hier stehen API-Form,
Item-Schema, Merge-Regel, Client-Store und App-Shell-Asset-Liste **fest**.
Jeder Subagent arbeitet ausschließlich an seinen Dateien (siehe unten) gegen
diesen Kontrakt.

## Architektur in einem Absatz
`/shopping` ist server-gerendert und funktioniert online auch ohne JS
(Phase 1). Phase 2 macht die Seite **offline-fähig**: ein Service-Worker cacht
die App-Shell, und `shopping-client.js` rendert die Liste aus **localStorage**
und hakt **optimistisch offline** ab. Sync gegen den Gusto-Server ist **Voll-State**:
der Client schickt seinen kompletten lokalen Stand, der Server merged
("letzter gewinnt" pro `id` + Tombstones) und schickt den gemergten
Gesamtstand zurück, den der Client übernimmt. Der Gusto-Server bleibt die einzige
Quelle der Wahrheit.

Lieblingsprodukte sind bewusst ein **separater, servergeführter Stammdaten-
Bestand**. `GET /api/favorites` liefert alle Einkaufsbedarfe, exakten Aliasse
und geordneten Produktkarten. Der Browser spiegelt die Antwort unter dem
localStorage-Key `gusto.favorites`, damit Empfehlungen offline lesbar bleiben;
es gibt dafür keinen Offline-Merge und keine Offline-Mutation. Änderungen
erfolgen online über Core/CLI oder die serverseitigen Formulare. Das gilt auch
für direkt aufgenommene oder ausgewählte Produktfotos: Sie werden online als
metadatenfreies WebP mit maximal 1.920 Pixel Kantenlänge gespeichert; erst die
fertige, in der Katalogantwort referenzierte Bild-URL wird offline gecacht.

## Item-Schema (identisch Client ⇄ Server)
Ein Item ist exakt das Dict von `core.EinkaufItem.to_dict()`:

```json
{
  "id": "string (opak, eindeutig)",
  "text": "200 g Spaghetti",
  "quantity": "",
  "checked": false,
  "source": "spaghetti-carbonara",   // oder null
  "created_at": "2026-06-23T18:00:00.123Z",
  "updated_at": "2026-06-23T18:00:00.124Z",
  "deleted": false
}
```

`source` bleibt ausschließlich der stabile Rezept-Slug; Archivieren schreibt
Einkaufsitems und damit den Sync-Stand nicht um. Die servergerenderte Liste und
`shopping-client.js` erhalten separat eine Präsentations-Map mit Titel, URL und
Lifecycle-Status und verlinken dadurch aktive Rezepte nach `/recipe/<slug>` und
archivierte nach `/archive/<slug>`. Ein unbekannter Slug wird sichtbar als
„nicht gefunden“ markiert. Diese Präsentation ist kein Teil des Sync-Schemas.

### Zeitstempel-Format (KRITISCH)
UTC mit **Millisekunden**, literal `Z`:
`YYYY-MM-DDTHH:MM:SS.sssZ`. Bereits gespeicherte sekundengenaue Zeitstempel
bleiben gültig. Server und Client vergleichen die Zeitstempel als geparste
Zeitwerte, nicht als rohe Strings. Bei mehreren Änderungen desselben Items
innerhalb einer Millisekunde wird der neue Wert künstlich um mindestens 1 ms
erhöht; dadurch bleibt auch "hinzufügen und sofort abhaken" eindeutig.

### id
Opaker, eindeutiger String. Server: `uuid4().hex`. Client für neue Items:
`crypto.randomUUID()`. Beide Formate sind erlaubt — gemerged wird per exaktem
`id`-Vergleich. Eine einmal vergebene `id` bleibt für das Item stabil.

## Merge-Regel (`core.shopping_merge`, 2A)
Voll-State, pro `id`:
- `id` nur lokal → bleibt.
- `id` nur remote → wird übernommen.
- `id` beidseitig → die Version mit dem **größeren `updated_at`** gewinnt
  (String-Vergleich). Bei Gleichstand bleibt die **lokale** (Server-)Version.
- Tombstones (`deleted: true`) sind ganz normale Versionen und propagieren
  nach derselben Regel.
Ergebnis enthält **alle** ids (inkl. Tombstones), wird gespeichert und
zurückgegeben.

## API (Subagent 2A — `gusto/web.py` API-Abschnitt + `core.shopping_merge`)
Die Stubs stehen bereits in `web.py` (`/api/shopping`, `/api/shopping/sync`) und
in `core.shopping_merge`. Implementieren:

- `GET /api/shopping` → `200 {"items": [<item>, ...]}` (alle inkl. Tombstones,
  via `core.shopping_load()`).
- `POST /api/shopping/sync` — Body `{"items": [<item>, ...]}` →
  `200 {"items": [<gemergt>, ...]}` (via `core.shopping_merge(body["items"])`).
  Fehlt `items`, wie leere Liste behandeln.

Beide liefern `application/json` (JSONResponse). Keine HTML-Redirects.

## Client-Store + Verhalten (Subagent 2C — `gusto/static/shopping-client.js`, `gusto/templates/shopping.html`)
**localStorage-Key:** `gusto.shopping`
**Wert:** `JSON.stringify({ items: [<item>, ...] })` (gleiches Item-Schema).

Zusätzlich hält `gusto.favorites` `{needs:[…]}` als offline lesbare Kopie des
gemeinsamen Präferenzkatalogs. Bei erfolgreichem `GET /api/favorites` wird sie
vollständig ersetzt; bei Netzwerkfehler bleibt der letzte lokale Stand erhalten.
Die Einkaufsliste wird weiterhin ausschließlich über `gusto.shopping` gemerged.

**Progressive Enhancement in `shopping.html`:**
- Die server-gerenderte Phase-1-Liste bleibt als **No-JS-Fallback** erhalten,
  umschlossen von z.B. `<div id="eink-server">`.
- Neu: ein leerer Container `<div id="eink-client" hidden></div>` und
  `<script src="/static/shopping-client.js" defer></script>`.
- Beim Start blendet der Client `#eink-server` aus und `#eink-client` ein und
  rendert dort. So funktioniert die Seite mit JS (offline-fähig) **und** ohne
  JS (online, Server-Forms).
- Der Client rendert mit **denselben CSS-Klassen** wie die Server-Version
  (`.eink-board`, `.eink-group`, `.eink-group-done`, `.eink-item`, `.is-done`,
  `.eink-box`, `.is-checked`, `.eink-body`, `.eink-text`, `.eink-quantity`,
  `.eink-source`, `.eink-x`, `.eink-add`, `.eink-clear`, `.eink-count`,
  `.eink-leer`), damit kein neues CSS nötig ist. Nur `shopping.html` und
  `shopping-client.js` anfassen (für JS-Umschaltung ggf. ein kleines inline
  `<style>` in `shopping.html`).

**Render:** Items aus localStorage, `deleted` rausfiltern, in
offen/erledigt gruppieren, nach `created_at` aufsteigend.

**Mutationen (immer: localStorage schreiben → neu rendern → `sync()` anstoßen):**
- Hinzufügen: neues Item `{id: crypto.randomUUID(), text, quantity, checked:false,
  source:null, created_at:now, updated_at:now, deleted:false}`.
- Abhaken/auf-offen: `checked` setzen, `updated_at=now`.
- Entfernen: `deleted=true`, `updated_at=now` (kein Hard-Delete).
- Erledigte entfernen: alle `checked && !deleted` → `deleted=true`,
  `updated_at=now`.
- Einkaufsliste leeren: alle `!deleted` → `deleted=true`, `updated_at=now`.

**`sync()`** (idempotent, fehlertolerant):
- Offline (`!navigator.onLine`) → nichts tun, Änderungen bleiben lokal.
- Online → `POST /api/shopping/sync` mit `{items: <localItems inkl. Tombstones>}`;
  bei Erfolg Antwort-`items` als neuen lokalen Stand übernehmen (localStorage
  überschreiben) und neu rendern. Bei Netzwerkfehler lokalen Stand behalten.

**Sync-Trigger:** beim App-Start (`DOMContentLoaded`) und beim `window`-Event
`online`. (So wird der erste Start auch ohne vorhandenes localStorage über
`POST {items: []}` mit dem Server-Stand befüllt.)

## PWA-Schale (Subagent 2B — `manifest.webmanifest`, Icons, `sw.js`, `base.html`)
**`gusto/static/manifest.webmanifest`:**
```json
{
  "name": "Gusto", "short_name": "Gusto",
  "start_url": "/shopping", "scope": "/",
  "display": "standalone",
  "background_color": "#f6efe1", "theme_color": "#bf4528",
  "icons": [
    {"src": "/static/icons/icon-192.png", "sizes": "192x192",
     "type": "image/png", "purpose": "any maskable"},
    {"src": "/static/icons/icon-512.png", "sizes": "512x512",
     "type": "image/png", "purpose": "any maskable"}
  ]
}
```

**Icons** (`gusto/static/icons/icon-192.png`, `icon-512.png`): mit Pillow
generieren (ist installiert). Vollflächiger Akzent-Hintergrund (#bf4528),
zentriert das cremefarbene Marken-Zeichen `❖` (Font `seguisym.ttf` hat es;
Größe ~55 % der Kantenlänge, im sicheren Bereich für „maskable"). Cremeton
`#fcf7ec`. Ein kleines Generator-Skript in `gusto/static/icons/` ablegen ist
ok, muss aber nicht.

**`gusto/static/sw.js`** — Service-Worker:
- Versionierter Cache-Name (z.B. `gusto-v2`); im `activate` alte Caches
  löschen.
- `install`: App-Shell **precachen** (Liste unten), dann `skipWaiting()`.
- `fetch`:
  - **Navigationen** (`request.mode === "navigate"`): network-first, bei
    Fehler aus dem Cache `/shopping` liefern (Offline-Fallback).
  - `/api/...`: **network-only** (nicht cachen — Offline regelt der Client
    über localStorage).
  - `/media/favorite/<content-specific-filename>`: **cache-first**. Beim
    Ersetzen eines Produktfotos entsteht ein neuer Dateiname, daher kann kein
    veraltetes Foto unter einer weiterverwendeten URL erscheinen.
  - sonstige same-origin GET (Static): **cache-first**.

**App-Shell-Asset-Liste (precache):**
```
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
(Google-Fonts sind cross-origin und werden NICHT precached; offline greifen
die System-Font-Fallbacks aus `style.css`.)

**`gusto/templates/base.html`** (nur 2B):
- `<link rel="manifest" href="/static/manifest.webmanifest">`
- `<meta name="theme-color" content="#bf4528">`
- Apple-Touch-Icon: `<link rel="apple-touch-icon" href="/static/icons/icon-192.png">`
- SW-Registrierung im `<head>` oder vor `</body>`:
  `if ("serviceWorker" in navigator) { window.addEventListener("load", () => navigator.serviceWorker.register("/static/sw.js")); }`

## Datei-Eigentum (disjunkt!)
| Subagent | Exklusiv |
|---|---|
| 2A | `gusto/core.py` (NUR `shopping_merge`), `gusto/web.py` (NUR die zwei `/api/...`-Stubs ausfüllen) |
| 2B | `gusto/static/manifest.webmanifest`, `gusto/static/sw.js`, `gusto/static/icons/*`, `gusto/templates/base.html` |
| 2C | `gusto/static/shopping-client.js` (neu), `gusto/templates/shopping.html` |

Keine zwei Subagents fassen dieselbe Datei an. `gusto/static/app.js` bleibt
unverändert.
