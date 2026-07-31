# Surfaces

The surfaces domain exposes one running Gusto Core to CLI and browser clients
and owns managed per-user installation, service, update, and removal lifecycle.

## Overview

`gusto/api.py` is the versioned anonymous LAN transport over `gusto/core.py`.
`gusto/client.py` and normal commands in `gusto/cli.py` are stdlib HTTP clients;
`gusto/web.py`, templates, and static assets provide the server-rendered UI and
offline shopping PWA from the same FastAPI process. Domain rules remain in Core.

Local CLI code is restricted to lifecycle/recovery/host delivery: `home`,
foreground `serve`, `install-skill`, `check --offline`,
`status|start|stop|restart`, `update`, and `uninstall`.
Normal catalog, log, favorites, image, archive, and shopping commands never
silently fall back to local Core or files.

## Terms

Cross-cutting runtime terms live in GLOSSARY → Gusto-Dienst, Betriebsbereite
Installation, and Deployment-Ziel.

### Thin client

**Definition:** A surface that converts transport input, calls the shared
service/Core contract, and formats results without reimplementing domain rules.

### Change revision

**Definition:** The monotonic server-owned number persisted with a bounded list
of affected resources. Clients replay later events after reconnect.

## Interfaces

The installed command is `gusto` (`gusto.cli:main`); `python -m gusto` is the
explicit development entry point. Every command accepts `--json`. Expected
failures are `{ "ok": false, "error": "..." }` on stdout with exit 1; parser
errors remain stderr/exit 2. `check --json` deliberately returns its complete
diagnostics object with `ok:false` and exit 1 on hard errors.

Global `--server` overrides `GUSTO_URL`, which overrides `server_url` in the
runtime settings; default is `http://127.0.0.1:8000`. `gusto home --json`
remains available offline and reports local resolution plus selected server,
reachability, and reachable-server data path/version/revision. Agents verify it
before mutation and reuse the same executable/server selection.

The API router under `/api/v1` exposes:

- `GET /health`: anonymous readiness, version, server data path, revision, and
  last affected resources.
- `POST /command`: strict `{operation, arguments, attachments}` envelope and
  `{ok:true,result}` / `{ok:false,error}` response. The operation registry
  covers every normal CLI domain command but rejects lifecycle commands.
- `GET /events?after=N`: SSE replay from the persisted revision journal,
  followed by live changes and heartbeat comments.

Attachments are named Base64 payloads materialized in private temporary files,
limited to 25 MB each and always cleaned. This supports CLI image imports
without making server filesystem paths part of the API.

`gusto edit` downloads a recipe into a temporary Markdown file and uploads the
final content after the editor exits. Headless callers use `gusto content set
<slug> --file|--stdin`. Both reach `recipe.content.set` and therefore preserve
server ownership and catalog locking.

The FastAPI application is `gusto.web:app` and includes the API router. It
serves active/archive catalog, log, suggestions, shopping, preferred products,
media, static assets, root-scoped service worker, and custom 404. Active entity
URLs use `/recipe/{slug}`; archived snapshots use `/archive/{slug}`.

The page shell opens `/api/v1/events` after initial load. Catalog/favorites/log
changes reload relevant ordinary pages; shopping changes trigger the existing
shopping sync/merge; other pages receive `gusto:change`. A body-stamped revision
enables replay between render and SSE connection.

The shopping PWA remains offline-first. Its service worker caches the app shell,
uses the shopping page as navigation fallback, and leaves every `/api/` request
network-only. `shopping-client.js` retains optimistic local mutations,
full-state merge, tombstones, and queued resync. Live events supplement this
contract and never replace it.

Browser photo forms keep separate outward-facing camera and image-library
controls. Pillow normalizes browser uploads to metadata-free WebP with a
1920-pixel maximum edge before Core; CLI uploads remain in validated original
formats.

## Packaging & Runtime

- `pyproject.toml` publishes the `gusto` console script and windowless
  `gusto-autostart` GUI launcher. Templates, CSS/JS, manifest, and PWA icons are
  package data. The canonical `skill/gusto` files install as Wheel data below
  `share/gusto/skill/gusto`. The server dependency set is installed by every
  managed app.
- `scripts/build_release.py` builds `gusto-release.zip`,
  `gusto-release.zip.sha256`, and `gusto-release.json`. The archive carries the
  wheel and bootstrap/lifecycle payload; it contains no user data.
- `.github/workflows/quality.yml` is reusable by normal PR/main CI and tagged
  releases. It runs all standalone script contracts on Windows/Linux at the
  supported minimum and current Python, plus real Chromium/PWA suites on both
  operating systems. `scripts/run_quality.py` is the shared local/CI entry
  point.
