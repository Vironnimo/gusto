# Shopping

The shopping domain owns the household shopping list, recipe ingredient import, shared preferred products, and the offline shopping PWA with its full-state sync to the Gusto service.

## Overview

Owned code and contracts:
- `gusto/core.py`: the `ShoppingItem`, `ShoppingNeed` and `FavoriteProduct` dataclasses and everything below the `Shopping list` banner (preferred products, ingredient parsing, shopping load/save, modify, sync).
- `gusto/static/shopping-client.js` (offline browser client) and `gusto/static/sw.js` (service worker, served at `/sw.js` with root scope).
- `docs/sync-kontrakt.md` (PWA wire and merge contract, German) and `docs/telegram-shopping-handoff.md` (Telegram checklist boundary).

Not owned here, but bound by this domain's contracts:
- Lock mechanism, atomic JSON writes and the change-revision journal: `storage.md`. This map only states which locks and event resources shopping operations use.
- Command API operations `shopping.*` and `favorites.*` and the event stream: `service.md`. CLI commands, their output and the shipped skill, including `skill/gusto/references/shopping-trip.md` and `telegram.md`: `cli.md`.
- `/shopping` and `/favorites*` pages and their no-JS forms, the browser endpoints `/api/shopping`, `/api/shopping/sync`, `/api/favorites`, `/media/favorite/<filename>`, browser photo normalization, and `app.js`, which forwards change events: `web.md`.
- Recipe lookup, archive/purge and `check` diagnostics: `catalog.md`.

Cross-domain touchpoints:
- Catalog feeds shopping: ingredient import and `shopping add --source` need an active recipe (`get`); sync validates visible sources against `recipe_references()` (active or archived).
- Shopping constrains catalog: archive purge refuses while a visible item references the slug (it holds catalog and shopping locks); `check` reports `unresolved_shopping_sources`, `duplicate_favorite_aliases`, orphaned or missing favorite image files and unreadable `shopping_list.json`/`favorites.json` as hard errors; recipe slug `_favorites` is reserved because `images/_favorites/` belongs here.
- Successful real mutations publish the `shopping` or `favorites` change resource; the shopping page consumes them without reloading.

## Terms

### Visible item

A shopping item that is not a tombstone. Checked items are visible: they count for the recipe import guard, the purge guard, `shopping list` and `shopping clear`.

### Tombstone

A shopping item kept with `deleted: true` so its removal propagates to other devices. Hidden from lists but part of every sync state; nothing hard-deletes it.

### Sync version

An item's `updated_at` UTC timestamp, compared per id to choose the last writer (see Sync Contract).

### Shopping need (`Einkaufsbedarf`)

Defined in `.vorch/GLOSSARY.md`. Domain detail: it has one canonical name and exact aliases, and list items relate to it only by matching their text at read time (`shopping/preferred-products.md`).

### Preferred-product card (`Lieblingsprodukt`)

One concrete product (name, brand, optional store, note, photo) in a shopping need's ranking; array order is the household's preference, best first.

## Data Model

- `data/shopping_list.json` is `{ "items": [...] }`, the complete state including tombstones. An item has an opaque string `id`, single-line `text`, free-text `quantity` (never parsed or combined), `checked`, `source` (recipe slug or `null`), `created_at`, `updated_at` and `deleted`. Exact JSON: `docs/sync-kontrakt.md`.
- Ids are opaque strings: Core creates 32-hex UUIDs, browsers hyphenated UUIDs or a longer fallback. Every client passes them through verbatim.
- Visible order is ascending `created_at` (string order); file order has no meaning.
- Loading tolerates legacy items without `checked`, `deleted`, `quantity`, `source` or timestamps (defaults apply), but one item with a wrong type or a missing `id`/`text` invalidates the whole file: every shopping read and write fails with a `ValueError` until repaired, and `check` names the file. The same all-or-nothing validation applies to `favorites.json`.
- `data/favorites.json` is `{ "needs": [...] }`; each need holds `aliases` and an ordered `products` array. Product photos live in `images/_favorites/` under `image_filename`; detail in `shopping/preferred-products.md`.
- Browser: `localStorage` key `gusto.shopping` holds the same `{ "items": [...] }` shape including tombstones; `gusto.favorites` holds the last fetched preference catalog for offline reading only.

## Interfaces

