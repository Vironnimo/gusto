# Lifecycle

The lifecycle domain installs Gusto per user, keeps its service running from login, updates it through verified side-by-side activation with rollback, removes it, delivers the bundled skill to agent hosts, and builds and publishes the release assets all of this consumes.

## Overview

Owned here: the public bootstraps `install.ps1` and `install.sh`, the installer `install.py`, the managed app root and its state files, user-service registration and control (`gusto/service.py`), `gusto update` (`gusto/update.py`), `gusto uninstall` (`gusto/uninstall.py`), `gusto install-skill` (`gusto/skill_install.py`), the compatibility adapters in `deploy/`, the release build `scripts/build_release.py`, and `.github/workflows/release.yml` with its install smoke test `scripts/ci_smoke_install.py` and `scripts/ci_systemctl.py`.

Not owned here: parsing, help texts and `--json` error mapping of the lifecycle commands, `gusto serve`, and the windowless `gusto-autostart` entry point (`autostart_main`) that the Windows task runs (all `gusto/cli.py`, `cli.md`); the health endpoint (`service.md`); how a process resolves its data directory from `GUSTO_HOME` and `gusto.settings.json` (`storage.md`); the skill's content (`cli.md`); the reusable gates in `quality.yml` (`development.md`).

Lifecycle commands act on the local machine's installation. They never pass through the command API, and `--server` does not redirect them.

Touchpoints:
- storage: the installer and updater write each version's `gusto.settings.json` (`data_dir` plus the local `server_url`); that runtime's service and CLI read it. `GUSTO_HOME` still takes precedence.
- service: every activation reads `GET /api/v1/health` and binds `ok`, `version` and `data_path`.
- cli: the stable wrapper runs the active runtime's `gusto`; installed CLIs default to the `server_url` the installer wrote.
- development: `release.yml` reuses `quality.yml`; `tests/test_ci.py` locks both workflows.

## Terms

The service itself is a shared term (`.vorch/GLOSSARY.md`).

### Managed app root

**Definition:** The per-user application directory that Gusto created and owns, recognized only by the pair `install-state.json` plus `current.json`. Not the data store, which always lives in a separate, non-nested directory.

### Active runtime

**Definition:** The version venv under `versions/<semver>/` that `current.json` names; the wrappers and the registered service run only this one. Not every installed version: an update keeps the previous verified version beside it for rollback.

### Activation

**Definition:** The short switch that makes a prepared runtime active: stop the service, write `current.json`, re-register the service, and pass the version- and store-bound health check. Rollback is the same switch back.

### Ready installation

**Definition:** A managed app root whose active runtime has a stable `gusto` command, a registered user autostart, a started service and a passed health check. Not a copied runtime and not an installed agent skill.

### Repair

**Definition:** The installer's explicit `--repair`/`-Repair` mode for an existing managed app root: re-register everything and, only when the active runtime is broken, rebuild it from a payload of the same version. Never an update and never a reconfiguration.

## Data Model

Managed app root, default `%LOCALAPPDATA%\Programs\Gusto` or `~/.local/opt/gusto`:
- `install-state.json` (schema 2): data directory, bind host and port, `manifest_url` (the persisted update source), and the Python runtime: `managed` (Windows: version, path and package SHA-256) or `system` (Linux: the resolved interpreter path). Readers reject other schemas, invalid hosts/ports and invalid runtime records.
- `current.json`: the active version; its runtime path must equal `versions/<version>`. `previous_version` is informational; nothing reads it and no rollback command exists.
- `versions/<semver>/`: one venv per Gusto version, each with its own `gusto.settings.json` and the marker `.gusto-runtime.json`, written only after an isolated import check passed. Only SemVer-named directories with a valid marker count as versions.
- `python/<x.y.z>/` (Windows only): the app-private CPython with marker `.gusto-python-runtime.json`.
- `bin/`: stable wrappers that resolve `current.json` on every call; Linux also links `~/.local/bin/gusto` to its wrapper.
- `.update.lock`: the per-app lock.

