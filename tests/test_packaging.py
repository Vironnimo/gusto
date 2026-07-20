"""Packaging smoke checks, runnable without pytest.

The web extra must be sufficient on a fresh installation. Importing the web
application alone is not enough in a developer environment because a missing
dependency may already be installed for unrelated reasons, so the declared
dependency list is checked directly as well.
"""
from __future__ import annotations

import json
import os
import re
import struct
import subprocess
import sys
import tempfile
import time
import venv
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, os.fspath(ROOT))

import install as gusto_installer  # noqa: E402


manifest = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
assert re.search(r'^name\s*=\s*"gusto"$', manifest, re.MULTILINE)
assert re.search(r'^gusto\s*=\s*"gusto\.cli:main"$', manifest, re.MULTILINE)
assert re.search(
    r'^gusto-autostart\s*=\s*"gusto\.cli:autostart_main"$',
    manifest,
    re.MULTILINE,
)
assert not (ROOT / "recipe").exists()

installer = ROOT / "install.py"
linux_autostart = ROOT / "deploy" / "install-systemd.sh"
windows_autostart = ROOT / "deploy" / "install-windows-task.ps1"
service_template = (ROOT / "deploy" / "gusto.service").read_text(encoding="utf-8")
linux_autostart_text = linux_autostart.read_text(encoding="utf-8")
windows_autostart_text = windows_autostart.read_text(encoding="utf-8")
assert installer.is_file()
assert linux_autostart.is_file()
assert windows_autostart.is_file()
assert "User=pi" not in service_template
assert "/home/pi/gusto" not in service_template
assert "@GUSTO_PROJECT@" not in service_template
assert 'Environment="GUSTO_HOME=' not in service_template
for marker in ["@GUSTO_USER@", "@GUSTO_HOME@", "@GUSTO_PYTHON@", "@GUSTO_PORT@"]:
    assert marker in service_template
assert 'project_dir/.venv' not in linux_autostart_text
assert 'Join-Path $ProjectDir ".venv"' not in windows_autostart_text
assert "$escapedData" not in windows_autostart_text
assert "--install-dir" in linux_autostart_text
assert "$InstallDir" in windows_autostart_text
assert 'Join-Path $InstallDir "Scripts\\gusto-autostart.exe"' in windows_autostart_text
assert "-Execute $AutostartLauncher" in windows_autostart_text
assert "-WindowStyle Hidden" not in windows_autostart_text
assert '"Scripts\\python.exe"' not in windows_autostart_text
assert (
    windows_autostart_text.index("Stop-ScheduledTask")
    < windows_autostart_text.index("& $bootstrap.Source")
    < windows_autostart_text.index("Register-ScheduledTask")
), "an active task must stop before its installed launcher is updated/replaced"


def pe_subsystem(executable: Path) -> int:
    """Read IMAGE_OPTIONAL_HEADER.Subsystem from a Windows PE launcher."""
    data = executable.read_bytes()
    assert data[:2] == b"MZ", f"Not a PE executable: {executable}"
    pe_offset = struct.unpack_from("<I", data, 0x3C)[0]
    assert data[pe_offset:pe_offset + 4] == b"PE\0\0"
    optional_header = pe_offset + 4 + 20
    return struct.unpack_from("<H", data, optional_header + 68)[0]

with tempfile.TemporaryDirectory(prefix="gusto-app-path-test-") as path_dir:
    path_root = Path(path_dir)
    fake_home = path_root / "home"
    windows_default = gusto_installer.default_install_dir(
        "win32", {"LOCALAPPDATA": os.fspath(path_root / "LocalAppData")}, fake_home,
    )
    linux_default = gusto_installer.default_install_dir("linux", {}, fake_home)
    assert windows_default == (path_root / "LocalAppData" / "Programs" / "Gusto").resolve()
    assert linux_default == (fake_home / ".local" / "opt" / "gusto").resolve()

    command_dir = path_root / "LocalAppData" / "Programs" / "Gusto" / "Scripts"
    updated_path, changed = gusto_installer.append_path_entry(
        os.pathsep.join([os.fspath(path_root / "existing"), "second"]), command_dir,
    )
    assert changed and updated_path.endswith(os.fspath(command_dir.resolve()))
    duplicate_path, changed = gusto_installer.append_path_entry(
        updated_path, command_dir,
    )
    assert not changed and duplicate_path == updated_path

    existing_runtime = path_root / "existing-runtime"
    existing_python = gusto_installer.venv_python(existing_runtime)
    existing_python.parent.mkdir(parents=True)
    existing_python.write_bytes(b"already installed")
    assert not gusto_installer.ensure_virtual_environment(existing_runtime)
    assert existing_python.read_bytes() == b"already installed"

with tempfile.TemporaryDirectory(prefix="gusto-installer-test-") as install_dir:
    dry_run = subprocess.run(
        [sys.executable, os.fspath(installer), "--dry-run", "--venv",
         os.fspath(Path(install_dir) / "venv")],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
    )
    assert dry_run.returncode == 0, dry_run.stdout + dry_run.stderr
    assert "CLI + Web" in dry_run.stdout
    assert "Projekt-Checkout" in dry_run.stdout
    assert not (Path(install_dir) / "venv").exists()

