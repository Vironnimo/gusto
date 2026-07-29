"""Static and hermetic no-elevation autostart checks."""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, os.fspath(ROOT))

from gusto import service  # noqa: E402

checks = 0


def check(value, message):
    global checks
    assert value, message
    checks += 1


linux = (ROOT / "deploy" / "install-systemd.sh").read_text(encoding="utf-8")
unit = (ROOT / "deploy" / "gusto.service").read_text(encoding="utf-8")
windows = (ROOT / "deploy" / "install-windows-task.ps1").read_text(encoding="utf-8")
check("systemctl --user" in linux and "sudo" in linux,
      "Linux adapter must use systemd --user and explicitly reject sudo")
check("sudo systemctl" not in linux and "/etc/systemd" not in linux,
      "Linux adapter must never touch system service state")
check("User=" not in unit and "WantedBy=default.target" in unit,
      "user unit must not select a system account")
check("-AtLogOn" in windows and "-RunLevel Limited" in windows,
      "Windows adapter must be a limited current-user logon task")
check("-RestartCount 5" in windows and "Start-ScheduledTask" in windows,
      "Windows adapter must restart failures and start immediately")
check("fremde geplante Aufgabe namens Gusto" in windows,
      "Windows adapter must refuse a foreign task-name collision")

with tempfile.TemporaryDirectory(prefix="gusto-autostart-") as temporary:
    root = Path(temporary)
    paths = service.managed_paths(root / "app")
    runtime = paths.version("1.0.0")
    (runtime / "Scripts").mkdir(parents=True)
    (runtime / "Scripts" / "gusto-autostart.exe").write_bytes(b"x")
    service.write_current(paths, "1.0.0")
    state = {"data_dir": os.fspath(root / "data"), "host": "0.0.0.0", "port": 8000}
    commands = []
    dry_result = service.install_user_service(
        paths, state, platform_name="win32", dry_run=True,
        runner=lambda command, **kwargs: commands.append(command)
        or subprocess.CompletedProcess(command, 0, stdout="", stderr=""),
    )
    command = dry_result["actions"][0]
    check(command[0].lower().endswith("powershell.exe")
          and "GUSTO_SERVICE_LAUNCHER" in command[-1],
          "Windows registration must receive the selected GUI launcher "
          "through the non-interpolated service environment")

print(f"OK - {checks} autostart checks passed")
