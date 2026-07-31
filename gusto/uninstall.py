"""Safe removal of an installed Gusto runtime and, optionally, its data."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from . import service


class UninstallError(RuntimeError):
    """Raised when Gusto cannot prove that an uninstall target is safe."""


@dataclass(frozen=True)
class UninstallTargets:
    application: Path
    data: Path
    command_directory: Path
    settings: Path
    platform: str
    managed: bool = False
    linux_command_link: Path | None = None
    windows_start_menu: Path | None = None


def discover_targets(
    *,
    prefix: str | os.PathLike[str] | None = None,
    package_file: str | os.PathLike[str] | None = None,
    data_root: str | os.PathLike[str] | None = None,
    platform_name: str | None = None,
) -> UninstallTargets:
    """Resolve and verify the installed runtime currently executing Gusto."""
    platform_name = platform_name or sys.platform
    if not (platform_name.startswith("win") or platform_name.startswith("linux")):
        raise UninstallError("Deinstallation wird nur unter Windows und Linux unterstützt.")

    runtime = Path(prefix or sys.prefix).expanduser().resolve()
    package = Path(package_file or __file__).expanduser().resolve()
    package_root = package.parent.parent
    if (package_root / "pyproject.toml").is_file():
        raise UninstallError(
            "Ein Projekt-Checkout wird nicht mit 'gusto uninstall' gelöscht. "
            "Der Befehl ist nur für eine installierte Gusto-Runtime gedacht."
        )
    application: Path | None = None
    install_state: dict[str, object] | None = None
    for candidate in (runtime, *runtime.parents):
        if ((candidate / service.STATE_FILENAME).is_file()
                and (candidate / service.CURRENT_FILENAME).is_file()
                and runtime.is_relative_to(candidate / "versions")):
            paths = service.managed_paths(candidate)
            current = service.read_current(paths)
            if Path(str(current["runtime"])).resolve() != runtime:
                raise UninstallError(
                    "Gusto läuft nicht aus der aktiven verwalteten Version."
                )
            application = candidate
            install_state = service.read_state(paths)
            break

    if application is None or install_state is None:
        raise UninstallError(
            "Gusto läuft nicht aus einer aktuellen verwalteten Installation."
        )
    settings = application / service.STATE_FILENAME

    if not package.is_relative_to(runtime):
        raise UninstallError(
            "Gusto läuft nicht aus einer eigenständigen verwalteten Installation."
        )
    if not settings.is_file():
        raise UninstallError(
            f"Die Installationsmarkierung fehlt: {settings}. "
            "Aus Sicherheitsgründen wird nichts gelöscht."
        )
    if not (runtime / "pyvenv.cfg").is_file():
        raise UninstallError(
            f"'{runtime}' ist keine eindeutig erkennbare virtuelle Umgebung. "
            "Aus Sicherheitsgründen wird nichts gelöscht."
        )

    if platform_name.startswith("win"):
        command_directory = application / "bin"
        command = command_directory / "gusto.cmd"
    else:
        command_directory = application / "bin"
        command = command_directory / "gusto"
    if not command.is_file():
        raise UninstallError(
            f"Der erwartete Gusto-Launcher fehlt: {command}. "
            "Aus Sicherheitsgründen wird nichts gelöscht."
        )

    data = Path(
        data_root or str(install_state["data_dir"])
    ).expanduser().resolve()
    linux_link = (
        (Path.home() / ".local" / "bin" / "gusto")
        if platform_name.startswith("linux") else None
    )
    windows_start_menu = None
    if platform_name.startswith("win"):
        appdata = Path(os.environ.get("APPDATA") or
                       Path.home() / "AppData" / "Roaming")
        windows_start_menu = (
            appdata / "Microsoft" / "Windows" / "Start Menu" /
            "Programs" / "Gusto.url"
        ).resolve()
    return UninstallTargets(
        application=application,
        data=data,
        command_directory=command_directory,
        settings=settings,
        platform=platform_name,
        managed=True,
        linux_command_link=linux_link,
        windows_start_menu=windows_start_menu,
    )


def validate_removal(targets: UninstallTargets, *, delete_data: bool) -> Path | None:
    """Return a separate data deletion target after conservative safety checks."""
    application = targets.application
    data = targets.data
    home = Path.home().resolve()
    filesystem_root = Path(data.anchor).resolve()
    temporary_root = Path(tempfile.gettempdir()).resolve()

    if application == Path(application.anchor).resolve() or application == home:
        raise UninstallError("Der ermittelte Installationspfad ist zu breit zum Löschen.")

    data_inside_application = data == application or data.is_relative_to(application)
    if not delete_data:
        if data_inside_application:
            raise UninstallError(
                "Der Datenordner liegt innerhalb der Installation und kann beim "
                "Entfernen der App nicht erhalten bleiben."
            )
        return None

    if data_inside_application:
        return None  # Removing the application already removes this data tree.
    if (data in {filesystem_root, home, temporary_root}
            or application.is_relative_to(data)):
        raise UninstallError(
            f"Der Datenpfad ist zu breit oder enthält die Installation: {data}"
        )
    if data.exists():
        expected = [data / name for name in ("recipes", "images", "data")]
        if not all(path.is_dir() for path in expected):
            raise UninstallError(
                f"'{data}' sieht nicht wie ein Gusto-Datenordner aus; erwartet "
                "wurden die Unterordner recipes, images und data."
            )
    return data


def remove_path_entry(current: str, directory: Path) -> tuple[str, bool]:
    """Remove one exact command directory from a PATH value."""
    target = os.path.normcase(os.path.normpath(os.fspath(directory.resolve())))
    kept: list[str] = []
    removed = False
    for entry in current.split(os.pathsep):
        if not entry:
            continue
        candidate = os.path.expandvars(entry.strip().strip('"'))
        normalized = os.path.normcase(os.path.normpath(candidate))
        if normalized == target:
            removed = True
        else:
            kept.append(entry)
    return os.pathsep.join(kept), removed


def remove_windows_user_path(directory: Path) -> bool:
    """Remove only Gusto's stable command directory from the user PATH."""
    if not sys.platform.startswith("win"):
        return False

    import ctypes
    import winreg

    with winreg.CreateKeyEx(
            winreg.HKEY_CURRENT_USER, "Environment",
            access=winreg.KEY_READ | winreg.KEY_SET_VALUE) as key:
        try:
            current, value_type = winreg.QueryValueEx(key, "Path")
        except FileNotFoundError:
            return False
        updated, removed = remove_path_entry(current, directory)
        if not removed:
            return False
        winreg.SetValueEx(key, "Path", 0, value_type, updated)

    try:
        result = ctypes.c_ulong()
        ctypes.windll.user32.SendMessageTimeoutW(
            0xFFFF, 0x001A, 0, ctypes.c_wchar_p("Environment"),
            0x0002, 5000, ctypes.byref(result),
        )
    except (AttributeError, OSError, ctypes.ArgumentError):
        pass
    return True


