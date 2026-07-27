---
name: gusto
description: Install, update, diagnose, and operate live household or development instances of the Gusto recipe system through its CLI and running service. Use to install the app on Windows/Linux, answer "what should I cook?" or "what can I make with these ingredients?", find, show, add, edit, archive, restore, or purge recipes, manage images, tag facets, the shopping list and preferred products, read or record the cooking log, control the user service, or safely uninstall when explicitly requested. Verify the intended server before mutation, never fall back to direct data-file access, and pass `--json` whenever parsing output.
---

# Gusto — operating the recipe system

The installed Gusto service owns the Core and data. The browser UI and normal
CLI commands are equal clients of its anonymous LAN API; never bypass it with
direct Markdown or JSON writes. Recipe bodies remain Markdown and can be
replaced through `content set`. Every command takes `--json` for
machine-readable output. `suggest` is intentionally simple — judgment for
"what should I cook?" comes from combining `log`, `suggest`, `search`, and
`list`. Recipe content/tag values are German; commands and JSON keys are
English.

With `--json`, expected command failures are also JSON on stdout:
`{"ok": false, "error": "..."}` with exit code 1. Argument/usage errors remain
plain stderr with exit code 2. `check --json` is the deliberate exception: hard
integrity errors return the full diagnostics object with `ok:false` and exit 1.
`edit --json` downloads a temporary copy, waits for the editor, uploads the
result, and reports the editor result. `serve --json` first validates every
server dependency; it is a foreground recovery/development command, not normal
installed startup.

## Install or repair the app

Read `references/installation.md` completely for an install, repair, update,
service-control, or uninstall request. Installation concerns the Gusto app
only; never install or alter this skill as part of that workflow.

## Select the instance first

Gusto deliberately separates the installed household service from a
development service. A command can succeed against the wrong server, so success
alone is not evidence that the live browser can see the result.

1. For normal user requests about their cookbook, cooking history, favorites,
   or shopping list, use the **installed release** by default.
2. Use `python -m gusto` only when the user explicitly asks to work in a
   development checkout or isolated test instance. The checkout normally
   selects the separate `gusto-dev` store.
3. At the first Gusto operation in every task, run `<chosen command> home
   --json`. Before mutation, inspect `server_url`, `server_reachable`,
   `server_data_path`, `path`, `source`, `platform_default`, and
   `settings_path`. If the server is unreachable or the intended
   live/development instance is ambiguous, do not write.
4. Reuse the exact same executable/server selection for every dependent call.
   Never create through one server and then edit, attach images, validate, or
   read through another.
5. If the installed `gusto` command is unavailable, use its explicit installed
   path; never silently fall back to `python -m gusto`.

## Invocation

- Installed Windows release (live default): `gusto <command> --json`. If the
  current agent process has an old `PATH`, use this PowerShell prefix:
  `& "$env:LOCALAPPDATA\Programs\Gusto\bin\gusto.cmd"`.
- Installed Linux release (live default):
  `~/.local/opt/gusto/bin/gusto <command> --json`.
- Development checkout (only when intentional):
  `python -m gusto <command> --json`.
- All examples below use `gusto`; replace it with the exact chosen installed
  path or intentional development prefix and keep that choice unchanged.

- Read-only: `home`, `list`, `search`, `show`, `tags`, `log`, `suggest`,
  `check`, `archive list|show`, `image list`, `shopping list`, and
  `favorites list|show|match`.
- Change state: `new`, `edit`, `content set`, `set`, `delete`, `cooked`,
  `image add|set|cover|remove`,
  `archive restore|purge`,
  `shopping add|add-many|add-recipe|check|uncheck|remove|remove-done|clear`,
  and the remaining `favorites …` commands.
- Local lifecycle/recovery: `home`, `serve`, `check --offline`,
  `status|start|stop|restart`, `update`, and `uninstall` (uninstall only on an
  explicit request). Normal `check` still uses the service.

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
- All normal writes, including `edit` and `content set`, pass through the
  server's Core locks. Keep local recovery/file inspection read-only unless the
  user explicitly authorizes repair work.

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

**Add a recipe from text or images**
1. Select the instance as above and run `gusto home --json`. Keep the confirmed
   `server_url` and use the same command prefix for every following step.
