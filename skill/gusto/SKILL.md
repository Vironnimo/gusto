---
name: gusto
description: Operate the Gusto recipe system through its `gusto` CLI. Use to answer "what should I cook?" and "what can I make with these ingredients?", find, show, add, edit or delete recipes, manage recipe images, filter recipes by tag categories, manage the shopping list and shared preferred products, record or read the cooking log, or safely uninstall an installed runtime when explicitly requested. Drive the `gusto` CLI and pass `--json` whenever you parse output.
---

# Gusto — operating the recipe system via the CLI

The `gusto` CLI is the only interface you need; every command takes `--json`
for machine-readable output. `suggest` is intentionally dumb — the judgment for
"what should I cook?" is yours, from combining `log`, `suggest`, `search` and
`list`. (Recipe content and tag values are German; commands and JSON keys are
English.)

With `--json`, expected command failures are also JSON on stdout:
`{"ok": false, "error": "..."}` with exit code 1. Argument/usage errors remain
plain stderr with exit code 2.

## Invocation

- Development checkout: `python -m gusto <command> --json`.
- Installed release: `gusto <command>` in a new Windows console, or
  `~/.local/opt/gusto/bin/gusto <command>` on Linux.
- Read-only: `home`, `list`, `search`, `show`, `tags`, `log`, `suggest`,
  `check`, `image list`, `shopping list`, and `favorites list|show|match`.
- Change state: `new`, `set`, `delete`, `cooked`, `image add|set|cover|remove`,
  `shopping add|add-many|add-recipe|check|uncheck|remove|remove-done|clear`,
  the remaining `favorites …` commands, and `uninstall` (only when the user
  explicitly asks to remove the installed application).
- Interactive/runtime: `edit` opens the configured editor and may change the
  recipe body; `serve` starts the long-running web server.

## Parallel calls

- Read-only commands may always run in parallel.
- Gusto serializes mutations that share its catalog, favorites, or shopping
  source of truth. Independent shopping adds/checks, recipe image imports, and
  catalog/favorite additions therefore retain every change even when tools are
  called in parallel.
- Prefer one `gusto shopping add-many "Milch" "Brot" "6 Eier" --json` for a
  known group: it adds the whole group in one transaction and returns an array.
- Keep dependent calls sequential: create a recipe/need before using its id or
  slug. Also serialize operations whose intended result depends on order, such
  as `product-move` with `product-add`, `image cover` with `image remove`, or
  `shopping clear` / `shopping remove-done` with `check`.
- Direct Markdown/category writes and interactive `edit` run outside the Core
  locks. Never perform them concurrently with another write to the same files.

## Workflows

