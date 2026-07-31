---
name: gusto
description: Install, update, diagnose, and operate Gusto through its JSON-capable CLI and running household service. Use for recipe discovery and suggestions, recipe/content/image/tag-category management, cooking history, shopping lists, preferred products, archives, service lifecycle, safe uninstall, or installing the bundled Gusto skill into vBot. Verify the selected live instance before domain mutations, never fall back to direct data-file access, and use `--json` whenever parsing output.
---

# Gusto — operate the recipe system

Gusto is agent-first: normal domain work goes through the same running service
as the browser UI. Use the CLI, not the live Markdown/JSON store. Commands and
JSON keys are English; recipe content and user-facing values are normally
German.

## Route the task before acting

- **Install, repair, update, service control, or uninstall the app:** read
  `references/installation.md` completely before acting.
- **Install or refresh this skill for vBot:** use the local `install-skill`
  workflow below. It does not need a running Gusto service.
- **Operate recipes, categories, history, images, favorites, or shopping:**
  select and verify the service instance first.
- **Need exact flags or JSON shapes:** read only the relevant section of
  `references/cli.md`.
- **Need a tappable Telegram shopping checklist:** read
  `references/telegram.md` completely before posting it.

## Non-negotiable operating contract

1. For a normal household request, use the installed `gusto` command. Use
   `python -m gusto` only when the user explicitly chose a development checkout
   or isolated test instance.
2. Before the first **domain** read or write in each task, run the exact chosen
   invocation with `home --json`. Inspect `server_url`, `server_reachable`,
   `server_data_path`, `path`, `source`, `platform_default`, and, when present,
   `settings_path`. The server must be reachable and its data path must match
   the user's intended household or development instance before any mutation.
3. Reuse that exact executable and server selection for every dependent call.
   A successful command against the wrong server is still a failure.
4. If the installed command is missing or the intended service is unreachable,
   stop and report it. Never silently switch to `python -m gusto`, `check
   --offline`, another server, or direct file access.
5. Add `--json` whenever output will be parsed. Expected failures then emit
   `{"ok":false,"error":"..."}` on stdout and exit 1. Parser/usage failures
   remain stderr and exit 2. `check --json` returns its full diagnostics object
   with `ok:false` and exit 1 for integrity errors.
6. Never edit live recipe Markdown or JSON directly. Use `new`, `set`,
   `content set`, `categories ...`, and the other CLI mutations so Core locking,
   invariants, events, and browser state stay correct.

Installed invocation:

- Windows: `gusto <command> --json`. If the current process has an old `PATH`,
  use `& "$env:LOCALAPPDATA\Programs\Gusto\bin\gusto.cmd"` as the prefix.
- Linux: `~/.local/opt/gusto/bin/gusto <command> --json`.
- Intentional checkout only: `python -m gusto <command> --json`.

The examples below use `gusto`; replace it with the already verified prefix.

## Safe concurrency

- Read-only calls may run in parallel.
- Gusto serializes writes sharing the catalog, favorites, or shopping source of
  truth. Independent adds, uploads, and item-specific checks do not lose data.
- Prefer one `shopping add-many` call for a known group.
- Keep dependent or order-sensitive calls sequential: create before using a
  returned slug/id; do not race reorder/remove/cover operations with changes to
  the same collection.

## Core workflows

### Decide what to cook

1. `gusto log --days 7 --json`
2. `gusto suggest --json`
3. Use `list` or `search` with the user's time/diet constraints as needed.
4. Return 2–3 concrete choices with a short reason, accounting for recent
   variety. Do not merely repeat `suggest`; its ordering is intentionally
   simple. A `--max-time` cap excludes recipes with unknown duration.

For available ingredients:

```text
gusto search "haehnchen paprika" --match any --json
```

Search covers title, tags, and complete recipe Markdown, including ingredients.

### Filter and maintain tag facets

- `gusto tags --json` shows used facets; `gusto tags --all --json` includes all
  configured tags.
- Repeated `--tag` values are ORed within one category and ANDed across
  categories.
- `new` and `set --tags` may return an additive `uncategorized_tags` warning.
  Inspect `gusto categories list --json`; when the intended facet is clear,
  resolve it with `gusto categories assign <key> <tag> ... --json` and verify
  with `gusto check --json`.
- If no suitable category exists, do not invent taxonomy silently. Ask the user
  or, when their intent is already explicit, create it with `gusto categories
  add <key> "<label>" --json`, then assign the tag.
- Category order, labels, assignment, unassignment, and removal are available
  through `categories ...`; use `references/cli.md` for the exact commands.

### Add a recipe from text or images