2. Run `gusto search "<title>" --match all --json` and inspect exact
   title/slug matches. Do not create a duplicate; update the existing recipe or
   clarify the user's intent when the match is ambiguous.
3. Run `gusto new "<title>" --tags a,b --duration 25 --servings 2 --json`.
   This is mandatory: it creates both the Markdown template and catalog entry.
   Use the returned `slug`; never create the `.md` first and never hand-edit
   `data/recipes.json` for a normal recipe.
4. Preserve the first line as `# <title>`, followed by a `## Zutaten` bullet
   list and a `## Zubereitung` numbered list; add no frontmatter. Prefer piping
   the complete body to `gusto content set <slug> --stdin --json`. If a
   temporary UTF-8 file is necessary, upload it with `--file`, verify the
   recipe, then delete that temporary file.
5. For every useful source image, run `gusto image add <slug> <source path>
   --role <purpose> --caption "<description>" --json` with the same command
   prefix. Gusto copies and owns these files.
6. Verify through that same instance with `gusto show <slug> --json`, `gusto
   image list <slug> --json`, and finally `gusto check --json`. Do not report
   completion until the recipe appears there and `check` has no related error.
7. If `new` or `set --tags` reports an additive `warnings` entry for a new tag,
   report that it remains in the `Sonstige` facet. Do not bypass the service to
   edit `data/categories.json`.

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
  At least one change flag is required. `set --title` always updates both the
  metadata title and the first Markdown H1.
  For a headless body edit, confirm the recipe with `show`, then send the
  complete updated Markdown with `gusto content set <slug> --stdin --json`.
  `--file` is also supported; delete temporary content after `show` and `check`
  verify the upload.
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

**Archive, restore, or permanently delete a recipe**
- A normal request to delete/remove a recipe maps to exactly one
  `gusto delete <slug> --json` call. Despite its compatibility name, `delete`
  is reversible: it moves the Markdown, complete metadata, and every owned
  recipe image together into `archive/<slug>/`.
- Inspect with `gusto archive list --json` or `gusto archive show <slug>
  --json`; restore the complete snapshot with `gusto archive restore <slug>
  --json`.
- Log history and existing shopping items remain in their own stores and keep
  resolving the archived slug. Do not remove them as part of archiving.
- Permanent destruction is a separate, explicitly confirmed action:
  `gusto archive purge <slug> --yes --json`. Use it only when the user clearly
  asked for irreversible deletion. It fails while visible shopping items still
  reference the recipe; remove those items only if that was also requested.

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
  the old buttons.

## Data model

- `/api/v1/health` and `gusto home --json` report the server-owned data root.
  These paths describe storage internals for diagnosis; operate through CLI/API
  commands. The checkout service uses `gusto-dev`; the installed service uses
  its production store. `GUSTO_HOME` is an explicit server/test override.
- `recipes/<slug>.md` — recipe content, no frontmatter.
- `data/recipes.json` — metadata including `images[]` and `cover_image_id`.
- `images/<slug>/` — original image files copied into and owned by Gusto.
- `archive/<slug>/` — one reversible recipe snapshot containing `recipe.md`,
  `metadata.json`, and its owned `images/` folder.
- `data/categories.json` — `{ key: { label, tags[] } }` (order = display order); defines the facets.
- `data/log.json`, `data/shopping_list.json`.
- `data/favorites.json` — shared shopping needs, exact aliases, and product
  rankings; `images/_favorites/` contains copied product images.

The `slug` links `.md` ↔ index. Never hand-edit the live store in a normal
workflow; use the CLI and let the running server mutate it atomically.

## Pitfalls

- Parsing output? always `--json`.
- `--max-time`, `log --days`, and `suggest --days` require positive integers;
  `suggest --limit 0` is intentionally allowed. `serve --port` accepts
  1 through 65535.
- For live household work, do not use `python -m gusto` merely because the
  current directory is the repository. Use the installed CLI and confirm
  `home --json` before acting.
- Do not assume loopback is always the intended target. A successful `new` can
  still reach the wrong configured server; resolve and verify it first.
- The `.md` holds content only — never put metadata/frontmatter in it.
- Never hand-write a live `.md` or edit `data/recipes.json`. Use `gusto new`,
  then `gusto content set`, and verify with `show` and `check` through the same
  server.
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

`references/installation.md` — public one-shot install, service, update,
repair, and uninstall lifecycle.