**"What should I cook?"**
1. `gusto log --days 7 --json` — what was cooked recently.
2. `gusto suggest --json` — not cooked lately, longest-ago first.
3. Choose with variety (don't repeat the recent cuisine); honor any constraint
   given: time `--max-time 25`, diet `--tag vegetarisch`. Answer with 2–3
   concrete picks, one reason each — not the raw list. A time cap excludes
   recipes with no known duration; unknown is not treated as potentially fast.

**"What can I make with X?"**
- `gusto search "haehnchen paprika" --match any --json` — searches title, tags
  and the recipe body (ingredients). `--match all` requires every term.

**Filter by tags (facets)**
- `gusto tags --json` lists the categories and their tags.
- `gusto list --tag italienisch --tag pasta --json` — OR within a category,
  AND across categories. `--tag` repeats and is comma-separated.

**Add a recipe**
- `gusto new "<title>" --tags a,b --duration 25 --servings 2 --json` writes the
  `.md` (a template) and the index entry.
- Before direct file access, run `gusto home --json` and use its `path`. Then
  write the body into `<path>/recipes/<slug>.md`: first line `# <title>`, then
  a `## Zutaten` bullet list and a `## Zubereitung` numbered list. No
  frontmatter. Never assume the checkout's `recipes/` directory is active.
- Used a new tag? `new` and `set --tags` return an additive `warnings` array
  when it has no category. Add it under a category in
  `<path>/data/categories.json`; until then it shares the `Sonstige` facet and
  `gusto check` reports it in `uncategorized_tags`.

**Add and manage recipe images**
- Inspect the source image yourself, then add it to the matching recipe with
  `gusto image add <slug> <path> --role <purpose> --caption "<description>" --json`.
  Gusto copies the file into its own store; the source path is not retained.
- A recipe can contain any number of images. The first becomes its cover
  automatically; pass `--cover` while adding or use `gusto image cover <slug>
  <id>` to select a different top image.
- `role` is a free descriptive value such as `result`, `ingredients`, or `step`;
  it does not constrain placement. Use `gusto image list <slug> --json` before
  changing images, `image set` for role/caption, and `image remove` to delete one.

**Edit · log · shopping**
- `gusto set <slug> --tags a,b --duration N` (`--tags` replaces the list);
  `--clear-duration` / `--clear-servings` remove optional numeric metadata.
  At least one change flag is required.
  For a headless body edit, resolve `gusto home --json` and rewrite
  `<path>/recipes/<slug>.md`; do not infer the store from the working directory.
- `gusto cooked <slug>` — record today; an explicit `--date` must be a valid
  `YYYY-MM-DD` no later than today (updates the log + `last_cooked`).
- `gusto shopping add-recipe <slug>` imports non-empty ingredients only when no
  visible items sourced from that recipe remain. It does not merge text or
  quantities; use explicit free-text adds for an intentional second need.
  The error reports how many visible items block a repeat. To attribute one
  manually selected ingredient, use `shopping add "<text>" --source <slug>`;
  the slug must exist and that item participates in the same import guard.
  Other operations: `shopping add "<text>"`,
  `shopping add-many "<text>" ...` (one atomic group),
  `shopping list [--pending]`, `shopping check|uncheck|remove <id>`,
  `shopping remove-done`, and `shopping clear`. Use `remove-done` only when all
  checked items should disappear. A request to empty the shopping list maps to
  exactly one `shopping clear` call, which tombstones every visible item,
  whether open or checked. Neither operation has a CLI restore.

**Find and maintain preferred products**
- `gusto favorites match "<shopping text>" --json` returns the shared household
  shopping need or `null`. Its `products` array is already ordered from most
  preferred to fallback. Matching changes only case and whitespace; never infer
  quantities, alternatives, or substrings.
- Use `favorites list|show` to inspect the catalog; `favorites add|set|remove`
  for shopping needs; and `favorites alias-add|alias-remove` to teach exact
  recurring formulations.
- Use `favorites product-add|product-set|product-move|product-remove` to manage
  required product name/brand, optional preferred store, note, owned image, and
  manual ranking. Read `references/cli.md` for exact flags and JSON shapes.

**Shopping list as a Telegram checklist**
You (the agent) run the whole thing yourself via vBot's `channel_send` tool; Gusto
stays the source of truth. The keyboard has **two kinds of buttons**:
- **Item buttons** (`data = chk:<id>`) — vBot's bundled **checklist** extension
  flips the tapped item's **leading** glyph ⬜↔✅ in the message instantly (no agent
  round-trip). This is **visual only**: it does **not** write to Gusto.
- **One "Fertig" button** (`data = run:done`) — tapping it **wakes you** with the
  message's current button state so you sync it to Gusto in one shot. This is the
  *only* path from a tap back to Gusto (the `chk` flips never reach you).

- **Build** from `gusto shopping list --json` (flat array). One button per item,
  label `⬜ <text>` when `checked:false`, `✅ <text>` when `true` — glyph **leading**
  (`⬜ Eier`, never `Eier ⬜`), `data` = `chk:<id>` (32-hex id → 36 bytes, under the
  64-byte cap). Add a **final row** with the submit button
  `{"label":"Fertig ✅","data":"run:done"}` (the payload is ignored — the state
  comes from the keyboard).
