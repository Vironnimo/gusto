# Sync-Kontrakt — Phase 2 (Einkaufsliste-PWA)

Linchpin für die zweite Parallel-Welle (2A/2B/2C). Hier stehen API-Form,
Item-Schema, Merge-Regel, Client-Store und App-Shell-Asset-Liste **fest**.
Jeder Subagent arbeitet ausschließlich an seinen Dateien (siehe unten) gegen
diesen Kontrakt.

## Architektur in einem Absatz
`/einkauf` ist server-gerendert und funktioniert online auch ohne JS
(Phase 1). Phase 2 macht die Seite **offline-fähig**: ein Service-Worker cacht
die App-Shell, und `einkauf-client.js` rendert die Liste aus **localStorage**
und hakt **optimistisch offline** ab. Sync gegen den Pi ist **Voll-State**:
der Client schickt seinen kompletten lokalen Stand, der Server merged
("letzter gewinnt" pro `id` + Tombstones) und schickt den gemergten
Gesamtstand zurück, den der Client übernimmt. Der Pi bleibt die einzige
Quelle der Wahrheit.

## Item-Schema (identisch Client ⇄ Server)
Ein Item ist exakt das Dict von `core.EinkaufItem.to_dict()`:

```json
{
  "id": "string (opak, eindeutig)",
  "text": "200 g Spaghetti",
  "menge": "",
  "checked": false,
  "quelle": "spaghetti-carbonara",   // oder null
  "erstellt_am": "2026-06-23T18:00:00Z",
  "geaendert_am": "2026-06-23T18:00:00Z",
  "geloescht": false
}
```

### Zeitstempel-Format (KRITISCH)
UTC, **sekundengenau**, literal `Z`, **keine Millisekunden**:
`YYYY-MM-DDTHH:MM:SSZ`. Nur so sind die Strings direkt vergleichbar (das ist
die Grundlage von "letzter gewinnt").
- Server: `core._jetzt_iso()` erzeugt genau dieses Format.
- Client (JS): `new Date().toISOString().replace(/\.\d{3}Z$/, "Z")`.

### id
Opaker, eindeutiger String. Server: `uuid4().hex`. Client für neue Items:
`crypto.randomUUID()`. Beide Formate sind erlaubt — gemerged wird per exaktem
`id`-Vergleich. Eine einmal vergebene `id` bleibt für das Item stabil.

## Merge-Regel (`core.einkauf_merge`, 2A)
Voll-State, pro `id`:
- `id` nur lokal → bleibt.
- `id` nur remote → wird übernommen.
- `id` beidseitig → die Version mit dem **größeren `geaendert_am`** gewinnt
  (String-Vergleich). Bei Gleichstand bleibt die **lokale** (Server-)Version.
- Tombstones (`geloescht: true`) sind ganz normale Versionen und propagieren
  nach derselben Regel.
Ergebnis enthält **alle** ids (inkl. Tombstones), wird gespeichert und
zurückgegeben.

## API (Subagent 2A — `recipe/web.py` API-Abschnitt + `core.einkauf_merge`)
Die Stubs stehen bereits in `web.py` (`/api/einkauf`, `/api/einkauf/sync`) und
in `core.einkauf_merge`. Implementieren:

- `GET /api/einkauf` → `200 {"items": [<item>, ...]}` (alle inkl. Tombstones,
  via `core.einkauf_load()`).
- `POST /api/einkauf/sync` — Body `{"items": [<item>, ...]}` →
  `200 {"items": [<gemergt>, ...]}` (via `core.einkauf_merge(body["items"])`).
  Fehlt `items`, wie leere Liste behandeln.

Beide liefern `application/json` (JSONResponse). Keine HTML-Redirects.

## Client-Store + Verhalten (Subagent 2C — `recipe/static/einkauf-client.js`, `recipe/templates/einkauf.html`)
**localStorage-Key:** `gusto.einkauf`
**Wert:** `JSON.stringify({ items: [<item>, ...] })` (gleiches Item-Schema).

**Progressive Enhancement in `einkauf.html`:**
- Die server-gerenderte Phase-1-Liste bleibt als **No-JS-Fallback** erhalten,
  umschlossen von z.B. `<div id="eink-server">`.
- Neu: ein leerer Container `<div id="eink-client" hidden></div>` und
  `<script src="/static/einkauf-client.js" defer></script>`.
- Beim Start blendet der Client `#eink-server` aus und `#eink-client` ein und
  rendert dort. So funktioniert die Seite mit JS (offline-fähig) **und** ohne
  JS (online, Server-Forms).
