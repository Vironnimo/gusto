"""Hermetic managed service lifecycle checks."""
from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
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
        "python_runtime": service.system_python_state(),
    }
    service.write_state(paths, state)
    service.write_current(paths, "1.2.3")
    check(service.read_current(paths)["version"] == "1.2.3",
          "current pointer must resolve a version inside versions/")
    check(service.read_state(paths)["data_dir"] == os.fspath(data),
          "install state must round-trip")
    check(service.server_url(state) == "http://127.0.0.1:8123",
          "wildcard listen host must become a local client URL")
    for description, corrupt, expected in (
        ("a non-numeric port", {**state, "port": "8000x"}, "Port"),
        ("a missing port", {**state, "port": None}, "Port"),
        ("a boolean port", {**state, "port": True}, "Port"),
        ("an exploding port", {**state, "port": 10**20}, "Port"),
        ("a port below the TCP range", {**state, "port": 0}, "Port"),
        ("a port above the TCP range", {**state, "port": 65536}, "Port"),
        ("an empty host", {**state, "host": ""}, "Host"),
        ("a host with a path", {**state, "host": "0.0.0.0/8"}, "Host"),
        ("a host with a space", {**state, "host": "0.0.0.0 0.0.0.1"}, "Host"),
        ("a non-string host", {**state, "host": 8123}, "Host"),
    ):
        service.write_state(paths, corrupt)
        try:
            service.read_state(paths)
        except service.ServiceError as error:
            check(expected in str(error),
                  f"read_state must reject {description}: {error}")
        else:
            raise AssertionError(f"read_state accepted {description}")
    for boundary in (1, 65535):
        service.write_state(paths, {**state, "port": boundary})
        check(service.read_state(paths)["port"] == boundary,
              "a valid port boundary must stay readable")
    service.write_state(paths, state)

    corrupt_python = service.managed_python_root(paths, "3.13.14")
    corrupt_python.mkdir(parents=True)
    (corrupt_python / "python.exe").write_bytes(b"not-an-executable")
    (corrupt_python / ".gusto-python-runtime.json").write_text(
        json.dumps({
            "schema_version": 1,
            "kind": "managed",
            "version": "3.13.14",
            "sha256": "0" * 64,
            "architecture": "x86_64",
        }),
        encoding="utf-8",
    )
    check(not service.managed_python_usable(
        corrupt_python, "3.13.14", "0" * 64,
    ), "a corrupt managed Python executable must be repairable, not crash probing")

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
    check("fremde geplante Aufgabe namens Gusto" in script,
          "Windows task registration must refuse a foreign task-name collision")
    with patch.object(service, "register_windows_app", return_value={}):
        service.install_user_service(
            # The health stopper is injected: this host may already run an
            # unrelated Gusto on the fixture port, and a hermetic lifecycle
            # check must never poll a real endpoint.
            paths, state, platform_name="win32", runner=runner,
            health_stopper=lambda url: None,
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
    status_command = service.service_action(
        "status", app_root=paths.app_root, platform_name="win32",
        dry_run=True,
    )["command"]
    check("fremde geplante Aufgabe namens Gusto" in status_command[-1],
          "Windows service controls must refuse a foreign task-name collision")

    for bad in ("1", "1.2", "../1.2.3", "01.2.3"):
        try:
            service.validate_version(bad)
        except service.ServiceError:
            checks += 1
        else:
            raise AssertionError(f"invalid semantic version accepted: {bad}")

# ==== F3: health fail-fast ====
    mismatch_calls = []

    def counted_opener(*args, **kwargs):
        mismatch_calls.append(args)
        return Response()

    started_at = time.monotonic()
    try:
        service.wait_for_health(
            "http://fixture/api/v1/health", timeout=3, interval=0.05,
            expected_version="9.9.9", expected_data_path=data,
            opener=counted_opener,
        )
    except service.ServiceError as error:
        check("9.9.9" in str(error) and "erwartet" in str(error),
              "a wrong health version must fail fast and name both versions")
    else:
        raise AssertionError("wrong health version accepted")
    check(len(mismatch_calls) == 1 and time.monotonic() - started_at < 1.5,
          "a reachable server with the wrong version is not transient and "
          "must not be polled until the health timeout")

    class UnhealthyResponse(Response):
        def read(self):
            return json.dumps({"ok": False, "error": "startet"}).encode("utf-8")

    unhealthy_calls = []

    def unhealthy_opener(*args, **kwargs):
        unhealthy_calls.append(args)
        return UnhealthyResponse()

    try:
        service.wait_for_health(
            "http://fixture/api/v1/health", timeout=3, interval=0.05,
            expected_version="1.2.3", expected_data_path=data,
            opener=unhealthy_opener,
        )
    except service.ServiceError as error:
        check("ok=true" in str(error),
              "an explicit ok=false must fail fast with its meaning")
    else:
        raise AssertionError("unhealthy health response accepted")
    check(len(unhealthy_calls) == 1,
          "a reachable server that reports ok=false must not be retried")

    class StartingResponse(Response):
        status = 503

    transient = [urllib.error.URLError("noch nicht erreichbar"), StartingResponse(),
                 Response()]

    def transient_opener(*args, **kwargs):
        result = transient.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    recovered = service.wait_for_health(
        "http://fixture/api/v1/health", timeout=5, interval=0.05,
        expected_version="1.2.3", expected_data_path=data,
        opener=transient_opener,
    )
    check(recovered["ok"] is True and not transient,
          "unreachable and HTTP-answering services must still be polled "
          "until they report the expected runtime")
# ==== F3 end ====

# ==== F9: atomic temp cleanup ====
    with patch.object(service.os, "replace", side_effect=OSError("gesperrt")):
        try:
            service.write_current(paths, "1.2.4")
        except OSError:
            checks += 1
        else:
            raise AssertionError("a failing atomic replace must propagate")
    check(not list(paths.app_root.glob("*.tmp")),
          "a failed atomic write must not leave a temporary sibling behind")
    check(service.read_current(paths)["version"] == "1.2.3",
          "a failed atomic write must not change the active pointer")
# ==== F9 end ====

print(f"OK - {checks} managed service checks passed")
