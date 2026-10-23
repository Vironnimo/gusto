# Testing guide

How to write and change Gusto's quality gates. The rules on what to test are in `AGENTS.md` -> Testing; this file covers how.

## Layout

- One gate per owner: `tests/test_<owner>.py` (`test_recipes.py` for the catalog, `test_shopping.py`, `test_favorites.py`, `test_merge.py` for shopping, `test_api.py` for the service, `test_cli.py` and `test_agent_contract.py` for the CLI and skill, `test_web_render.py` for templates, and one file per lifecycle module). Extend the owner's gate; a new file must be added to `SCRIPT_CHECKS` in `scripts/run_quality.py`.
- A gate is a script: a module-level `check(condition, message)` helper counts passed checks and raises on the first failure; the script ends with `print(f"OK - {checks} ... checks passed")`. Keep that last line; the survey and CI logs read it.
- Real-browser behavior goes into `scripts/browser_check.py` (web UI, no-JS fallback) or `scripts/pwa_check.py` (offline shell, sync, two-device merge).
- Run one gate with `python scripts/run_quality.py tests/test_recipes.py` or a group with `scripts`, `browser` or `all`. The runner applies the same isolation and 180-second deadline locally, at push and in CI. Direct Python invocation still works but bypasses the runner's environment and deadline.

## Isolation

- The runner's `gate_environment` gives each gate an empty `HOME` and `USERPROFILE`, isolated `LOCALAPPDATA` and `APPDATA`, and a default `GUSTO_HOME` under a fresh `.tmp/quality-*/` directory. It unsets `XDG_CONFIG_HOME`, `XDG_DATA_HOME`, `XDG_CACHE_HOME` and `GUSTO_URL`. Child processes inherit that environment; the directory is removed after the gate.
- Import `gusto` before allocating temporary files (with `# noqa: E402, F401` for side-effect-only imports): it points Python's temp directory and the `TMPDIR`, `TEMP`, `TMP` of child processes at the checkout's `.tmp/`. This also protects direct invocation. Repository-local temporary files are a project requirement; do not move them to the system temp directory.
- Then allocate the gate's store with `tempfile.mkdtemp(prefix="gusto-<owner>-test-")` and set `GUSTO_HOME` to it before importing Core or starting a server. Subprocesses and servers get the same `GUSTO_HOME` through their environment.
- A new server fixture must pick a free port, wait for `/api/v1/health`, verify `data_path` and stop the server in `finally`. The browser gates do this; the CLI and concurrency gates currently use weaker startup health checks, with explicit temporary stores passed to their server processes.
- The Playwright browser cache is shared read-only during checks: `gate_environment` preserves an explicit `PLAYWRIGHT_BROWSERS_PATH` or resolves its usual per-user location before replacing home paths. The runner does not isolate the network, system service manager or filesystem outside those default paths. Lifecycle gates therefore still pass explicit roots and fake runners/openers rather than touching a real installation.
- Timeout and interruption kill the gate's process tree. Successful gates must close their own servers, workers and browser contexts; there is no general leaked-process detector.

## Assertions

- Exact strings only for stable contracts: JSON keys and values the CLI or API promises, persisted file formats, exit codes, `outcome_unknown`, the Markdown H1 rule. German UI and error prose is editable; assert structured fields, status codes or the presence of an identifier instead of whole sentences.
- CLI behavior is checked through the JSON result and exit code, the way an agent sees it.

## Timing

- Never stabilize a flaky check with a longer wait. Synchronize on the condition instead: poll the observable state with a short interval and an overall deadline (as `wait_up` in the browser checks), or use file barriers between processes (as `tests/test_update.py` does for competing lock attempts).
- `tests/test_cli.py` spawns one CLI process per call and is the slowest gate (about 40 s); prefer checking business behavior through `test_api.py` or Core gates and keep `test_cli.py` for the CLI's own contract (arguments, JSON shape, exit codes, server selection).
- Service lifecycle checks inject `opener`, `runner`, `health_waiter` and `health_stopper`; update checks inject runtime installation and service actions. There is no shared clock or sleep override today. Keep new timing tests controlled at the owning module; do not patch process-wide `time.sleep`.

## Runner limits

There are no `stress` or `durable` tags: this standalone-script runner has no marker system or optional load/scale gates, and the application has no `fsync` calls to bypass. Keep new load variants outside the default gate list. Every script starts in a fresh interpreter, so cross-file module names cannot collide; there are no parallel workers, shared database fixtures or model caches to configure. Revisit those choices only when the suite gains those needs.