Transports (all reach the same Core functions):
- CLI and agents use `POST /api/v1/command` operations `shopping.*` and `favorites.*` (`service.md`).
- The PWA uses `POST /api/shopping/sync` (full-state exchange; a body object whose missing or null `items` means an empty state) and `GET /api/favorites`. `GET /api/shopping` returns the full state including tombstones for scripts and tests; the PWA never calls it.
- No-JS web forms call Core directly; their toggle flips the state, while CLI `check`/`uncheck` set it explicitly.

List mutation semantics (also stated in the skill):
- Adds create new ids with `checked: false`. Text must be non-empty and single-line; quantity is single-line free text. `add-many` validates every text, then commits the group in one transaction in argument order. Nothing deduplicates same-looking texts or combines quantities.
- Ingredient import (`shopping_add_recipe`) needs an active recipe and at least one parsed ingredient, and refuses while any visible item has that recipe as `source`; the error names their count. Items get `source = slug`. Once the old items are tombstoned, a new import creates new ids.
- A sourced single add (`shopping add --source`) needs an active recipe and joins the same import guard. Archived recipes are rejected for new attribution but remain valid sources of existing items, whose presentation resolves through the catalog's active-or-archived reference map.
- Explicit check/uncheck to the current state and removing an existing tombstone are no-ops: no write, no new `updated_at`, no change event. Unknown ids are errors; check/uncheck of a tombstone is an error too.
- `remove-done` tombstones every visible checked item, `clear` every visible item, each in one transaction returning `{ "removed": N }`. No restore exists in Core, API, CLI or web.

Ingredient parsing (`parse_ingredients`, owned here): only lines after a line that is exactly `## Zutaten` (after trimming), up to the next line starting with `## `. Only `-` or `*` bullets followed by whitespace count, each whole remaining line one entry; `+` bullets, numbered lists, bold lines and thematic breaks are ignored, while H3 subsections and nested bullets stay inside the section. Changing the rule changes what existing recipes import.

Locks and events (mechanism: `storage.md`):
- Unsourced adds, add-many, check/uncheck/toggle, remove, remove-done, clear: shopping lock; event `shopping`.
- Sourced add and ingredient import: catalog and shopping locks; event `shopping`.
- Merge: shopping lock, plus the catalog lock when any incoming item is visible and has a source (so archive/restore cannot hide a valid source mid-merge); event `shopping`.
- Every preferred-product mutation: favorites lock; event `favorites`.
- Events are published only when a file actually changed.

Preferred products (detail: `shopping/preferred-products.md`): `favorite_match(text)` returns the one need whose canonical name or alias equals the text after `normalize_shopping_text` (trim, collapse whitespace, lowercase, `ß` to `ss`), else `None`; the CLI prints `null` with exit 0. No substring, fuzzy or quantity-stripping match exists, and a normalized label belongs to at most one need. `shopping-client.js` duplicates this normalization; change both together.

Telegram checklist: Gusto's side is only the CLI. The agent lists items, posts ids verbatim through vBot's `channel_send` `checklist`, and afterwards writes explicit `shopping check|uncheck <id>` for the changed items only. Contract: `docs/telegram-shopping-handoff.md`; agent steps: `skill/gusto/references/telegram.md`; status: `.vorch/PROJECT.md` -> Context.

Store trips: Gusto persists no store layout or walking route (user decision 2026-09-23). Route order is agent judgment per answer (`skill/gusto/references/shopping-trip.md`) and never reorders, renames or rewrites items.

## Sync Contract

Wire details: `docs/sync-kontrakt.md`. Invariants every shopping change keeps:
- Merge per id: the later valid `updated_at` wins; on a tie the receiving side keeps its version; ids present on one side only are kept, so a missing item never means deletion. Every exchange sends and returns the full state including tombstones.
- Timestamps: new values are UTC `YYYY-MM-DDTHH:MM:SS.sssZ`; legacy second precision stays valid. Values compare as instants; a valid value beats a malformed one; two malformed values compare as strings; UTC-conversion overflow counts as malformed. The browser's `isNewer` mirrors Core's `_version_key`.
- Every state change of an item sets `updated_at` at least 1 ms after its previous value (Core `_now_iso(previous)`, browser `nowIso(previous)`), comparing at the persisted millisecond precision so same-millisecond changes still advance.
- Remote items must be objects with non-empty string `id` and `text`, string `quantity`, present boolean `checked` and `deleted`, non-empty string timestamps, and `source` string or null. A winning version whose timestamp cannot advance another millisecond is invalid too.
- A winning visible item's `source` must name an active or archived recipe; tombstones and losing versions are not validated, so purged slugs survive in tombstones.
- Any invalid item rejects the whole merge before saving (HTTP 400 on the sync endpoint).
- A merge without a winning or new item saves nothing and publishes no event; an empty first sync creates no file.
- Clients merge a response into their current local state, never overwrite it, and sync again when the list changed during a request.

