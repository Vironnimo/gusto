"""Per-user managed runtime and background-service lifecycle for Gusto."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any


CURRENT_FILENAME = "current.json"
STATE_FILENAME = "install-state.json"
TASK_NAME = "Gusto"
UNIT_NAME = "gusto.service"
STATE_SCHEMA = 1


class ServiceError(RuntimeError):
    """Raised when a managed runtime or its user service is invalid."""


Runner = Callable[..., subprocess.CompletedProcess[str]]


@dataclass(frozen=True)
class ManagedPaths:
    app_root: Path
    versions: Path
    wrappers: Path
    current: Path
    state: Path

    def version(self, version: str) -> Path:
        validate_version(version)
        candidate = (self.versions / version).resolve()
        if not candidate.is_relative_to(self.versions):
            raise ServiceError("Ungültiger Versionspfad.")
        return candidate


def default_app_root(
    platform_name: str | None = None,
    environ: dict[str, str] | None = None,
    home: str | Path | None = None,
) -> Path:
    """Return Gusto's normal, unprivileged application root."""
    platform_name = platform_name or sys.platform
    environ = os.environ if environ is None else environ
    user_home = Path.home() if home is None else Path(home)
    if platform_name.startswith("win"):
        base = Path(environ.get("LOCALAPPDATA") or
                    user_home / "AppData" / "Local")
        return (base / "Programs" / "Gusto").expanduser().resolve()
    if platform_name == "darwin":
        return (user_home / "Library" / "Application Support" /
                "Gusto" / "app").resolve()
    return (user_home / ".local" / "opt" / "gusto").resolve()


def default_linux_unit_path(
    environ: dict[str, str] | None = None,
    home: str | Path | None = None,
) -> Path:
    environ = os.environ if environ is None else environ
    user_home = Path.home() if home is None else Path(home)
    base = Path(environ.get("XDG_CONFIG_HOME") or user_home / ".config")
    return (base / "systemd" / "user" / UNIT_NAME).expanduser().resolve()


def managed_paths(app_root: str | Path | None = None) -> ManagedPaths:
    root = Path(app_root or default_app_root()).expanduser().resolve()
    home = Path.home().resolve()
    if root == Path(root.anchor).resolve() or root == home:
        raise ServiceError("Der App-Pfad ist zu breit für eine verwaltete Installation.")
    return ManagedPaths(
        app_root=root,
        versions=(root / "versions").resolve(),
        wrappers=(root / "bin").resolve(),
        current=(root / CURRENT_FILENAME).resolve(),
        state=(root / STATE_FILENAME).resolve(),
    )


def validate_version(version: str) -> str:
    """Accept a conservative SemVer value suitable as one directory name."""
    import re
    if not re.fullmatch(
        r"(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)"
        r"(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?"
        r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?",
        version,
    ):
        raise ServiceError(f"Ungültige Release-Version: {version!r}")
    return version


def version_key(version: str) -> tuple[tuple[int, int, int], tuple[tuple[int, Any], ...]]:
    """Return a SemVer comparison key (build metadata is ignored)."""
    validate_version(version)
    core = version.split("+", 1)[0]
    numeric, separator, prerelease = core.partition("-")
    major, minor, patch = (int(value) for value in numeric.split("."))
    if not separator:
        pre_key: tuple[tuple[int, Any], ...] = ((2, ""),)
    else:
        fields: list[tuple[int, Any]] = []
        for value in prerelease.split("."):
            fields.append((0, int(value)) if value.isdigit() else (1, value))
        pre_key = tuple(fields)
    return (major, minor, patch), pre_key


