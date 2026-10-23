# Web

The web domain is the FastAPI host process and its server-rendered German browser UI: pages, form actions, media, Markdown rendering, browser photo normalization, page scripts, styles and the design.

## Overview

`gusto/web.py` defines `gusto.web:app`, the single ASGI app that `gusto serve` and the installed service run. It includes the `/api/v1` router from `gusto/api.py` (see `service.md`), mounts `/static`, serves the root-scoped `/sw.js`, owned media and the Jinja templates in `gusto/templates/`. `gusto/static/app.js`, `photo-input.js`, `style.css`, `manifest.webmanifest` and `icons/` belong here; `shopping-client.js` and `sw.js` belong to the shopping domain (`shopping.md`).

`gusto/rendering.py` owns recipe Markdown presentation: title omission,
Markdown conversion and allowlist sanitization. Web routes consume its rendered
HTML; it neither reads the store nor knows about HTTP or templates.

Web routes call the owning modules under `gusto/core/` in-process; they are not clients of `/api/v1/command`. They only convert form input (comma-separated tags, blank numbers as clear flags, the `# {title}` line before the body) and present results. Domain rules, validation, locking and change events stay in Core (`catalog.md`, `shopping.md`). A web action exists only for a capability Core, API and CLI already offer (PROJECT.md).

Not owned here: the command API, health and SSE endpoints (`service.md`), the offline shopping client, sync contract and service worker (`shopping.md`), starting and stopping the server (`cli.md`, `lifecycle.md`).

## Interfaces

**Request pattern.** GET routes render a template; POST form actions mutate through Core and answer `303` to a GET page (post/redirect/get). Expected Core rejections (`ValueError`) redirect back with `?error=<message>`, shown escaped in `.error`; recipe create/edit instead re-render the form with status 400 and the submitted values. Missing entities raise Starlette's 404, which the app-wide handler renders as `404.html` for every path, including unknown `/api/v1/...` routes and missing static files. Other HTTP errors are plain text. FastAPI validation failures (a missing required form field, `/suggestions?days=0`) return FastAPI's 422 JSON, not a page.

**Browser write boundary.** The host rejects unsafe HTTP methods before routing when `Origin` differs from the request's scheme/host/port (including a null or invalid origin), when `Sec-Fetch-Site` is neither `same-origin` nor `none`, or when the fallback `Referer` has a foreign origin. This covers forms, shopping sync and the mounted command API, with a definite 403 JSON error. Headerless non-browser clients remain accepted; this is browser request protection, not authentication. No cookies or CSRF token are required.

**Templates.** Normal pages extend `base.html` and pass `nav` (active navigation key) and `title`. `base.html` sets `lang="de"`, registers `/sw.js`, loads Google Fonts, `style.css`, `app.js` and `photo-input.js`, and stamps `<body data-gusto-live data-gusto-revision>` from `storage.change_state()` (Jinja global `gusto_change_state`). Calls use `templates.TemplateResponse(request, "name.html", {...})` with the request first; Starlette 1.x has no name-first form. The standalone `mutation_error.html` recovery page deliberately reads no store state and loads no scripts.

**Post-save failures.** Every mutation route lets `errors.PostMutationError` pass before catching `ValueError`; the app handler returns 500 with fixed uncertainty guidance and a GET link to inspect the affected area. It never renders a retry form or enables live reload of the POST. Shopping sync receives JSON with `outcome_unknown:true`; a definite sync validation failure stays 400 with `error: "invalid shopping state"` and a safe-to-display `detail` from Core. When a web action makes several Core calls, failures after its first successful write must also become uncertain (image metadata followed by cover selection).

**Missing entities and invalid state.** Core's public `errors.NotFoundError` distinguishes an absent entity from another `ValueError`. Cooking, favorite deletion, shopping check and shopping removal return 404 only for a missing entity; invalid persisted data before a write returns 400 with the diagnostic. A post-write failure still takes the uncertainty path above.

**URLs other code builds.** Active recipes live at `/recipe/{slug}`, archived snapshots at `/archive/{slug}`; `_recipe_reference_map` adds these as `url` to Core's reference map, and the log, the shopping fallback and `shopping-client.js` link through it. The recipe list is `/?q=...&tag=...` with repeatable `tag`. `/media/recipe/{slug}/{image_id}` and `/media/archive/{slug}/{image_id}` keep stable URLs (the service worker fetches them network-only); `/media/favorite/{filename}` serves only filenames a product references, and replacing an image creates a new filename (cached offline). Renaming any of these paths needs matching changes in `sw.js` and `shopping-client.js`.