def _completed_error(result: subprocess.CompletedProcess[str]) -> str:
    detail = (result.stderr or result.stdout or "").strip()
    return detail or f"Exit-Code {result.returncode}"


def remove_windows_autostart(
    *,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> bool:
    """Stop and unregister Gusto's fixed Task Scheduler entry."""
    powershell = shutil.which("powershell.exe")
    if not powershell:
        raise UninstallError("powershell.exe für die Autostart-Bereinigung fehlt.")
    script = r'''
$ErrorActionPreference = "Stop"
$task = Get-ScheduledTask -TaskName "Gusto" -ErrorAction SilentlyContinue
if ($null -eq $task) {
    Write-Output "absent"
    exit 0
}
if ($task.Description -ne "Gusto Rezeptserver beim Anmelden starten") {
    throw "Eine fremde geplante Aufgabe namens Gusto wird nicht entfernt."
}
if ($task.State -eq "Running") {
    Stop-ScheduledTask -TaskName "Gusto"
    $deadline = [DateTime]::UtcNow.AddSeconds(15)
    do {
        Start-Sleep -Milliseconds 200
        $task = Get-ScheduledTask -TaskName "Gusto" -ErrorAction Stop
    } while ($task.State -eq "Running" -and [DateTime]::UtcNow -lt $deadline)
    if ($task.State -eq "Running") {
        throw "Die laufende Gusto-Aufgabe konnte nicht beendet werden."
    }
}
Unregister-ScheduledTask -TaskName "Gusto" -Confirm:$false
Write-Output "removed"
'''
    result = runner(
        [powershell, "-NoProfile", "-NonInteractive", "-Command", script],
        text=True, encoding="utf-8", errors="replace", capture_output=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if result.returncode:
        raise UninstallError(
            "Windows-Autostart konnte nicht entfernt werden: "
            + _completed_error(result)
        )
    return "removed" in (result.stdout or "")


def remove_linux_autostart(
    *,
    interactive: bool = False,
    service_path: Path | None = None,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> bool:
    """Disable and remove Gusto's systemd user unit without sudo."""
    service_path = service_path or service.default_linux_unit_path()
    if not service_path.exists():
        return False
    systemctl = shutil.which("systemctl")
    if not systemctl:
        raise UninstallError("systemctl für die Autostart-Bereinigung fehlt.")

    commands = [
        [systemctl, "--user", "disable", "--now", "gusto.service"],
        [systemctl, "--user", "daemon-reload"],
    ]
    result = runner(
        commands[0], text=True, encoding="utf-8", errors="replace",
        **({} if interactive else {"capture_output": True}),
    )
    if result.returncode:
        raise UninstallError(
            "Linux-Autostart konnte nicht entfernt werden: "
            + _completed_error(result)
        )
    service_path.unlink(missing_ok=True)
    for command in commands[1:]:
        options = {"text": True, "encoding": "utf-8", "errors": "replace"}
        if not interactive:
            options["capture_output"] = True
        result = runner(command, **options)
        if result.returncode:
            raise UninstallError(
                "Linux-Autostart konnte nicht entfernt werden: "
                + _completed_error(result)
            )
    return True


def remove_windows_registration(targets: UninstallTargets) -> bool:
    """Remove the exact HKCU Installed Apps record and Start Menu URL."""
    if not targets.platform.startswith("win"):
        return False
    import winreg
    removed = False
    key_path = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\Gusto"
    try:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, key_path)
        removed = True
    except FileNotFoundError:
        pass
    if targets.windows_start_menu is not None:
        existed = targets.windows_start_menu.exists()
        targets.windows_start_menu.unlink(missing_ok=True)
        removed = removed or existed
    return removed


def remove_linux_command_link(targets: UninstallTargets) -> bool:
    link = targets.linux_command_link
    if link is None or not link.is_symlink():
        return False
    try:
        if link.resolve() != (targets.application / "bin" / "gusto").resolve():
            return False
    except OSError:
        return False
    link.unlink()
    return True


def remove_autostart(
    targets: UninstallTargets,
    *,
    interactive: bool,
) -> bool:
    if targets.platform.startswith("win"):
        return remove_windows_autostart()
    return remove_linux_autostart(interactive=interactive)


_REMOVAL_HELPER = r'''from __future__ import annotations
import os
import shutil
import sys
import time
import traceback
from pathlib import Path

parent_pid = int(sys.argv[1])
application = Path(sys.argv[2])
data_text = sys.argv[3]
data = Path(data_text) if data_text else None
log_path = Path(sys.argv[4])
script_path = Path(sys.argv[5])

def wait_for_parent() -> None:
    if parent_pid <= 0:
        return
    if os.name == "nt":
        import ctypes
        synchronize = 0x00100000
        handle = ctypes.windll.kernel32.OpenProcess(synchronize, False, parent_pid)
        if handle:
            try:
                ctypes.windll.kernel32.WaitForSingleObject(handle, 30000)
            finally:
                ctypes.windll.kernel32.CloseHandle(handle)
        return
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        try:
            os.kill(parent_pid, 0)
        except ProcessLookupError:
            return
        except PermissionError:
            return
        time.sleep(0.1)

def remove_tree(path: Path) -> None:
    if not path.exists():
        return
    last_error = None
    for _ in range(100):
        try:
            shutil.rmtree(path)
            return
        except FileNotFoundError:
            return
        except OSError as error:
            last_error = error
            time.sleep(0.1)
    raise last_error or OSError(f"could not remove {path}")

log_path.parent.mkdir(parents=True, exist_ok=True)
with log_path.open("a", encoding="utf-8") as log:
    try:
        wait_for_parent()
        remove_tree(application)
        if data is not None:
            remove_tree(data)
        print("Gusto deinstallation completed.", file=log)
    except Exception:
        traceback.print_exc(file=log)
        raise
    finally:
        try:
            script_path.unlink()
        except OSError:
            pass
'''


_WINDOWS_REMOVAL_HELPER = r'''param(
    [int]$ParentPid,
    [string]$Application,
    [string]$Data,
    [string]$LogPath,
    [string]$ScriptPath
)
$ErrorActionPreference = "Stop"

function Remove-Tree([string]$Target) {
    if (-not $Target -or -not (Test-Path -LiteralPath $Target)) { return }
    $lastError = $null
    for ($attempt = 0; $attempt -lt 100; $attempt++) {
        try {
            Remove-Item -LiteralPath $Target -Recurse -Force -ErrorAction Stop
            return
        } catch {
            $lastError = $_
            Start-Sleep -Milliseconds 100
        }
    }
    throw $lastError
}

$logDirectory = [IO.Path]::GetDirectoryName($LogPath)
New-Item -ItemType Directory -Force -Path $logDirectory | Out-Null
try {
    if ($ParentPid -gt 0) {
        $deadline = [DateTime]::UtcNow.AddSeconds(30)
        while ((Get-Process -Id $ParentPid -ErrorAction SilentlyContinue) `
                -and [DateTime]::UtcNow -lt $deadline) {
            Start-Sleep -Milliseconds 100
        }
    }
    Remove-Tree $Application
    Remove-Tree $Data
    Add-Content -LiteralPath $LogPath -Encoding UTF8 `
        -Value "Gusto deinstallation completed."
} catch {
    Add-Content -LiteralPath $LogPath -Encoding UTF8 -Value ($_ | Out-String)
    exit 1
} finally {
    Remove-Item -LiteralPath $ScriptPath -Force -ErrorAction SilentlyContinue
}
'''


def write_removal_helper(platform_name: str) -> Path:
    """Write the one-shot helper outside both application and data roots."""
    windows = platform_name.startswith("win")
    descriptor, name = tempfile.mkstemp(
        prefix="gusto-uninstall-", suffix=".ps1" if windows else ".py",
    )
    path = Path(name)
    with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as file:
        file.write(_WINDOWS_REMOVAL_HELPER if windows else _REMOVAL_HELPER)
    return path


def _base_interpreter(application: Path) -> Path:
    executable = Path(getattr(sys, "_base_executable", sys.executable)).resolve()
    if executable == application or executable.is_relative_to(application):
        raise UninstallError(
            "Kein unabhängiger Python-Interpreter für die Selbstlöschung gefunden."
        )
    if not executable.is_file():
        raise UninstallError(f"Python für die Selbstlöschung fehlt: {executable}")
    return executable


def schedule_removal(
    targets: UninstallTargets,
    *,
    separate_data_target: Path | None,
    parent_pid: int | None = None,
    launcher: Callable[..., subprocess.Popen[bytes]] = subprocess.Popen,
) -> Path:
    """Start an external helper which deletes the app after this CLI exits."""
    helper = write_removal_helper(targets.platform)
    timestamp = time.strftime("%Y%m%d-%H%M%S")
    log_path = Path(tempfile.gettempdir()) / f"gusto-uninstall-{timestamp}.log"
    actual_parent_pid = os.getpid() if parent_pid is None else parent_pid
    if targets.platform.startswith("win"):
        powershell = shutil.which("powershell.exe")
        if not powershell:
            helper.unlink(missing_ok=True)
            raise UninstallError("powershell.exe für die Selbstlöschung fehlt.")
        command = [
            powershell,
            "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
            "-File", os.fspath(helper),
            "-ParentPid", str(actual_parent_pid),
            "-Application", os.fspath(targets.application),
            "-Data", os.fspath(separate_data_target) if separate_data_target else "",
            "-LogPath", os.fspath(log_path),
            "-ScriptPath", os.fspath(helper),
        ]
    else:
        interpreter = _base_interpreter(targets.application)
        command = [
            os.fspath(interpreter), "-I", os.fspath(helper),
            str(actual_parent_pid),
            os.fspath(targets.application),
            os.fspath(separate_data_target) if separate_data_target else "",
            os.fspath(log_path),
            os.fspath(helper),
        ]
    options: dict[str, object] = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "cwd": tempfile.gettempdir(),
        "close_fds": True,
    }
    if targets.platform.startswith("win"):
        # PowerShell 5.1 exits before running -File when launched with
        # DETACHED_PROCESS. CREATE_NO_WINDOW still decouples it from the
        # caller's console while allowing the one-shot script to execute.
        options["creationflags"] = getattr(
            subprocess, "CREATE_NO_WINDOW", 0x08000000,
        )
    else:
        options["start_new_session"] = True
    try:
        launcher(command, **options)
    except Exception:
        helper.unlink(missing_ok=True)
        raise
    return log_path


def perform_uninstall(
    targets: UninstallTargets,
    *,
    delete_data: bool,
    dry_run: bool,
    interactive: bool,
    autostart_remover: Callable[..., bool] = remove_autostart,
    path_remover: Callable[[Path], bool] = remove_windows_user_path,
    registration_remover: Callable[[UninstallTargets], bool] = remove_windows_registration,
    linux_link_remover: Callable[[UninstallTargets], bool] = remove_linux_command_link,
    removal_scheduler: Callable[..., Path] = schedule_removal,
) -> dict[str, object]:
    """Clean platform integration and schedule the verified trees for removal."""
    separate_data = validate_removal(targets, delete_data=delete_data)
    result: dict[str, object] = {
        "status": "dry_run" if dry_run else "scheduled",
        "application_path": os.fspath(targets.application),
        "data_path": os.fspath(targets.data),
        "delete_data": delete_data,
        "autostart_removed": False,
        "path_entry_removed": False,
        "registration_removed": False,
        "command_link_removed": False,
    }
    if dry_run:
        return result

    result["autostart_removed"] = autostart_remover(
        targets, interactive=interactive,
    )
    if targets.platform.startswith("win"):
        result["path_entry_removed"] = path_remover(
            targets.command_directory,
        )
        result["registration_removed"] = registration_remover(targets)
    else:
        result["command_link_removed"] = linux_link_remover(targets)
    log_path = removal_scheduler(
        targets, separate_data_target=separate_data,
    )
    result["log_path"] = os.fspath(log_path)
    return result
