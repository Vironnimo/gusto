---
name: gusto
description: Operate the Gusto recipe system through its `gusto` CLI. Use to answer "what should I cook?" and "what can I make with these ingredients?", find, show, add, edit or delete recipes, manage recipe images, filter recipes by tag categories, manage the shopping list, and record or read the cooking log. Drive the `gusto` CLI and pass `--json` whenever you parse output.
---

# Gusto — operating the recipe system via the CLI

The `gusto` CLI is the only interface you need; every command takes `--json`
for machine-readable output. `suggest` is intentionally dumb — the judgment for
"what should I cook?" is yours, from combining `log`, `suggest`, `search` and
`list`. (Recipe content and tag values are German; commands and JSON keys are
English.)

## Invocation

- `python -m gusto <command> --json` (or `gusto <command>` if installed).
- Side-effect-free: `list`, `search`, `show`, `tags`, `log`, `suggest`, `check`.
- Change state: `new`, `set`, `delete`, `cooked`, `image …`, `shopping …`.

## Workflows

**"What should I cook?"**
1. `gusto log --days 7 --json` — what was cooked recently.
2. `gusto suggest --json` — not cooked lately, longest-ago first.
3. Choose with variety (don't repeat the recent cuisine); honor any constraint
   given: time `--max-time 25`, diet `--tag vegetarisch`. Answer with 2–3
   concrete picks, one reason each — not the raw list.

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
- Then write the body into `recipes/<slug>.md`: first line `# <title>`, then a
  `## Zutaten` bullet list and a `## Zubereitung` numbered list. No frontmatter.
  (From Python in one step: `core.add_recipe(title, tags=..., content=md)`.)
- Used a new tag? add it under a category in `data/categories.json`;
  `gusto check` reports uncategorized tags.

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
  edit the body by rewriting `recipes/<slug>.md`.
- `gusto cooked <slug>` — record a cook (updates the log + `last_cooked`).
- `gusto shopping add-recipe <slug>` (all ingredients), `shopping add "<text>"`,
  `shopping list [--pending]`, `shopping check|uncheck|remove <id>`,
  `shopping clear`.

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
  `gusto shopping clear` (tombstones all checked) after the sync.
- **Manage** (then re-render by posting a fresh list): add `gusto shopping add
  "<text>"`; clear done `gusto shopping clear` → `{ "removed": N }`; open-only
  `gusto shopping list --pending --json`. A rebuild (`add-recipe`) mints new ids
  → send a fresh list, don't reuse the old buttons. Background:
  `docs/telegram-shopping-handoff.md`.

## Data model

- `recipes/<slug>.md` — recipe content, no frontmatter.
- `data/recipes.json` — metadata including `images[]` and `cover_image_id`.
- `images/<slug>/` — original image files copied into and owned by Gusto.
- `data/categories.json` — `{ key: { label, tags[] } }` (order = display order); defines the facets.
- `data/log.json`, `data/shopping_list.json`.

The `slug` links `.md` ↔ index. Prefer the CLI over hand-editing JSON; after a
manual edit run `gusto check`.

## Pitfalls

- Parsing output? always `--json`.
- The `.md` holds content only — never put metadata/frontmatter in it.
- Don't hand-write a `.md` without an index entry — use `gusto new`, or add the
  entry and run `gusto check`.
- Tags are facets: multiple `--tag`, OR within a category, AND across.
- Checklist buttons: the ⬜/✅ glyph must be **leading** in the label, or the `chk`
  flip silently does nothing. A `chk` tap flips the message only — it never writes
  to Gusto; the **Fertig** button (`run:done`) is the single point that syncs the
  state to Gusto. Always include a Fertig button, or no tap ever reaches Gusto.
- Never propose or build MCP — forbidden in this project.

## Full reference

`references/cli.md` — every command, flag and the exact `--json` shapes.
