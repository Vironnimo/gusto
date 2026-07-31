"""Release asset and public bootstrap checks, without network access."""
from __future__ import annotations

import hashlib
import json
import os
import re
import struct
import subprocess
import sys
import tempfile
import venv
import zipfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, os.fspath(ROOT))

import install  # noqa: E402
import gusto  # noqa: E402
from gusto import service  # noqa: E402
from scripts import build_release  # noqa: E402

checks = 0


def check(value, message):
    global checks
    assert value, message
    checks += 1


def pe_subsystem(executable: Path) -> int:
    data = executable.read_bytes()
    check(data[:2] == b"MZ", f"not a PE executable: {executable}")
    pe_offset = struct.unpack_from("<I", data, 0x3C)[0]
    check(data[pe_offset:pe_offset + 4] == b"PE\0\0",
          f"invalid PE signature: {executable}")
    return struct.unpack_from("<H", data, pe_offset + 4 + 20 + 68)[0]


project = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
version = re.search(r'^version\s*=\s*"([^"]+)"$', project, re.MULTILINE)
check(version and version.group(1) == gusto.__version__,
      "package and runtime versions must agree")
for requirement in (
    "fastapi", "uvicorn[standard]", "jinja2", "markdown",
    "python-multipart", "pillow",
):
    check(f'"{requirement}"' in project.lower(),
          f"web runtime dependency missing: {requirement}")


for path in (
    ROOT / "install.py", ROOT / "install.ps1", ROOT / "install.sh",
    ROOT / "deploy" / "install-systemd.sh",
    ROOT / "deploy" / "install-windows-task.ps1",
    ROOT / ".github" / "workflows" / "release.yml",
):
    check(path.is_file(), f"release surface missing: {path}")

shell = (ROOT / "install.sh").read_text(encoding="utf-8")
powershell = (ROOT / "install.ps1").read_text(encoding="utf-8")
for text in (shell, powershell):
    check("gusto-release.json" in text and "sha256" in text.lower(),
          "bootstrap must fetch a manifest and verify SHA-256")
    check(not re.search(r"(?m)^\s*sudo\s", text.lower())
          and "-verb runas" not in text.lower(),
          "bootstrap must not request elevation")
check("gusto update" in shell, "bootstrap must identify gusto update as normal flow")
check("exit $LASTEXITCODE" not in powershell,
      "piped PowerShell bootstrap must not close the caller's shell")
check("Get-FileHash" not in powershell
      and "Security.Cryptography.SHA256" in powershell,
      "PowerShell bootstrap checksum must not require optional cmdlets")

