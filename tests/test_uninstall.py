"""Hermetic managed uninstall lifecycle checks."""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, os.fspath(ROOT))

from gusto import service, uninstall  # noqa: E402

checks = 0


def check(value, message):
    global checks
    assert value, message
    checks += 1


def expect_error(call, message):
    try:
        call()
    except uninstall.UninstallError:
        check(True, message)
    else:
        raise AssertionError(message)


with tempfile.TemporaryDirectory(prefix="gusto-uninstall-") as temporary:
    root = Path(temporary)
    app = root / "Gusto App"
    paths = service.managed_paths(app)
    runtime = paths.version("1.2.3")
    scripts = runtime / "Scripts"
    package = runtime / "Lib" / "site-packages" / "gusto" / "uninstall.py"
    scripts.mkdir(parents=True)
    package.parent.mkdir(parents=True)
    package.write_text("# installed\n", encoding="utf-8")
    (runtime / "pyvenv.cfg").write_text("home=x\n", encoding="utf-8")
    (app / "bin").mkdir(parents=True)
    (app / "bin" / "gusto.cmd").write_text("@echo off\n", encoding="utf-8")
    data = root / "data"
    for name in ("recipes", "images", "data"):
        (data / name).mkdir(parents=True)
    python_root = paths.python_runtimes / "3.13.14"
    python_root.mkdir(parents=True)
    (python_root / "python.exe").write_bytes(b"app-private-python")
    service.write_state(paths, {
        "data_dir": os.fspath(data), "host": "0.0.0.0", "port": 8000,
        "python_runtime": service.managed_python_state(
            paths, "3.13.14", "0" * 64,
        ),
    })
    service.write_current(paths, "1.2.3")

    targets = uninstall.discover_targets(
        prefix=runtime, package_file=package,
        data_root=data, platform_name="win32",
    )
    check(targets.managed and targets.application == app.resolve(),
          "managed uninstall must target the complete versioned app root")
    check(targets.command_directory == (app / "bin").resolve(),
          "managed PATH cleanup must target only the stable wrapper directory")
    check(uninstall.validate_removal(targets, delete_data=False) is None,
          "default uninstall must preserve the separate store")
    check(uninstall.validate_removal(targets, delete_data=True) == data.resolve(),
          "confirmed data deletion must target only the verified store")

    service.write_current(paths, "1.2.2", previous_version="1.2.3")
    paths.version("1.2.2").mkdir()
    expect_error(
        lambda: uninstall.discover_targets(
            prefix=runtime, package_file=package,
            data_root=data, platform_name="win32",
        ),
        "an inactive version may not uninstall the application",
    )
    service.write_current(paths, "1.2.3")

    # Corrupt lifecycle state must surface as UninstallError (the CLI's
    # --json contract), never as a raw service error.
    paths.current.write_text("{defekt", encoding="utf-8")
    expect_error(
        lambda: uninstall.discover_targets(
            prefix=runtime, package_file=package,
            data_root=data, platform_name="win32",
        ),
        "a corrupt current pointer must raise UninstallError instead of a "
        "raw service error",
    )
    service.write_current(paths, "1.2.3")
    paths.state.write_text("[]", encoding="utf-8")
    expect_error(
        lambda: uninstall.discover_targets(
            prefix=runtime, package_file=package,
            data_root=data, platform_name="win32",
        ),
        "a corrupt install state must raise UninstallError instead of a "
        "raw service error",
    )
    service.write_state(paths, {
        "data_dir": os.fspath(data), "host": "0.0.0.0", "port": 8000,
        "python_runtime": service.managed_python_state(
            paths, "3.13.14", "0" * 64,
        ),
    })

    orchestration = []
    preview = uninstall.perform_uninstall(
        targets, delete_data=False, dry_run=True, interactive=False,
    )
    check(preview["status"] == "dry_run" and app.exists() and data.exists(),
          "dry-run must not mutate app or data")

    def fake_autostart(target, *, interactive):
        orchestration.append("service")
        return True

    def fake_path(directory):
        orchestration.append("path")
        return True

    def fake_registration(target):
        orchestration.append("registration")
        return True

    def fake_schedule(target, *, separate_data_target):
        orchestration.append("delete")
        check(separate_data_target is None, "keep-data must not reach helper")
        return root / "uninstall.log"

    result = uninstall.perform_uninstall(
        targets, delete_data=False, dry_run=False, interactive=False,
        autostart_remover=fake_autostart, path_remover=fake_path,
        registration_remover=fake_registration,
        removal_scheduler=fake_schedule,
    )
    check(orchestration == ["service", "path", "registration", "delete"],
          "service and exact shell integration must precede deletion")
    check(result["application_path"] == os.fspath(app.resolve())
          and result["data_path"] == os.fspath(data.resolve()),
          "machine result must distinguish app and preserved data")
    windows_commands = []
    original_which = uninstall.shutil.which
    try:
        uninstall.shutil.which = lambda name: "powershell.exe"
        removed = uninstall.remove_windows_autostart(
            runner=lambda command, **kwargs: windows_commands.append(command)
            or subprocess.CompletedProcess(
                command, 0, stdout="absent\n", stderr=""
            ),
        )
    finally:
        uninstall.shutil.which = original_which
    check(
        not removed and "fremde geplante Aufgabe namens Gusto"
        in windows_commands[0][-1],
        "Windows uninstall must refuse a foreign task-name collision",
    )

    removal_commands = []
    original_which = uninstall.shutil.which
    try:
        uninstall.shutil.which = lambda name: r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"
        removal_log = uninstall.schedule_removal(
            targets,
            separate_data_target=data,
            parent_pid=0,
            launcher=lambda command, **kwargs: removal_commands.append(
                (command, kwargs)
            ),
        )
    finally:
        uninstall.shutil.which = original_which
    removal_command = removal_commands[0][0]
    removal_options = removal_commands[0][1]
    helper = Path(removal_command[removal_command.index("-File") + 1])
    application_arg = Path(
        removal_command[removal_command.index("-Application") + 1]
    )
    data_arg = Path(removal_command[removal_command.index("-Data") + 1])
    check(removal_log.parent.resolve() == Path(tempfile.gettempdir()).resolve()
          and helper.suffix == ".ps1"
          and application_arg.resolve() == app.resolve()
          and data_arg.resolve() == data.resolve()
          and removal_options["creationflags"]
          == getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000),
          "Windows self-removal must use external PowerShell and include the full "
          "app-owned Python tree")
    helper.unlink(missing_ok=True)

    # systemd removal must be user-scoped and remove only the fixture unit.
    unit = root / ".config" / "systemd" / "user" / "gusto.service"
    unit.parent.mkdir(parents=True)
    unit.write_text("[Service]\n", encoding="utf-8")
    commands = []
    original_which = uninstall.shutil.which
    try:
        uninstall.shutil.which = lambda name: "/usr/bin/systemctl"
        removed = uninstall.remove_linux_autostart(
            service_path=unit,
            runner=lambda command, **kwargs: commands.append(command)
            or subprocess.CompletedProcess(command, 0, stdout="", stderr=""),
        )
    finally:
        uninstall.shutil.which = original_which
    check(removed and not unit.exists()
          and all("--user" in command for command in commands),
          "Linux uninstall must remove only the user service without sudo")

    # The obsolete flat ZIP-era virtualenv is no longer claimed by new code.
    legacy = root / "legacy"
    (legacy / "Scripts").mkdir(parents=True)
    (legacy / "Scripts" / "gusto.exe").write_bytes(b"x")
    (legacy / "pyvenv.cfg").write_text("home=x\n", encoding="utf-8")
    (legacy / "gusto.settings.json").write_text("{}", encoding="utf-8")
    legacy_package = legacy / "Lib" / "site-packages" / "gusto" / "uninstall.py"
    legacy_package.parent.mkdir(parents=True)
    legacy_package.write_text("# installed\n", encoding="utf-8")
    expect_error(
        lambda: uninstall.discover_targets(
            prefix=legacy, package_file=legacy_package,
            data_root=data, platform_name="win32",
        ),
        "obsolete flat virtualenvs must not keep a hidden compatibility path",
    )

print(f"OK - {checks} managed uninstall checks passed")
