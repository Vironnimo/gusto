"""Static contracts for the reusable CI and gated release workflow."""
from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, os.fspath(ROOT))

from scripts import ci_smoke_install  # noqa: E402


QUALITY = ROOT / ".github/workflows/quality.yml"
RELEASE = ROOT / ".github/workflows/release.yml"
DEPENDABOT = ROOT / ".github/dependabot.yml"
BROWSER_CHECK = ROOT / "scripts/browser_check.py"
PWA_CHECK = ROOT / "scripts/pwa_check.py"
checks = 0


def check(condition: bool, message: str) -> None:
    global checks
    checks += 1
    if not condition:
        raise AssertionError(message)


def load(path: Path) -> dict:
    value = yaml.load(path.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    check(isinstance(value, dict), f"{path.name} must contain a YAML object")
    return value


quality = load(QUALITY)
release = load(RELEASE)
dependabot = load(DEPENDABOT)

quality_events = quality["on"]
check(
    all(event in quality_events for event in (
        "pull_request", "push", "workflow_dispatch", "workflow_call",
    )),
    "quality workflow must run normally and remain reusable",
)
matrix = quality["jobs"]["script-tests"]["strategy"]["matrix"]
check(
    set(matrix["os"]) == {"ubuntu-latest", "windows-latest"},
    "script tests must run on Linux and Windows",
)
check(
    {"3.10", "3.14"}.issubset(set(matrix["python"])),
    "script tests must cover minimum and current Python",
)
browser_matrix = quality["jobs"]["browser"]["strategy"]["matrix"]["os"]
check(
    set(browser_matrix) == {"ubuntu-latest", "windows-latest"},
    "real browser/PWA checks must run on Linux and Windows",
)

jobs = release["jobs"]
check(
    jobs["quality"]["uses"] == "./.github/workflows/quality.yml",
    "release must reuse the normal quality gates",
)
check(
    jobs["quality"]["needs"] == "validate-tag",
    "invalid or mismatched tags must fail before expensive quality gates",
)
check(
    jobs["build"]["needs"] == "quality",
    "release build must wait for all quality gates",
)
smoke_matrix = jobs["smoke-install"]["strategy"]["matrix"]["include"]
check(
    {entry["os"] for entry in smoke_matrix}
    == {"ubuntu-latest", "windows-latest"},
    "built release must be installed on Linux and Windows",
)
publish_needs = set(jobs["publish"]["needs"])
check(
    {"build", "smoke-install"}.issubset(publish_needs),
    "publication must wait for the built asset and both smoke installs",
)
publish_permissions = jobs["publish"]["permissions"]
check(
    publish_permissions.get("contents") == "write"
    and publish_permissions.get("id-token") == "write"
    and publish_permissions.get("attestations") == "write",
    "only the publish job should receive release and attestation authority",
)
release_text = RELEASE.read_text(encoding="utf-8")
check(
    '--repo "$GITHUB_REPOSITORY"' in release_text,
    "release publication must not depend on a local Git checkout",
)
check(
    any(
        update.get("package-ecosystem") == "github-actions"
        for update in dependabot["updates"]
    ),
    "commit-pinned GitHub actions must receive automated update PRs",
)

for path in (QUALITY, RELEASE):
    text = path.read_text(encoding="utf-8")
    for used in re.findall(r"^\s*uses:\s*(\S+)", text, re.MULTILINE):
        if used.startswith("./"):
            continue
        check(
            re.fullmatch(r"[^@\s]+@[0-9a-f]{40}", used) is not None,
            f"third-party action is not pinned to a full commit: {used}",
        )

for helper in (
    "scripts/run_quality.py",
    "scripts/ci_smoke_install.py",
    "scripts/ci_systemctl.py",
):
    result = subprocess.run(
        [sys.executable, "-m", "py_compile", helper],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    check(result.returncode == 0, result.stdout + result.stderr)

for browser_check in (BROWSER_CHECK, PWA_CHECK):
    check(
        "networkidle" not in browser_check.read_text(encoding="utf-8"),
        f"{browser_check.name} must not wait for network idle while SSE remains open",
    )

with tempfile.TemporaryDirectory(prefix="gusto-ci-contract-") as temporary:
    smoke_root = Path(temporary)
    config_home = smoke_root / "xdg-config"
    environment = {
        "HOME": os.fspath(smoke_root / "home"),
        "XDG_CONFIG_HOME": os.fspath(config_home),
        "PATH": "",
    }
    ci_smoke_install.prepare_linux_service_adapter(smoke_root, environment)
    check(
        Path(environment["GUSTO_CI_UNIT_PATH"])
        == config_home / "systemd/user/gusto.service",
        "Linux smoke adapter must follow the installer's XDG user-unit path",
    )

print(f"OK - {checks} CI contract checks passed")
