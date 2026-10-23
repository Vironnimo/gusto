# Storage

The storage domain is the persistence kernel in `gusto/core/storage.py`: it selects the data root, defines the store layout and persisted-format rules, serializes mutations with cross-process locks, writes files atomically, and persists change revisions; `gusto/_temp.py` keeps checkout scratch files in `.tmp/`.

## Overview

Storage owns `gusto/core/storage.py`, `gusto/_temp.py`, and the checkout's `gusto.settings.json`. Shared domain record decoding lives in public `gusto/core/records.py`; domain owners own their loaders and unlocked savers. Entity meaning and file shapes belong to their owners: recipes, archive, images, categories and log to `catalog.md`; shopping list and favorites to `shopping.md`. HTTP exposure of health and the event stream is `service.md`; writing an installed runtime's settings and managed app paths is `lifecycle.md`; `server_url` selection from the same settings file is `cli.md`.

Only Core touches the store, normally inside the one running service (PROJECT.md). Local exceptions in the CLI process: `home` (read-only `storage_info()`), `check --offline` (read-only `checks.check()` against the CLI machine's own data root) and the autostart log path.

## Terms

### Data root

The one directory holding a Gusto store. The CLI machine's data root can differ from the service's; selection and reporting belong to Data Model.

### Resource

A named source of truth whose mutations are serialized together and reported together in change events (see Interfaces).

### Change revision

A persisted sequence number identifying a group of durable changes to resources (see Interfaces). It carries no diff and is unrelated to shopping sync versions.

## Data Model

**Data-root selection** (`_resolve_data_root`, recomputed on every call, no caching):
1. `GUSTO_HOME` when non-empty; the settings file is then not read at all (even an invalid one).
2. The instance's `gusto.settings.json`: the checkout root when the package's parent contains `pyproject.toml`, otherwise `sys.prefix` (the installed venv). Its `data_dir` must be a non-empty string; environment variables and `~` are expanded; a relative value resolves against the parent of the platform default. The checkout's `{"data_dir": "gusto-dev"}` therefore means `%LOCALAPPDATA%\gusto-dev` or `$XDG_DATA_HOME/gusto-dev` (`~/.local/share/gusto-dev`), shared by every checkout and worktree. An existing but invalid settings file raises `ValueError` on every data access; there is no fallback.
3. Legacy: the package's parent directory when it contains a recognizable store and the platform default does not (source `legacy`).
4. Platform default: `%LOCALAPPDATA%\Gusto`, `$XDG_DATA_HOME/gusto` (else `~/.local/share/gusto`), macOS `~/Library/Application Support/Gusto`.

`storage_info()` returns `path`, `source` (`environment`, `settings`, `legacy`, `platform_default`), `platform_default`, and `settings_path` when that file exists. `home --json` reports the local root as `path` and the service's as `server_data_path`. A store is "recognizable" (`contains_gusto_data`) when `data/` holds any of the five domain data JSON files or `recipes/` holds a `*.md`; a change journal alone does not qualify.

**Store layout:**

| Path | Owner |
|---|---|
| `recipes/`, `images/<slug>/`, `archive/`, `data/recipes.json`, `data/categories.json`, `data/log.json` | `catalog.md` |
| `data/shopping_list.json`, `data/favorites.json`, `images/_favorites/` | `shopping.md` |
| `data/changes.json` (change journal) | storage, `storage/change-journal.md` |
| `data/.gusto-<resource>.lock` | storage; persistent, never deleted, not data |
| `.<filename>.<random>.tmp` beside a target | storage; transient atomic-write staging (git-ignored `*.tmp`), left only after a crash |
| `gusto-autostart.log` in the root | `lifecycle.md` |

**Persisted formats:**
- JSON is UTF-8 with `ensure_ascii=False`, 2-space indent, trailing newline, LF. Markdown/text is written UTF-8 verbatim with LF translation disabled.
- A missing file means empty state. Loaders never write, repair or persist migrations on read. Malformed JSON or shapes raise a German `ValueError`; `check` collects these with the file context into `invalid_data_files` (`catalog/check.md`).
- Dataclass-backed records (`Recipe`, `RecipeImage`, `ShoppingItem`, `ShoppingNeed`, `FavoriteProduct`) validate field types, default missing optional fields, and silently drop unknown keys, so the next save of that file removes them. A new persisted field needs a dataclass field with a default; an older runtime (for example after an update rollback) drops it on its next write. `data/categories.json` is strict instead: unknown keys reject the file.

## Interfaces

**Transactions.** The domain resources are `catalog`, `log`, `favorites` and `shopping`; `events` is an internal lock for the change journal only. Every Core mutation is one function wrapped in `@locked_mutation(<resources>, event_resources=...)`, or `execute_locked_mutation(resources, fn, event_resources=...)` when the resources depend on arguments (sourced `shopping_add`, `shopping_merge`). The locks span the complete read-modify-write, not only the final save. `event_resources` defaults to the locked resources.
- Domain implementations import the storage module and call its functions through that module, including `write_json`, `write_text`, checkpoints and the journal. Fault injection patches this actual owner; copied function aliases must not create a second mutation context or bypass a storage patch.
- Inside a transaction use the `_..._unlocked` savers and plain loaders. Never call another public locked Core function: the per-path `threading.Lock` is not reentrant, so the same resource deadlocks the thread, and nesting a different resource breaks the sorted acquisition order.
- Acquire every resource in the one call; `_mutation_locks` sorts lock paths, which keeps multi-resource transactions deadlock-free.
- Reads take no lock. Atomic replacement shows readers the old or new complete file, but a multi-file transaction can be observed mid-way.

**Write tracking and failure semantics.**
- `write_json`/`write_text` compare the new content with the file and return `False` without writing when it is identical. A real replacement increments the thread-local write count. Any other durable filesystem change inside a transaction (moves, `mkdtemp`, a failed cleanup that leaves a copy) must call `mark_mutation()` itself.
- After the function: no counted write -> its result or original exception, no event. Writes and success -> one change event. Writes and an exception -> the event is still recorded, then `PostMutationError` (a `ValueError` subclass) wraps the original; the API turns it into an uncertain outcome (`service.md`). A journal failure after committed writes also raises `PostMutationError`; nothing is undone.
- Rollback protocol for multi-file mutations: take `mutation_checkpoint()` before the first write; after a complete rollback call `rollback_completed(checkpoint)` so the original failure stays a clean rejection without an event. Rollback writes that must not count use `track_mutation=False`.
- Consequence: an operation that rewrites identical content emits no revision. Idempotent Core operations stay silent only as long as they do not rewrite.

**Change journal.** Every changed transaction persists a new monotonic revision; health reports the latest, and the SSE stream replays newer ones. `change_state()` returns `{revision, resources}`; `change_events(after)` returns ordered `{revision, resources}` events newer than `after`, beginning with one synthetic compacted event when `after` predates retained history. `_record_change` runs after the business locks are released, under the `events` lock. Format, compaction and recovery: `storage/change-journal.md`.

**Paths.** Use `project_root()` and the path helpers (`data_dir()`, `recipe_file()`, `changes_path()`, ...); never derive store paths from the working directory or the package directory.

## Conventions

- A new persisted file gets a path helper, a loader that returns empty state for a missing file and a German `ValueError` for invalid content, an `_unlocked` saver through `write_json`, a resource (new names must also be added to the `_record_change` allowlist and the journal's legacy-migration list), and a `check` diagnostic.
- Checkout temp policy (`gusto/_temp.py`): `import gusto` runs `configure_checkout_temp()` before Core loads. It activates only when the package's parent contains both `pyproject.toml` and `scripts/run_quality.py`; it creates `<repo>/.tmp`, replaces Python's cached `tempfile.tempdir`, and exports `TMPDIR`, `TEMP`, `TMP` to child processes. It loads no Core and resolves no data. Failure to create `.tmp` makes the import fail; an existing directory's writability is checked only when a temp allocation uses it. Python allocations in the importing process use the cached path without fallback. Installed packages keep the caller's temp policy. Explicit `dir=` staging (atomic writes, archive transaction folders) stays beside its destination for same-filesystem renames. In a checkout, API attachment and web upload scratch files therefore also land in `.tmp/`.
- Standalone tests and scripts import `gusto` before allocating temp files and point `GUSTO_HOME` into `.tmp`; several tests assert that `project_root()` lies under `tempfile.gettempdir()` before writing.

## Constraints & Gotchas

- Windows: atomic replacement retries for up to 1 s on `winerror` 5, 32 or 33, caused by concurrent reader handles (for example SSE journal reads); a persistent conflict still raises.
- Locks: a persistent lock file is opened with `O_RDWR | O_CREAT`, then locked with `msvcrt.locking` (Windows) or `flock` (POSIX). Empty files are valid: Windows locks one byte beyond EOF without writing a sentinel; existing one-byte files remain usable too. Never replace or delete the lock file while a process can hold its lock. Locks are advisory: direct file edits bypass them. Cross-process waiting times out after 30 s with a German `ValueError` before business data is written; threads in one process wait on the thread lock without timeout.
- A corrupt `data/changes.json` makes `/api/v1/health` and every server-rendered page fail with HTTP 500 (the base template reads the revision), and turns every mutation that writes into `PostMutationError`. `check` reports it in `invalid_data_files` while continuing other diagnostics; it never repairs or resets the journal. Recovery: `storage/change-journal.md`.
- A process killed after committing but before `_record_change` leaves a change without an event. Revisions are assigned after locks are released, so their order need not match commit order; clients treat events as "refresh these resources", not as ordered diffs.
- The checkout's settings select only `gusto-dev` and no `server_url`, so checkout CLI domain commands target `http://127.0.0.1:8000`, the default port of an installed production service. Use `--server`/`GUSTO_URL` for a dev server and verify `server_data_path` before writing.
- `gusto-dev` is shared by all checkouts and worktrees; tests must always use `GUSTO_HOME`.
- Duplicated logic to keep in sync: `install.py` repeats the platform default and `contains_gusto_data` (standalone installer); `gusto/client.py` repeats `default_settings_path` for `server_url`. Checkout detection differs: settings need `pyproject.toml`, temp policy also `scripts/run_quality.py`.

## Tests

`tests/test_concurrency.py` (empty lock recovery, lock timeout before business writes, cross-process contention on catalog, favorites, shopping, log and images, parallel CLI adds against a real server, sync during restore, Windows open-reader replacement), `tests/test_temp.py` (checkout temp policy), `tests/test_recipes.py` (`transaction_failure_checks`, monotonic parallel revisions, corrupt journal diagnostics without repair), `tests/test_merge.py` (silent no-op merge, preserved bytes and revision), `tests/test_api.py` (compacted replay, journal failure as uncertain outcome). `tests/test_paths.py` checks settings-selected roots, relative `data_dir`, invalid settings and `GUSTO_HOME` precedence through public storage discovery alongside lifecycle path contracts. Not covered: legacy fallback, journal legacy migration.

## References

Read these only when your task matches - not by default.

- Changing the change journal, compaction or replay, or recovering a broken `data/changes.json` -> `storage/change-journal.md`
