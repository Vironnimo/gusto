"""Hermetic managed and legacy uninstall lifecycle checks."""
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
    service.write_state(paths, {
        "data_dir": os.fspath(data), "host": "0.0.0.0", "port": 8000,
    })
    service.write_current(paths, "1.2.3")

    targets = uninstall.discover_targets(
        prefix=runtime, base_prefix=root / "python", package_file=package,
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
            prefix=runtime, base_prefix=root / "python", package_file=package,
            data_root=data, platform_name="win32",
        ),
        "an inactive version may not uninstall the application",
    )
    service.write_current(paths, "1.2.3")

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

    # Legacy flat virtualenv remains a safe compatibility path.
    legacy = root / "legacy"
    (legacy / "Scripts").mkdir(parents=True)
    (legacy / "Scripts" / "gusto.exe").write_bytes(b"x")
    (legacy / "pyvenv.cfg").write_text("home=x\n", encoding="utf-8")
    (legacy / "gusto.settings.json").write_text("{}", encoding="utf-8")
    legacy_package = legacy / "Lib" / "site-packages" / "gusto" / "uninstall.py"
    legacy_package.parent.mkdir(parents=True)
    legacy_package.write_text("# installed\n", encoding="utf-8")
    legacy_targets = uninstall.discover_targets(
        prefix=legacy, base_prefix=root / "python", package_file=legacy_package,
        data_root=data, platform_name="win32",
    )
    check(not legacy_targets.managed and legacy_targets.application == legacy.resolve(),
          "legacy flat virtualenv must remain explicitly uninstallable")

print(f"OK - {checks} managed uninstall checks passed")
