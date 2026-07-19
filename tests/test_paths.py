"""Cross-platform data-directory and legacy-compatibility checks."""
from __future__ import annotations

import json
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
    path, source, _ = core._resolve_data_root(
        {}, default, legacy, settings_path=None,
    )
    check(path == default and source == "platform_default",
          "a fresh install must use the platform data directory")

    (legacy / "data").mkdir(parents=True)
    (legacy / "data" / "recipes.json").write_text("[]\n", encoding="utf-8")
    path, source, _ = core._resolve_data_root(
        {}, default, legacy, settings_path=None,
    )
    check(path == legacy and source == "legacy",
          "an existing checkout store must remain active during migration")

    (default / "data").mkdir(parents=True)
    (default / "data" / "recipes.json").write_text("[]\n", encoding="utf-8")
    path, source, _ = core._resolve_data_root(
        {}, default, legacy, settings_path=None,
    )
    check(path == default and source == "platform_default",
          "the platform store must win once it contains Gusto data")

    configured = sandbox / "portable"
    path, source, reported_default = core._resolve_data_root(
        {"GUSTO_HOME": os.fspath(configured)}, default, legacy,
        settings_path=None,
    )
    check(path == configured.resolve() and source == "environment",
          "GUSTO_HOME must override every automatic location")
    check(reported_default == default,
          "storage diagnostics must retain the platform default")

    source_root = sandbox / "source"
    source_root.mkdir()
    (source_root / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
    source_settings = core.default_settings_path(source_root, sandbox / "prefix")
    check(source_settings == source_root / core.SETTINGS_FILENAME,
          "a source checkout must own its instance settings")
    installed_settings = core.default_settings_path(
        sandbox / "site-packages", sandbox / "installed",
    )
    check(installed_settings == sandbox / "installed" / core.SETTINGS_FILENAME,
          "an installed runtime must own its instance settings")

    settings_file = sandbox / "instance" / core.SETTINGS_FILENAME
    settings_file.parent.mkdir()
    settings_file.write_text(
        json.dumps({"data_dir": "gusto-dev"}), encoding="utf-8",
    )
    platform_default = sandbox / "LocalAppData" / "Gusto"
    settings_root, source, _ = core._resolve_data_root(
        {}, platform_default, legacy, settings_path=settings_file,
    )
    check(settings_root == (platform_default.parent / "gusto-dev").resolve()
          and source == "settings",
          "a relative setting must select a sibling of the platform data folder")

    environment_root = sandbox / "environment-wins"
    settings_root, source, _ = core._resolve_data_root(
        {"GUSTO_HOME": os.fspath(environment_root)}, platform_default, legacy,
        settings_path=settings_file,
    )
    check(settings_root == environment_root.resolve() and source == "environment",
          "GUSTO_HOME must override instance settings for isolated tests")

    settings_file.write_text("[]\n", encoding="utf-8")
    try:
        core._resolve_data_root(
            {}, platform_default, legacy, settings_path=settings_file,
        )
    except ValueError as error:
        check("JSON-Objekt erwartet" in str(error),
              "malformed instance settings must fail with a useful error")
    else:
        raise AssertionError("malformed instance settings must be rejected")

    print(f"OK - {checks} cross-platform path checks passed")


if __name__ == "__main__":
    main()
