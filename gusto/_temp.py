"""Keep checkout scratch files in one repository-local directory."""
from __future__ import annotations

import os
import tempfile
from pathlib import Path


def configure_checkout_temp() -> Path | None:
    """Configure Python and child processes, only when running from source.

    Installed packages have no repository and retain the caller's temp policy.
    Explicit same-filesystem staging directories are deliberately unaffected.
    A missing/unwritable checkout temp directory must never cause a fallback to
    system temp. Reset Python's cache too: a dependency may have populated it.
    """
    root = Path(__file__).resolve().parent.parent
    if not ((root / "pyproject.toml").is_file()
            and (root / "scripts" / "run_quality.py").is_file()):
        return None
    temporary = root / ".tmp"
    temporary.mkdir(exist_ok=True)
    path = os.fspath(temporary)
    for name in ("TMPDIR", "TEMP", "TMP"):
        os.environ[name] = path
    tempfile.tempdir = path
    return temporary