def _atomic_json(path: Path, value: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f".{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def read_json_object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise ServiceError(f"{label} fehlt: {path}") from error
    except (OSError, json.JSONDecodeError) as error:
        raise ServiceError(f"{label} ist nicht lesbar: {error}") from error
    if not isinstance(value, dict):
        raise ServiceError(f"{label} muss ein JSON-Objekt sein.")
    return value


def read_state(paths: ManagedPaths) -> dict[str, Any]:
    state = read_json_object(paths.state, "Installationszustand")
    if state.get("schema_version") != STATE_SCHEMA:
        raise ServiceError("Unbekannte Version des Installationszustands.")
    for field in ("data_dir", "host", "port"):
        if field not in state:
            raise ServiceError(f"Installationszustand enthält kein {field!r}.")
    return state


def write_state(paths: ManagedPaths, state: dict[str, object]) -> None:
    value = dict(state)
    value["schema_version"] = STATE_SCHEMA
    _atomic_json(paths.state, value)


def read_current(paths: ManagedPaths, *, require_runtime: bool = True) -> dict[str, Any]:
    current = read_json_object(paths.current, "Aktiver Versionszeiger")
    version = current.get("version")
    if not isinstance(version, str):
        raise ServiceError("Aktiver Versionszeiger enthält keine Version.")
    runtime = paths.version(validate_version(version))
    stored_path = current.get("runtime")
    if stored_path and Path(str(stored_path)).resolve() != runtime:
        raise ServiceError("Aktiver Versionszeiger verweist aus dem App-Root.")
    if require_runtime and not runtime.is_dir():
        raise ServiceError(f"Aktive Runtime fehlt: {runtime}")
    return {**current, "version": version, "runtime": os.fspath(runtime)}


def write_current(
    paths: ManagedPaths,
    version: str,
    *,
    previous_version: str | None = None,
) -> None:
    runtime = paths.version(version)
    value: dict[str, object] = {
        "schema_version": STATE_SCHEMA,
        "version": version,
        "runtime": os.fspath(runtime),
    }
    if previous_version:
        validate_version(previous_version)
        value["previous_version"] = previous_version
    _atomic_json(paths.current, value)


def runtime_python(runtime: Path, platform_name: str | None = None) -> Path:
    platform_name = platform_name or sys.platform
    return (runtime / "Scripts" / "python.exe" if platform_name.startswith("win")
            else runtime / "bin" / "python")


def runtime_command(runtime: Path, platform_name: str | None = None) -> Path:
    platform_name = platform_name or sys.platform
    return (runtime / "Scripts" / "gusto.exe" if platform_name.startswith("win")
            else runtime / "bin" / "gusto")


def runtime_autostart(runtime: Path) -> Path:
    return runtime / "Scripts" / "gusto-autostart.exe"


def register_windows_app(
    paths: ManagedPaths,
    state: dict[str, Any],
    version: str,
) -> dict[str, str]:
    """Register the managed app in current-user Windows shell surfaces."""
    import winreg
    key_path = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\Gusto"
    wrapper = paths.wrappers / "gusto.cmd"
    command_processor = os.environ.get("COMSPEC") or "cmd.exe"
    with winreg.CreateKeyEx(
        winreg.HKEY_CURRENT_USER, key_path, access=winreg.KEY_SET_VALUE,
    ) as key:
        values = {
            "DisplayName": "Gusto",
            "DisplayVersion": version,
            "Publisher": "Gusto",
            "InstallLocation": os.fspath(paths.app_root),
            "UninstallString": (
                f'"{command_processor}" /d /c ""{wrapper}" uninstall"'
            ),
            "NoModify": 1,
            "NoRepair": 1,
        }
        for name, value in values.items():
            value_type = winreg.REG_DWORD if isinstance(value, int) else winreg.REG_SZ
            winreg.SetValueEx(key, name, 0, value_type, value)
    appdata = Path(os.environ.get("APPDATA") or
                   Path.home() / "AppData" / "Roaming")
    shortcut = appdata / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Gusto.url"
    shortcut.parent.mkdir(parents=True, exist_ok=True)
    shortcut.write_text(
        f"[InternetShortcut]\nURL={server_url(state)}\n",
        encoding="utf-8",
    )
    return {"uninstall_key": key_path, "start_menu": os.fspath(shortcut)}


def server_url(state: dict[str, Any], *, local: bool = True) -> str:
    host = str(state.get("host", "0.0.0.0"))
    if local and host in {"0.0.0.0", "::", "[::]"}:
        host = "127.0.0.1"
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    return f"http://{host}:{int(state.get('port', 8000))}"


def health_url(state: dict[str, Any]) -> str:
    return server_url(state).rstrip("/") + "/api/v1/health"


def wait_for_health(
    url: str,
    *,
    timeout: float = 30.0,
    interval: float = 0.2,
    expected_version: str | None = None,
    expected_data_path: str | Path | None = None,
    opener: Callable[..., Any] = urllib.request.urlopen,
) -> dict[str, Any]:
    """Wait for the intended version/store, never merely any Gusto process."""
    deadline = time.monotonic() + timeout
    last_error = "keine Antwort"
    expected_data = (
        Path(expected_data_path).expanduser().resolve()
        if expected_data_path is not None else None
    )
    while True:
        try:
            with opener(url, timeout=min(2.0, max(timeout, 0.1))) as response:
                if getattr(response, "status", 200) != 200:
                    raise OSError(f"HTTP {response.status}")
                body = response.read()
            value = json.loads(body.decode("utf-8"))
            if not isinstance(value, dict) or value.get("ok") is not True:
                raise ValueError("Health-Antwort meldet nicht ok=true")
            if (expected_version is not None
                    and value.get("version") != expected_version):
                raise ValueError(
                    "Health-Antwort stammt von Version "
                    f"{value.get('version')!r}, erwartet {expected_version!r}"
                )
            if expected_data is not None:
                actual_data = value.get("data_path")
                if not isinstance(actual_data, str):
                    raise ValueError("Health-Antwort enthält keinen Datenpfad")
                actual_path = Path(actual_data).expanduser().resolve()
                if os.path.normcase(os.fspath(actual_path)) != os.path.normcase(
                    os.fspath(expected_data)
                ):
                    raise ValueError(
                        f"Health-Antwort verwendet Datenpfad {actual_path}, "
                        f"erwartet {expected_data}"
                    )
            return value
        except (OSError, ValueError, json.JSONDecodeError,
                urllib.error.URLError) as error:
            last_error = str(error)
        if time.monotonic() >= deadline:
            raise ServiceError(
                f"Gusto wurde unter {url} nicht erreichbar: {last_error}"
            )
        time.sleep(interval)


def wait_for_unhealthy(
    url: str,
    *,
    timeout: float = 15.0,
    interval: float = 0.1,
    opener: Callable[..., Any] = urllib.request.urlopen,
) -> None:
    """Wait until the old Gusto health endpoint no longer answers successfully."""
    deadline = time.monotonic() + timeout
    while True:
        try:
            with opener(url, timeout=min(1.0, max(timeout, 0.1))) as response:
                response.read()
                still_healthy = getattr(response, "status", 200) == 200
        except (OSError, urllib.error.URLError):
            return
        if not still_healthy:
            return
        if time.monotonic() >= deadline:
            raise ServiceError(
                f"Der bisherige Gusto-Prozess unter {url} wurde nicht beendet."
            )
        time.sleep(interval)


def render_linux_unit(paths: ManagedPaths, state: dict[str, Any]) -> str:
    current = read_current(paths)
    python = runtime_python(Path(current["runtime"]), "linux")
    data_dir = Path(str(state["data_dir"])).expanduser().resolve()
    host = str(state.get("host", "0.0.0.0"))
    port = int(state.get("port", 8000))

    def quote(value: str) -> str:
        if any(character in value for character in "\0\r\n"):
            raise ServiceError("systemd-Werte dürfen keine Steuerzeichen enthalten.")
        return '"' + value.replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%") + '"'

    return (
        "[Unit]\n"
        "Description=Gusto Rezeptserver\n"
        "After=network-online.target\n"
        "Wants=network-online.target\n\n"
        "[Service]\n"
        "Type=simple\n"
        f"WorkingDirectory={quote(os.fspath(data_dir))}\n"
        f"ExecStart={quote(os.fspath(python))} -m gusto serve "
        f"--host {quote(host)} --port {port}\n"
        "Restart=on-failure\n"
        "RestartSec=3\n\n"
        "[Install]\n"
        "WantedBy=default.target\n"
    )


def _run(
    runner: Runner,
    command: list[str],
    *,
    dry_run: bool,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    if dry_run:
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
    result = runner(
        command, text=True, encoding="utf-8", errors="replace",
        capture_output=True, env=env,
    )
    if result.returncode:
        detail = (result.stderr or result.stdout or "").strip()
        raise ServiceError(
            f"Befehl fehlgeschlagen ({result.returncode}): "
            f"{' '.join(command)}{': ' + detail if detail else ''}"
        )
    return result


def install_user_service(
    paths: ManagedPaths,
    state: dict[str, Any],
    *,
    platform_name: str | None = None,
    dry_run: bool = False,
    runner: Runner = subprocess.run,
    unit_path: Path | None = None,
    health_stopper: Callable[..., None] = wait_for_unhealthy,
) -> dict[str, object]:
    """Install/switch and immediately start Gusto's current-user service."""
    platform_name = platform_name or sys.platform
    current = read_current(paths)
    runtime = Path(current["runtime"])
    actions: list[list[str]] = []
    if platform_name.startswith("win"):
        powershell = shutil.which("powershell.exe") or "powershell.exe"
        launcher = runtime_autostart(runtime)
        if not dry_run and not launcher.is_file():
            raise ServiceError(f"Autostart-Launcher fehlt: {launcher}")
        host = str(state.get("host", "0.0.0.0"))
        port = int(state.get("port", 8000))
        script = r'''
$ErrorActionPreference = "Stop"
$Launcher = $env:GUSTO_SERVICE_LAUNCHER
$WorkingDirectory = $env:GUSTO_SERVICE_WORKDIR
$HostName = $env:GUSTO_SERVICE_HOST
$PortNumber = $env:GUSTO_SERVICE_PORT
$action = New-ScheduledTaskAction -Execute $Launcher `
  -Argument "--host `"$HostName`" --port $PortNumber" `
  -WorkingDirectory $WorkingDirectory
$trigger = New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME"
$principal = New-ScheduledTaskPrincipal `
  -UserId "$env:USERDOMAIN\$env:USERNAME" `
  -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet `
  -RestartCount 5 -RestartInterval (New-TimeSpan -Minutes 1) `
  -ExecutionTimeLimit (New-TimeSpan -Days 0)
$old = Get-ScheduledTask -TaskName "Gusto" -ErrorAction SilentlyContinue
if ($null -ne $old -and $old.Description -ne "Gusto Rezeptserver beim Anmelden starten") {
  throw "Eine fremde geplante Aufgabe namens Gusto blockiert die Installation."
}
if ($null -ne $old -and $old.State -eq "Running") {
  Stop-ScheduledTask -TaskName "Gusto"
  $deadline = [DateTime]::UtcNow.AddSeconds(15)
  do {
    Start-Sleep -Milliseconds 100
    $old = Get-ScheduledTask -TaskName "Gusto" -ErrorAction Stop
  } while ($old.State -eq "Running" -and [DateTime]::UtcNow -lt $deadline)
  if ($old.State -eq "Running") {
    throw "Die bisherige Gusto-Aufgabe konnte nicht beendet werden."
  }
}
Register-ScheduledTask -TaskName "Gusto" -Action $action -Trigger $trigger `
  -Principal $principal -Settings $settings `
  -Description "Gusto Rezeptserver beim Anmelden starten" -Force | Out-Null
'''
        register_command = [
            powershell, "-NoProfile", "-NonInteractive", "-Command", script,
        ]
        start_command = [
            powershell, "-NoProfile", "-NonInteractive", "-Command",
            "Start-ScheduledTask -TaskName 'Gusto'",
        ]
        actions.extend((register_command, start_command))
        if not dry_run:
            register_windows_app(paths, state, str(current["version"]))
        _run(
            runner,
            register_command,
            dry_run=dry_run,
            env={
                **os.environ,
                "GUSTO_SERVICE_LAUNCHER": os.fspath(launcher),
                "GUSTO_SERVICE_WORKDIR": os.fspath(
                    Path(str(state["data_dir"])).resolve()
                ),
                "GUSTO_SERVICE_HOST": host,
                "GUSTO_SERVICE_PORT": str(port),
            },
        )
        if not dry_run:
            health_stopper(health_url(state))
        _run(runner, start_command, dry_run=dry_run)
        integration = "windows_task"
    elif platform_name.startswith("linux"):
        destination = unit_path or default_linux_unit_path()
        text = render_linux_unit(paths, state)
        if not dry_run:
            destination.parent.mkdir(parents=True, exist_ok=True)
            temporary = destination.with_name(destination.name + ".tmp")
            temporary.write_text(text, encoding="utf-8", newline="\n")
            os.replace(temporary, destination)
        systemctl = shutil.which("systemctl") or "systemctl"
        for arguments in (
            [systemctl, "--user", "daemon-reload"],
            [systemctl, "--user", "enable", "--now", UNIT_NAME],
        ):
            actions.append(arguments)
            _run(runner, arguments, dry_run=dry_run)
        integration = "systemd_user"
    else:
        raise ServiceError("User-Autostart wird nur unter Windows und Linux unterstützt.")
    return {
        "ok": True,
        "integration": integration,
        "version": current["version"],
        "actions": actions if dry_run else [],
    }


def remove_user_service(
    paths: ManagedPaths,
    *,
    platform_name: str | None = None,
    dry_run: bool = False,
    runner: Runner = subprocess.run,
    unit_path: Path | None = None,
) -> bool:
    platform_name = platform_name or sys.platform
    if platform_name.startswith("win"):
        powershell = shutil.which("powershell.exe") or "powershell.exe"
        script = r'''
$task = Get-ScheduledTask -TaskName "Gusto" -ErrorAction SilentlyContinue
if ($null -eq $task) { Write-Output "absent"; exit 0 }
if ($task.Description -ne "Gusto Rezeptserver beim Anmelden starten") {
  throw "Eine fremde geplante Aufgabe namens Gusto wird nicht entfernt."
}
if ($task.State -eq "Running") {
  Stop-ScheduledTask -TaskName "Gusto"
  $deadline = [DateTime]::UtcNow.AddSeconds(15)
  do {
    Start-Sleep -Milliseconds 100
    $task = Get-ScheduledTask -TaskName "Gusto" -ErrorAction Stop
  } while ($task.State -eq "Running" -and [DateTime]::UtcNow -lt $deadline)
  if ($task.State -eq "Running") {
    throw "Die laufende Gusto-Aufgabe konnte nicht beendet werden."
  }
}
Unregister-ScheduledTask -TaskName "Gusto" -Confirm:$false
Write-Output "removed"
'''
        result = _run(
            runner,
            [powershell, "-NoProfile", "-NonInteractive", "-Command", script],
            dry_run=dry_run,
        )
        return dry_run or "removed" in (result.stdout or "")
    if platform_name.startswith("linux"):
        destination = unit_path or default_linux_unit_path()
        systemctl = shutil.which("systemctl") or "systemctl"
        existed = destination.exists()
        disable_error: Exception | None = None
        try:
            _run(runner, [systemctl, "--user", "disable", "--now", UNIT_NAME],
                 dry_run=dry_run)
        except (OSError, ServiceError) as error:
            disable_error = error
        finally:
            if not dry_run:
                destination.unlink(missing_ok=True)
        try:
            _run(runner, [systemctl, "--user", "daemon-reload"],
                 dry_run=dry_run)
        except (OSError, ServiceError):
            if disable_error is None:
                raise
        if disable_error is not None:
            raise ServiceError(
                f"Linux-User-Dienst konnte nicht vollständig deaktiviert "
                f"werden: {disable_error}"
            ) from disable_error
        return dry_run or existed
    raise ServiceError("User-Autostart wird nur unter Windows und Linux unterstützt.")


def service_action(
    action: str,
    *,
    app_root: str | Path | None = None,
    platform_name: str | None = None,
    dry_run: bool = False,
    runner: Runner = subprocess.run,
    health_waiter: Callable[..., dict[str, Any]] = wait_for_health,
    health_stopper: Callable[..., None] = wait_for_unhealthy,
) -> dict[str, object]:
    """Dispatch a local status/start/stop/restart lifecycle operation."""
    if action not in {"status", "start", "stop", "restart"}:
        raise ServiceError(f"Unbekannte Service-Aktion: {action}")
    paths = managed_paths(app_root)
    state = read_state(paths)
    current = read_current(paths)
    platform_name = platform_name or sys.platform
    if platform_name.startswith("win"):
        powershell = shutil.which("powershell.exe") or "powershell.exe"
        ownership_check = (
            "if($null -ne $t -and $t.Description -ne "
            "'Gusto Rezeptserver beim Anmelden starten'){"
            "throw 'Eine fremde geplante Aufgabe namens Gusto blockiert die Service-Steuerung.'};"
        )
        stop_script = (
            "$t=Get-ScheduledTask -TaskName 'Gusto' -ErrorAction SilentlyContinue;"
            + ownership_check +
            "if($null -ne $t -and $t.State -eq 'Running'){"
            "Stop-ScheduledTask -TaskName 'Gusto';"
            "$d=[DateTime]::UtcNow.AddSeconds(15);"
            "do{Start-Sleep -Milliseconds 100;"
            "$t=Get-ScheduledTask -TaskName 'Gusto' -ErrorAction Stop}"
            "while($t.State -eq 'Running' -and [DateTime]::UtcNow -lt $d);"
            "if($t.State -eq 'Running'){throw 'Gusto konnte nicht beendet werden.'}}"
        )
        verbs = {
            "status": (
                "$t=Get-ScheduledTask -TaskName 'Gusto' -ErrorAction SilentlyContinue;"
                + ownership_check + "if($null -eq $t){'absent'}else{$t.State}"
            ),
            "start": (
                "$t=Get-ScheduledTask -TaskName 'Gusto' -ErrorAction SilentlyContinue;"
                + ownership_check + "Start-ScheduledTask -TaskName 'Gusto'"
            ),
            "stop": stop_script,
            "restart": stop_script,
        }
        command = [
            powershell, "-NoProfile", "-NonInteractive", "-Command", verbs[action],
        ]
    elif platform_name.startswith("linux"):
        systemctl = shutil.which("systemctl") or "systemctl"
        verb = "is-active" if action == "status" else action
        command = [systemctl, "--user", verb, UNIT_NAME]
    else:
        raise ServiceError("Service-Steuerung wird nur unter Windows und Linux unterstützt.")
    if action == "status" and platform_name.startswith("linux") and not dry_run:
        result = runner(
            command, text=True, encoding="utf-8", errors="replace",
            capture_output=True,
        )
        if result.returncode not in {0, 3, 4}:
            detail = (result.stderr or result.stdout or "").strip()
            raise ServiceError(
                f"Service-Status konnte nicht gelesen werden: "
                f"{detail or 'Exit-Code ' + str(result.returncode)}"
            )
    elif action == "restart" and platform_name.startswith("win") and not dry_run:
        result = _run(runner, command, dry_run=False)
        health_stopper(health_url(state))
        start_command = [
            powershell, "-NoProfile", "-NonInteractive", "-Command",
            "Start-ScheduledTask -TaskName 'Gusto'",
        ]
        _run(runner, start_command, dry_run=False)
    else:
        result = _run(runner, command, dry_run=dry_run)
    if (action == "stop" and platform_name.startswith("win")
            and not dry_run):
        health_stopper(health_url(state))
    if action in {"start", "restart"} and not dry_run:
        health_waiter(
            health_url(state),
            expected_version=str(current["version"]),
            expected_data_path=state["data_dir"],
        )
    status_text = (result.stdout or "").strip().lower()
    running = (
        status_text in {"active", "running"}
        if action == "status" else action in {"start", "restart"}
    )
    if action == "status" and not running and not status_text:
        status_text = "stopped"
    return {
        "ok": True,
        "action": action,
        "status": "dry_run" if dry_run else (
            "running" if running else
            ("stopped" if action == "stop" else status_text)
        ),
        "running": running,
        "version": current["version"],
        "url": server_url(state),
        "command": command if dry_run else None,
    }
