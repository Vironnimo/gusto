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
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, os.fspath(ROOT))

import install as gusto_installer  # noqa: E402


manifest = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
assert re.search(r'^name\s*=\s*"gusto"$', manifest, re.MULTILINE)
assert re.search(r'^gusto\s*=\s*"gusto\.cli:main"$', manifest, re.MULTILINE)
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
