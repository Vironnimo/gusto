# Uninstall ownership and partial outcomes

Owner: `gusto/uninstall.py`; `cli.md` owns data-choice parsing/prompts. Uninstall is local and does not accept a caller-chosen app root.

## Ownership before removal

`discover_targets()` walks the executing runtime's ancestors for the managed state/current pair, requires that runtime to equal the active pointer, requires the package to live inside the runtime and checks `pyvenv.cfg` and the platform wrapper. A checkout or inactive retained version is refused. State errors are wrapped as `UninstallError` for the CLI's JSON contract.

`validate_removal()` rejects filesystem roots/home as app roots. Preserving data refuses an installation that contains its store; deleting a separate store rejects broad roots, the temp root, or a store containing the app, and requires recognizable `recipes/`, `images/`, `data/` directories when it exists. These conservative checks protect custom paths; they are not a substitute for installer target separation.

## Partial effects and detached deletion

Removal first unregisters autostart, then platform integration: user PATH and app registration on Windows, the exact owned command symlink on Linux. Windows task removal requires the same fixed Gusto description as registration/control. Then a helper outside the app waits for the invoking CLI to exit and removes the verified app and optional separate data tree.

Windows uses PowerShell with `CREATE_NO_WINDOW`: `DETACHED_PROCESS` causes PowerShell 5.1 to exit before running the helper. Linux uses an independent base interpreter and a new session. Helpers have closed stdin, redirected output and a log path outside the deleted app. Installed packages follow the caller's temp policy; checkout tests use `.tmp`.

`status:scheduled` means the helper was launched, not that deletion finished; `log_path` is the completion evidence. Dry-run validates the plan and returns `status:dry_run` without integration removal or helper launch. On partial failure, `status:uninstall_failed` and `failed_step` accompany tri-state removal flags: `true` confirmed removed, `false` not removed by that step, `null` the failing step may have partly executed. Earlier removals are not rolled back; the message names confirmed removals and tells the caller to inspect paths before retrying.

## Verification

`tests/test_uninstall.py` covers target ownership, inactive/checkout refusal, data safety, partial-effect flags, helper arguments and Windows no-window flags with injected removers/launcher. Release smoke installs then uninstalls with data kept; real data deletion is not automatically exercised.
