# Gusto application installation and lifecycle

This reference covers installation, service control, update, recovery,
uninstall, and installation of the bundled vBot skill on the local machine.
The managed app carries the matching agent-skill payload; the app installer
does not silently modify an agent host.

## Contents

- [Requirements and trust boundary](#requirements-and-trust-boundary)
- [One-shot first installation](#one-shot-first-installation)
- [Install or refresh the vBot skill](#install-or-refresh-the-vbot-skill)
- [Normal update](#normal-update)
- [Explicit repair](#explicit-repair)
- [Service control and diagnosis](#service-control-and-diagnosis)
- [Paths](#paths)
- [Uninstall](#uninstall)

## Requirements and trust boundary

- Windows PowerShell 5.1 or newer; no preinstalled Python is required.
- Linux with Python 3.10 or newer.
- Run as the normal target user. Never request administrator rights, `sudo`, a
  machine-wide PATH change, or a system service.
- Gusto binds to the LAN and intentionally has no authentication or tokens.
  This is the user's explicit trusted-home-LAN model. Never expose its port
  directly to the public internet.
- Windows starts it with a limited Scheduled Task at current-user logon. Linux
  starts it with `systemd --user`. Neither starts before login.
- Lifecycle commands act on this machine's installation; `--server` does not
  redirect them to a remote host. For a remote service, run them on its host.
- Service control and `update` use the managed installation containing the
  running CLI, including a custom app directory. From a checkout or unmanaged
  Python they use the platform-default app path; pass `--app-root PATH` to
  select another managed installation explicitly.
- Windows install, repair, update, service control, and uninstall refuse to
  take over or remove an unrelated Scheduled Task that is merely named
  `Gusto`; the user must resolve such a collision explicitly.

## One-shot first installation

Windows PowerShell:

```powershell
irm https://github.com/Vironnimo/gusto/releases/latest/download/install.ps1 | iex
```

Linux:

```bash
curl -fsSL https://github.com/Vironnimo/gusto/releases/latest/download/install.sh | bash
```

When an agent needs a machine-readable installation result, invoke the same
bootstrap with JSON enabled instead of parsing human/pip output:

```powershell
$gustoInstaller = Join-Path $env:TEMP ("gusto-install-" + [guid]::NewGuid() + ".ps1")
irm https://github.com/Vironnimo/gusto/releases/latest/download/install.ps1 -OutFile $gustoInstaller
try { & $gustoInstaller -Json } finally { Remove-Item -LiteralPath $gustoInstaller -Force }
```

```bash
curl -fsSL https://github.com/Vironnimo/gusto/releases/latest/download/install.sh |
  bash -s -- --json
```

Expect exactly one JSON object on stdout. Require `ok:true` and
`status:"installed"`; on Windows also require `python_runtime.kind:"managed"`.
For a new or unclear household target, use `gusto home` to identify the
service URL and server data directory before writing. If this shell still has the old PATH, use the default stable wrapper:
PowerShell `& "$env:LOCALAPPDATA\Programs\Gusto\bin\gusto.cmd" home --json`,
or Linux `~/.local/opt/gusto/bin/gusto home --json`. Custom installs use their
reported application directory's `bin` wrapper.

The bootstrap verifies every release asset's SHA-256 against
`gusto-release.json`. It installs the app and matching skill, registers login
autostart, starts the service, and verifies `/api/v1/health`. App and data
directories remain separate; Windows supplies its own pinned Python runtime.

Do not report success when registration, start, or health verification failed.
Give the user the returned local URL. From another LAN device, use the service
host's LAN address and its configured port; a loopback URL addresses that
device itself.

## Install or refresh the vBot skill

This action is explicit and local. It does not need the Gusto service and does
not target the recipe data directory.

```text
gusto install-skill vbot --json
```

Use `--dry-run` when the user asks to inspect the destination or replacement
scope before installation; it is not required for an already requested install.

The default vBot data directory `~/.vbot` must already exist. Gusto creates its
`skills/` child if needed and installs to `~/.vbot/skills/gusto`. When that
Gusto skill already exists, the command replaces its complete directory from a
staged copy; it does not merge files or retain stale content. JSON reports the
active Gusto version, source, destination, file list, and whether a previous
copy was overwritten.

Do not report the skill as installed based only on the app installation. Run
the explicit command and verify its `status`. If vBot is absent, report the
clear error rather than creating `~/.vbot` or guessing another host path.

## Normal update

Run the supported update command:

```text
gusto update
```

Inspect without changing anything:

```bash
gusto update --check
```

The update downloads and verifies the published manifest and Wheel, installs
the new runtime at its final, inactive version path under the per-app lock,
stops the service, atomically switches the current-version pointer, restarts,
and checks both expected version and data path. Activation failure switches
back and health-checks the prior version; JSON distinguishes `update_failed`
with `rollback:true` from `rollback_failed`.
A damaged downloaded Windows Python package fails with `status:"failed"`
before activation; the active version remains selected.

On Windows, an update also downloads a new pinned Python asset when the release
changes Python versions. Retained Gusto versions keep every Python runtime they
still reference, so rollback remains usable.
If a retained version's runtime marker is unreadable or incomplete, interpreter
cleanup is skipped and `removed_python_runtimes` is empty.

Never present re-running the installer as the update path. If an old Windows
installation repeatedly rolls back with a launcher fatal error, keep the healthy
version selected and report the update diagnostics; repeated attempts do not
repair a broken launcher. Recover through a verified release and explicitly
selected app root, not by moving virtual environments or editing their launchers.

An app update refreshes the skill payload in the newly active runtime. It does
not mutate an already installed vBot copy. Run `gusto install-skill vbot
--json` when the user requests refreshing that copy as well.

## Explicit repair

The bootstrap rejects an already managed installation by default. Use repair
only when the installed runtime or user-service registration is broken; it
reuses the installed version/state rather than acting as an update.
If the active runtime itself is damaged, the repair payload must match that
active version exactly. In that case pass
`https://github.com/Vironnimo/gusto/releases/download/v<active-version>` as
PowerShell `-ReleaseBase` or shell `--release-base`; repair deliberately
refuses to smuggle in a newer application.

Windows:

```powershell
$gustoInstaller = Join-Path $env:TEMP ("gusto-install-" + [guid]::NewGuid() + ".ps1")
irm https://github.com/Vironnimo/gusto/releases/latest/download/install.ps1 -OutFile $gustoInstaller
try { & $gustoInstaller -Repair -Json } finally { Remove-Item -LiteralPath $gustoInstaller -Force }
```

Linux:

```bash
curl -fsSL https://github.com/Vironnimo/gusto/releases/latest/download/install.sh |
  bash -s -- --repair --json
```

Use `-DryRun` / `--dry-run` to inspect targets first. Custom `InstallDir`,
`DataDir`, host or port are user-owned deployment choices; do not change them
during repair unless explicitly requested. A new custom app directory must be
empty, and app/data directories must be separate, non-nested trees.

## Service control and diagnosis

```bash
gusto status --json
gusto start --json
gusto stop --json
gusto restart --json
gusto home --json
```

`start` and `restart` wait for health. Normal business commands require the
server and fail clearly when it is unavailable; they never use a local
filesystem fallback. Use `gusto check --offline --json` only for an explicitly
requested local recovery diagnosis while the service is stopped. `gusto serve`
is the foreground recovery/development command.

## Paths

| Purpose | Windows | Linux |
|---|---|---|
| Managed app root | `%LOCALAPPDATA%\Programs\Gusto` | `~/.local/opt/gusto` |
| Python used for Gusto venvs | app root `\python\<version>` | existing system Python |
| Stable command | app root `\bin\gusto.cmd` | app root `/bin/gusto` plus `~/.local/bin/gusto` |
| User data | `%LOCALAPPDATA%\Gusto` | `$XDG_DATA_HOME/gusto` or `~/.local/share/gusto` |
| Autostart | Scheduled Task `Gusto` | `~/.config/systemd/user/gusto.service` |

The app root contains `versions/`, `current.json`, `install-state.json`, stable
wrappers, and on Windows the app-private Python runtime(s). Normal updates
retain the active and previous verified versions plus the Python runtimes they
need; user data is not part of a release asset.
The bundled skill lives below the active runtime's `share/gusto/skill/gusto`;
normal agents install it through `gusto install-skill`, not by copying this
internal path themselves.

## Uninstall

Only uninstall on an explicit user request:

```bash
gusto uninstall --keep-data --json
```

This removes the service registration, current-user app registration/PATH
integration, managed venvs, and app-private Windows Python while preserving
recipes, images, favorites, shopping state and cooking history. Permanent data
deletion is separate:

```bash
gusto uninstall --delete-data --yes --json
```

Use `--dry-run` when the target or removal scope needs inspection. Never infer
permission to delete data from a request to uninstall the application.

`status:"scheduled"` means background file cleanup was started, not confirmed
complete. Its `log_path` records the outcome; report a pending removal if the
helper has not finished. If a step fails after partial removal, JSON exits 1
with `status:"uninstall_failed"`, `failed_step`, and the completed
`*_removed` flags. A failing integration step's flag is null when its effect is
unknown; false means no confirmed removal (for example, already absent or not
attempted). Report those effects before deciding how to continue.

App uninstall does not remove `~/.vbot/skills/gusto`, because that is a
separately installed agent-host copy. Remove it only through the agent host's
own lifecycle and only when explicitly requested.