- `.github/workflows/release.yml` waits for that complete workflow, builds the
  release once, and passes the immutable artifact to Windows/Linux install
  smoke jobs. `scripts/ci_smoke_install.py` verifies bootstrap, user service,
  health, UI/PWA shell, remote CLI, bundled vBot skill install/overwrite,
  restart, update check, and uninstall before the final job attests and
  publishes the same bytes. External actions are
  commit-pinned with weekly Dependabot updates; only the publish job has
  write/OIDC/attestation permissions.
- Public root `install.ps1` and `install.sh` download the latest manifest,
  archive, and checksum, verify SHA-256 and safe ZIP extraction, then run the
  bundled `install.py`. They install the version-matched skill payload with the
  app but never mutate an agent host; `gusto install-skill vbot` explicitly
  stages and replaces `~/.vbot/skills/gusto` without contacting the service.
- Managed app roots are `%LOCALAPPDATA%\Programs\Gusto` and
  `~/.local/opt/gusto`. They contain immutable `versions/<semver>` runtimes,
  `current.json`, `install-state.json`, and stable `bin/gusto` wrappers. Data
  remains under `%LOCALAPPDATA%\Gusto` or XDG user data.
- First install is per-user and no-admin. Windows registers a limited
  current-user logon Scheduled Task, current-user Installed Apps entry, Start
  Menu URL, and exact user PATH entry. Linux writes and enables a
  `systemd --user` unit. Both start immediately and must pass `/api/v1/health`;
  neither runs before login. Every Windows lifecycle path verifies the fixed
  Gusto task description before it stops, replaces, starts, or removes an
  existing task named `Gusto`; a foreign name collision is an error.
- A normal installer rerun is rejected. `--repair` re-registers/restarts an
  existing managed state and may reconstruct a damaged runtime only from a
  payload of the same active version. `gusto update` is the normal update path:
  acquire the per-app lock, download and verify, publish from staging
  side-by-side, stop, switch current pointer, reinstall the user service, and
  health-check the expected version and data path. Activation failure rolls
  back the pointer and re-verifies the previous version.
- `gusto uninstall` removes only a verified managed installation. Default/
  `--keep-data` removes service and app integrations while retaining the store;
  `--delete-data --yes` is the separate destructive path. Windows uses a
  detached helper for self-removal after the active executable exits. A
  separately installed vBot skill remains owned by the agent host.
- `gusto serve` remains foreground recovery/development startup. It validates
  the full server runtime before emitting its JSON startup object.

## Conventions

- Add domain behavior in Core, register it in the API, expose it through CLI,
  then add web presentation. Keep `CLAUDE.md` and `skill/gusto/` synchronized.
- Keep the anonymous/no-token model explicit. It is accepted for the trusted
  home LAN, not permission to expose Gusto to the public internet.
- Preserve German product copy and English code identifiers.
- Starlette template calls pass request first.
- Browser and PWA checks always use throwaway `GUSTO_HOME` data.
- Update the service-worker cache version whenever a cached asset/offline shell
  changes.

## Constraints & Gotchas

- `gusto home` is local diagnosis plus remote health. The local `path` and
  remote `server_data_path` can differ when `--server`/`GUSTO_URL` targets
  another machine; never confuse them.
- API command validation is strict. Adding a CLI domain operation requires the
  matching API registry entry, argument validation, result semantics, tests,
  and resource-change classification.
- Idempotent Core operations must not advance the change revision. Persist the
  journal atomically with monotonic revisions. Bounded-history compaction must
  retain the union of dropped resources so an old client refreshes safely.
- EventSource has no token/header setup by design. Do not add authentication
  workarounds that break the settled LAN contract.
- A browser reload on change must not destroy offline shopping mutations;
  shopping uses its merge path instead.
- Release installation/update trusts neither ZIP paths nor a downloaded
  archive until manifest/checksum/hash checks pass. Keep previous verified
  runtime available until the new one passes health.
- Never let the release workflow rebuild per operating system or publish an
  untested copy. Both smoke jobs and the attestation/publish job must consume
  the single artifact emitted by the gated build job.
- A first install may claim only an absent/empty app root; state/current
  mismatches and foreign non-empty roots are rejected. App and data trees may
  never be equal or nested because app-only uninstall must preserve data.
- A Windows task-name match alone does not prove ownership. Keep the
  description check aligned across managed registration, service control,
  update/repair, uninstall, and the compatibility adapter.
- Run `tests/test_api.py` for API/journal changes, `tests/test_cli.py` for CLI,
  `tests/test_agent_contract.py` and `tests/test_skill_install.py` for the
  fresh-agent and host-delivery contracts,
  `tests/test_service.py` and `tests/test_update.py` for lifecycle,
  `tests/test_packaging.py` for assets, `scripts/browser_check.py` for visible
  live behavior, and `scripts/pwa_check.py` for offline/live shopping behavior.
