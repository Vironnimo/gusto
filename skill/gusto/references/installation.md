# Gusto application installation and lifecycle

This reference installs and manages the Gusto **application only**. Agent hosts
deliver the surrounding skill separately; never copy, install, update, or
remove the skill as part of these commands.

## Requirements and trust boundary

- Windows or Linux with Python 3.10 or newer.
- Run as the normal target user. Never request administrator rights, `sudo`, a
  machine-wide PATH change, or a system service.
- Gusto binds to the LAN and intentionally has no authentication or tokens.
  This is the user's explicit trusted-home-LAN model. Never expose its port
  directly to the public internet.
- Windows starts it with a limited Scheduled Task at current-user logon. Linux
  starts it with `systemd --user`. Neither starts before login.

## One-shot first installation

Windows PowerShell:

```powershell
irm https://github.com/Vironnimo/gusto/releases/latest/download/install.ps1 | iex
```

Linux:

```bash
curl -fsSL https://github.com/Vironnimo/gusto/releases/latest/download/install.sh | bash
```

The bootstrap downloads `gusto-release.json`, the stable release ZIP and its
checksum from the latest public GitHub release. It verifies manifest agreement,
SHA-256 and safe ZIP paths before running the bundled installer.

A successful install:

1. creates a versioned per-user runtime and stable `gusto` wrapper;
2. preserves application and data as separate directories;
3. registers per-user login autostart;
4. starts Gusto immediately;
5. waits for `/api/v1/health`;
6. returns the local URL.

Do not report success when registration, start, or health verification failed.
Open `http://localhost:8000` locally or `http://<host>:8000` from another LAN
device after installation.

## Normal update

Use only:

```bash
gusto update
```

Inspect without changing anything:

```bash
gusto update --check --json
```

The update downloads and verifies the published manifest/archive, installs the
new runtime in a per-app locked staging area, atomically publishes it
side-by-side, stops the service, switches the current-version pointer, restarts,
and checks both expected version and data path. Activation failure switches
back and health-checks the prior version; JSON distinguishes `update_failed`
with `rollback:true` from `rollback_failed`.

Never present re-running the installer as the update path.

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
| Stable command | app root `\bin\gusto.cmd` | app root `/bin/gusto` plus `~/.local/bin/gusto` |
| User data | `%LOCALAPPDATA%\Gusto` | `$XDG_DATA_HOME/gusto` or `~/.local/share/gusto` |
| Autostart | Scheduled Task `Gusto` | `~/.config/systemd/user/gusto.service` |

The app root contains `versions/`, `current.json`, `install-state.json`, and
stable wrappers. Normal updates retain the active and previous verified
versions; user data is not part of a release archive.

## Uninstall

Only uninstall on an explicit user request:

```bash
gusto uninstall --keep-data --json
```

This removes the service registration, current-user app registration/PATH
integration and managed runtimes while preserving recipes, images, favorites,
shopping state and cooking history. Permanent data deletion is separate:

```bash
gusto uninstall --delete-data --yes --json
```

Use `--dry-run` first when exact targets matter. Never infer permission to
delete data from a request to uninstall the application.
