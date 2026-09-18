"""Hermetic verified update and rollback checks."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
import zipfile
from contextlib import redirect_stdout
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import StringIO
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, os.fspath(ROOT))

import gusto  # noqa: E402
import install  # noqa: E402
from gusto import service, update  # noqa: E402

checks = 0


def check(value, message):
    global checks
    assert value, message
    checks += 1


def fixture(
    root: Path,
    version: str,
    *,
    python_version: str = "3.13.14",
) -> Path:
    wheel = root / f"gusto-{version}-py3-none-any.whl"
    wheel.write_bytes(b"fixture-" + version.encode("ascii"))
    installer = root / "install.py"
    installer.write_text("# fixture\n", encoding="utf-8")
    python_runtime = root / "python-runtime-windows-x64.nupkg"
    with zipfile.ZipFile(python_runtime, "w") as package:
        package.writestr("tools/python.exe", b"python-fixture")
        package.writestr("tools/pythonw.exe", b"pythonw-fixture")
        package.writestr("tools/LICENSE.txt", b"license-fixture")
    def asset(path: Path, **extra: str) -> dict[str, str]:
        return {
            "name": path.name,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            **extra,
        }
    manifest = root / "gusto-release.json"
    manifest.write_text(json.dumps({
        "schema_version": 2,
        "version": version,
        "minimum_system_python": "3.10",
        "assets": {
            "wheel": asset(wheel),
            "installer": asset(installer),
            "windows_bootstrap": asset(installer),
            "linux_bootstrap": asset(installer),
            "windows_python_x64": asset(
                python_runtime, version=python_version, layout="tools",
            ),
        },
    }), encoding="utf-8")
    return manifest


with tempfile.TemporaryDirectory(prefix="gusto-update-") as temporary:
    root = Path(temporary)
    app = root / "app"
    paths = service.managed_paths(app)
    old = paths.version("1.0.0")
    old.mkdir(parents=True)
    data = root / "data"
    data.mkdir()
    service.write_state(paths, {
        "data_dir": os.fspath(data), "host": "127.0.0.1", "port": 8000,
        "manifest_url": os.fspath(root / "gusto-release.json"),
        "python_runtime": service.system_python_state(),
    })
    service.write_current(paths, "1.0.0")
    manifest = fixture(root, "1.1.0")

    before = sorted(str(path.relative_to(app)) for path in app.rglob("*"))
    checked = update.run_update(app_root=app, manifest_url=os.fspath(manifest),
                                check=True)
    after = sorted(str(path.relative_to(app)) for path in app.rglob("*"))
    check(checked["status"] == "update_available"
          and checked["latest_version"] == "1.1.0",
          "--check must report a newer semantic version")
    check(before == after, "--check must not mutate the installation")

    orchestration = []

    def runtime_installer(
        paths_arg, version, wheel, data_dir, *, base_python,
        python_version, server_url_value=None,
    ):
        target = paths_arg.version(version)
        target.mkdir(parents=True)
        (target / "verified").write_text(wheel.name, encoding="utf-8")
        orchestration.append((
            "install", version, data_dir, server_url_value,
            base_python, python_version,
        ))
        return target

    def controller(action, **kwargs):
        orchestration.append((action,))
        return {"ok": True}

    def service_installer(paths_arg, state):
        orchestration.append(("activate", service.read_current(paths_arg)["version"]))
        return {"ok": True}

    health_expectations = []
    result = update.run_update(
        app_root=app, manifest_url=os.fspath(manifest),
        runtime_installer=runtime_installer,
        service_controller=controller,
        service_installer=service_installer,
        health_waiter=lambda url, **kwargs: (
            health_expectations.append(kwargs) or {"ok": True}
        ),
    )
    check(result["status"] == "updated"
          and service.read_current(paths)["version"] == "1.1.0",
          "verified update must atomically select the new runtime")
    python_state = service.read_state(paths)["python_runtime"]
    check(orchestration[:3] == [
        (
            "install", "1.1.0", data.resolve(), "http://127.0.0.1:8000",
            Path(python_state["path"]), python_state["version"],
        ),
        ("stop",), ("activate", "1.1.0"),
    ], "runtime must be prepared before the short service switch")
    check(health_expectations == [{
        "expected_version": "1.1.0", "expected_data_path": data.resolve(),
    }], "activation health must bind version and data store")
    check(old.exists() and paths.version("1.1.0").exists(),
          "current and previous versions must remain side by side")
    check(service.read_state(paths)["data_dir"] == os.fspath(data),
          "updates must preserve the data path")

    bad_archive = root / "bad.zip"
    with zipfile.ZipFile(bad_archive, "w") as bundle:
        bundle.writestr("../escape.txt", "bad")
    try:
        update.safe_extract(bad_archive, root / "unsafe")
    except update.UpdateError:
        checks += 1
    else:
        raise AssertionError("ZIP traversal must be rejected")

    rollback_manifest = fixture(root, "1.2.0")
    health_count = 0

    def failing_health(url, **kwargs):
        global health_count
        health_count += 1
        if health_count == 1:
            raise service.ServiceError("forced health failure")
        return {"ok": True}

    try:
        update.run_update(
            app_root=app, manifest_url=os.fspath(rollback_manifest),
            runtime_installer=runtime_installer,
            service_controller=controller,
            service_installer=service_installer,
            health_waiter=failing_health,
        )
    except update.UpdateError as error:
        check("wiederhergestellt" in str(error),
              "activation failure must report successful rollback")
    else:
        raise AssertionError("forced health failure must fail the update")
    check(service.read_current(paths)["version"] == "1.1.0"
          and not paths.version("1.2.0").exists(),
          "rollback must restore the pointer and discard failed runtime")

    managed_root = root / "managed-python-update"
    managed_paths = service.managed_paths(managed_root / "app")
    managed_data = managed_root / "data"
    managed_data.mkdir(parents=True)
    old_python = service.managed_python_root(managed_paths, "3.13.14")
    old_python.mkdir(parents=True)
    (old_python / "python.exe").write_bytes(b"old-python")
    old_runtime = managed_paths.version("1.0.0")
    old_runtime.mkdir(parents=True)
    (old_runtime / ".gusto-runtime.json").write_text(json.dumps({
        "version": "1.0.0", "verified": True,
        "python_runtime": "3.13.14",
    }), encoding="utf-8")
    service.write_state(managed_paths, {
        "data_dir": os.fspath(managed_data),
        "host": "127.0.0.1",
        "port": 8010,
        "manifest_url": "fixture",
        "python_runtime": service.managed_python_state(
            managed_paths, "3.13.14", "1" * 64,
        ),
    })
    service.write_current(managed_paths, "1.0.0")

    def fake_python_installer(paths_arg, source, version, sha256, **kwargs):
        target = service.managed_python_root(paths_arg, version)
        target.mkdir(parents=True, exist_ok=True)
        executable = target / "python.exe"
        executable.write_bytes(b"managed-python")
        return executable

    def managed_runtime_installer(
        paths_arg, version, wheel, data_dir, *, base_python,
        python_version, server_url_value=None,
    ):
        target = paths_arg.version(version)
        target.mkdir(parents=True)
        (target / ".gusto-runtime.json").write_text(json.dumps({
            "version": version, "verified": True,
            "python_runtime": python_version,
        }), encoding="utf-8")
        return target

    managed_manifest = fixture(
        managed_root, "1.1.0", python_version="3.14.0",
    )
    with patch.object(
        update.service, "install_managed_python",
        side_effect=fake_python_installer,
    ):
        managed_result = update.run_update(
            app_root=managed_paths.app_root,
            manifest_url=os.fspath(managed_manifest),
            runtime_installer=managed_runtime_installer,
            service_controller=lambda *args, **kwargs: {"ok": True},
            service_installer=lambda *args, **kwargs: {"ok": True},
            health_waiter=lambda *args, **kwargs: {"ok": True},
        )
    managed_state = service.read_state(managed_paths)
    check(managed_result["status"] == "updated"
          and managed_state["python_runtime"]["version"] == "3.14.0"
          and service.managed_python_root(
              managed_paths, "3.14.0",
          ).is_dir()
          and old_python.is_dir(),
          "managed update must activate new Python while retaining rollback Python")

    rollback_python_manifest = fixture(
        managed_root, "1.2.0", python_version="3.15.0",
    )
    managed_health_calls = 0

    def fail_managed_activation(*args, **kwargs):
        global managed_health_calls
        managed_health_calls += 1
        if managed_health_calls == 1:
            raise service.ServiceError("forced managed activation failure")
        return {"ok": True}

    with patch.object(
        update.service, "install_managed_python",
        side_effect=fake_python_installer,
    ):
        try:
            update.run_update(
                app_root=managed_paths.app_root,
                manifest_url=os.fspath(rollback_python_manifest),
                runtime_installer=managed_runtime_installer,
                service_controller=lambda *args, **kwargs: {"ok": True},
                service_installer=lambda *args, **kwargs: {"ok": True},
                health_waiter=fail_managed_activation,
            )
        except update.UpdateError:
            checks += 1
        else:
            raise AssertionError("managed Python activation failure must roll back")
    check(service.read_state(managed_paths)["python_runtime"]["version"] == "3.14.0"
          and service.read_current(managed_paths)["version"] == "1.1.0"
          and not service.managed_python_root(managed_paths, "3.15.0").exists(),
          "rollback must restore Python state and remove its unused runtime")

    try:
        update.verify_asset(b"x", "0" * 64)
    except update.UpdateError:
        checks += 1
    else:
        raise AssertionError("checksum mismatch must fail")

    stage_paths = service.managed_paths(root / "staging-app")
    stage_paths.versions.mkdir(parents=True)
    abandoned = stage_paths.versions / ".staging-2.0.0-abandoned"
    abandoned.mkdir()
    wheel = root / "gusto-2.0.0-py3-none-any.whl"
    wheel.write_bytes(b"fixture")

    def environment_builder(target):
        (target / ("Scripts" if os.name == "nt" else "bin")).mkdir(
            parents=True,
        )
        service.runtime_python(target).write_bytes(b"python")

    installed = update.install_runtime(
        stage_paths, "2.0.0", wheel, data,
        base_python=Path(sys.executable),
        python_version="3.13.14",
        server_url_value="http://127.0.0.1:9123",
        environment_builder=environment_builder,
        runner=lambda command, **kwargs: subprocess.CompletedProcess(
            command, 0, stdout="2.0.0\n", stderr="",
        ),
    )
    settings = json.loads(
        (installed / "gusto.settings.json").read_text(encoding="utf-8")
    )
    check(installed == stage_paths.version("2.0.0")
          and not abandoned.exists()
          and not list(stage_paths.versions.glob(".staging-*")),
          "runtime must publish by atomic staging rename and recover abandoned staging")
    check(settings["server_url"] == "http://127.0.0.1:9123"
          and settings["data_dir"] == os.fspath(data.resolve()),
          "updated runtime must retain the configured server and data locations")

    with update.update_lock(paths):
        try:
            with update.update_lock(paths):
                raise AssertionError("parallel lock unexpectedly acquired")
        except update.UpdateError as error:
            check(error.result["status"] == "update_locked",
                  "parallel updates must fail with a machine-readable lock result")

    race_app = root / "fresh-lock-app"
    race_app.mkdir()
    barrier = root / "start-update-lock"
    race_code = (
        "import sys,time; from pathlib import Path; "
        "from gusto import service,update; "
        "paths=service.managed_paths(Path(sys.argv[1])); "
        "barrier=Path(sys.argv[2]); "
        "\nwhile not barrier.exists(): time.sleep(0.005)\n"
        "try:\n"
        "  with update.update_lock(paths):\n"
        "    print('acquired', flush=True); time.sleep(0.2)\n"
        "except update.UpdateError as error:\n"
        "  print(error.result['status'], flush=True)\n"
    )
    contenders = [
        subprocess.Popen(
            [sys.executable, "-c", race_code,
             os.fspath(race_app), os.fspath(barrier)],
            cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8",
        )
        for _ in range(4)
    ]
    barrier.write_text("go", encoding="utf-8")
    race_results = []
    for contender in contenders:
        stdout, stderr = contender.communicate(timeout=10)
        check(contender.returncode == 0, stderr or stdout)
        race_results.append(stdout.strip())
    check(race_results.count("acquired") == 1
          and race_results.count("update_locked") == 3,
          "fresh cross-process update lock initialization must elect one owner "
          "without a Windows pre-lock write race")

# --- shared fakes for the installer hardening checks ---
    def fake_installer_wrappers(application, bootstrap):
        wrapper = application / "bin" / "gusto.cmd"
        wrapper.parent.mkdir(parents=True, exist_ok=True)
        wrapper.write_text("@echo off\n", encoding="utf-8")
        return wrapper

# ==== F1: rollback stops before restart ====
    rollback_app = root / "rollback-app"
    rollback_paths = service.managed_paths(rollback_app)
    rollback_paths.version("1.0.0").mkdir(parents=True)
    rollback_data = root / "rollback-data"
    rollback_data.mkdir(exist_ok=True)
    service.write_state(rollback_paths, {
        "data_dir": os.fspath(rollback_data), "host": "127.0.0.1", "port": 8031,
        "manifest_url": "fixture",
        "python_runtime": service.system_python_state(),
    })
    service.write_current(rollback_paths, "1.0.0")
    rollback_manifest = fixture(root, "1.1.0")
    events = []

    def rollback_runtime_installer(
        paths_arg, version, wheel, data_dir, *, base_python,
        python_version, server_url_value=None,
    ):
        target = paths_arg.version(version)
        target.mkdir(parents=True)
        events.append(("install", version))
        return target

    def rollback_controller(action, **kwargs):
        events.append((action,))
        return {"ok": True}

    def rollback_service_installer(paths_arg, state):
        events.append(("activate", service.read_current(paths_arg)["version"]))
        return {"ok": True}

    def switch_then_fail(url, **kwargs):
        events.append(("health", kwargs.get("expected_version")))
        if kwargs.get("expected_version") == "1.1.0":
            raise service.ServiceError("forced activation failure")

    try:
        update.run_update(
            app_root=rollback_app, manifest_url=os.fspath(rollback_manifest),
            runtime_installer=rollback_runtime_installer,
            service_controller=rollback_controller,
            service_installer=rollback_service_installer,
            health_waiter=switch_then_fail,
        )
    except update.UpdateError as error:
        check("wiederhergestellt" in str(error),
              "the forced activation failure must report a completed rollback")
    else:
        raise AssertionError("a forced activation failure must fail the update")
    check(events == [
        ("install", "1.1.0"), ("stop",), ("activate", "1.1.0"),
        ("health", "1.1.0"), ("stop",), ("activate", "1.0.0"),
        ("health", "1.0.0"),
    ], "the rollback must stop the failed instance before it restarts the "
       "previous runtime, so the restored version is the one that serves")
    check(service.read_current(rollback_paths)["version"] == "1.0.0"
          and not rollback_paths.version("1.1.0").exists(),
          "the rollback must restore the pointer and drop the failed runtime")
# ==== F1 end ====

# ==== F2: installer holds the app update lock ====
    installer_app = root / "installer-app"
    installer_data = root / "installer-data"
    lock_states = []

    def observing_runtime_installer(
        source, runtime, version, data_dir, server_url, **kwargs,
    ):
        try:
            with update.update_lock(service.managed_paths(runtime.parent.parent)):
                lock_states.append("free")
        except update.UpdateError:
            lock_states.append("held")

    with (
        patch.object(install, "install_source_runtime",
                     side_effect=observing_runtime_installer),
        patch.object(install, "write_wrappers",
                     side_effect=fake_installer_wrappers),
        patch.object(install, "add_windows_user_path", return_value=False),
        patch.object(service, "install_user_service", return_value={"ok": True}),
        patch.object(service, "wait_for_health", return_value={"ok": True}),
    ):
        installed_code = install.main([
            "--install-dir", os.fspath(installer_app),
            "--data-dir", os.fspath(installer_data),
        ])
    check(installed_code == 0 and lock_states == ["held"],
          "the installer must hold the app update lock while it builds and "
          "publishes the runtime")

    contended_paths = service.managed_paths(root / "contended-app")
    contended_paths.app_root.mkdir(parents=True)
    contended_paths.state.write_text("{}\n", encoding="utf-8")
    contended_paths.current.write_text("{}\n", encoding="utf-8")

    def contended_snapshot():
        # The held lock file cannot be read back on Windows, so its size is
        # the observable proof that the installer left it alone.
        snapshot = {}
        for item in contended_paths.app_root.iterdir():
            snapshot[item.name] = (
                item.stat().st_size if item.name == ".update.lock"
                else item.read_bytes()
            )
        return snapshot

    contended_output = StringIO()
    with update.update_lock(contended_paths), redirect_stdout(contended_output):
        contended_before = contended_snapshot()
        contended_code = install.main([
            "--repair", "--install-dir", os.fspath(contended_paths.app_root),
            "--data-dir", os.fspath(root / "contended-data"), "--json",
        ])
    contended_error = json.loads(contended_output.getvalue())["error"]
    contended_after = contended_snapshot()
    check(contended_code == 1 and "läuft bereits" in contended_error
          and contended_before == contended_after,
          "a concurrent update must stop the installer with a JSON error "
          "before it mutates the app root")
# ==== F2 end ====

# ==== F4: stale update lock recovery ====
    stale_paths = service.managed_paths(root / "stale-lock-app")
    stale_lock = stale_paths.app_root / ".update.lock"
    stale_lock.parent.mkdir(parents=True)
    stale_lock.write_bytes(b"")
    abandoned = time.time() - (update.LOCK_INIT_STALE_SECONDS + 60)
    os.utime(stale_lock, (abandoned, abandoned))
    try:
        with update.update_lock(stale_paths):
            pass
    except update.UpdateError as error:
        raise AssertionError(
            "an abandoned empty lock must be reclaimed instead of blocking "
            f"every later update: {error}"
        ) from error
    check(stale_lock.stat().st_size == 1,
          "a reclaimed lock must be re-initialized for the next holder")

    initializing_paths = service.managed_paths(root / "initializing-lock-app")
    initializing_lock = initializing_paths.app_root / ".update.lock"
    initializing_lock.parent.mkdir(parents=True)
    initializing_lock.write_bytes(b"")
    with patch.object(update, "LOCK_INIT_WAIT_SECONDS", 0.2):
        try:
            with update.update_lock(initializing_paths):
                raise AssertionError(
                    "a lock that is initializing right now must never be "
                    "reclaimed"
                )
        except update.UpdateError:
            checks += 1
    check(initializing_lock.exists() and initializing_lock.stat().st_size == 0,
          "only a lock that stayed empty past the safe period may be removed")
# ==== F4 end ====

# ==== F5: repair verifies runtime removal ====
    repair_paths = service.managed_paths(root / "repair-app")
    repair_data = root / "repair-data"
    repair_data.mkdir()
    repair_runtime = repair_paths.version(gusto.__version__)
    repair_runtime.mkdir(parents=True)
    (repair_runtime / "leftover.txt").write_text("partial", encoding="utf-8")
    service.write_state(repair_paths, {
        "data_dir": os.fspath(repair_data), "host": "127.0.0.1", "port": 8021,
        "manifest_url": "fixture",
        "python_runtime": service.system_python_state(),
    })
    service.write_current(repair_paths, gusto.__version__)
    rebuilt = []
    repair_output = StringIO()

    def repairing_runtime_installer(
        source, runtime, version, data_dir, server_url, **kwargs,
    ):
        rebuilt.append(version)

    def blocked_rmtree(target, *args, **kwargs):
        return None

    with (
        patch.object(install, "install_source_runtime",
                     side_effect=repairing_runtime_installer),
        patch.object(install.shutil, "rmtree", side_effect=blocked_rmtree),
        patch.object(install, "write_wrappers",
                     side_effect=fake_installer_wrappers),
        patch.object(install, "add_windows_user_path", return_value=False),
        patch.object(service, "remove_user_service", return_value=True),
        patch.object(service, "install_user_service", return_value={"ok": True}),
        patch.object(service, "wait_for_health", return_value={"ok": True}),
        redirect_stdout(repair_output),
    ):
        repair_code = install.main([
            "--repair", "--install-dir", os.fspath(repair_paths.app_root),
            "--json",
        ])
    repair_result = json.loads(repair_output.getvalue())
    check(repair_code == 1 and not rebuilt
          and "beenden" in str(repair_result.get("error", "")),
          "a runtime directory that survived removal must abort the repair "
          "with an actionable error instead of rebuilding over it")
    check(repair_runtime.exists(),
          "the aborted repair must leave the locked runtime untouched")
# ==== F5 end ====

# ==== F5b: venv marker guard ====
    guarded_runtime = root / "guarded-runtime"
    guarded_python = install.venv_python(guarded_runtime)
    guarded_python.parent.mkdir(parents=True)
    guarded_python.write_bytes(b"stale-python")
    venv_calls = []

    class FakeServiceLoader:
        @staticmethod
        def create_virtual_environment(base, target):
            venv_calls.append(target)
            python = install.venv_python(target)
            python.parent.mkdir(parents=True, exist_ok=True)
            python.write_bytes(b"python")
            (target / "pyvenv.cfg").write_text("home=x\n", encoding="utf-8")

    with (
        patch.object(install, "load_service_module",
                     return_value=FakeServiceLoader),
        patch.object(install.subprocess, "run",
                     return_value=subprocess.CompletedProcess([], 0, "", "")),
    ):
        install.install_source_runtime(
            ROOT, guarded_runtime, "9.9.9", root / "guarded-data",
            "http://127.0.0.1:8123", base_python=Path(sys.executable),
            python_version="3.13.14",
        )
    check(venv_calls == [guarded_runtime],
          "a runtime directory without its venv marker must be rebuilt "
          "instead of reused")
# ==== F5b end ====

# ==== F6: download retries ====
    served = []

    class DownloadHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            served.append(self.path)
            if self.path == "/flaky" and served.count("/flaky") == 1:
                self.send_error(503, "try later")
                return
            if self.path == "/missing":
                self.send_error(404, "not here")
                return
            body = b"verified-release-bytes"
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), DownloadHandler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    location = f"https://127.0.0.1:{server.server_address[1]}"

    def local_opener(request, **kwargs):
        return urllib.request.urlopen(
            request.full_url.replace("https://", "http://", 1), **kwargs,
        )

    try:
        with patch.object(update, "RETRY_BASE_DELAY_SECONDS", 0.0, create=True):
            payload = update.fetch_bytes(location + "/flaky", opener=local_opener)
            check(payload == b"verified-release-bytes",
                  "a transient download failure must be retried and succeed")
            check(served.count("/flaky") == 2,
                  "HTTP 503 must use exactly one retry before the successful "
                  "attempt")
            try:
                update.fetch_bytes(location + "/missing", opener=local_opener)
            except update.UpdateError as error:
                check("404" in str(error),
                      "a permanent download failure must name its HTTP status")
            else:
                raise AssertionError("a 404 download must not be retried")
        check(served.count("/missing") == 1,
              "4xx download failures must fail immediately without retries")
    finally:
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=5)
# ==== F6 end ====

# ==== F7: Windows wrapper quoting ====
    wrapper_app = root / "Gusto App's Dir"
    with patch.object(install.sys, "platform", "win32"):
        wrapper_path = install.write_wrappers(wrapper_app, Path(sys.executable))
    wrapper_text = wrapper_path.read_text(encoding="utf-8")
    check("$env:GUSTO_CURRENT" in wrapper_text
          and "GUSTO_CURRENT=%~dp0..\\current.json" in wrapper_text,
          "the Windows wrapper must pass the pointer path through the "
          "environment and read it in PowerShell")
    check("'" not in wrapper_text,
          "the Windows wrapper must not inline a single-quoted path that an "
          "apostrophe in the installation path could break")
# ==== F7 end ====

# ==== F8: invalid port exit code ====
    port_failures = []
    for port in ("0", "70000"):
        result = subprocess.run(
            [
                sys.executable, os.fspath(ROOT / "install.py"), "--json",
                "--install-dir", os.fspath(root / "port-app"),
                "--port", port,
            ],
            cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
        )
        parsed = json.loads(result.stdout)
        port_failures.append((
            result.returncode, parsed["ok"], "65535" in parsed["error"],
            result.stderr,
        ))
    check(port_failures == [(1, False, True, "")] * 2,
          "an invalid --port is an expected CLI failure: JSON on stdout and "
          "exit 1, never a parser exit code")
# ==== F8 end ====

print(f"OK - {checks} update checks passed")
