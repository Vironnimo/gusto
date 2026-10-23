# Lifecycle

Lifecycle owns the per-user installation, service registration and control, verified updates with rollback, uninstall, delivery of the bundled skill, and the release assets these operations consume.

## Overview

Owners: `install.ps1`, `install.sh` and `install.py` bootstrap and orchestrate install/repair; `gusto/runtime.py` owns managed paths/state, Python and Gusto runtime preparation/retention, verified markers, settings and dependency probes; `gusto/service.py` owns autostart, service control and health verification; `gusto/update.py` owns release verification, update locking, activation, rollback and the retention keep set; `gusto/uninstall.py` owns removal; `gusto/skill_install.py` delivers the skill; `deploy/` holds compatibility registration adapters; `scripts/build_release.py`, `.github/workflows/release.yml`, `scripts/ci_smoke_install.py` and `scripts/ci_systemctl.py` build and check releases.

Not owned here: lifecycle command parsing, help, plain output, JSON error mapping, `serve` and the windowless `autostart_main` entry point (`gusto/_cli_local.py` and `gusto/cli.py`, `cli.md`); health response (`service.md`); process data-directory selection (`storage.md`); skill content (`cli.md`); reusable quality gates (`development.md`).

Lifecycle commands act on the local machine. They do not use the command API; `--server` does not redirect them. `runtime.managed_paths()` selects an explicit `--app-root`, otherwise the managed root containing the executing `sys.prefix` at `versions/<version>` when both state markers exist, otherwise the platform default. Service control and update share this selection. Uninstall has stricter discovery: it requires execution from the active installed runtime and refuses checkouts.

Touchpoints:
- storage: install and update write the runtime's `gusto.settings.json` with `data_dir` and local `server_url`; `GUSTO_HOME` still overrides the data path.
- service: activation verifies `GET /api/v1/health` against the expected version and data path.
- cli: stable wrappers select the active runtime on every invocation; that runtime's CLI reads its server URL from settings.
- development: release CI reuses `quality.yml`; `tests/test_ci.py` protects the dependency graph.

## Terms

Service and operational installation are shared terms in `.vorch/GLOSSARY.md`.

### Managed app root

The per-user application directory Gusto owns, recognized by `install-state.json` plus `current.json`. Not the data store, which the installer requires to be separate and non-nested; persisted details are in `lifecycle/install-repair.md`.

### Active runtime

The version venv under the managed root selected by `current.json`, used by the wrappers and registered service. Not every retained version: update keeps the previous verified runtime for rollback (`lifecycle/update-rollback.md`).

### Activation

The switch from a prepared runtime to the version served by the registered user service, completed by a version- and store-bound health check. Rollback activates the previous version (`lifecycle/update-rollback.md`).

### Repair

The installer's explicit mode for restoring an existing managed installation using its recorded configuration. Not update or reconfiguration; rebuilding a broken runtime requires a matching payload (`lifecycle/install-repair.md`).

## Data Model

Managed roots default to `%LOCALAPPDATA%\Programs\Gusto` or `~/.local/opt/gusto`. They contain `install-state.json` (configuration and Python runtime), `current.json` (active version), `versions/` (Gusto venvs), `python/` (app-private Windows Python), `bin/` (stable wrappers), and `.update.lock`.

State and active pointer are separate atomic JSON files, not one atomic transaction. State readers reject unsupported schemas, invalid hosts/ports and invalid Python records. The current pointer cannot select a runtime outside `versions/<version>`; reading it requires that directory, but does not itself verify its runtime marker. Install/update own runtime verification. Exact marker and cleanup semantics: `lifecycle/install-repair.md` and `lifecycle/update-rollback.md`.

The data store is outside the app root. Lifecycle code creates the initial directories, can seed an empty store from checkout data during install, and deletes the store only for explicit uninstall data deletion. Domain reads and writes remain Core's responsibility.

## Interfaces

- `service.service_action()` returns JSON-ready service state; `start` and `restart` finish with health verification. A dry-run executes no platform command and reports `running:false`.
- `runtime.prepare_source_runtime()` applies install/repair's live dependency probe; `prepare_release_runtime()` applies update's marker reuse and isolated import/version probe. Both prepare at the final version path before activation. `runtime.load_web_runtime()` performs complete foreground-server preflight lazily.
- `update.run_update()` reads the stored update source, prepares a verified side-by-side runtime and activates it under the per-app lock. `--check` and `--dry-run` do not mutate the installation. Update/rollback outcomes: `lifecycle/update-rollback.md`.
- `uninstall.discover_targets()` verifies ownership; `perform_uninstall()` removes integration and schedules deletion after the CLI exits. Scheduled is not completed; partial failure is not rollback (`lifecycle/uninstall.md`).
- `skill_install.install_skill()` copies the bundled payload to an explicitly selected supported host. It neither calls the service nor installs an agent host (`lifecycle/skill-delivery.md`).

