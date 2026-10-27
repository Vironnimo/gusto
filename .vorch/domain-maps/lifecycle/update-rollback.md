# Update and rollback

Owner: `gusto/update.py` orchestrates release verification, activation, rollback and retention. `gusto/runtime.py` owns state, Python and package preparation; `gusto/service.py` owns user-service integration and health checks. The CLI maps `UpdateError.result` unchanged under `--json` with exit 1.

## Preparation and lock

`run_update()` reads the chosen installation's state/current and loads its persisted `manifest_url`; the hidden per-call `--manifest-url` override is not persisted. Only a SemVer-newer release activates; build metadata does not make a version newer. `--check`, no available update and `--dry-run` return before acquiring the lock or mutating the installation.

Downloads accept HTTPS and local fixture paths. Transient errors have bounded retries in `fetch_bytes`; downloaded Gusto wheel and managed Python package are checked against manifest SHA-256 before installation. Manifest requirements are in `lifecycle/release-pipeline.md`.

The per-app `.update.lock` covers preparation, activation and cleanup and is shared with install/repair. It uses a nonblocking OS lock; contention reports `status:update_locked`. The file persists after unlock. Empty initialization is handled separately: wait up to 2 seconds for a first byte, reclaim an empty file older than 10 seconds, otherwise fail with the uninitialized-lock message. This is distinct from Core's data-resource locks (`storage.md`).

After acquiring the lock, update re-reads current and state together. Another updater may have finished during manifest download; an already-current release succeeds without repeating activation. Rollback uses this refreshed original state, including its Python runtime.

`runtime.prepare_release_runtime()` builds the inactive venv at its permanent version path. An interrupted/unverified build is rebuilt; an exact verified marker with an existing interpreter permits reuse. Fresh builds use an isolated import/version probe; installer repair separately requires a live web-dependency probe. Managed Windows Python is prepared side by side when version/hash changes. Reusing the same Python version with a different hash is refused. A broken currently installed Python with no version change needs installer repair, not an update over it.

## Activation and failure meaning

Activation stops the old service, writes the new current pointer, writes changed Python state, re-registers/starts the user service and waits for the expected version and data path. Rollback explicitly stops the failed instance before restoring pointer/state and registration, then verifies old-version health; Linux `enable --now` alone cannot switch an already running process.

| Status | Meaning |
|---|---|
| `current` | No newer release needs activation. |
| `update_available` / `dry_run` | A newer release exists; no installation change. |
| `updated` | New runtime passed health; current and previous versions retained. |
| `failed` | An expected failure occurred before activation; no runtime switch. |
| `update_locked` | Another lifecycle operation owns the per-app lock. |
| `update_failed`, `rollback:true` | Activation failed, previous runtime/state was restored and passed health. |
| `rollback_failed`, `rollback:false` | Recovery also failed; no claim that the previous service is healthy. |

On successful rollback the failed new Gusto runtime is removed best-effort. On success, `runtime.cleanup_versions()` removes older SemVer directories only if they have a matching verified marker and Python reference; arbitrary sibling folders are left alone. Update supplies the keep set: current and previous stay. `runtime.cleanup_python_runtimes()` keeps Python versions referenced by retained runtime markers and skips interpreter cleanup entirely if a version directory has an unreadable or incomplete marker. Cleanup occurs after health success, outside the activation rollback handler; a cleanup error is not evidence that activation failed.

## Verification

`tests/test_update.py` covers checks, executing-root discovery, activation order, rollback, concurrent state refresh, Python replacement/hash rules, downloads, cross-process lock contention and empty-lock recovery. `tests/test_service.py` owns health matching. Real activation/rollback is not exercised by release smoke: it checks only that the installed release is current.
