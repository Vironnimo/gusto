"""Cross-platform data-directory and legacy-compatibility checks."""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, os.fspath(ROOT))

from gusto import core  # noqa: E402


checks = 0


def check(condition, message):
    global checks
    assert condition, message
    checks += 1


def main():
    sandbox = Path(tempfile.mkdtemp(prefix="gusto-paths-test-"))
    fake_home = sandbox / "home"

    windows = core.default_data_root(
        "win32", {"LOCALAPPDATA": os.fspath(sandbox / "LocalAppData")}, fake_home,
    )
    check(windows == (sandbox / "LocalAppData" / "Gusto").resolve(),
          "Windows must use LOCALAPPDATA/Gusto")

    linux = core.default_data_root(
        "linux", {"XDG_DATA_HOME": os.fspath(sandbox / "xdg")}, fake_home,
    )
    check(linux == (sandbox / "xdg" / "gusto").resolve(),
          "Linux must use XDG_DATA_HOME/gusto")
    linux_fallback = core.default_data_root("linux", {}, fake_home)
    check(linux_fallback == (fake_home / ".local" / "share" / "gusto").resolve(),
          "Linux must fall back to ~/.local/share/gusto")

    default = sandbox / "default"
    legacy = sandbox / "checkout"
    path, source, _ = core._resolve_data_root({}, default, legacy)
    check(path == default and source == "platform_default",
          "a fresh install must use the platform data directory")

    (legacy / "data").mkdir(parents=True)
    (legacy / "data" / "recipes.json").write_text("[]\n", encoding="utf-8")
    path, source, _ = core._resolve_data_root({}, default, legacy)
    check(path == legacy and source == "legacy",
          "an existing checkout store must remain active during migration")

    (default / "data").mkdir(parents=True)
    (default / "data" / "recipes.json").write_text("[]\n", encoding="utf-8")
    path, source, _ = core._resolve_data_root({}, default, legacy)
    check(path == default and source == "platform_default",
          "the platform store must win once it contains Gusto data")

    configured = sandbox / "portable"
    path, source, reported_default = core._resolve_data_root(
        {"GUSTO_HOME": os.fspath(configured)}, default, legacy,
    )
    check(path == configured.resolve() and source == "environment",
          "GUSTO_HOME must override every automatic location")
    check(reported_default == default,
          "storage diagnostics must retain the platform default")

    print(f"OK - {checks} cross-platform path checks passed")


if __name__ == "__main__":
    main()