Per-user integrations outside the root have fixed names: the Scheduled Task `Gusto` with the description `Gusto Rezeptserver beim Anmelden starten`, the HKCU key `Software\Microsoft\Windows\CurrentVersion\Uninstall\Gusto`, the Start Menu `Gusto.url`, the user `PATH` entry for `bin/` (Windows); `$XDG_CONFIG_HOME/systemd/user/gusto.service` (Linux).

The data store (default `%LOCALAPPDATA%\Gusto` or `$XDG_DATA_HOME/gusto`; at install time `--data-dir`, else `GUSTO_HOME`) is never read or written by lifecycle code except: the installer creates `recipes/`, `images/`, `archive/` and `data/`, a checkout-based install seeds an empty store with the checkout's own data, and `uninstall --delete-data` deletes it.

Release manifest `gusto-release.json` (schema 2): `version`, `minimum_system_python`, and assets `wheel`, `installer`, `windows_bootstrap`, `linux_bootstrap`, `windows_python_x64` (plus `version` and `layout: "tools"`), each a bare file `name` plus `sha256`. `gusto update` requires `wheel`, `installer` and `windows_python_x64` on every platform.

## Interfaces

Results the CLI prints under `--json` (exit 1 on failure):
- `install.py --json` prints exactly one object: `ok:true` with `status` `installed`, `repaired` or `dry_run`, paths, URL, Python runtime and `data_migrated`; or `{ok:false, error}`. An invalid `--port` is such a JSON failure, not a parser exit 2.
- `status|start|stop|restart` return `action`, `status`, `running`, `version`, `url` and, in dry-run, the `command`; a dry-run never claims `running:true`.
- `gusto update` returns `current`, `update_available`, `dry_run` or `updated`. Failures carry a `status`: `update_locked`, `failed` (nothing switched), `update_failed` with `rollback:true` (previous version restored and healthy), or `rollback_failed` with `rollback:false`.
- `gusto uninstall` returns `dry_run` or `scheduled` with `log_path`; a partial failure returns `status:uninstall_failed`, `failed_step` and tri-state `*_removed` flags (true removed, null unknown, false not removed). Failure is never rollback.
- `gusto install-skill vbot` returns `installed` or `dry_run` with `source`, `destination`, `overwritten` and `files`.

Each lifecycle module raises its own error type (`ServiceError`, `UpdateError`, `UninstallError`, `ValueError` for skills) and converts errors of the modules it calls into it, because the CLI maps only those types (plus `OSError`) onto the JSON contract; an escaping foreign error prints a traceback.

Skill delivery: the source is `skill/gusto/` in a checkout, else the active runtime's `share/gusto/skill/gusto` (wheel data files). The host registry has one host, `vbot`; `~/.vbot` must already exist. The command copies into a staging folder under `~/.vbot/skills/`, swaps it for the old `gusto` folder (full replace, no merge) and restores the backup if publishing fails. It never contacts the service, and neither install nor update touches an agent host.

## Conventions

- Per user only: no admin or root, no system service, no machine `PATH`. The Linux installer refuses root; the Windows task runs at the user's logon with `RunLevel Limited`. Neither platform starts Gusto before login, and Linux does not enable lingering.
- Health is bound: install, repair, update, rollback, `start` and `restart` require `ok:true`, the expected version and the expected data path. Only an unreachable or non-200 endpoint is polled (30 s); a reachable mismatch fails at once. Never relax this to "some Gusto answers".
- One per-app lock serializes first install, repair and update; each re-reads state and ownership after acquiring it.
- A Windows task name alone never proves ownership: every path that stops, replaces, starts or removes `Gusto` first compares the fixed description. Keep that check identical in `service.py` (registration, removal, control), `uninstall.py` and `deploy/install-windows-task.ps1`.
- Paths, host and port reach PowerShell through environment variables, never interpolated into script text.
- Platform side effects sit behind injectable seams (`runner`, health waiters, `downloader`, runtime/service installers, removers, `launcher`). Tests use them; only `scripts/ci_smoke_install.py` installs for real, and it refuses to run outside GitHub Actions.
- Behavior changes update `skill/gusto/references/installation.md` and `README.md` in the same change (`cli.md` owns the skill).

## External Dependencies

