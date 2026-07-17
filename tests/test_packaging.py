"""Packaging smoke checks, runnable without pytest.

The web extra must be sufficient on a fresh installation. Importing the web
application alone is not enough in a developer environment because a missing
dependency may already be installed for unrelated reasons, so the declared
dependency list is checked directly as well.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, os.fspath(ROOT))


manifest = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
assert re.search(r'^name\s*=\s*"gusto"$', manifest, re.MULTILINE)
assert re.search(r'^gusto\s*=\s*"gusto\.cli:main"$', manifest, re.MULTILINE)
assert not (ROOT / "recipe").exists()

installer = ROOT / "deploy" / "install.sh"
service_template = (ROOT / "deploy" / "gusto.service").read_text(encoding="utf-8")
assert installer.is_file()
assert "User=pi" not in service_template
assert "/home/pi/gusto" not in service_template
for marker in ["@GUSTO_USER@", "@GUSTO_PROJECT@", "@GUSTO_HOME@", "@GUSTO_PYTHON@"]:
    assert marker in service_template

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

from gusto.web import app  # noqa: E402

assert app is not None
print("OK - web packaging metadata and application import")