with tempfile.TemporaryDirectory(prefix="gusto-release-test-") as temporary:
    output = Path(temporary)
    archive = build_release.build_release(output)
    manifest_path = output / build_release.MANIFEST_NAME
    checksum_path = output / build_release.CHECKSUM_NAME
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    check(archive.name == "gusto-release.zip"
          and manifest["archive"] == archive.name,
          "release archive must use a stable latest asset name")
    check(manifest["sha256"] == digest
          and checksum_path.read_text(encoding="utf-8").split()[0] == digest,
          "manifest and checksum asset must bind the exact archive")
    check((output / "install.ps1").read_bytes() == (ROOT / "install.ps1").read_bytes()
          and (output / "install.sh").read_bytes() == (ROOT / "install.sh").read_bytes(),
          "standalone bootstraps must be published beside release assets")
    with zipfile.ZipFile(archive) as bundle:
        names = bundle.namelist()
        for suffix in (
            "/install.py", "/install.ps1", "/install.sh",
            "/deploy/gusto.service", "/deploy/install-systemd.sh",
            "/deploy/install-windows-task.ps1", "/skill/gusto/SKILL.md",
            "/skill/gusto/references/cli.md",
            "/skill/gusto/references/installation.md",
            "/skill/gusto/references/telegram.md",
        ):
            check(any(name.endswith(suffix) for name in names),
                  f"release payload missing {suffix}")
        check(sum(name.endswith(".whl") for name in names) == 1,
              "release must contain exactly one wheel")
        wheel_name = next(name for name in names if name.endswith(".whl"))
        wheel_path = output / Path(wheel_name).name
        wheel_path.write_bytes(bundle.read(wheel_name))
        shell_info = next(info for info in bundle.infolist()
                          if info.filename.endswith("/install.sh"))
        check((shell_info.external_attr >> 16) & 0o111,
              "POSIX bootstrap must be executable after extraction")
    with zipfile.ZipFile(wheel_path) as wheel:
        packaged = set(wheel.namelist())
    for required in (
        "gusto/api.py", "gusto/client.py", "gusto/service.py",
        "gusto/update.py", "gusto/uninstall.py", "gusto/skill_install.py",
        "gusto/templates/base.html",
        "gusto/static/app.js", "gusto/static/shopping-client.js",
        "gusto/static/sw.js", "gusto/static/manifest.webmanifest",
        "gusto/static/icons/icon-192.png", "gusto/static/icons/icon-512.png",
    ):
        check(required in packaged, f"wheel runtime asset missing: {required}")
    for required in (
        "share/gusto/skill/gusto/SKILL.md",
        "share/gusto/skill/gusto/references/cli.md",
        "share/gusto/skill/gusto/references/installation.md",
        "share/gusto/skill/gusto/references/telegram.md",
    ):
        check(any(name.endswith(required) for name in packaged),
              f"wheel agent-skill asset missing: {required}")

    # Exercise the exact built wheel in a clean runtime. This proves that the
    # local command does not accidentally depend on the source checkout copy.
    skill_runtime = output / "skill runtime"
    venv.EnvBuilder(with_pip=True).create(skill_runtime)
    skill_python = service.runtime_python(skill_runtime)
    installed_skill_runtime = subprocess.run(
        [os.fspath(skill_python), "-m", "pip", "install", "--no-deps",
         os.fspath(wheel_path)],
        capture_output=True, text=True, encoding="utf-8",
    )
    check(installed_skill_runtime.returncode == 0,
          installed_skill_runtime.stdout + installed_skill_runtime.stderr)
    fake_home = output / "skill user"
    (fake_home / ".vbot").mkdir(parents=True)
    skill_environment = {
        **os.environ,
        "HOME": os.fspath(fake_home),
        "USERPROFILE": os.fspath(fake_home),
    }
    first_skill_install = subprocess.run(
        [os.fspath(skill_python), "-m", "gusto", "install-skill", "vbot", "--json"],
        capture_output=True, text=True, encoding="utf-8", env=skill_environment,
    )
    first_skill_result = (
        json.loads(first_skill_install.stdout)
        if first_skill_install.returncode == 0 else {}
    )
    installed_skill_file = fake_home / ".vbot/skills/gusto/SKILL.md"
    check(first_skill_install.returncode == 0
          and first_skill_result.get("status") == "installed"
          and first_skill_result.get("overwritten") is False
          and installed_skill_file.is_file(),
          first_skill_install.stdout + first_skill_install.stderr)
    installed_skill_file.write_text("stale", encoding="utf-8")
    second_skill_install = subprocess.run(
        [os.fspath(skill_python), "-m", "gusto", "install-skill", "vbot", "--json"],
        capture_output=True, text=True, encoding="utf-8", env=skill_environment,
    )
    second_skill_result = (
        json.loads(second_skill_install.stdout)
        if second_skill_install.returncode == 0 else {}
    )
    check(second_skill_install.returncode == 0
          and second_skill_result.get("overwritten") is True
          and installed_skill_file.read_text(encoding="utf-8") != "stale",
          second_skill_install.stdout + second_skill_install.stderr)

    if sys.platform.startswith("win"):
        powershell_exe = (
            subprocess.run(
                ["where.exe", "pwsh.exe"], capture_output=True, text=True,
                encoding="utf-8",
            ).stdout.splitlines() or [None]
        )[0]
        if powershell_exe:
            bootstrap = subprocess.run(
                [
                    powershell_exe, "-NoProfile", "-File",
                    os.fspath(ROOT / "install.ps1"),
                    "-ReleaseBase", os.fspath(output),
                    "-InstallDir", os.fspath(output / "bootstrap-app"),
                    "-DataDir", os.fspath(output / "bootstrap-data"),
                    "-DryRun", "-Json",
                ],
                capture_output=True, text=True, encoding="utf-8",
            )
            check(bootstrap.returncode == 0, bootstrap.stdout + bootstrap.stderr)
            check(json.loads(bootstrap.stdout)["status"] == "dry_run",
                  "PowerShell bootstrap must consume local release fixtures")
        runtime = output / "runtime with spaces"
        venv.EnvBuilder(with_pip=True).create(runtime)
        installed = subprocess.run(
            [
                os.fspath(runtime / "Scripts" / "python.exe"),
                "-m", "pip", "install", "--no-deps", os.fspath(wheel_path),
            ],
            capture_output=True, text=True, encoding="utf-8",
        )
        check(installed.returncode == 0, installed.stdout + installed.stderr)
        check(pe_subsystem(runtime / "Scripts" / "gusto.exe") == 3,
              "gusto launcher must use the Console subsystem")
        check(pe_subsystem(runtime / "Scripts" / "gusto-autostart.exe") == 2,
              "autostart launcher must use the GUI subsystem")
    else:
        environment = dict(os.environ)
        environment["PYTHON_BIN"] = sys.executable
        bootstrap = subprocess.run(
            [
                "bash", os.fspath(ROOT / "install.sh"),
                "--release-base", os.fspath(output),
                "--install-dir", os.fspath(output / "bootstrap-app"),
                "--data-dir", os.fspath(output / "bootstrap-data"),
                "--dry-run", "--json",
            ],
            capture_output=True, text=True, encoding="utf-8", env=environment,
        )
        check(bootstrap.returncode == 0, bootstrap.stdout + bootstrap.stderr)
        check(json.loads(bootstrap.stdout)["status"] == "dry_run",
              "POSIX bootstrap must consume local release fixtures")

