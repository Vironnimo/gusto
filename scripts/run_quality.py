"""Run Gusto's standalone quality gates with one cross-platform command."""
from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import gusto  # noqa: E402, F401 - configure temp for this process and its children


SCRIPT_CHECKS = (
    "tests/test_backup.py",
    "tests/test_api.py",
    "tests/test_recipes.py",
    "tests/test_shopping.py",
    "tests/test_favorites.py",
    "tests/test_merge.py",
    "tests/test_concurrency.py",
    "tests/test_cli.py",
    "tests/test_web_render.py",
    "tests/test_agent_contract.py",
    "tests/test_skill_install.py",
    "tests/test_service.py",
    "tests/test_update.py",
    "tests/test_autostart.py",
    "tests/test_uninstall.py",
    "tests/test_paths.py",
    "tests/test_temp.py",
    "tests/test_packaging.py",
    "tests/test_ci.py",
    "tests/test_worktree.py",
    "tests/test_push.py",
)

BROWSER_CHECKS = (
    "scripts/browser_check.py",
    "scripts/pwa_check.py",
)

# A gate is one standalone script, not an individual assertion. The slowest
# local gate takes about 40 seconds; leave room for the slower CI platforms.
GATE_TIMEOUT_SECONDS = 180


def gate_environment(root: Path) -> dict[str, str]:
    """Keep default user paths and stores inside this gate's scratch directory."""
    environment = dict(os.environ)
    if "PLAYWRIGHT_BROWSERS_PATH" not in environment:
        if sys.platform == "win32":
            cache = Path(environment.get("LOCALAPPDATA", Path.home() / "AppData/Local"))
        elif sys.platform == "darwin":
            cache = Path.home() / "Library/Caches"
        else:
            cache = Path(environment.get("XDG_CACHE_HOME", Path.home() / ".cache"))
        environment["PLAYWRIGHT_BROWSERS_PATH"] = os.fspath(cache / "ms-playwright")
    home = root / "home"
    local = home / "AppData/Local"
    roaming = home / "AppData/Roaming"
    for directory in (home, local, roaming):
        directory.mkdir(parents=True, exist_ok=True)
    environment.update({
        "HOME": os.fspath(home),
        "USERPROFILE": os.fspath(home),
        "LOCALAPPDATA": os.fspath(local),
        "APPDATA": os.fspath(roaming),
        "GUSTO_HOME": os.fspath(root / "store"),
        "PYTHONUTF8": "1",
    })
    for name in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME", "GUSTO_URL"):
        environment.pop(name, None)
    return environment


def stop_process_tree(process: subprocess.Popen) -> None:
    """Stop a hung gate and the servers or CLI children it started."""
    if sys.platform == "win32":
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
        )
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    if process.poll() is None:
        process.kill()
    process.wait()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Gusto-Qualitätsgates reproduzierbar ausführen.",
    )
    parser.add_argument(
        "suite",
        choices=("scripts", "browser", "all", *SCRIPT_CHECKS, *BROWSER_CHECKS),
        help="Script-Tests, Browser-Checks, beide Gruppen oder ein einzelnes Gate.",
    )
    return parser


def run(paths: tuple[str, ...], *, timeout: float = GATE_TIMEOUT_SECONDS) -> int:
    for relative in paths:
        print(f"\n==> {relative}", flush=True)
        started = time.monotonic()
        with tempfile.TemporaryDirectory(prefix="quality-") as temporary:
            process = subprocess.Popen(
                [sys.executable, relative], cwd=ROOT,
                env=gate_environment(Path(temporary)),
                start_new_session=sys.platform != "win32",
            )
            try:
                returncode = process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                stop_process_tree(process)
                print(
                    f"\nFEHLER: {relative} hat das Zeitlimit von {timeout:g} s "
                    "ueberschritten; der Prozess und seine Kinder wurden beendet.",
                    file=sys.stderr,
                )
                return 124
            except BaseException:
                stop_process_tree(process)
                raise
        print(f"Dauer: {time.monotonic() - started:.2f} s", flush=True)
        if returncode:
            print(
                f"\nFEHLER: {relative} endete mit Status "
                f"{returncode}.",
                file=sys.stderr,
            )
            return returncode
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.suite in (*SCRIPT_CHECKS, *BROWSER_CHECKS):
        return run((args.suite,))
    if args.suite in {"scripts", "all"}:
        result = run(SCRIPT_CHECKS)
        if result:
            return result
    if args.suite in {"browser", "all"}:
        return run(BROWSER_CHECKS)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