- Der Client rendert mit **denselben CSS-Klassen** wie die Server-Version
  (`.eink-board`, `.eink-group`, `.eink-group-done`, `.eink-item`, `.is-done`,
  `.eink-box`, `.is-checked`, `.eink-body`, `.eink-text`, `.eink-menge`,
  `.eink-quelle`, `.eink-x`, `.eink-add`, `.eink-clear`, `.eink-count`,
  `.eink-leer`), damit kein neues CSS nötig ist. Nur `einkauf.html` und
  `einkauf-client.js` anfassen (für JS-Umschaltung ggf. ein kleines inline
  `<style>` in `einkauf.html`).

**Render:** Items aus localStorage, `geloescht` rausfiltern, in
offen/erledigt gruppieren, nach `erstellt_am` aufsteigend.

**Mutationen (immer: localStorage schreiben → neu rendern → `sync()` anstoßen):**
- Hinzufügen: neues Item `{id: crypto.randomUUID(), text, menge, checked:false,
  quelle:null, erstellt_am:now, geaendert_am:now, geloescht:false}`.
- Abhaken/auf-offen: `checked` setzen, `geaendert_am=now`.
- Entfernen: `geloescht=true`, `geaendert_am=now` (kein Hard-Delete).
- Erledigte entfernen: alle `checked && !geloescht` → `geloescht=true`,
  `geaendert_am=now`.

**`sync()`** (idempotent, fehlertolerant):
- Offline (`!navigator.onLine`) → nichts tun, Änderungen bleiben lokal.
- Online → `POST /api/einkauf/sync` mit `{items: <localItems inkl. Tombstones>}`;
  bei Erfolg Antwort-`items` als neuen lokalen Stand übernehmen (localStorage
  überschreiben) und neu rendern. Bei Netzwerkfehler lokalen Stand behalten.

**Sync-Trigger:** beim App-Start (`DOMContentLoaded`) und beim `window`-Event
`online`. (So wird der erste Start auch ohne vorhandenes localStorage über
`POST {items: []}` mit dem Server-Stand befüllt.)

## PWA-Schale (Subagent 2B — `manifest.webmanifest`, Icons, `sw.js`, `base.html`)
**`recipe/static/manifest.webmanifest`:**
```json
{
  "name": "Gusto", "short_name": "Gusto",
  "start_url": "/einkauf", "scope": "/",
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

**Icons** (`recipe/static/icons/icon-192.png`, `icon-512.png`): mit Pillow
generieren (ist installiert). Vollflächiger Akzent-Hintergrund (#bf4528),
zentriert das cremefarbene Marken-Zeichen `❖` (Font `seguisym.ttf` hat es;
Größe ~55 % der Kantenlänge, im sicheren Bereich für „maskable"). Cremeton
`#fcf7ec`. Ein kleines Generator-Skript in `recipe/static/icons/` ablegen ist
ok, muss aber nicht.

**`recipe/static/sw.js`** — Service-Worker:
- Versionierter Cache-Name (z.B. `gusto-v1`); im `activate` alte Caches
  löschen.
- `install`: App-Shell **precachen** (Liste unten), dann `skipWaiting()`.
- `fetch`:
  - **Navigationen** (`request.mode === "navigate"`): network-first, bei
    Fehler aus dem Cache `/einkauf` liefern (Offline-Fallback).
  - `/api/...`: **network-only** (nicht cachen — Offline regelt der Client
    über localStorage).
  - sonstige same-origin GET (Static): **cache-first**.

**App-Shell-Asset-Liste (precache):**
```
/einkauf
/
/static/style.css
/static/app.js
/static/einkauf-client.js
/static/manifest.webmanifest
/static/icons/icon-192.png
/static/icons/icon-512.png
```
(Google-Fonts sind cross-origin und werden NICHT precached; offline greifen
die System-Font-Fallbacks aus `style.css`.)

**`recipe/templates/base.html`** (nur 2B):
- `<link rel="manifest" href="/static/manifest.webmanifest">`
- `<meta name="theme-color" content="#bf4528">`
- Apple-Touch-Icon: `<link rel="apple-touch-icon" href="/static/icons/icon-192.png">`
- SW-Registrierung im `<head>` oder vor `</body>`:
  `if ("serviceWorker" in navigator) { window.addEventListener("load", () => navigator.serviceWorker.register("/static/sw.js")); }`

## Datei-Eigentum (disjunkt!)
| Subagent | Exklusiv |
|---|---|
| 2A | `recipe/core.py` (NUR `einkauf_merge`), `recipe/web.py` (NUR die zwei `/api/...`-Stubs ausfüllen) |
| 2B | `recipe/static/manifest.webmanifest`, `recipe/static/sw.js`, `recipe/static/icons/*`, `recipe/templates/base.html` |
| 2C | `recipe/static/einkauf-client.js` (neu), `recipe/templates/einkauf.html` |

Keine zwei Subagents fassen dieselbe Datei an. `recipe/static/app.js` bleibt
unverändert.