- **Post** with `channel_send`: `channel_id` (your Telegram channel),
  `platform_target` (chat/group id; omit to reuse the session's last reply target),
  `message` (e.g. `🛒 Einkaufsliste`), and `buttons` as rows of `{label, data}`, e.g.
  `[[{"label":"⬜ 200 g Spaghetti","data":"chk:a04381…"}],[{"label":"✅ Eier","data":"chk:4479aa…"}],[{"label":"Fertig ✅","data":"run:done"}]]`.
  (`buttons` can't be combined with `file_paths`.)
- **On the "Fertig" tap you are woken** with a system note that lists the current
  buttons, e.g. `- "✅ 200 g Spaghetti" (chk:a04381…)` / `- "⬜ Eier" (chk:4479aa…)`
  / `- "Fertig ✅" (run:done)`. Read the state from the `chk:` lines — **leading ✅**
  = checked, **leading ⬜** = unchecked, id = the `chk:<id>` data; ignore the `run:`
  button (it's the trigger, not an item). Then **make Gusto match**: per item
  `gusto shopping check <id>` if ✅ else `gusto shopping uncheck <id>` (idempotent;
  unknown id → exit 1, so check the code). Diff against `gusto shopping list --json`
  first to skip no-ops if you like. Finally **confirm in chat** (e.g. "✅ In Gusto
  übernommen."). vBot **auto-closes the keyboard** on this tap (the buttons vanish),
  so your chat confirmation is the feedback — if the closed message should still show
  the list, also put the item lines in the `message` text, not only in buttons.
- **"Fertig" saves the current checked-state to Gusto; it does not remove bought
  items.** To make "Fertig" also clear the bought ones, additionally run
  `gusto shopping remove-done` after the sync only when removal was requested.
  To empty the complete list instead, run `gusto shopping clear`; both removals
  use tombstones and have no CLI restore.
- **Manage** (then re-render by posting a fresh list): add one with `gusto
  shopping add "<text>"` (optionally `--source <slug>`), or a known group with
  `gusto shopping add-many "<text>" ...`; remove checked items with
  `gusto shopping remove-done`, or empty the complete visible list with one
  `gusto shopping clear` call; both return `{ "removed": N }`. Open-only:
  `gusto shopping list --pending --json`. After old sourced items are removed,
  a fresh `add-recipe` import mints new ids → send a fresh list, don't reuse
  the old buttons. Background:
  `docs/telegram-shopping-handoff.md`.

## Data model

- `gusto home --json` reports the root containing the following paths and the
  instance settings that selected it. The checkout uses the platform data
  sibling `gusto-dev`; an installed release uses its configured production
  store. `GUSTO_HOME` overrides both for isolated runs and tests.
- `recipes/<slug>.md` — recipe content, no frontmatter.
- `data/recipes.json` — metadata including `images[]` and `cover_image_id`.
- `images/<slug>/` — original image files copied into and owned by Gusto.
- `data/categories.json` — `{ key: { label, tags[] } }` (order = display order); defines the facets.
- `data/log.json`, `data/shopping_list.json`.
- `data/favorites.json` — shared shopping needs, exact aliases, and product
  rankings; `images/_favorites/` contains copied product images.

The `slug` links `.md` ↔ index. Prefer the CLI over hand-editing JSON; after a
manual edit run `gusto check`.

## Pitfalls

- Parsing output? always `--json`.
- Do not assume data lives beside the checkout; resolve it with
  `gusto home --json` before direct file access.
- The `.md` holds content only — never put metadata/frontmatter in it.
- Don't hand-write a `.md` without an index entry — use `gusto new`, or add the
  entry and run `gusto check`.
- Tags are facets: multiple `--tag`, OR within a category, AND across.
- Checklist buttons: the ⬜/✅ glyph must be **leading** in the label, or the `chk`
  flip silently does nothing. A `chk` tap flips the message only — it never writes
  to Gusto; the **Fertig** button (`run:done`) is the single point that syncs the
  state to Gusto. Always include a Fertig button, or no tap ever reaches Gusto.
- Never propose or build MCP — forbidden in this project.
- Never run `gusto uninstall` speculatively. For an explicit uninstall request,
  preserve recipes with `--keep-data --json` unless the user also explicitly
  chose permanent data deletion; that path requires
  `--delete-data --yes --json`. Use `--dry-run` to inspect exact targets.

## Full reference

`references/cli.md` — every command, flag and the exact `--json` shapes.