editable_dry_run = subprocess.run(
    [sys.executable, os.fspath(installer), "--dry-run", "--editable"],
    cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
)
assert editable_dry_run.returncode == 0, (
    editable_dry_run.stdout + editable_dry_run.stderr
)
assert os.fspath((ROOT / ".venv").resolve()) in editable_dry_run.stdout

with tempfile.TemporaryDirectory(prefix="gusto-migration-test-") as migration_dir:
    migration_root = Path(migration_dir)
    source = migration_root / "checkout"
    destination = migration_root / "user-data"
    (source / "recipes").mkdir(parents=True)
    (source / "data").mkdir()
    (source / "recipes" / "suppe.md").write_text("# Suppe\n", encoding="utf-8")
    (source / "data" / "recipes.json").write_text("[]\n", encoding="utf-8")
    assert gusto_installer.migrate_checkout_data(source, destination)
    assert (destination / "recipes" / "suppe.md").is_file()
    (destination / "recipes" / "suppe.md").write_text("changed\n", encoding="utf-8")
    assert not gusto_installer.migrate_checkout_data(source, destination)
    assert (destination / "recipes" / "suppe.md").read_text(encoding="utf-8") == "changed\n"

with tempfile.TemporaryDirectory(prefix="gusto-settings-test-") as settings_dir:
    settings_root = Path(settings_dir)
    data_root = settings_root / "user-data"
    settings_path = gusto_installer.write_instance_settings(
        settings_root / "application", data_root,
    )
    assert json.loads(settings_path.read_text(encoding="utf-8")) == {
        "data_dir": os.fspath(data_root.resolve()),
    }

optional_dependencies = re.search(
    r"\[project\.optional-dependencies\](.*?)(?=\n\[|\Z)", manifest, re.DOTALL
)
assert optional_dependencies is not None
web_dependencies = re.search(r"^web\s*=.*$", optional_dependencies.group(1), re.MULTILINE)
assert web_dependencies is not None
normalized = web_dependencies.group(0).lower()

assert '"python-multipart"' in normalized, (
    "The web extra must install python-multipart because the application uses "
    "HTML form routes."
)
assert '"pillow"' in normalized, (
    "The web extra must install Pillow because browser photo uploads are "
    "resized and stripped of metadata before storage."
)

# A regular wheel (not only an editable checkout) must contain everything the
# web application reads at runtime. Missing package data used to make an
# apparently successful installation fail as soon as gusto.web was imported.
with tempfile.TemporaryDirectory(prefix="gusto-wheel-test-") as wheel_dir:
    build = subprocess.run(
        [sys.executable, "-m", "pip", "wheel", "--no-deps",
         "--wheel-dir", wheel_dir, os.fspath(ROOT)],
        capture_output=True, text=True,
    )
    assert build.returncode == 0, build.stdout + build.stderr
    wheels = list(Path(wheel_dir).glob("gusto-*.whl"))
    assert len(wheels) == 1, f"Expected one Gusto wheel, found: {wheels}"
    with zipfile.ZipFile(wheels[0]) as archive:
        packaged = set(archive.namelist())

    required_assets = {
        "gusto/uninstall.py",
        "gusto/templates/base.html",
        "gusto/templates/list.html",
        "gusto/static/style.css",
        "gusto/static/app.js",
        "gusto/static/shopping-client.js",
        "gusto/static/manifest.webmanifest",
        "gusto/static/icons/icon-192.png",
        "gusto/static/icons/icon-512.png",
    }
    missing_assets = required_assets - packaged
    assert not missing_assets, (
        "The wheel is missing web runtime assets: " + ", ".join(sorted(missing_assets))
    )

    if sys.platform.startswith("win"):
        # Install the real wheel under a path containing spaces.  setuptools
        # must create two distinct PE launchers: regular Console for the CLI,
        # GUI for Task Scheduler autostart.
        runtime = Path(wheel_dir) / "runtime with spaces"
        venv.EnvBuilder(with_pip=True).create(runtime)
        runtime_python = runtime / "Scripts" / "python.exe"
        installed = subprocess.run(
            [os.fspath(runtime_python), "-m", "pip", "install", "--no-deps",
             os.fspath(wheels[0])],
            capture_output=True, text=True,
        )
        assert installed.returncode == 0, installed.stdout + installed.stderr
        console_launcher = runtime / "Scripts" / "gusto.exe"
        autostart_launcher = runtime / "Scripts" / "gusto-autostart.exe"
        assert pe_subsystem(console_launcher) == 3, (
            "the normal gusto launcher must remain a Console application"
        )
        assert pe_subsystem(autostart_launcher) == 2, (
            "the autostart launcher must be a windowless GUI application"
        )

        isolated_data = Path(wheel_dir) / "data with spaces"
        gusto_installer.write_instance_settings(runtime, isolated_data)
        launched = subprocess.run(
            [os.fspath(autostart_launcher), "--help"],
            cwd=runtime, capture_output=True,
        )
        assert launched.returncode == 0
        assert launched.stdout == b"" and launched.stderr == b""
        autostart_log = isolated_data / "gusto-autostart.log"
        assert "usage: gusto serve" in autostart_log.read_text(encoding="utf-8")

        uninstall_preview = subprocess.run(
            [os.fspath(console_launcher), "uninstall", "--keep-data",
             "--dry-run", "--json"],
            cwd=runtime, capture_output=True, text=True, encoding="utf-8",
        )
        assert uninstall_preview.returncode == 0, (
            uninstall_preview.stdout + uninstall_preview.stderr
        )
        preview = json.loads(uninstall_preview.stdout)
        assert preview["status"] == "dry_run" and not preview["delete_data"]
        assert Path(preview["application_path"]) == runtime.resolve()
        assert Path(preview["data_path"]) == isolated_data.resolve()
        assert console_launcher.is_file() and autostart_log.is_file()

        # Exercise self-removal from an actually active installed venv without
        # touching platform autostart/PATH. The detached helper must wait for
        # this interpreter and its Windows launchers before deleting the app.
        self_remove_code = (
            "import sys; from pathlib import Path; "
            "from gusto.uninstall import UninstallTargets, schedule_removal; "
            "root=Path(sys.prefix); "
            "targets=UninstallTargets(root, Path(sys.argv[1]), root/'Scripts', "
            "root/'gusto.settings.json', sys.platform); "
            "print(schedule_removal(targets, separate_data_target=None))"
        )
        self_remove = subprocess.run(
            [os.fspath(runtime_python), "-c", self_remove_code,
             os.fspath(isolated_data)],
            cwd=wheel_dir, capture_output=True, text=True, encoding="utf-8",
        )
        assert self_remove.returncode == 0, self_remove.stdout + self_remove.stderr
        self_remove_log = Path(self_remove.stdout.strip())
        deadline = time.monotonic() + 15
        while ((runtime.exists() or not self_remove_log.is_file())
               and time.monotonic() < deadline):
            time.sleep(0.1)
        assert not runtime.exists(), "the active installed runtime was not self-removed"
        assert isolated_data.is_dir(), "app-only self-removal deleted the data store"
        assert "completed" in self_remove_log.read_text(encoding="utf-8")
        self_remove_log.unlink()

