# Shopping: preferred products

Shopping needs (`Einkaufsbedarf`), aliases and ranked preferred-product cards in `gusto/core/favorites.py`, stored in `data/favorites.json` and `images/_favorites/`.

## Identity and labels

- Commands address a need by its id or its exact canonical name after normalization; aliases do not address a need.
- A label is the canonical name or an alias, compared after `normalize_shopping_text` (trim, collapse whitespace, lowercase, `ß` to `ss`). A normalized label may belong to only one need; creating, renaming or aliasing into another need's label is an error, and `check` reports violations as `duplicate_favorite_aliases`.
- Creating a need silently drops aliases that repeat its name or each other. Re-adding an alias the need already has is an idempotent no-op without an event; removing an absent alias is an error.
- Renaming keeps the old name as an alias so existing list wording keeps matching, and removes an alias equal to the new name.
- Names, aliases, brand, store and note must be single-line.

## Products and ranking

- A new card needs name and brand; store, note and image are optional. Appending puts it last; `product-move` takes a one-based position. Array order is the preference.
- `product-set` changes only the given fields; an empty store clears the restriction. Image replace and image removal are mutually exclusive. With no effective change the file is not rewritten and no event is published; the API and CLI reject a call that names no change.
- Removing a product or need deletes its owned images and removes `images/_favorites/` when empty. Removing an unknown product or need is an error.

## Store narrowing

- `store` is comma-separated; empty means available everywhere. Store names match after the same normalization, never as substrings.
- `favorite_for_store(need, store)` (CLI `--store` on `favorites show|match`) keeps the manual order: `products` holds cards naming that store or none, `other_products` the rest; the result also carries the trimmed `store`. The stored ranking never changes. An empty store is an error (HTTP 400).
- `favorites match` without a match returns `null` (exit 0); `favorites show` with an unknown need is an error (404 at the API).

## Product images

- Core copies the given file after extension (`.jpg`, `.jpeg`, `.png`, `.webp`, `.gif`) and header validation to `images/_favorites/<uuid><ext>`, so every replacement gets a new filename. Browser uploads are normalized to WebP by the web layer first (`web.md`); CLI files arrive as API attachments in their original format.
- A failed save deletes the fresh copy only when nothing was written; the old file is deleted after the new state is saved. A failure after the save is reported as an uncertain outcome (`storage.md`).
- `/media/favorite/<filename>` serves only filenames referenced by a product; `check` reports orphaned and missing files.

## Web and offline

- `/favorites` manages needs and cards online; `/favorites/match` is the no-JS match page. The shopping sheet's assign/create forms post to `/favorites/assign` and `/favorites/add` (`shopping/offline-pwa.md`).
- `GET /api/favorites` adds an `image_url` per product for the offline-readable browser copy.

## Tests

`tests/test_favorites.py` (Core rules, images, events and failure cleanup); `tests/test_api.py` (command shapes, store narrowing over the API); `scripts/pwa_check.py` (offline catalog and image cache).
