# Phase 3: Live-UI und unveränderte Offline-PWA

**Goal:** Online geöffnete Browser reagieren automatisch auf Server-/CLI-
Mutationen, während die Einkaufs-PWA offline unverändert weiterarbeitet und
beim Reconnect merged.

## Required decisions

- Transport ist Server-Sent Events über `/api/v1/events`.
- Normale Seiten dürfen bei relevanter Revision vollständig neu laden.
- Die Einkaufsseite darf auf Events niemals lokalen Zustand blind ersetzen;
  sie ruft den vorhandenen Full-State-Merge auf.
- Service Worker behandelt sämtliche `/api/`-Requests einschließlich SSE
  network-only.
- PWA-Schema, LocalStorage-Keys, Timestamp-Regel und Tombstones bleiben
  unverändert.

## Tasks

- Globalen Live-Client ergänzen ⚡ *parallel zur Lifecycle-Arbeit* — read:
  [.vorch/domain-maps/surfaces.md], files:
  [gusto/static/app.js, gusto/templates/base.html]
  - Eine `EventSource` pro Seite.
  - Letzte verarbeitete Revision deduplizieren.
  - Ressourcenbezug der Seite aus stabilen `data-*`-Attributen lesen.
  - Für normale betroffene Seiten Reload; für Shopping ein CustomEvent.
  - Reconnect dem Browser überlassen; keine Fehlerschleife oder Offline-
    Fehlermeldungsflut.

- Shopping-PWA an Live-Events anbinden — read:
  [.vorch/domain-maps/shopping.md, docs/sync-kontrakt.md], files:
  [gusto/static/shopping-client.js, gusto/templates/shopping.html,
  gusto/static/sw.js]
  - `gusto:change` für Ressource `shopping` startet den vorhandenen
    fehlertoleranten `sync()`.
  - Gleichzeitige lokale Mutation/aktive Synchronisation nutzt weiterhin
    `pending` und Merge-into-current.
  - Offline wird kein Sync versucht; `online` bleibt Trigger.
  - Favorites-Änderungen aktualisieren den offline lesbaren Katalog online.
  - Cache-Version bei Assetänderung erhöhen; API/SSE nicht cachen.

- Browser- und PWA-Gates erweitern — files:
  [scripts/browser_check.py, scripts/pwa_check.py]
  - Browserseite öffnen, Mutation über Command-API/CLI auslösen und ohne
    manuelles Reload auf neuen Inhalt warten.
  - Offlineshopping mutieren, serverseitig parallel ändern, online schalten
    und beweisen, dass beide ids/neueren Zustände erhalten bleiben.
  - SSE-Ausfall darf normale Navigation und No-JS-Fallback nicht brechen.
  - Screenshots weiterhin nur aus Throwaway-Stores.

## Dependencies

Phase 1 Event-Endpunkt und Phase 2 CLI-Transport müssen integriert sein, bevor
die End-to-End-Gates abgeschlossen werden. Static-/Template-Arbeit kann gegen
den festgelegten Event-Envelope vorgezogen werden.

## Agent ownership

Root-Agent: sämtliche hier genannten Dateien. Kein Subagent berührt diese
Dateien parallel.

## Done when

- Eine CLI-Mutation erscheint auf einer bereits geöffneten relevanten Seite
  innerhalb von höchstens drei Sekunden.
- Shopping synchronisiert auf Event ohne Reload und ohne Verlust lokaler
  In-flight-Änderungen.
- Offline add/check/remove/clear sowie Reconnect und Zwei-Geräte-Merge bestehen
  unverändert.
- Ohne JavaScript bleiben die servergerenderten Formulare funktionsfähig.

