# Option (auf Eis): Einkaufsliste als installierbare PWA über HTTPS im LAN

**Status: nicht entschieden.** Dies ist die griffbereite Alternative zum
Telegram-Weg ([telegram-shopping-handoff.md](telegram-shopping-handoff.md)),
falls der zu aufwändig wird. Vorteil: **ist schon zur Hälfte gebaut** — die PWA
existiert im Repo, diese Option macht sie auf dem Handy nur nutzbar.

Die Anleitung beschreibt bewusst das Raspberry-Pi/Linux-Deployment. Gusto
selbst und seine normale Installation bleiben unabhängig davon Windows- und
Linux-fähig.

## Das Problem in einem Satz

Die PWA (offline + „installieren") braucht einen **Service Worker**, und der
läuft nur in einem *secure context* — also `https://`. Ein LAN-Zugriff
`http://pi.local:8000` / `http://192.168.x.x:8000` ist **kein** secure context →
Service Worker registriert sich nicht → kein Offline, keine echte Installation.
Ein selbst-signiertes Zertifikat bringt Browser-Warnungen + Zertifikat-Import auf
jedem Gerät — für ältere Nutzer tot. Ziel bleibt: **mehr als „installieren" darf
nicht nötig sein.**

## Die Lösung: echtes Let's-Encrypt-Zertifikat, ohne den Pi öffentlich zu stellen

Kern-Trick: Zertifikat per **DNS-01-Challenge** holen. Die prüft nur einen
TXT-DNS-Eintrag, **nicht** ob der Server öffentlich erreichbar ist. Damit gibt es
ein echtes, vom Browser vertrautes Zertifikat für einen Namen, dessen A-Eintrag
auf eine **private** LAN-IP zeigt. Der Pi bleibt LAN-only, Daten bleiben im Haus,
trotzdem grünes Schloss ohne Warnung.

Zwei Bausteine:
- **DuckDNS** (gratis): Subdomain `gusto.duckdns.org`, A-Eintrag → Pi-LAN-IP.
- **Caddy** als Reverse-Proxy vor `gusto serve`: holt & **erneuert** das
  Zertifikat automatisch, terminiert TLS, proxyt auf `localhost:8000`.

Nutzer-Erlebnis (Android, im Heim-WLAN): `https://gusto.duckdns.org` öffnen →
grünes Schloss, keine Warnung → Chrome bietet „App installieren" → Standalone-App,
Service Worker aktiv, offline nutzbar, Sync zuhause. **Für den Nutzer: nur
installieren.**

## Wichtig: die PWA ist schon gebaut

Kein App-Code nötig. `gusto/static/manifest.webmanifest`, `sw.js`,
`shopping-client.js` existieren bereits (Roadmap: „Shopping list & PWA" = erledigt).
Diese Option ist **nur** Serving/Zertifikat (Caddy) + DNS. Sync-Verhalten:
[sync-kontrakt.md](sync-kontrakt.md).

## Setup (griffbereit)

1. **DuckDNS**: mit Google/GitHub einloggen, Subdomain anlegen, **Token**
   notieren, die IP der Subdomain = Pi-LAN-IP (`192.168.x.x`).
2. **Caddy mit DuckDNS-DNS-Plugin** auf dem Pi. Caddys DNS-Provider sind Plugins,
   also nicht im Standard-Binary enthalten — entweder
   `xcaddy build --with github.com/caddy-dns/duckdns` bauen, oder ein
   Docker-/Paket-Image nutzen, das das Plugin enthält.
3. **Caddyfile**:
   ```
   gusto.duckdns.org {
       reverse_proxy localhost:8000
       tls {
           dns duckdns {env.DUCKDNS_TOKEN}
       }
   }
   ```
   (Token als Umgebungsvariable `DUCKDNS_TOKEN`; exakte Syntax im README von
   `caddy-dns/duckdns` gegenprüfen.)
4. `gusto serve` läuft weiter auf `127.0.0.1:8000`; Caddy lauscht auf **443**.
   Damit ist die URL schlicht `https://gusto.duckdns.org` (ohne `:8000`).
5. Auf dem Handy `https://gusto.duckdns.org` im Heim-WLAN öffnen → installieren.

## Caveats

- Funktioniert nur im **Heim-WLAN** (private IP) — genau die gewollte Nutzung.
  Unterwegs läuft die installierte PWA offline aus dem Service-Worker-Cache; der
  Sync passiert beim nächsten Mal zuhause. Deckt sich mit dem Sync-Kontrakt
  (schreiben zuhause, lesen/abhaken unterwegs).
- Der Pi braucht **ausgehendes** Internet (zu Let's Encrypt + DuckDNS) für
  Ausstellung und Erneuerung. **Kein** eingehender Port, kein Port-Forwarding.
- Erneuerung macht Caddy automatisch — danach wartungsfrei.
- Betrieb: zweite systemd-Unit für Caddy neben [deploy/gusto.service](../deploy/gusto.service).

## Alternativen zum Namen

- **Eigene Domain** statt DuckDNS: gleiches Prinzip; der DNS-Anbieter muss von
  Caddy für die DNS-01-Challenge unterstützt sein.
- **Cloudflare Tunnel**: echte HTTPS-URL, auch **von unterwegs** erreichbar —
  aber Traffic läuft über Cloudflare und die App ist aus dem Internet erreichbar.
  Nur sinnvoll, wenn Zugriff von außerhalb ausdrücklich gewünscht ist.

## Aufwand

Einmalig bei dir (~20 Min: DuckDNS-Account + Token + Pi-IP; Caddy richte ich ein),
danach wartungsfrei. Für die Nutzer: nur installieren.

## Telegram vs. PWA — Kurzvergleich

| | PWA + DuckDNS (diese Option) | Telegram-Checkliste |
|---|---|---|
| Pi öffentlich erreichbar? | nein, bleibt im Haus | nein (bei Long-Polling) |
| Offline im Laden | ja (Service Worker) | Liste lesbar, Tap synct bei Signal |
| Neue Fähigkeit nötig? | nur Caddy + DNS (App fertig) | Inline-Keyboard + `callback_query` in der Agent-App |
| Installier-Hürde | 1× „App installieren" | Bot in Telegram öffnen (vertraut) |
| Daten bei Dritten? | nein | ja (Telegram sieht den Inhalt) |
