"""Packaging smoke checks, runnable without pytest.

The web extra must be sufficient on a fresh installation. Importing the web
application alone is not enough in a developer environment because a missing
dependency may already be installed for unrelated reasons, so the declared
dependency list is checked directly as well.
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, os.fspath(ROOT))


manifest = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
assert re.search(r'^name\s*=\s*"gusto"$', manifest, re.MULTILINE)
assert re.search(r'^gusto\s*=\s*"gusto\.cli:main"$', manifest, re.MULTILINE)
assert not (ROOT / "recipe").exists()

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

from gusto.web import app  # noqa: E402

assert app is not None
print("OK - web packaging metadata and application import")
