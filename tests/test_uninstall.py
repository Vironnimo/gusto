"""Hermetic uninstall lifecycle checks, runnable without pytest."""
from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import tempfile
from contextlib import redirect_stdout
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, os.fspath(ROOT))

from gusto import cli  # noqa: E402
from gusto import uninstall  # noqa: E402


checks = 0


def check(condition: object, message: str) -> None:
    global checks
    assert condition, message
    checks += 1


def expect_uninstall_error(call, message: str) -> None:
    try:
        call()
    except uninstall.UninstallError:
        check(True, message)
    else:
        raise AssertionError(message)


with tempfile.TemporaryDirectory(prefix="gusto-uninstall-test-") as temporary:
    root = Path(temporary)
    application = root / "Application With Spaces"
    command_directory = application / "Scripts"
    command_directory.mkdir(parents=True)
    (command_directory / "gusto.exe").write_bytes(b"launcher")
    (application / "gusto.settings.json").write_text(
        '{"data_dir": "unused"}\n', encoding="utf-8",
    )
    (application / "pyvenv.cfg").write_text("home = system-python\n", encoding="utf-8")
    package_file = application / "Lib" / "site-packages" / "gusto" / "uninstall.py"
    package_file.parent.mkdir(parents=True)
    package_file.write_text("# packaged\n", encoding="utf-8")

    data = root / "Data With Spaces"
    for name in ("recipes", "images", "data"):
        (data / name).mkdir(parents=True)
    (data / "recipes" / "suppe.md").write_text("# Suppe\n", encoding="utf-8")

    targets = uninstall.discover_targets(
        prefix=application,
        base_prefix=root / "System Python",
        package_file=package_file,
        data_root=data,
        platform_name="win32",
    )
    check(targets.application == application.resolve(),
          "the executing managed runtime must own the app deletion target")
    check(targets.data == data.resolve(),
          "the active store must be shown as the data target")
    check(uninstall.validate_removal(targets, delete_data=False) is None,
          "keep-data must never schedule the separate store for deletion")
    check(uninstall.validate_removal(targets, delete_data=True) == data.resolve(),
          "delete-data must target the exact verified Gusto store")

    venv_marker = application / "pyvenv.cfg"
    venv_marker.unlink()
    expect_uninstall_error(
        lambda: uninstall.discover_targets(
            prefix=application,
            base_prefix=root / "System Python",
            package_file=package_file,
            data_root=data,
            platform_name="win32",
        ),
        "a runtime without the virtual-environment marker must be preserved",
    )
    venv_marker.write_text("home = system-python\n", encoding="utf-8")

    expect_uninstall_error(
        lambda: uninstall.discover_targets(
            prefix=application,
            base_prefix=root / "System Python",
            package_file=ROOT / "gusto" / "uninstall.py",
            data_root=data,
            platform_name="win32",
        ),
        "a source checkout must never be self-deleted",
    )
    broad_targets = uninstall.UninstallTargets(
        application=application.resolve(), data=Path.home().resolve(),
        command_directory=command_directory.resolve(),
        settings=(application / "gusto.settings.json").resolve(),
        platform="win32",
    )
    expect_uninstall_error(
        lambda: uninstall.validate_removal(broad_targets, delete_data=True),
        "the user home must be rejected as an over-broad data target",
    )
    nested_targets = uninstall.UninstallTargets(
        application=application.resolve(), data=(application / "data-root").resolve(),
        command_directory=command_directory.resolve(),
        settings=(application / "gusto.settings.json").resolve(),
        platform="win32",
    )
    expect_uninstall_error(
        lambda: uninstall.validate_removal(nested_targets, delete_data=False),
        "keep-data must refuse a store nested inside the removed application",
    )

    other = root / "Other Scripts"
    current_path = os.pathsep.join([
        os.fspath(other), f'"{command_directory}"', os.fspath(root / "More"),
    ])
    updated_path, removed = uninstall.remove_path_entry(
        current_path, command_directory,
    )
    check(removed and os.fspath(command_directory) not in updated_path,
          "only the exact installed Gusto command directory must leave PATH")
    check(os.fspath(other) in updated_path and os.fspath(root / "More") in updated_path,
          "unrelated PATH entries must be preserved")

    parser = cli.build_parser()
    menu_args = parser.parse_args(["uninstall"])
    answers = iter(["2", "DATEN LÖSCHEN"])
    with redirect_stdout(io.StringIO()):
        menu_delete = cli._uninstall_choice(
            menu_args, targets, interactive=True,
            input_func=lambda prompt: next(answers),
        )
    check(menu_delete is True,
          "the interactive menu must require a second exact data confirmation")
    keep_args = parser.parse_args(["uninstall", "--keep-data", "--json"])
    check(cli._uninstall_choice(keep_args, targets, interactive=False) is False,
          "--keep-data must be non-interactive and agent-safe")
    delete_args = parser.parse_args([
        "uninstall", "--delete-data", "--yes", "--json",
    ])
    check(cli._uninstall_choice(delete_args, targets, interactive=False) is True,
          "--delete-data --yes must be non-interactive and agent-safe")
    unsafe_args = parser.parse_args(["uninstall", "--delete-data", "--json"])
    expect_uninstall_error(
        lambda: cli._uninstall_choice(unsafe_args, targets, interactive=False),
        "non-interactive data deletion must require --yes",
    )
    no_choice_args = parser.parse_args(["uninstall", "--json"])
    expect_uninstall_error(
        lambda: cli._uninstall_choice(no_choice_args, targets, interactive=False),
        "non-interactive uninstall must require an explicit scope",
    )

    preview = uninstall.perform_uninstall(
        targets, delete_data=True, dry_run=True, interactive=False,
    )
    check(preview["status"] == "dry_run" and application.exists() and data.exists(),
          "dry-run must report targets without changing app or data")

    orchestration: list[object] = []
    scheduled_log = root / "scheduled.log"

    def fake_autostart(target, *, interactive):
        orchestration.append(("autostart", target, interactive))
        return True

    def fake_path(directory):
        orchestration.append(("path", directory))
        return True

    def fake_schedule(target, *, separate_data_target):
        orchestration.append(("schedule", target, separate_data_target))
        return scheduled_log

    scheduled = uninstall.perform_uninstall(
        targets,
        delete_data=True,
        dry_run=False,
        interactive=False,
        autostart_remover=fake_autostart,
        path_remover=fake_path,
        removal_scheduler=fake_schedule,
    )
    check([entry[0] for entry in orchestration] == ["autostart", "path", "schedule"],
          "autostart and PATH cleanup must precede irreversible tree deletion")
    check(scheduled["status"] == "scheduled"
          and scheduled["log_path"] == os.fspath(scheduled_log),
          "scheduled JSON must expose the external completion log")

    # The Windows cleanup command is fixed and passes no interpolated path to a
    # shell.  Record it without touching the real Task Scheduler.
    recorded: list[list[str]] = []

    def fake_runner(command, **kwargs):
        recorded.append(command)
        return subprocess.CompletedProcess(command, 0, stdout="removed\n", stderr="")

    original_which = uninstall.shutil.which
    try:
        uninstall.shutil.which = lambda name: r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"
        removed_task = uninstall.remove_windows_autostart(runner=fake_runner)
    finally:
        uninstall.shutil.which = original_which
    check(removed_task and len(recorded) == 1,
          "Windows autostart cleanup must run once and report removal")
    check("Stop-ScheduledTask" in recorded[0][-1]
          and "Unregister-ScheduledTask" in recorded[0][-1],
          "Windows cleanup must stop the server before unregistering its task")

    linux_service = root / "etc" / "systemd" / "system" / "gusto.service"
    linux_service.parent.mkdir(parents=True)
    linux_service.write_text("[Service]\n", encoding="utf-8")
    linux_commands: list[list[str]] = []

    def fake_linux_runner(command, **kwargs):
        linux_commands.append(command)
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    original_which = uninstall.shutil.which
    try:
        uninstall.shutil.which = lambda name: f"/usr/bin/{name}"
        removed_service = uninstall.remove_linux_autostart(
            interactive=False,
            service_path=linux_service,
            runner=fake_linux_runner,
            effective_uid=lambda: 0,
        )
    finally:
        uninstall.shutil.which = original_which
    check(removed_service and len(linux_commands) == 3,
          "Linux cleanup must disable, remove, and reload the systemd unit")
    check(linux_commands[0][-2:] == ["--now", "gusto.service"]
          and linux_commands[2][-1] == "daemon-reload",
          "Linux cleanup must stop the service before removing its unit")

    # Exercise the actual one-shot helper against throwaway paths with spaces.
    helper_app = root / "Disposable App With Spaces"
    helper_data = root / "Disposable Data With Spaces"
    helper_app.mkdir()
    helper_data.mkdir()
    (helper_app / "file.txt").write_text("app", encoding="utf-8")
    (helper_data / "file.txt").write_text("data", encoding="utf-8")
    helper = uninstall.write_removal_helper()
    helper_log = root / "helper.log"
    helper_run = subprocess.run([
        sys.executable, "-I", os.fspath(helper), "0",
        os.fspath(helper_app), os.fspath(helper_data), os.fspath(helper_log),
        os.fspath(helper),
    ], capture_output=True, text=True, encoding="utf-8")
    check(helper_run.returncode == 0, helper_run.stdout + helper_run.stderr)
    check(not helper_app.exists() and not helper_data.exists(),
          "the helper must remove both explicitly selected throwaway trees")
    check(not helper.exists() and "completed" in helper_log.read_text(encoding="utf-8"),
          "the helper must clean itself and leave an observable completion log")

    keep_app = root / "Disposable App Only"
    kept_data = root / "Preserved Data"
    keep_app.mkdir()
    kept_data.mkdir()
    helper = uninstall.write_removal_helper()
    keep_log = root / "keep-helper.log"
    keep_run = subprocess.run([
        sys.executable, "-I", os.fspath(helper), "0",
        os.fspath(keep_app), "", os.fspath(keep_log), os.fspath(helper),
    ], capture_output=True, text=True, encoding="utf-8")
    check(keep_run.returncode == 0 and not keep_app.exists() and kept_data.exists(),
          "app-only helper mode must preserve the separate data directory")

    # Verify the command's stable JSON handoff without scheduling deletion.
    original_discover = cli.uninstall.discover_targets
    original_perform = cli.uninstall.perform_uninstall
    try:
        cli.uninstall.discover_targets = lambda: targets
        cli.uninstall.perform_uninstall = lambda *args, **kwargs: preview
        output = io.StringIO()
        with redirect_stdout(output):
            cli.main(["uninstall", "--delete-data", "--yes", "--json"])
        json_result = json.loads(output.getvalue())
    finally:
        cli.uninstall.discover_targets = original_discover
        cli.uninstall.perform_uninstall = original_perform
    check(json_result["status"] == "dry_run" and json_result["delete_data"] is True,
          "the agent-facing uninstall result must remain machine-readable")


print(f"OK - {checks} uninstall lifecycle checks passed")
