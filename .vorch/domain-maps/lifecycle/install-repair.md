# Installation and repair

Owners: `install.py`, `install.ps1`, `install.sh`, and the path/state/Python helpers in `gusto/service.py`. Public usage is in `skill/gusto/references/installation.md`; this file describes constraints an implementation change must preserve.

## Targets and state

Install selects app root from `--install-dir` (alias `--venv`) or the platform default. Data selection is `--data-dir`, then `GUSTO_HOME`, then `%LOCALAPPDATA%/Gusto` or `$XDG_DATA_HOME/gusto`. App/data roots must be separate and non-nested. A first-install root must be empty; a partial state/current marker pair is refused. The installer creates `recipes/`, `images/`, `archive/`, `data/` and seeds from checkout data only when the destination has no recognizable data (`migrate_checkout_data`). A standalone release has no checkout data to seed.

`install-state.json` uses schema 2 and stores data path, host, port, update `manifest_url` and Python runtime. The runtime is `managed` on public Windows installs (version, root, package SHA-256) or `system` on Linux (resolved executable path). State validation does not prove that the Python executable works; `state_base_python()` performs that check.

`current.json` selects a SemVer version under `versions/<version>` and can include `previous_version`. The latter is informational, not an exposed rollback command. `read_current()` validates the target path and, normally, directory existence; it does not validate `.gusto-runtime.json`.

The version marker `.gusto-runtime.json` records the Gusto version, `verified:true` and the base Python version. Install writes it only after an isolated target-interpreter import/version/web-dependency check; the updater's isolated check verifies import/version. `-I` prevents checkout/PYTHONPATH shadowing from making a broken runtime appear healthy. `python/<version>/.gusto-python-runtime.json` records the managed interpreter's expected version, hash and x86_64 architecture; its probe must agree.

Stable wrappers in `bin/` read `current.json` on every call. Linux links `~/.local/bin/gusto` to its wrapper and refuses a foreign link/file there. Windows adds the wrapper directory only to the user PATH. Venvs are built at their final path: moving one breaks embedded launcher paths.

## Install and repair outcomes

First install acquires the same `.update.lock` used by update, rechecks ownership, prepares the runtime and settings, publishes state/current, installs wrappers/integration and verifies health. A concurrent installer cannot have its root deleted by another caller's stale cleanup decision. Failed first install removes its app/integration best-effort and leaves the data store; failed repair preserves the existing managed root.

An existing managed root refuses normal installer reruns and names `gusto update` and explicit repair. Repair uses stored data path, host, port and manifest source; new configuration flags do not reconfigure it. A healthy runtime is kept while wrappers/settings/integration are restored. A broken runtime can be rebuilt only from a payload matching its active Gusto version. Managed Python repair also needs the installed version/hash; it cannot change Python kind. If the recorded Linux system interpreter vanished, repair cannot recreate it.

Before rebuilding, repair attempts service removal; if deleting the old runtime still fails it names `gusto stop` and refuses to install over the remainder. Repair success still needs the version/store-bound health check.

`install.py --json` emits one object: `ok:true`, `status:installed|repaired|dry_run`, paths, URL and Python details; successful real install/repair includes `data_migrated`. Failures emit `{ok:false,error}` and exit 1. Out-of-range numeric `--port` follows this JSON failure path; non-numeric parsing is still argparse. Bootstrap failures before `install.py` do not promise this JSON contract.

## Verification

`tests/test_paths.py`: root and target safety. `tests/test_packaging.py`: source/wheel installs, dry-run/rerun, isolated imports, repair rebuilding and first-install cleanup. `tests/test_update.py`: concurrent installer/update lock, ownership recheck, failed runtime removal, repair payload matching and wrapper quoting. These tests inject side effects; they do not register the developer's real service.