with tempfile.TemporaryDirectory(prefix="gusto-dry-run-") as temporary:
    destination = Path(temporary) / "app"
    result = subprocess.run(
        [sys.executable, os.fspath(ROOT / "install.py"), "--dry-run",
         "--install-dir", os.fspath(destination), "--json"],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
    )
    check(result.returncode == 0 and not destination.exists(),
          "installer dry-run must be non-mutating")
    value = json.loads(result.stdout)
    check(value["status"] == "dry_run" and value["autostart"] in {
        "windows_task", "systemd_user",
    }, "installer dry-run must describe the user service")

    # A second bootstrap is not the update flow and must fail before touching
    # the existing managed root.
    destination.mkdir(parents=True)
    (destination / "current.json").write_text("{}\n", encoding="utf-8")
    (destination / "install-state.json").write_text("{}\n", encoding="utf-8")
    before = {
        path.name: path.read_bytes() for path in destination.iterdir()
    }
    repeated = subprocess.run(
        [sys.executable, os.fspath(ROOT / "install.py"),
         "--install-dir", os.fspath(destination)],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
    )
    after = {path.name: path.read_bytes() for path in destination.iterdir()}
    check(repeated.returncode == 1 and "gusto update" in repeated.stderr,
          "repeated bootstrap must direct the user to gusto update")
    check(before == after, "repeated bootstrap failure must not mutate the app")

with tempfile.TemporaryDirectory(prefix="gusto-install-rollback-") as temporary:
    root = Path(temporary)
    app_root = root / "claimed-app"
    data_root = root / "data"

    def fake_runtime(source, runtime, version, data, server_url):
        runtime.mkdir(parents=True)

    def fake_wrappers(application, bootstrap):
        wrapper = application / "bin" / "gusto.cmd"
        wrapper.parent.mkdir(parents=True, exist_ok=True)
        wrapper.write_text("@echo off\n", encoding="utf-8")
        return wrapper

    with (
        patch.object(install.sys, "platform", "win32"),
        patch.object(install, "install_source_runtime",
                     side_effect=fake_runtime),
        patch.object(install, "write_wrappers", side_effect=fake_wrappers),
        patch.object(install, "add_windows_user_path", return_value=True),
        patch.object(install, "remove_windows_user_path", return_value=True),
        patch.object(install, "remove_windows_app_registration"),
        patch.object(service, "install_user_service",
                     side_effect=service.ServiceError("forced start failure")),
        patch.object(service, "remove_user_service", return_value=True),
    ):
        failed = install.main([
            "--install-dir", os.fspath(app_root),
            "--data-dir", os.fspath(data_root),
        ])
    check(failed == 1 and not app_root.exists(),
          "failed first activation must release the complete newly claimed app root")
    check(data_root.exists(),
          "failed first activation must preserve the separate data root")

with tempfile.TemporaryDirectory(prefix="gusto-runtime-repair-") as temporary:
    root = Path(temporary)
    app_root = root / "managed-app"
    data_root = root / "data"
    paths = service.managed_paths(app_root)
    data_root.mkdir()
    service.write_state(paths, {
        "data_dir": os.fspath(data_root),
        "host": "0.0.0.0",
        "port": 9123,
        "manifest_url": "fixture",
    })
    service.write_current(paths, gusto.__version__)
    rebuilt = []

    def rebuild_runtime(source, runtime, version, data, server_url):
        rebuilt.append((version, server_url))
        scripts = runtime / "Scripts"
        scripts.mkdir(parents=True)
        (scripts / "python.exe").write_bytes(b"python")
        (runtime / ".gusto-runtime.json").write_text(
            json.dumps({"version": version, "verified": True}),
            encoding="utf-8",
        )

    with (
        patch.object(install.sys, "platform", "win32"),
        patch.object(install, "install_source_runtime",
                     side_effect=rebuild_runtime),
        patch.object(install, "write_wrappers", side_effect=fake_wrappers),
        patch.object(install, "add_windows_user_path", return_value=False),
        patch.object(service, "remove_user_service", return_value=True),
        patch.object(service, "install_user_service", return_value={"ok": True}),
        patch.object(service, "wait_for_health", return_value={"ok": True}),
    ):
        repaired = install.main([
            "--repair", "--install-dir", os.fspath(app_root),
        ])
    repaired_settings = json.loads(
        (paths.version(gusto.__version__) / "gusto.settings.json").read_text(
            encoding="utf-8",
        )
    )
    check(repaired == 0
          and rebuilt == [(gusto.__version__, "http://127.0.0.1:9123")],
          "repair must reconstruct a missing active runtime from matching payload")
    check(repaired_settings["server_url"] == "http://127.0.0.1:9123",
          "repair must restore the runtime's configured client URL")

print(f"OK - {checks} release packaging checks passed")
