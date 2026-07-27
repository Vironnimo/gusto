"""Hermetic managed service lifecycle checks."""
from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, os.fspath(ROOT))

from gusto import service  # noqa: E402

checks = 0


def check(value, message):
    global checks
    assert value, message
    checks += 1


with tempfile.TemporaryDirectory(prefix="gusto-service-") as temporary:
    root = Path(temporary)
    paths = service.managed_paths(root / "Gusto")
    runtime = paths.version("1.2.3")
    (runtime / "bin").mkdir(parents=True)
    (runtime / "bin" / "python").write_bytes(b"python")
    (runtime / "Scripts").mkdir()
    (runtime / "Scripts" / "gusto-autostart.exe").write_bytes(b"launcher")
    data = root / "data"
    data.mkdir()
    state = {
        "data_dir": os.fspath(data), "host": "0.0.0.0", "port": 8123,
        "manifest_url": "fixture",
    }
    service.write_state(paths, state)
    service.write_current(paths, "1.2.3")
    check(service.read_current(paths)["version"] == "1.2.3",
          "current pointer must resolve a version inside versions/")
    check(service.read_state(paths)["data_dir"] == os.fspath(data),
          "install state must round-trip")
    check(service.server_url(state) == "http://127.0.0.1:8123",
          "wildcard listen host must become a local client URL")

    commands = []
    runner_calls = []

    def runner(command, **kwargs):
        commands.append(command)
        runner_calls.append((command, kwargs))
        stdout = "active\n" if "is-active" in command else ""
        return subprocess.CompletedProcess(command, 0, stdout=stdout, stderr="")

    unit = root / ".config" / "systemd" / "user" / "gusto.service"
    installed = service.install_user_service(
        paths, state, platform_name="linux", runner=runner, unit_path=unit,
    )
    text = unit.read_text(encoding="utf-8")
    check(installed["integration"] == "systemd_user",
          "Linux must use a user service")
    check(all("--user" in command for command in commands),
          "every systemctl call must be scoped to the current user")
    check("User=" not in text and "sudo" not in text
          and "WantedBy=default.target" in text,
          "unit must never depend on root or a system account")
    check("versions" in text and "1.2.3" in text and "python" in text,
          "unit must point at the selected side-by-side runtime")

    commands.clear()
    status = service.service_action(
        "status", app_root=paths.app_root, platform_name="linux", runner=runner,
    )
    check(status["running"] is True and status["version"] == "1.2.3",
          "status must be JSON-ready and version-aware")
    health_calls = []

    def healthy(url, **expectations):
        health_calls.append((url, expectations))
        return {"ok": True}

    started = service.service_action(
        "start", app_root=paths.app_root, platform_name="linux", runner=runner,
        health_waiter=healthy,
    )
    check(started["running"] is True
          and health_calls[0][0] == "http://127.0.0.1:8123/api/v1/health"
          and health_calls[0][1]["expected_version"] == "1.2.3"
          and Path(health_calls[0][1]["expected_data_path"]) == data,
          "start must verify the configured version and data store")

    windows = service.install_user_service(
        paths, state, platform_name="win32", dry_run=True, runner=runner,
    )
    script = windows["actions"][0][4]
    check("New-ScheduledTaskTrigger -AtLogOn" in script
          and "-RunLevel Limited" in script
          and "-RestartCount 5" in script,
          "Windows must use Current-User AtLogOn with failure restart")
    check("Highest" not in script and "Administrator" not in script,
          "Windows task must not request elevation")
    with patch.object(service, "register_windows_app", return_value={}):
        service.install_user_service(
            paths, state, platform_name="win32", runner=runner,
        )
    windows_env = next(
        kwargs["env"] for _, kwargs in reversed(runner_calls)
        if (kwargs.get("env") or {}).get("GUSTO_SERVICE_LAUNCHER")
    )
    check(windows_env["GUSTO_SERVICE_LAUNCHER"].endswith("gusto-autostart.exe")
          and windows_env["GUSTO_SERVICE_HOST"] == "0.0.0.0"
          and windows_env["GUSTO_SERVICE_PORT"] == "8123",
          "Windows task registration must pass paths/host/port without "
          "PowerShell command-string interpolation")

    class Response:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *args): return None
        def read(self):
            return json.dumps({
                "ok": True, "version": "1.2.3",
                "data_path": os.fspath(data),
            }).encode("utf-8")

    health = service.wait_for_health(
        "http://fixture/api/v1/health", timeout=0.01,
        expected_version="1.2.3", expected_data_path=data,
        opener=lambda *args, **kwargs: Response(),
    )
    check(health["ok"] is True,
          "health wait must validate version and data path")
    try:
        service.wait_for_health(
            "http://fixture/api/v1/health", timeout=0,
            expected_version="9.9.9", expected_data_path=data,
            opener=lambda *args, **kwargs: Response(),
        )
    except service.ServiceError as error:
        check("Version" in str(error),
              "a stale server version must never satisfy activation health")
    else:
        raise AssertionError("wrong health version accepted")
    try:
        service.wait_for_health(
            "http://fixture/api/v1/health", timeout=0,
            expected_version="1.2.3", expected_data_path=root / "other-data",
            opener=lambda *args, **kwargs: Response(),
        )
    except service.ServiceError as error:
        check("Datenpfad" in str(error),
              "a server using another store must not satisfy activation health")
    else:
        raise AssertionError("wrong health data path accepted")

    stop_sequence = []

    def windows_runner(command, **kwargs):
        script_text = command[-1]
        stop_sequence.append(
            "start" if script_text.startswith("Start-ScheduledTask") else "stop"
        )
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    def stopped(url):
        stop_sequence.append("down")

    def restarted(url, **expectations):
        stop_sequence.append(("healthy", expectations))
        return {"ok": True}

    service.service_action(
        "restart", app_root=paths.app_root, platform_name="win32",
        runner=windows_runner, health_stopper=stopped,
        health_waiter=restarted,
    )
    check(
        stop_sequence[0:3] == ["stop", "down", "start"]
        and stop_sequence[3][1]["expected_version"] == "1.2.3",
        "Windows restart must observe old health down before starting "
        "and verifying the selected runtime",
    )

    for bad in ("1", "1.2", "../1.2.3", "01.2.3"):
        try:
            service.validate_version(bad)
        except service.ServiceError:
            checks += 1
        else:
            raise AssertionError(f"invalid semantic version accepted: {bad}")

print(f"OK - {checks} managed service checks passed")
