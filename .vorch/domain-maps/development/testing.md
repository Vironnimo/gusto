# Testing guide

How to write and change Gusto's quality gates. The rules on what to test are in `AGENTS.md` -> Testing; this file covers how.

## Layout

- One gate per owner: `tests/test_<owner>.py` (`test_recipes.py` for the catalog, `test_shopping.py`, `test_favorites.py`, `test_merge.py` for shopping, `test_api.py` for the service, `test_cli.py` and `test_agent_contract.py` for the CLI and skill, `test_web_render.py` for templates, and one file per lifecycle module). Extend the owner's gate; a new file must be added to `SCRIPT_CHECKS` in `scripts/run_quality.py`.
- A gate is a script: a module-level `check(condition, message)` helper counts passed checks and raises on the first failure; the script ends with `print(f"OK - {checks} ... checks passed")`. Keep that last line; the survey and CI logs read it.
- Real-browser behavior goes into `scripts/browser_check.py` (web UI, no-JS fallback) or `scripts/pwa_check.py` (offline shell, sync, two-device merge).

## Isolation

- Import `gusto` first (with `# noqa: E402, F401`): it points Python's temp directory and the `TMPDIR`, `TEMP`, `TMP` of child processes at the checkout's `.tmp/`.
- Then allocate the gate's store with `tempfile.mkdtemp(prefix="gusto-<owner>-test-")` and set `GUSTO_HOME` to it before importing Core or starting a server. Subprocesses and servers get the same `GUSTO_HOME` through their environment.
- A gate that starts a server picks a free port, waits for `/api/v1/health`, checks that its `data_path` is the gate's store, and stops the server in a `finally` block.
- What is not isolated: the user's home directory, `LOCALAPPDATA` and the Playwright browser cache. Lifecycle gates that exercise install, update or uninstall pass explicit roots and environments instead of the real ones; never call them with defaults.

## Assertions

- Exact strings only for stable contracts: JSON keys and values the CLI or API promises, persisted file formats, exit codes, `outcome_unknown`, the Markdown H1 rule. German UI and error prose is editable; assert structured fields, status codes or the presence of an identifier instead of whole sentences.
- CLI behavior is checked through the JSON result and exit code, the way an agent sees it.

## Timing

- Never stabilize a flaky check with a longer wait. Synchronize on the condition instead: poll the observable state with a short interval and an overall deadline (as `wait_up` in the browser checks), or use file barriers between processes (as `tests/test_update.py` does for competing lock attempts).
- `tests/test_cli.py` spawns one CLI process per call and is the slowest gate (about 40 s); prefer checking business behavior through `test_api.py` or Core gates and keep `test_cli.py` for the CLI's own contract (arguments, JSON shape, exit codes, server selection).
