"""Hermetic verified update and rollback checks."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, os.fspath(ROOT))

from gusto import service, update  # noqa: E402

checks = 0


def check(value, message):
    global checks
    assert value, message
    checks += 1


def fixture(root: Path, version: str) -> Path:
    archive = root / "gusto-release.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr(f"gusto-{version}/install.py", "# fixture\n")
        bundle.writestr(
            f"gusto-{version}/gusto-{version}-py3-none-any.whl", b"fixture",
        )
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    manifest = root / "gusto-release.json"
    manifest.write_text(json.dumps({
        "schema_version": 1, "version": version,
        "archive": archive.name, "sha256": digest,
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
        paths_arg, version, wheel, data_dir, *, server_url_value=None,
    ):
        target = paths_arg.version(version)
        target.mkdir(parents=True)
        (target / "verified").write_text(wheel.name, encoding="utf-8")
        orchestration.append(("install", version, data_dir, server_url_value))
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
    check(orchestration[:3] == [
        ("install", "1.1.0", data, "http://127.0.0.1:8000"),
        ("stop",), ("activate", "1.1.0"),
    ], "runtime must be prepared before the short service switch")
    check(health_expectations == [{
        "expected_version": "1.1.0", "expected_data_path": data,
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

    try:
        update.verify_archive(b"x", "0" * 64)
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

print(f"OK - {checks} update checks passed")
