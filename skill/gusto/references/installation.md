# Gusto application installation and lifecycle

The managed Gusto application carries the matching agent-skill payload inside
each versioned runtime. The app installer does not silently modify an agent
host. Installing or refreshing the separate vBot copy is the explicit local
`gusto install-skill vbot` workflow below.

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
$script = Join-Path $env:TEMP "gusto-install.ps1"
irm https://github.com/Vironnimo/gusto/releases/latest/download/install.ps1 -OutFile $script
try { & $script -Json } finally { Remove-Item -LiteralPath $script -Force }
```

```bash
curl -fsSL https://github.com/Vironnimo/gusto/releases/latest/download/install.sh |
  bash -s -- --json
```

Expect exactly one JSON object on stdout. Require `ok:true` and
`status:"installed"`; on Windows also require `python_runtime.kind:"managed"`.
Then run `gusto home --json` with the installed command before any domain
mutation.

The bootstrap downloads `gusto-release.json` plus the individually listed
assets from the latest public GitHub release. Every asset is SHA-256 checked.
Windows then safely extracts the pinned Python runtime, runs the verified
installer with it, stores that Python below Gusto's app root, and creates the
Gusto version in its own venv. Linux runs the verified installer and Wheel with
the existing system Python. There is no outer Gusto release ZIP.

A successful install:

1. creates a versioned per-user runtime and stable `gusto` wrapper;
2. includes the exact same version's Gusto agent skill in that runtime;
3. preserves application and data as separate directories;
4. registers per-user login autostart;
5. starts Gusto immediately;
6. waits for `/api/v1/health`;
7. returns the local URL.

Do not report success when registration, start, or health verification failed.
Open `http://localhost:8000` locally or `http://<host>:8000` from another LAN
device after installation.

## Install or refresh the vBot skill

This action is explicit and local. It does not need the Gusto service and does
not target the recipe data directory.

```bash
gusto install-skill vbot --dry-run --json
gusto install-skill vbot --json
```

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

Use only:

```bash
gusto update
```

Inspect without changing anything:

```bash
gusto update --check --json
```

The update downloads and verifies the published manifest and Wheel, installs
the new runtime in a per-app locked staging area, atomically publishes it
side-by-side, stops the service, switches the current-version pointer, restarts,
and checks both expected version and data path. Activation failure switches
back and health-checks the prior version; JSON distinguishes `update_failed`
with `rollback:true` from `rollback_failed`.

On Windows, an update also downloads a new pinned Python asset when the release
changes Python versions. Retained Gusto versions keep every Python runtime they
still reference, so rollback remains usable.

Never present re-running the installer as the update path.

An app update refreshes the skill payload in the newly active runtime. It does
not mutate an already installed vBot copy. Rerun `gusto install-skill vbot
--json` when the vBot skill should follow the updated Gusto version.

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
$script = Join-Path $env:TEMP "gusto-install.ps1"
irm https://github.com/Vironnimo/gusto/releases/latest/download/install.ps1 -OutFile $script
& $script -Repair
Remove-Item -LiteralPath $script
```

Linux:

```bash
curl -fsSL https://github.com/Vironnimo/gusto/releases/latest/download/install.sh |
  bash -s -- --repair
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

Use `--dry-run` first when exact targets matter. Never infer permission to
delete data from a request to uninstall the application.

App uninstall does not remove `~/.vbot/skills/gusto`, because that is a
separately installed agent-host copy. Remove it only through the agent host's
own lifecycle and only when explicitly requested.