## Conventions

- New shopping behavior goes into Core, then the command API and CLI with `--json`, then web and PWA (`.vorch/PROJECT.md` -> Architecture). The PWA is the only offline writer; the CLI never queues offline work.
- A list mutation is one load-modify-save transaction under its lock: it advances `updated_at` per changed item, tombstones instead of deleting, and skips the save when nothing changed. `shopping_save` and `favorites_save` replace the whole state and exist for tests; product code must not apply mutations through them.
- User-visible messages and errors are German; error strings reach agents through the CLI JSON error contract.
- Contract changes update together: Core, `shopping-client.js` (it duplicates normalization, timestamp and merge rules), the matching `docs/` contract, and the skill's shopping sections (`SKILL.md`, `references/cli.md`, `shopping-trip.md`, `telegram.md`; owner `cli.md`).
- Tests owned here: `tests/test_shopping.py` (list rules, ingredient parsing, data validation), `tests/test_favorites.py` (needs, aliases, matching, store narrowing, product images, their events), `tests/test_merge.py` (merge and timestamps), `scripts/pwa_check.py` (Chromium: sync API, offline use, two devices, live events, product-image cache, manifest, service worker). Related elsewhere: `tests/test_api.py` (command cases and revisions), `tests/test_concurrency.py` (parallel adds and merges), `tests/test_cli.py` (commands and help), `scripts/browser_check.py` (no-JS `/shopping`).

## External Dependencies

- A service worker registers only in a secure context (HTTPS or localhost). Over plain `http://<lan-ip>` a phone gets no offline shell and no install; the page still works online with localStorage, and ids fall back from `crypto.randomUUID` to `getRandomValues`. LAN HTTPS option (undecided): `docs/https-pwa-option.md`.
- Telegram checklists depend on vBot's built-in `channel_send` `checklist`; Gusto contains no vBot code.

## Constraints & Gotchas

- `sw.js` serves every `/static/` asset cache-first from its active cache, also online, and static URLs carry no version. Any change to a precached asset (`style.css`, `app.js`, `photo-input.js`, `shopping-client.js`, manifest, icons) or to offline behavior needs a `CACHE` bump in `sw.js`, or installed browsers keep the old file. No test enforces the bump.
- Sync rejection is all-or-nothing and the client retries the same payload. A device that was offline while an item was removed and its recipe purged, and that then changes its stale visible copy, sends a winning visible item with an unknown source: every later sync from that device fails until the user removes that item on the device.
- Tombstones are never compacted, so the file and every sync payload grow. Dropping tombstones would let an offline device resurrect removed items, because ids present on one side only are kept.
- Preferred-product changes and product photos are online-only server mutations; the PWA shows the last fetched catalog and cached photos offline and queues nothing for favorites.
- Order-sensitive pairs such as `clear` or `remove-done` with `check` resolve by lock acquisition order; callers sequence them when order expresses intent.
- Idempotency follows each operation's contract, not its spelling: repeated check/uncheck, tombstone removal and re-adding an alias to its own need are safe; creating an existing need or removing an unknown alias, product or need is an error.

## References

Read these only when your task matches - not by default.

- Changing `shopping-client.js`, `sw.js`, the shopping page hooks, `/api/shopping*` or `/api/favorites`, or debugging offline or multi-device behavior -> `shopping/offline-pwa.md`
- Changing shopping needs, aliases, product cards, store narrowing, product images or `favorites` commands -> `shopping/preferred-products.md`
- Exact PWA wire format and merge rules (German) -> `docs/sync-kontrakt.md`
- Telegram checklist boundary (German) -> `docs/telegram-shopping-handoff.md`
- Lock mechanism, atomic writes, change journal -> `storage.md`
