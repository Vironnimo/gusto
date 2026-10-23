# Web: live pages

Behavior of `gusto/static/app.js`, `gusto/static/photo-input.js` and the recipe-list markup they drive. Entry point: `web.md` -> References.

## Recipe list, search and facets

- `/` renders `search.search(query=q, tags=tag)` sorted by title and the facet bar from `categories.tag_groups(only_used=True)`. Each chip is a link that toggles its tag case-insensitively while keeping the query and the other tags (`_tag_bar`). Selected tags that no used facet lists anymore (old bookmarks) appear under `Weitere Filter` and stay individually removable. Selection semantics (OR within, AND across facets) belong to Core (`catalog.md`).
- Without JS the search form submits `q` plus hidden `tag` inputs, so facets survive a text search.
- Live search: `input` and native `search` events debounce 150 ms, then fetch the same `/?q=...&tag=...` page and swap `#results`, `#tagfilter` and the `#result-count` text by cloning parsed nodes; `history.replaceState` keeps the URL current. Every text change increments a sequence number at once, so an older response is dropped even before the next request starts. Tag links in the form are rewritten to the current text immediately, so a quick chip click or open-in-new-tab keeps the query. A `search` event with unchanged text is ignored because Chromium fires one after Enter and it must not cancel that submit. Enter runs the update with fallback: a failed fetch or non-OK response falls back to a normal form submit.
- `#results.is-live` suppresses the card fade-in for swapped results.
- The filter panel (`details.filter-panel`) is always open above 720 px; at or below 720 px it starts collapsed unless tags are selected (`data-selected`), and it re-evaluates when the viewport crosses the breakpoint.
- The `/` key focuses the search field when focus is not in an input or textarea.

## Live change events

- Pages stamp the change revision they rendered. After a 1 s delay `app.js` opens `EventSource('/api/v1/events?after=<revision>')`, so changes between render and connect replay (`service.md` owns the stream).
- An event whose revision equals the latest seen one is skipped; any other revision, including a smaller one after a journal reset, is followed.
- `pageResources` maps paths to resources: `/shopping` -> shopping, favorites, catalog; `/favorites*` -> favorites; `/log` -> log, catalog; `/suggestions` -> catalog, log; `/archive*` -> catalog; `/` and `/recipe/{slug}` -> catalog, log; everything else, including `/new`, `/recipe/*/edit` and `/recipe/*/images`, -> none.
- A relevant event on `/shopping` dispatches `gusto:change` (handled by `shopping-client.js`); elsewhere the page reloads unless a POST form has `data-dirty="true"`, which any input or change inside it sets.
- Adding a page: add its path to `pageResources` with the resources it displays, or leave it out when a reload could destroy input.

## Forms

- Submit guard: the first submit of a POST form sets `data-submitted`, a second submit in the same moment is prevented, and submit buttons are disabled in a `setTimeout(0)` after the event. A back-forward-cache restore (`pageshow` with `persisted`) clears the flag and re-enables the buttons.
- `data-confirm` forms call `window.confirm` with the attribute text; a dismissed dialog prevents the submit and the guard. Without JS the forms submit directly.
- `photo-input.js`: choosing a file in one picker input clears the other and shows a local object-URL preview; `Verwerfen` clears both. The server still rejects two files, which is reachable only without JS.