with tempfile.TemporaryDirectory(prefix="gusto-task-path-test-") as task_dir:
    task_root = Path(task_dir)
    install_with_spaces = task_root / "Application With Spaces"
    data_with_spaces = task_root / "Data With Spaces"
    if sys.platform.startswith("win"):
        dry_task = subprocess.run(
            ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
             "-File", os.fspath(windows_autostart), "-DryRun",
             "-InstallDir", os.fspath(install_with_spaces),
             "-DataDir", os.fspath(data_with_spaces), "-Port", "8123"],
            cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
        )
        assert dry_task.returncode == 0, dry_task.stdout + dry_task.stderr
        expected_launcher = install_with_spaces / "Scripts" / "gusto-autostart.exe"
        assert f'"{expected_launcher}" --host 0.0.0.0 --port 8123' in dry_task.stdout

# A release bundle must install without a checkout or GitHub access. It contains
# one regular wheel, the standalone installer, and both optional autostart adapters.
with tempfile.TemporaryDirectory(prefix="gusto-release-test-") as release_dir:
    release_root = Path(release_dir)
    build_release = subprocess.run(
        [sys.executable, os.fspath(ROOT / "scripts" / "build_release.py"),
         "--output-dir", os.fspath(release_root)],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
    )
    assert build_release.returncode == 0, build_release.stdout + build_release.stderr
    archives = list(release_root.glob("gusto-*-release.zip"))
    assert len(archives) == 1, f"Expected one release archive, found: {archives}"
    with zipfile.ZipFile(archives[0]) as archive:
        archive.extractall(release_root / "extracted")
        bundled = set(archive.namelist())
    assert any(name.endswith("/install.py") for name in bundled)
    assert any(name.endswith("/deploy/install-systemd.sh") for name in bundled)
    assert any(name.endswith("/deploy/install-windows-task.ps1") for name in bundled)
    assert sum(name.endswith(".whl") for name in bundled) == 1
    assert not any(name.endswith("/gusto.settings.json") for name in bundled)
    with zipfile.ZipFile(archives[0]) as archive:
        systemd_script = next(
            info for info in archive.infolist()
            if info.filename.endswith("/deploy/install-systemd.sh")
        )
    assert (systemd_script.external_attr >> 16) & 0o111

    bundle_root = next((release_root / "extracted").iterdir())
    standalone_dry_run = subprocess.run(
        [sys.executable, os.fspath(bundle_root / "install.py"), "--dry-run",
         "--venv", os.fspath(release_root / "installed")],
        cwd=bundle_root, capture_output=True, text=True, encoding="utf-8",
    )
    assert standalone_dry_run.returncode == 0, (
        standalone_dry_run.stdout + standalone_dry_run.stderr
    )
    assert "Release-Paket" in standalone_dry_run.stdout
    assert not (release_root / "installed").exists()

from gusto.web import app  # noqa: E402

assert app is not None
print("OK - web packaging metadata and application import")
