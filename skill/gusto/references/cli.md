# Less common Gusto workflows

Use the selected executable and service from the main skill; `<placeholders>` come from the request or returned identifiers. Command `--help` owns the full argument list.

## Preferred products

A shopping need groups exact names/aliases and a ranked list of product cards. For the request "Remember Zentis Samt as our first choice for Marmelade at REWE", look for the need; create it only if absent, then add the product:

```text
gusto favorites match "Marmelade"
gusto favorites add "Marmelade"
gusto favorites product-add "Marmelade" "Samt" --brand "Zentis" --store "REWE"
gusto favorites product-move "Marmelade" <product-id> 1
```

Use the ID returned by `product-add`; omit the move if the result already places it first. `favorites show <need>` reads a need by ID or exact name; `favorites match "<text>"` matches a name or alias after case/whitespace folding. A missing match does not infer that a differently named need is absent: use `favorites list` if the user means an existing need under another wording. Save that equivalence with `gusto favorites alias-add <need> "<alias>"` when requested.

`--store` filters `show`/`match` to applicable cards in preference order, with other-store cards shown separately. Omitted/empty product store means everywhere; comma-separated names mean several stores. These are household records, not verified availability. Renaming a need keeps its old name as an alias; removing a need removes all its cards and owned images. Product removal removes only that card and its owned image.

## Facets and images

When a saved tag has a clear existing facet, assign it using the key from `categories list`:

```text
gusto categories list
gusto categories assign <key> <tag>
```

Assignment moves a tag's existing category; category removal leaves recipe tags in `Sonstige`. Create taxonomy only when the user supplies or approves it; category and tag positions start at 1.

To attach an image and make it the cover:

```text
gusto image add <slug> "<image-file>" --role result --caption "<description>" --cover
```

Gusto copies JPG, PNG, WebP or GIF files up to 25 MiB; source files remain yours. To change an existing gallery, get IDs with `image list <slug>`, then use `image set`, `image cover` or `image remove` for the selected ID. Removing the cover makes the first remaining image the cover; image removal has no separate undo.

## Backup and restore

Create a verified backup in a new local ZIP file:

```text
gusto backup create --file "<new-backup-file>"
```

For an explicitly requested restore, preview the supplied file on the intended service:

```text
gusto backup preview --file "<backup-file>"
```

Review its counts, warnings and added/removed/replaced files with the user's intended replacement scope. Restore replaces the household store, not just one recipe; if that scope was not authorized, ask before proceeding. Use the same file and returned token:

```text
gusto backup restore --file "<backup-file>" --token <token>
```

The token binds the archive and current server state; a changed state needs a new preview and review. A successful restore returns a safety backup ID for the previous state; download it when the user needs a local copy:

```text
gusto backup safety-download <safety-backup-id> --file "<new-safety-file>"
```

A missing restore reply leaves the outcome uncertain; inspect the intended service and report the uncertainty before another restore. Do not assume the preview token proves a previous restore failed.

## Interactive editor recovery

`gusto edit <slug>` opens a human editor and waits; headless changes use `set --file` or `--stdin`. A failed `edit` or `new --edit` can retain a local copy (`recovery_path` in JSON); a new recipe may already exist. Read `show` on the same service to determine whether the upload succeeded and obtain the current revision; if needed, merge concurrent changes into the retained file and upload it:

```text
gusto show <slug>
gusto set <slug> --file "<recovery-file>" --revision <revision>
```

A successful upload with `retained_path` only means local cleanup failed; do not upload again. Source files passed to `--file` remain in place; UTF-8 is preferred, and files also accept UTF-8/UTF-16 BOMs, while stdin is UTF-8.

## Processing JSON

Add `--json` after a leaf command when code needs stable fields:

```text
gusto shopping list --pending --store "REWE" --json
```

Success prints one JSON value and exits 0, including empty arrays or `null` for an unmatched favorite. Lists remain arrays, not envelopes; without explicit paging flags they contain the full result. `shopping list --store` adds each item's `favorite` (need with ranked `products` and `other_products`, or `null`). Multi-ID check/uncheck returns `items`, `changed` and `unchanged`; a single ID returns its item as before. Multi-ID removal returns `ids`, `removed` and `unchanged`; a single ID returns `id` and `deleted`.

Command failures print `{"ok":false,"error":"..."}` to stdout and exit 1; usage errors go to stderr and exit 2. Integrity `check` and lifecycle failures can return their complete diagnostics instead. `outcome_unknown:true` requires the state inspection described in the main skill, even when a server cleanup failed after saving. Additive warnings do not negate success. Recipe create/update/show returns content and revision; JSON cooking logs remain oldest-first.
