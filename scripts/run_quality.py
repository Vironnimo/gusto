"""Run Gusto's standalone quality gates with one cross-platform command."""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent

SCRIPT_CHECKS = (
    "tests/test_api.py",
    "tests/test_recipes.py",
    "tests/test_shopping.py",
    "tests/test_favorites.py",
    "tests/test_merge.py",
    "tests/test_concurrency.py",
    "tests/test_cli.py",
    "tests/test_service.py",
    "tests/test_update.py",
    "tests/test_autostart.py",
    "tests/test_uninstall.py",
    "tests/test_paths.py",
    "tests/test_packaging.py",
    "tests/test_ci.py",
)

BROWSER_CHECKS = (
    "scripts/browser_check.py",
    "scripts/pwa_check.py",
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Gusto-Qualitätsgates reproduzierbar ausführen.",
    )
    parser.add_argument(
        "suite",
        choices=("scripts", "browser", "all"),
        help="Script-Tests, echte Browser-Checks oder beide Gruppen.",
    )
    return parser


def run(paths: tuple[str, ...]) -> int:
    for relative in paths:
        print(f"\n==> {relative}", flush=True)
        result = subprocess.run(
            [sys.executable, relative],
            cwd=ROOT,
        )
        if result.returncode:
            print(
                f"\nFEHLER: {relative} endete mit Status "
                f"{result.returncode}.",
                file=sys.stderr,
            )
            return result.returncode
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.suite in {"scripts", "all"}:
        result = run(SCRIPT_CHECKS)
        if result:
            return result
    if args.suite in {"browser", "all"}:
        return run(BROWSER_CHECKS)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