`runtime.py` raises `RuntimePreparationError` for invalid state and preparation failure. Service, update and uninstall translate it at their public boundaries into `ServiceError`, `UpdateError` and `UninstallError`; skill delivery uses `ValueError`. The CLI wrappers catch those plus `OSError`; an unconverted foreign exception can escape the JSON error contract. Error/result text rationale: `lifecycle/agent-facing-text.md`.

## Conventions

- Installation is per user: no system service or machine PATH. Linux refuses root; Windows uses an interactive logon task with limited privileges. There is no before-login start and Linux does not enable lingering.
- Install, repair and update share the per-app lock and re-read state/ownership after acquiring it. Runtime venvs are built at their final path because their launchers embed absolute interpreter paths.
- Install, repair, update, rollback, start and restart bind health to `ok:true`, expected version and expected data path. A parsed, mismatching response fails immediately. Unreachable/non-200 responses and invalid JSON/UTF-8 responses are polled for up to 30 seconds.
- A Windows task name alone does not establish ownership. Registration, control and removal compare the fixed Gusto description before touching an existing task. Keep the check consistent in `service.py`, `uninstall.py` and the Windows deploy adapter.
- `service.py` passes paths, bind host and port into PowerShell through environment variables, not script interpolation. Test platform side effects through injected runners, health waiters, installers, removers and launchers.
- Lifecycle behavior changes also update `skill/gusto/references/installation.md` and `README.md`; `cli.md` owns that content.

## External Dependencies

GitHub Releases supplies the default update manifest and assets. PyPI resolves the unpinned `web` dependencies while installing the verified Gusto wheel; only release assets, not transitive packages, are hash-verified. Release provenance attestations are published but not checked by installer/update. Windows uses the pinned NuGet Python package, PowerShell ScheduledTasks and HKCU; Linux uses system Python 3.10+ and `systemctl --user`. See `lifecycle/release-pipeline.md` for release-source contracts.

## Constraints & Gotchas

- Integration names are fixed per user. A real install into a different root, including `.tmp`, can re-register the household service. Never run install, service control, update or uninstall for real on a developer machine; use injected hermetic checks or dry-runs. `ci_smoke_install.py` refuses outside GitHub Actions.
- Checkout/unmanaged CLI execution still chooses the platform-default installation unless `--app-root` is explicit. A custom root is discovered only from its managed version runtime, not from the working directory or `--server`.
- Service registration points directly to a version runtime, so activation and rollback re-register it. On Linux `enable --now` does not restart an already running unit; update and rollback explicitly stop first.
- Inherited `GUSTO_HOME` can override installed settings, produce a different health data path and fail activation.
- Real `systemd --user` is not exercised automatically: Linux release smoke uses the `ci_systemctl.py` adapter. Activation/rollback/repair/data deletion have hermetic coverage, not a real deployment check.

## Tests

`tests/test_service.py` covers state, root selection, registration, control and health; `tests/test_update.py` covers update, rollback, runtime and lock behavior plus installer repair/concurrency; `tests/test_packaging.py` covers release payloads, bootstraps, wheel/skill content, installer lifecycle and Windows launcher subsystem; `tests/test_uninstall.py`, `tests/test_skill_install.py` and `tests/test_autostart.py` cover their owners. Shared contracts: `tests/test_paths.py` with storage and `tests/test_ci.py` with development. The only real install check is `scripts/ci_smoke_install.py` in release CI.

## Agent-facing text

This domain owns result and error messages in `install.py`, the public bootstraps, `gusto/runtime.py` (including server dependency preflight), `gusto/service.py`, `gusto/update.py`, `gusto/uninstall.py` and `gusto/skill_install.py`. Before changing a sentence, read `lifecycle/agent-facing-text.md` and update its reason row with the text. `cli.md` owns CLI help, ordinary lifecycle output formatting and uninstall choice prompts.

## References

Read these only when the task matches.

- Changing install roots, wrappers, runtime/state markers, first-install cleanup or repair -> `lifecycle/install-repair.md`
- Changing update downloads, locking, activation, rollback or retained-runtime cleanup -> `lifecycle/update-rollback.md`
- Changing service registration/control, health waits or deploy adapters -> `lifecycle/user-service.md`
- Changing uninstall ownership, data deletion, integration removal or detached helpers -> `lifecycle/uninstall.md`
- Changing skill packaging or delivery to an agent host -> `lifecycle/skill-delivery.md`
- Changing release assets, manifests, public bootstraps or release CI -> `lifecycle/release-pipeline.md`
- Changing lifecycle result/error wording -> `lifecycle/agent-facing-text.md`
