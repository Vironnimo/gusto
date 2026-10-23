# Shopping: offline PWA

Browser client `gusto/static/shopping-client.js` and service worker `gusto/static/sw.js`. Wire contract: `docs/sync-kontrakt.md`. Merge invariants: `shopping.md` -> Sync Contract.

## Page coupling

- `gusto/templates/shopping.html` (web-owned) renders the no-JS list in `#shop-server` and a hidden `#shop-client`. The client hides the former and renders into the latter with the same `shop-*` CSS classes; without `#shop-client` it does nothing.
- `#shop-recipe-references` is a JSON map from slug to title, archived flag and URL used to label sources; unknown slugs render as `nicht gefunden`. On a `catalog` event or `online`, the client fetches `/shopping` as HTML and re-parses that tag. Renaming the id, or letting the service worker cache non-navigation `/shopping` fetches, breaks source refresh.
- `#favorite-sheet` and `#favorite-sheet-content` host the preferred-product sheet.
- `app.js` (web-owned) opens `/api/v1/events` and, on `/shopping` only, dispatches a `gusto:change` DOM event for `shopping`, `favorites` or `catalog` instead of reloading. The client maps `shopping` to `sync()`, `favorites` to a `/api/favorites` refetch, `catalog` to the source refresh.

## Client state and mutations

- Device source of truth: `localStorage` `gusto.shopping`. Each mutation loads, changes (new id or `nowIso(previous)`), saves, renders and calls `sync()`.
- Browser-created items always have `source: null`; sourced items come only from Core. The PWA toggle flips; remove-done and clear tombstone locally; clear asks `window.confirm` first.
- If storage is full or blocked, items stay in memory, renders and syncs continue, and the next successful write restores persistence. Reads never replace newer memory with an older disk copy while writes fail; closing the page loses unsynced memory.
- Re-renders keep the add-panel node (half-typed input survives) and restore list focus by item id, else by index.

## Sync loop

- `sync()` runs on init (an empty local state fills from the server), after each mutation, on `online`, on `shopping` events, and once more when a mutation happened during an in-flight request. It does nothing while `navigator.onLine` is false.
- It posts all local items including tombstones; on success it merges the response into the current local state (`mergeInto`, `isNewer`).
- Any non-2xx or network failure keeps local state and retries with backoff: 2 s doubling plus up to 500 ms jitter, capped at 60 s, at most 5 attempts; success or `online` resets the budget. A 400 is retried like a network failure with the same payload, so one permanently invalid item blocks all later syncs of that device (see `shopping.md` -> Constraints & Gotchas).
- The sync endpoint also answers 400 `invalid shopping state` when the merge was saved but a later step failed (Core `PostMutationError` is a `ValueError`); the retry is then a no-op.

## Preferred products in the PWA

- The catalog is fetched from `/api/favorites` on init, `online` and `favorites` events; success replaces `gusto.favorites`, failure keeps the last copy. Each product `image_url` is then fetched once so the service worker caches it.
- Each row shows a hint (product count, missing products, or no need) and opens a read-only ranking sheet with a link to `/favorites/<id>`.
- Offline, the assign/create forms are replaced by an online-only note. Online they are plain form POSTs to `/favorites/assign` (alias add) and `/favorites/add` (new need with the item text as alias) with `return_to=/shopping`: a navigation, never queued.
- A sheet containing a form marked `data-dirty` (set by `app.js`) is not re-rendered by a catalog refresh.
- The sheet makes the background `inert`, traps Tab, closes on Escape, and returns focus to the matching row even after a sync re-render.

## Service worker

- `base.html` registers `/sw.js` on every page; web serves it with `Cache-Control: no-cache`. Scope `/`; the manifest's `start_url` is `/shopping`.
- Install precaches the app shell with `cache: "reload"` and calls `skipWaiting`; activate deletes other cache versions and claims clients.
- Fetch handles same-origin GET only: navigations network-first with fallback to cached `/shopping`, then `/`; `/api/*`, `/media/recipe/*`, `/media/archive/*` and every other non-static request (live search HTML, source refresh) network-only; `/static/*` and `/media/favorite/*` cache-first from the active cache, storing successful non-HTML responses.
- Product photos get a new filename on replacement, so cache-first never shows a stale photo; old entries remain until the next `CACHE` bump. Cross-origin fonts are not cached.
- Bump `CACHE` (currently `gusto-v17`) for every precached-asset or offline-behavior change; `scripts/ci_smoke_install.py` only checks that a `gusto-` cache name exists.

## Verification

`scripts/pwa_check.py` drives Chromium against throwaway data under `.tmp/pwa-*/`: sync API validation and merge, mixed timestamp precision, offline add and reload from the cache, two-device merge, live events while typing, focus handling, malformed-timestamp ranking, storage failure, opaque ids, product-image caching, manifest and service worker. Run it for any change here; `scripts/browser_check.py` covers the no-JS fallback.