**Shopping JSON endpoints hosted here.** `GET /api/shopping`, `GET /api/favorites` (adds `image_url` per product) and `POST /api/shopping/sync` live in `web.py` outside `/api/v1`; their contract is `shopping.md` -> Sync Contract and `docs/sync-kontrakt.md`. The merge runs in a worker thread so a lock wait never blocks other requests. `/sw.js` is served from the root with `Cache-Control: no-cache` so the worker's scope covers every page and updates are not delayed by the HTTP cache.

**DOM contracts with scripts.**
- `shopping.html` and `shopping-client.js`: `#shop-server` holds the no-JS list and forms (the client hides it), `#shop-client` is the client container, `#favorite-sheet`/`#favorite-sheet-content` the product sheet, and `<script id="shop-recipe-references" type="application/json">` the recipe reference map (`|tojson`); the client re-fetches `/shopping` to read that script again. The client renders with the same `.shop-*`/`.favorite-*` classes and posts its sheet forms to `/favorites/assign` and `/favorites/add` with `return_to=/shopping`. Renaming these ids, classes or routes is a shopping change too.
- `list.html` and `app.js`: `form.filters`, `input[name=q]`, `#results`, `#tagfilter`, `#result-count` and `.filter-panel[data-selected]` (see `web/live-pages.md`).
- Forms with `data-confirm` get a confirmation dialog; `[data-photo-picker]` markup from `_photo_picker.html` is wired by `photo-input.js`.

**Live updates.** About one second after load, `app.js` opens `/api/v1/events?after=<stamped revision>` and maps the current path to the resources it shows (`pageResources`). A relevant event reloads the page unless a POST form on it is dirty; `/new`, `/recipe/*/edit` and `/recipe/*/images` never auto-reload; `/shopping` receives a `gusto:change` DOM event instead of a reload so offline mutations survive. A new page must be classified in `pageResources` deliberately. Details: `web/live-pages.md`.

**Browser photo uploads.** Recipe and product forms offer two file inputs from `_photo_picker.html`: `image_camera` (`capture="environment"`, only a hint) and `image_file` (JPEG/PNG/WebP/GIF). `_prepared_photo` accepts at most one, rejects more than 25 MiB, applies EXIF orientation, shrinks the longest edge to 1920 px (never enlarges), keeps alpha (RGBA) or converts to RGB, uses the first frame of an animation, and writes metadata-free WebP (quality 84) to temp files it always deletes, before Core copies the result. Only read/convert failures become the German "unreadable photo" `ValueError`; failures inside the Core call propagate unchanged. CLI uploads bypass this and keep their validated original format, so the CLI client stays dependency-free.

## Conventions

- Progressive enhancement: every page and form works without JavaScript through plain GET/POST; scripts only add live search, live reload, confirmation dialogs, the double-submit guard, photo previews and the offline shopping client. Keep a no-JS path for every new action.
- HTML safety: templates autoescape; `|safe` is used only for `content_html` from `rendering.render_recipe`, the single Markdown render point. It runs Python-Markdown (`extra`, `sane_lists`) and then a stdlib allowlist sanitizer: allowed tags with per-tag attributes, `javascript:`/`vbscript:`/`data:` URLs dropped, other tags removed while their text stays escaped. No sanitizer dependency is added for this. Scripts never assign server HTML through `innerHTML`; live search clones parsed nodes. Confirmation texts stay in `data-confirm` attributes, never in inline JS strings. Redirect targets from forms pass `_safe_return_to` (local absolute paths only).
- Most pages render the `error` query parameter as escaped text, so a crafted link can show any notice; `/shopping` shows only fixed notices for known markers (`SHOPPING_NOTICES`).
- UI copy is German (PROJECT.md). Visual identity, tokens, responsive rules and copy tone: `web/design.md`.
- A change to any precached or cache-first asset (`style.css`, `app.js`, `photo-input.js`, `manifest.webmanifest`, icons) or to the `/` or `/shopping` shell must bump `CACHE` in `sw.js` (shopping domain): every page registers the worker and static URLs carry no version, so installed browsers otherwise keep the old files.
- `pyproject.toml` package data ships only `templates/*.html`, `static/*.css`, `static/*.js`, `static/*.webmanifest` and `static/icons/*.png`; a new subfolder or file type is missing from the wheel until those globs and `tests/test_packaging.py` change.
- Adding a server dependency means updating the `web` extra in `pyproject.toml`, `_WEB_DEPENDENCIES` in `gusto/runtime.py` (serve startup and installer probe) and the list in `tests/test_packaging.py`.

