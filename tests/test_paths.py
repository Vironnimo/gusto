"""Managed app/data path boundary checks."""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, os.fspath(ROOT))

import install  # noqa: E402
from gusto import service  # noqa: E402
import gusto.core.storage as core_storage  # noqa: E402

checks = 0


def check(value, message):
    global checks
    assert value, message
    checks += 1


with tempfile.TemporaryDirectory(prefix="gusto-paths-") as temporary:
    root = Path(temporary)
    home = root / "home"
    environment = {
        "LOCALAPPDATA": os.fspath(root / "Local"),
        "XDG_DATA_HOME": os.fspath(root / "xdg-data"),
        "XDG_CONFIG_HOME": os.fspath(root / "xdg-config"),
    }
    check(
        service.default_app_root("win32", environment, home)
        == (root / "Local" / "Programs" / "Gusto").resolve(),
        "Windows app must live under the current user's LOCALAPPDATA",
    )
    check(
        service.default_app_root("linux", environment, home)
        == (home / ".local" / "opt" / "gusto").resolve(),
        "Linux app must live under the current user's ~/.local/opt",
    )
    check(
        service.default_linux_unit_path(environment, home)
        == (root / "xdg-config" / "systemd" / "user" / "gusto.service").resolve(),
        "Linux unit must live in XDG_CONFIG_HOME/systemd/user",
    )
    check(
        install.default_data_dir("win32", environment, home)
        == (root / "Local" / "Gusto").resolve(),
        "Windows data must remain separate from the versioned app",
    )
    check(
        install.default_data_dir("linux", environment, home)
        == (root / "xdg-data" / "gusto").resolve(),
        "Linux data must honor XDG_DATA_HOME",
    )

    paths = service.managed_paths(root / "app")
    runtime = paths.version("2.3.4")
    runtime.mkdir(parents=True)
    service.write_current(paths, "2.3.4", previous_version="2.3.3")
    service.write_state(paths, {
        "data_dir": os.fspath(root / "data"),
        "host": "0.0.0.0", "port": 8000,
        "python_runtime": service.system_python_state(),
    })
    check(Path(service.read_current(paths)["runtime"]) == runtime,
          "pointer runtime must be derived inside versions/")
    check(paths.current.parent == paths.app_root
          and paths.state.parent == paths.app_root,
          "pointer and state must be stable siblings of versions/")
    try:
        service.managed_paths(Path.home())
    except service.ServiceError:
        checks += 1
    else:
        raise AssertionError("home directory must be rejected as an app root")

    configured = root / "portable"
    settings = root / "runtime" / "gusto.settings.json"
    settings.parent.mkdir()
    settings.write_text(json.dumps({"data_dir": os.fspath(configured)}),
                        encoding="utf-8")
    with patch.object(core_storage, "default_settings_path", return_value=settings), \
            patch.object(core_storage, "default_data_root", return_value=root / "default"), \
            patch.dict(os.environ, {"GUSTO_HOME": ""}):
        info = core_storage.storage_info()
        check(Path(info["path"]) == configured.resolve() and info["source"] == "settings",
              "each selected runtime's settings must preserve the shared store")
        # The explicit settings override has no public entry point; retain its
        # focused resolver contract as well as normal public discovery above.
        resolved, source, _ = core_storage._resolve_data_root(
            {}, root / "default", root / "legacy", settings_path=settings,
        )
        check(resolved == configured.resolve() and source == "settings",
              "an explicit resolver settings path must select that runtime's store")
        settings.write_text('{"data_dir": "relative-store"}', encoding="utf-8")
        check(core_storage.project_root() == (root / "relative-store").resolve(),
              "relative data settings must resolve beside the platform default")
        for invalid_settings in ("{", "[]", '{"data_dir": ""}'):
            settings.write_text(invalid_settings, encoding="utf-8")
            try:
                core_storage.storage_info()
            except ValueError as error:
                check(str(settings) in str(error),
                      "invalid settings must fail with file context instead of falling back")
            else:
                raise AssertionError("invalid settings unexpectedly selected a store")
            with patch.dict(os.environ, {"GUSTO_HOME": os.fspath(configured)}):
                info = core_storage.storage_info()
                check(Path(info["path"]) == configured.resolve()
                      and info["source"] == "environment",
                      "an explicit data root must bypass even invalid instance settings")
            check(settings.read_text(encoding="utf-8") == invalid_settings,
                  "path discovery must never repair or rewrite invalid settings")

    settings_written = install.write_instance_settings(
        root / "new-runtime", configured, "http://127.0.0.1:9123",
    )
    settings_value = json.loads(settings_written.read_text(encoding="utf-8"))
    check(settings_value == {
        "data_dir": os.fspath(configured.resolve()),
        "server_url": "http://127.0.0.1:9123",
    }, "runtime settings must preserve a custom service URL")

    nonempty = root / "unrelated-app-root"
    nonempty.mkdir()
    (nonempty / "personal.txt").write_text("keep", encoding="utf-8")
    try:
        install.validate_install_targets(
            nonempty, root / "separate-data", managed=False,
        )
    except ValueError:
        checks += 1
    else:
        raise AssertionError("nonempty foreign app root was accepted")

    for data_candidate in (
        root / "app" / "data-inside",
        root,
        root / "app",
    ):
        try:
            install.validate_install_targets(
                root / "app", data_candidate, managed=True,
            )
        except ValueError:
            checks += 1
        else:
            raise AssertionError(
                f"nested app/data paths were accepted: {data_candidate}"
            )

print(f"OK - {checks} managed path checks passed")
