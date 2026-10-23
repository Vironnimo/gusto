# User service and deploy adapters

Owner: `gusto/service.py`. `gusto/cli.py::autostart_main` owns the windowless Windows launcher and its log in the data root (`cli.md`); the HTTP health response belongs to `service.md`.

## Integration and control

Windows registers the fixed Scheduled Task `Gusto`, description `Gusto Rezeptserver beim Anmelden starten`, with an interactive current-user logon trigger, `RunLevel Limited`, failure restart and no execution time limit. The task launches the active runtime's `Scripts/gusto-autostart.exe`. Registration also writes HKCU `Software/Microsoft/Windows/CurrentVersion/Uninstall/Gusto` and Start Menu `Gusto.url`. Paths/host/port enter the registration script through environment variables.

Existing Windows tasks are compared by description before stop/replacement/control/removal. A same-name foreign task is refused. Registration and restart wait for the old health endpoint to stop answering HTTP 200 before starting the selected task; the wait is 15 seconds and any other HTTP-200 health server on that port blocks it.

Linux writes `$XDG_CONFIG_HOME/systemd/user/gusto.service` (default `~/.config/systemd/user/`) with active-runtime Python, data working directory, `Restart=on-failure` and `WantedBy=default.target`. Unit quoting escapes backslashes, quotes and systemd percent specifiers, and rejects NUL/newline. It calls only `systemctl --user`; no root or lingering setup. Service registration uses daemon-reload then enable --now. Update/rollback stop the old unit first.

`status|start|stop|restart` resolve their local root through `runtime.managed_paths()` (root map). A malformed recognized executing installation fails when its state is read; it does not fall back to a different default installation. Runtime state errors are converted to `ServiceError`. Linux status accepts systemctl exit 0/3/4 as running/stopped/absent states; other errors fail. Status observes task/unit state, not health. Start/restart verify version/store health; stop does not perform a subsequent start. Dry-run returns the command and `running:false` without executing it.

Lifecycle health URLs map wildcard bind hosts to loopback (`0.0.0.0` -> `127.0.0.1`, `::` -> `[::1]`). The local URL can therefore differ from the browser's LAN URL. A parsed wrong version, wrong store or `ok:false` response fails immediately; transport/HTTP/JSON-decoding failures are polled within the configured deadline.

## Compatibility adapters

`deploy/install-windows-task.ps1` registers the same named/owned task from explicit app-root state. It stops an old running owned task but does not perform the full Python lifecycle health-down/health-up verification; it is not a complete operational installer. `deploy/install-systemd.sh` and `deploy/gusto.service` are the Linux compatibility path. The public installer remains the normal installation entry point.

## Verification limits

`tests/test_service.py` checks state, root selection, generated unit/task, command execution through injected runners and health semantics. `tests/test_autostart.py` covers launch/deploy integration. Linux release smoke substitutes `scripts/ci_systemctl.py` for real systemd; a green smoke run does not prove behavior on a live user-session systemd manager. Never probe a developer machine's fixed task/unit name as a test.