## External Dependencies

- FastAPI/Starlette, Jinja2, Python-Markdown, python-multipart (form and upload parsing) and Pillow are unpinned in the `web` extra; installs and updates resolve current releases, so an upstream breaking change can reach users without a Gusto change.
- Normal pages load Fraunces and Hanken Grotesk from Google Fonts. The service worker ignores foreign origins, so offline or without internet pages fall back to Georgia and system fonts. The standalone mutation-recovery page uses those fallbacks without a font request.

## Constraints & Gotchas

- `errors.PostMutationError` subclasses `ValueError`; catch it first in new mutation routes so a committed write never becomes a 400/404 or an ordinary error redirect. The recovery page cannot inherit `base.html`: the same damaged change journal may make its revision read fail again. Its inspection link can still fail while that journal is corrupt; `check` diagnoses it (`catalog/check.md`), with no automatic repair.
- Core deliberately does not deduplicate cooking-log entries; the guard in `app.js` blocks a second submit of a POST form and disables its buttons only after the submit event (disabling during it cancels the submission in Chromium). GET forms are excluded because the clicked button carries the value (day switch).
- The host's browser write check compares against the externally requested origin. Reverse proxies must preserve its Host and scheme; headerless LAN clients still have the anonymous access described in `service.md`. Purge confirmation is a form field, not authorization.
- Web multipart uploads have no request-size cap; the 25 MiB check runs after the whole file is received (the command API enforces its own cap).
- The rendered body omits the first `# ` line (`rendering.without_title`); the edit form shows the body without it and the route rebuilds `# {title}`. Core still enforces the title/H1 invariant (`catalog.md`). Blank duration/servings on the edit form clear the values through Core's clear flags.
- Only Chromium is exercised locally and in CI; Safari/iOS and Firefox behavior (camera `capture`, `backdrop-filter`, bottom sheet) is unverified.
- `EventSource` cannot send headers; the live stream stays anonymous by design. Do not add auth workarounds to it.

## Tests

- `tests/test_web_render.py`: focused sanitizer matrices; HTTP shopping notices, local redirects, full-state sync serialization and atomic validation failures, sync off the event loop; stale tag chips, photo error split, browser write rejection at the mounted app, persisted-write uncertainty and unchanged pre-write validation.
- `scripts/browser_check.py`: real Chromium against a throwaway copy of the checkout's sample `recipes/`, `data/` and `images/` under `.tmp/browser-*/` (screenshots in `shots/`); covers live search, facets, no-JS flows, photo normalization, double submit, confirmations, mobile and 320 px layouts and live SSE refresh. Its expectations (three cards, `kokos` -> 1 hit) depend on those sample files.
- `scripts/pwa_check.py` (shopping) covers manifest, service worker and offline shell; `tests/test_packaging.py` (lifecycle) the packaged assets and the web dependency list.

## References

Read these only when your task matches - not by default.

- Changing colors, type, layout, motion, icons, page structure or UI copy tone, or adding a page or component -> `web/design.md`
- Changing `app.js` behavior (live search, filter panel, live reload, submit guard, confirmations, photo preview) or the list markup it reads -> `web/live-pages.md`
- Changing page revisions or diagnosing live-update journal failures -> `storage/change-journal.md`
- Changing host-level JSON error text that an API client can receive -> `web/agent-facing-text.md`

## Agent-facing text

This domain owns the host-level JSON write-rejection message and shared mutation-uncertainty notice in `gusto/web.py`. Before changing a sentence, read `web/agent-facing-text.md` and update its reason row. Operation-specific API errors belong to `service.md`; CLI help and skill wording belong to `cli.md`.
