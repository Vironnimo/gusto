"""Exercise checkout temp isolation across imports and process boundaries."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import gusto  # noqa: E402 - configure before creating the test's own sandbox


checks = 0


def check(value, message):
    global checks
    assert value, message
    checks += 1


def run(code, *, cwd, package_root, temporary):
    environment = {
        **os.environ,
        "PYTHONPATH": str(package_root),
        **{name: str(temporary) for name in ("TMPDIR", "TEMP", "TMP")},
    }
    return subprocess.run(
        [sys.executable, "-c", code], cwd=cwd, env=environment,
        capture_output=True, text=True, encoding="utf-8",
    )


def package_copy(destination):
    package = destination / "gusto"
    package.mkdir(parents=True)
    for name in ("__init__.py", "_temp.py"):
        shutil.copy2(ROOT / "gusto" / name, package / name)


check(Path(tempfile.gettempdir()) == ROOT / ".tmp",
      "direct test invocation must use repository temp")

with tempfile.TemporaryDirectory(prefix="temp-contract-") as directory:
    sandbox = Path(directory)
    caller_temp = sandbox / "caller temp"
    caller_temp.mkdir()
    child_code = (
        "import json,tempfile; "
        "d=tempfile.TemporaryDirectory(); "
        "print(json.dumps(d.name)); d.cleanup()"
    )
    code = f"""
import json, os, subprocess, sys, tempfile
# Populate Python's cache BEFORE Gusto is imported.
before = tempfile.gettempdir()
import gusto
with tempfile.TemporaryDirectory() as directory:
    child = subprocess.check_output([sys.executable, '-c', {child_code!r}], text=True)
    print(json.dumps({{'before': before, 'directory': directory,
        'child': json.loads(child),
        'environment': [os.environ[n] for n in ('TMPDIR', 'TEMP', 'TMP')],
        'core_loaded': 'gusto.core' in sys.modules}}))
"""
    result = run(code, cwd=sandbox, package_root=ROOT, temporary=caller_temp)
    check(result.returncode == 0, result.stdout + result.stderr)
    value = json.loads(result.stdout)
    check(Path(value["before"]) == caller_temp,
          "fixture must exercise an already cached foreign temp path")
    check(Path(value["directory"]).parent == ROOT / ".tmp",
          "source import must replace the cache even from another cwd")
    check(Path(value["child"]).parent == ROOT / ".tmp",
          "children without a Gusto import must inherit repository temp")
    check(all(Path(path) == ROOT / ".tmp" for path in value["environment"]),
          "Windows, POSIX and Node temp selectors must agree")
    check(not value["core_loaded"],
          "temp bootstrap must not resolve or load household data")
    check(not list(caller_temp.iterdir()), "no scratch files may leak to caller temp")

    # Installed code must not discover a repo from its working directory.
    installed = sandbox / "installed"
    package_copy(installed)
    # -c puts cwd first on sys.path; remove it explicitly for the installed probe.
    result = run("import sys; sys.path.pop(0)\n" + code,
                 cwd=ROOT, package_root=installed, temporary=caller_temp)
    check(result.returncode == 0, result.stdout + result.stderr)
    value = json.loads(result.stdout)
    check(Path(value["directory"]).parent == caller_temp
          and Path(value["child"]).parent == caller_temp,
          "installed imports must preserve the caller's temp policy")
    check(not (installed / ".tmp").exists(),
          "installed packages must not create a repository temp directory")

    # A broken checkout destination must fail instead of silently using /tmp.
    blocked = sandbox / "blocked checkout"
    package_copy(blocked)
    (blocked / "pyproject.toml").touch()
    (blocked / "scripts").mkdir()
    (blocked / "scripts/run_quality.py").touch()
    (blocked / ".tmp").write_text("blocked", encoding="utf-8")
    result = run("import gusto", cwd=sandbox, package_root=blocked,
                 temporary=caller_temp)
    check(result.returncode != 0 and ".tmp" in result.stderr,
          "an unusable checkout temp root must fail without system fallback")
    check(not list(caller_temp.iterdir()), "failed setup must not leak files")

    with tempfile.NamedTemporaryFile(dir=sandbox) as handle:
        check(Path(handle.name).parent == sandbox,
              "explicit same-filesystem atomic staging must still work")

print(f"OK - {checks} repository temp checks passed")