1. Verify the instance as above.
2. Search the proposed title with `search --match all --json`. Update an exact
   match; clarify an ambiguous match; do not create a duplicate.
3. Run `gusto new "<title>" --tags a,b --duration 25 --servings 2 --json`.
   Use the returned slug. This creates both Markdown and catalog metadata.
4. Send the complete recipe through `gusto content set <slug> --stdin --json`
   (or `--file`). Use `# <title>`, `## Zutaten` with bullets, and `##
   Zubereitung` with numbered steps. Add no frontmatter. Core rewrites the first
   H1 to the stored title.
5. Inspect every useful supplied image and add it with `gusto image add <slug>
   <path> --role <purpose> --caption "<description>" --json`. Gusto copies and
   owns the file; the first image becomes the default cover.
6. Resolve any tag warning as described above.
7. Verify with `show`, `image list` when images were added, and `check`, all
   through the same instance. Do not report completion before they agree.

Use `--cover` while adding or `image cover <slug> <id>` later. Roles such as
`result`, `ingredients`, and `step` are descriptive, not a closed enum.

### Edit or record a recipe

- Metadata: `gusto set <slug> ... --json`. `--tags` replaces the complete list;
  `--clear-duration` and `--clear-servings` remove values. `--title` updates
  metadata and the Markdown H1 together.
- Body, headless: read with `show`, then send the complete replacement through
  `content set --stdin` or `--file`. Re-read with `show` and run `check`.
- Body, interactive human editor: `gusto edit <slug>` downloads a temporary
  copy and uploads it after the editor exits.
- Cooking history: `gusto cooked <slug> [--date YYYY-MM-DD] --json`. Explicit
  dates must be valid and not in the future.

### Manage the shopping list

- Add free text with `shopping add`; add a known group atomically with
  `shopping add-many`.
- `shopping add-recipe <slug>` imports non-empty ingredients only while no
  visible sourced items from that recipe remain. It never merges similar text
  or guesses quantities. `shopping add --source <slug>` participates in the
  same guard.
- `shopping check` and `uncheck` set an explicit state and are idempotent.
- `shopping remove <id>` removes one item through a sync tombstone.
- `shopping remove-done` removes checked visible items. `shopping clear`
  removes every visible item, open or checked. Neither bulk removal has a CLI
  restore; use them only when that scope matches the request.

### Archive or destroy a recipe

- A normal request to delete/remove a recipe maps to exactly one `gusto delete
  <slug> --json`. Despite its compatibility name, it reversibly archives the
  Markdown, complete metadata, and owned images together.
- Inspect with `archive list|show`; restore the complete snapshot with `archive
  restore`. Log history and shopping items remain separate and keep resolving
  the archived slug.
- Permanent destruction is only `gusto archive purge <slug> --yes --json` and
  requires an explicit user request for irreversible deletion. It fails while
  visible shopping items still reference the recipe; remove those only if that
  was also requested.

### Match and maintain preferred products

- `favorites match "<shopping text>" --json` matches only an exact canonical
  name or learned alias after case/whitespace normalization. Never infer
  substrings, alternatives, or quantities.
- The returned `products` array is already ordered from preferred to fallback.
- Use `alias-add|alias-remove` to teach exact wording and
  `product-add|product-set|product-move|product-remove` for ranked product
  cards. Images are copied into Gusto. Exact flags are in `references/cli.md`.

### Install or refresh the vBot skill

This is a local, explicit host action; do not run `home` and do not require the
Gusto service.

```text
gusto install-skill vbot --dry-run --json
gusto install-skill vbot --json
```

The command requires the default vBot directory `~/.vbot`, creates its
`skills/` directory if needed, and installs the active Gusto version's bundled
skill at `~/.vbot/skills/gusto`. A repeat fully replaces the existing Gusto
skill, including removal of stale files. It does not install vBot itself.

## Final safety reminders

- `home`, `serve`, `install-skill`, service control, `update`, `uninstall`, and
  explicit `check --offline` are local. Normal catalog/category/log/image/
  favorites/shopping commands require the service.
- The API is anonymous by design for a trusted home LAN. Never expose its port
  directly to the public internet.
- `uninstall` is never speculative. Preserve data with `--keep-data`; permanent
  data deletion additionally requires `--delete-data --yes`. The separately
  installed vBot skill is not removed by app uninstall.
- Never propose or build MCP for Gusto.

## References

- `references/cli.md` — complete command, flag, result, and JSON contracts.
- `references/installation.md` — app install, repair, service, update, skill
  delivery, and uninstall lifecycle.
- `references/telegram.md` — exact vBot Telegram checklist round-trip.