- GitHub Releases: `releases/latest/download/` is the default release base and update source. The base a first install used is persisted as `manifest_url` and stays the update source; the hidden `gusto update --manifest-url` overrides one call without persisting.
- PyPI: every runtime build installs `gusto[web]` from the verified wheel, but pip resolves the unpinned web dependencies from the package index at install, repair and update time. Only Gusto's wheel and the Windows Python package are hash-verified; published build attestations are not checked by any installer or updater.
- NuGet: the pinned `python` package supplies the Windows runtime (`scripts/build_release.py`).
- `powershell.exe` with the ScheduledTasks cmdlets and `winreg` on Windows; `systemctl --user` and Python 3.10+ on Linux. Other platforms fail at service registration and uninstall.

## Constraints & Gotchas

- The task name, HKCU key, Start Menu entry, unit name and `~/.local/bin/gusto` are fixed per user. A real install into any other app root, even under `.tmp`, re-registers the household's autostart to that root. Never run the bootstraps, `install.py`, service control, update or uninstall for real on a development machine; use dry-runs and the hermetic tests.
- `status|start|stop|restart|update` use `--app-root` or the platform-default app root, not the root of the running wrapper; uninstall instead discovers the root from the running runtime. Custom `--install-dir` installs need `--app-root`.
- Venvs are not relocatable (launchers embed absolute interpreter paths): build each version at its final `versions/<semver>` path and never rename or copy one.
- The registered service points at the active version (unit `ExecStart`, task action), so every activation and rollback re-registers it. On Linux `enable --now` does not restart an active unit, so update and rollback stop the service first.
- Windows activation waits up to 15 s for the old health endpoint to stop answering before it starts the task; any other server answering health on that port blocks it.
- A `GUSTO_HOME` in the service's environment overrides the runtime settings, makes health report another data path, and fails activation.
- Repair uses the stored state: `--data-dir`, `--host`, `--port` and `--manifest-url` are ignored. It cannot switch the Python kind; on Linux a vanished recorded system interpreter cannot be repaired.
- Update never downgrades: it activates only a SemVer-newer manifest version. It refuses a release that ships the installed Windows Python version with a different hash.
- Real `systemd --user` is exercised by no automated check: the release smoke test runs the generated unit through `scripts/ci_systemctl.py`. Real activation, rollback, repair and `--delete-data` are covered only hermetically.
- The skill payload is listed file by file in `pyproject.toml` data files. A new skill file that is not listed ships in checkouts but not in the wheel; also update `REQUIRED_SKILL_FILES` when it is mandatory, and the wheel list in `tests/test_packaging.py`.

## Tests

- `tests/test_service.py`: state and pointer validation, unit and task rendering, service control, health binding.
- `tests/test_update.py`: update orchestration, rollback order, Python switch, the update lock (also across processes), installer lock and repair hardening, download retries, wrapper quoting, installer `--port` JSON.
- `tests/test_packaging.py`: release assets and manifest, bootstrap rules, wheel and skill contents, skill install from the built wheel, installer dry-run and rerun refusal, failed-first-install cleanup, repair rebuild, Windows launcher subsystems.
- `tests/test_uninstall.py`, `tests/test_skill_install.py`, `tests/test_autostart.py` (deploy adapters, Windows registration).
- Shared: `tests/test_paths.py` with storage (default app and data roots, unit path, install-target validation); `tests/test_ci.py` with development (release job graph, permissions, pinned actions, smoke adapter).
- `scripts/ci_smoke_install.py` is the only real installation, run by `release.yml`.

## Agent-facing text

This domain owns the result and error messages that `install.py`, the bootstraps, `gusto/service.py`, `gusto/update.py`, `gusto/uninstall.py` and `gusto/skill_install.py` return to an agent; help texts and the uninstall choice prompts belong to `cli.md`. Open: the reason table `lifecycle/agent-facing-text.md` is not written yet. Until it exists, treat messages that tests assert (for example the rerun refusal naming `gusto update`, the repair hint naming `gusto stop`, `zwischenzeitlich`, `laeuft bereits`, `wiederhergestellt`, the foreign-task refusals, the partial-uninstall message that claims no rollback) as contract and change them only together with their tests.

## References

Open: the planned supplementary files (install and repair, update and rollback, user service and `deploy/` adapters, uninstall, release pipeline, agent-facing text) are not written yet. Until then, read the source modules named in the Overview and their tests.
