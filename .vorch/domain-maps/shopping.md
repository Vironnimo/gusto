# Shopping

The shopping domain owns the shared shopping list, ingredient import, preferred products, offline mutations, and full-state synchronization between browser clients and the server.

## Overview

Server-side rules and persistence live in the shopping section of `gusto/core.py`. `gusto/web.py` provides HTML fallbacks and APIs, while `gusto/static/shopping-client.js` owns the offline browser copies and optimistic list interaction. Recipe lookup and ingredient content come from the catalog domain.

## Terms

The cross-cutting term GLOSSARY → Einkaufsbedarf defines the durable owner of aliases and product rankings.

### Tombstone

**Definition:** A shopping item retained with `deleted=true` so a deletion can propagate to other devices. It is hidden from normal lists but remains part of synchronization state.

### Sync version

**Definition:** An item's `updated_at` UTC timestamp, compared per id to choose the last writer. Current writers use millisecond precision and must advance beyond that item's previous value.

## Data Model

`data/shopping_list.json` has the shape `{ "items": [...] }`. Each item has a unique id, text, optional quantity, checked state, optional source recipe slug, creation and update timestamps, and deletion state. Visible list order is ascending `created_at`; tombstones are hidden unless explicitly requested.

The browser stores the same full item array, including tombstones, under `localStorage` key `gusto.shopping`.

`data/favorites.json` stores shared household Einkaufsbedarfe. Each has one
canonical name, explicit exact-match aliases, and a product array whose order
is the manual preference ranking. Product cards require name and brand; store,
note, and an owned image filename are optional. Images live under
`images/_favorites/`; this reserved folder is not a recipe image folder. The
browser mirrors the catalog under `localStorage` key `gusto.favorites` for
offline reading only.

## Interfaces

Core operations load and save full state, add individual entries, add a
free-text group in one transaction, import ingredients from a recipe, list
visible entries, set or toggle checked state, tombstone entries, clear completed
entries, clear the complete visible list, and merge a remote full state.

The sync endpoint accepts only an object containing an `items` list. Core
validates each remote item's required fields and field types before merging;
malformed JSON shapes return HTTP 400 and are never persisted.

Under `gusto shopping`, the CLI exposes `list`, `add`, `add-many`, `add-recipe`,
`check`, `uncheck`, `remove`, `remove-done`, and `clear`. `add-many` accepts one
or more positional free-text entries, commits them together, and returns their
array in argument order. Explicit check and uncheck are idempotent and suited
to agents synchronizing an external checklist; a repeated target state does
not write or advance its sync version. Re-removing an existing tombstone has
the same no-op behavior.

`shopping add --source <slug>` attributes one free-text item to an existing
recipe. Core validates the recipe while holding both catalog and shopping locks;
unsourced adds retain the shopping-only lock. A visible manually sourced item
participates in the same active-recipe guard as bulk imports.

Ingredient import requires at least one parsed ingredient and refuses a new
import while any visible item with the same recipe source remains. This blocks
accidental repeated clicks without deduplicating same-looking text across
recipes or guessing how quantities should combine. Once the old sourced items
are tombstoned, a fresh import creates new ids. Rejection reports the number of
visible sourced items that caused the block.

`shopping remove-done` tombstones every checked visible item. `shopping clear`
tombstones every visible item, open or checked, in one transaction. Both return
the removed count, retain tombstones for sync, hide removed items from normal
lists, and have no CLI restore operation.

The web provides server-rendered `/shopping` forms when JavaScript is unavailable. With JavaScript, the client hides that fallback, mutates local state first, and synchronizes in the background. `GET /api/shopping` returns all server items including tombstones; `POST /api/shopping/sync` accepts `{ "items": [...] }` and returns the merged full state.

Core also owns deterministic favorite matching, need/alias CRUD, ranked-product
CRUD and movement, image ownership, and consistency checks. The CLI exposes
these as `gusto favorites list|show|match|add|set|remove|alias-add|alias-remove`
and `product-add|product-set|product-move|product-remove`. The web exposes
central management under `/favorites`, a no-JS match page, product media, and
`GET /api/favorites` for the offline-readable browser copy.

Product create/edit forms expose separate native camera and image-library
actions. The web shell converts either browser upload to metadata-free WebP
with a 1920 px maximum edge before invoking the existing core product mutation.

## Sync Contract

- Merge is per item id. The version with the later valid `updated_at` wins; equal versions keep the receiving side's current value.
- Items present on only one side are retained, so every exchange uploads and returns full state rather than a delta.
- Tombstones participate like any other version and are never hard-deleted by shopping operations.
- Both legacy second-precision and current millisecond timestamps are accepted. Valid timestamps outrank malformed legacy values on the server.
- The browser merges a response into its current state instead of overwriting it, preserving mutations made while a request was in flight. A queued follow-up sync handles changes during an active sync.
- Every server-side list mutation and full-state merge holds the shopping
  transaction lock across load, change, and atomic save. Ingredient import also
  holds the catalog lock while reading its recipe snapshot; locks are acquired
  in a stable order.

## Constraints & Gotchas

- Ingredient import recognizes only non-empty `-` or `*` bullets under the exact `## Zutaten` heading and stops at the next H2.
- Idempotency follows each owner's contract rather than command spelling.
  Shopping tombstones and explicit check states support safe sync retries;
  repeating an alias on the same Einkaufsbedarf is also safe. Creating an
  already existing Einkaufsbedarf or removing an unknown hard-deleted product
  remains an error because silently accepting it could hide a wrong identity.
- Independent item adds/checks may arrive concurrently; order-sensitive pairs
  such as `clear` or `remove-done` with `check` still have a result determined
  by lock acquisition order and should be sequenced by the caller when order
  expresses intent.
- Offline and failed syncs deliberately preserve local state. The browser retries on the next mutation, queued follow-up, initialization, or `online` event.
- Preferred-product matching only lowercases (including `ß` → `ss`) and
  collapses whitespace. It does
  not strip quantities, interpret alternatives, or use substring/fuzzy matches;
  a name or alias may belong to only one Einkaufsbedarf.
- Preference catalog mutations are server-side and require connectivity. The
  last successful catalog and content-specific product-image URLs remain
  readable offline; replacing an image creates a new filename.
- Taking or selecting a product photo is an online catalog mutation and is not
  queued by the shopping PWA. The camera control requests the outward-facing
  camera as a hint; the separate library control remains the fallback.
- The service worker uses network-only handling for the sync API and recipe media. Navigations are network-first with the cached shopping page as fallback; other same-origin static GETs are cache-first.
- Update the cache version when changing cached assets or offline shell behavior.
- Verify core behavior with `tests/test_shopping.py`, `tests/test_favorites.py`, and `tests/test_merge.py`; verify offline, two-device, API, manifest, product-image cache, and service-worker behavior with `scripts/pwa_check.py`.
