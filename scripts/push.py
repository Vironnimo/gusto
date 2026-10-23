#!/usr/bin/env python
"""Check the commit main points to completely, then push exactly that commit.

Commits and merges run static checks only (``scripts/commit_check.py``), so the
push is where every check runs (``.vorch/workflows/push-workflow.md``):

1. The remote's main must be an ancestor of local main; otherwise nothing is
   checked or pushed (never force-push, never pull on the user's behalf).
2. The commit is checked out detached in a private checkout under ``.worktrees/``,
   so other sessions' uncommitted work cannot affect the result, and every step in
   ``STEPS`` runs there. Each step's complete output goes to a log file.
3. Only when every step passed is that commit pushed to the remote's main, as a
   fast-forward. Commits that reached main meanwhile go with the next run.

``--no-push`` runs the checks only. Configure the steps for the project below.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

PRIMARY_BRANCH = "main"
REMOTE = "origin"
WORKTREES_DIR_NAME = ".worktrees"
TAIL_LINES = 40
"""Lines of a failing step's output the report shows; the log has everything."""


@dataclass(frozen=True)
class Step:
    name: str
    command: list[str]
    requires: tuple[str, ...] = ()
    """Steps that must pass first; when one failed, this step is skipped."""
    cwd: str = "."
    """Directory inside the checkout to run in."""


PY = ".venv/Scripts/python.exe" if sys.platform == "win32" else ".venv/bin/python"

# The complete checks, in order: the checkout's own environment first, then the
# lint the commit hook runs (over the whole project) and every quality gate of
# scripts/run_quality.py, real Chromium browser and PWA checks included. CI runs
# the same gates on Linux and Windows.
STEPS: list[Step] = [
    Step("venv", [sys.executable, "-m", "venv", ".venv"]),
    Step(
        "install",
        [PY, "-m", "pip", "install", "-q", "-e", ".[web,test,dev]"],
        requires=("venv",),
    ),
    Step("ruff", [PY, "-m", "ruff", "check", "."], requires=("install",)),
    Step("script gates", [PY, "scripts/run_quality.py", "scripts"], requires=("install",)),
    Step("chromium", [PY, "-m", "playwright", "install", "chromium"], requires=("install",)),
    Step(
        "browser gates",
        [PY, "scripts/run_quality.py", "browser"],
        requires=("install", "chromium"),
    ),
]


def git(*args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


def primary_root() -> Path:
    here = Path(__file__).resolve().parent
    common = git("rev-parse", "--path-format=absolute", "--git-common-dir", cwd=here).stdout
    return Path(common.strip()).parent


def remote_is_behind(root: Path, commit: str) -> tuple[bool, str]:
    """Fetch the remote's main; return whether *commit* contains it, and its commit."""
    fetched = git("fetch", "-q", REMOTE, PRIMARY_BRANCH, cwd=root)
    if fetched.returncode != 0:
        raise SystemExit(
            f"error: fetching {REMOTE}/{PRIMARY_BRANCH} failed: {fetched.stderr.strip()}"
        )
    remote = git("rev-parse", f"{REMOTE}/{PRIMARY_BRANCH}", cwd=root).stdout.strip()
    contained = git("merge-base", "--is-ancestor", remote, commit, cwd=root).returncode == 0
    return contained, remote


def remove_checkout(root: Path, checkout: Path) -> None:
    git("worktree", "remove", "--force", str(checkout), cwd=root)
    shutil.rmtree(checkout, ignore_errors=True)
    git("worktree", "prune", cwd=root)


def run_step(step: Step, checkout: Path, log: Path) -> bool:
    # A relative path names a file in the private checkout (such as its own .venv),
    # never one of the primary checkout.
    executable = step.command[0]
    if "/" in executable and not Path(executable).is_absolute():
        local = checkout / step.cwd / executable
        if sys.platform == "win32" and not local.exists():
            local = local.with_name(local.name + ".exe")
        executable = str(local)
    else:
        executable = shutil.which(executable) or executable
    started = time.monotonic()
    with log.open("w", encoding="utf-8", errors="replace") as handle:
        try:
            result = subprocess.run(
                [executable, *step.command[1:]],
                cwd=checkout / step.cwd,
                stdout=handle,
                stderr=subprocess.STDOUT,
                check=False,
            )
            passed = result.returncode == 0
        except OSError as exc:
            handle.write(f"{step.command[0]} could not run: {exc}\n")
            passed = False
    print(
        f"{'PASS' if passed else 'FAIL'}  {step.name} ({time.monotonic() - started:.0f} s)",
        flush=True,
    )
    return passed


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--no-push", action="store_true", help="run the checks only")
    args = parser.parse_args(argv)

    root = primary_root()
    commit = git("rev-parse", PRIMARY_BRANCH, cwd=root).stdout.strip()
    contained, remote = remote_is_behind(root, commit)
    if not contained:
        print(
            f"error: {REMOTE}/{PRIMARY_BRANCH} ({remote[:12]}) has commits that local "
            f"{PRIMARY_BRANCH} lacks; nothing was checked or pushed. Tell the user; never "
            "force-push."
        )
        return 1
    if remote == commit:
        print(f"{REMOTE}/{PRIMARY_BRANCH} already has {commit[:12]}; nothing to push.")
        return 0

    worktrees = root / WORKTREES_DIR_NAME
    for leftover in worktrees.glob(".push-*"):
        remove_checkout(root, leftover)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    checkout = worktrees / f".push-{stamp}"
    logs = worktrees / ".push-logs" / stamp
    logs.mkdir(parents=True, exist_ok=True)
    added = git("worktree", "add", "--detach", str(checkout), commit, cwd=root)
    if added.returncode != 0:
        print(f"error: the private checkout could not be created: {added.stderr.strip()}")
        return 1

    print(f"checking {commit[:12]} in {checkout.as_posix()}", flush=True)
    results: dict[str, bool | None] = {}
    try:
        for step in STEPS:
            if any(results.get(required) is not True for required in step.requires):
                results[step.name] = None
                print(f"SKIP  {step.name} (requires {', '.join(step.requires)})", flush=True)
                continue
            results[step.name] = run_step(
                step, checkout, logs / f"{step.name.replace(' ', '-')}.log"
            )
    finally:
        remove_checkout(root, checkout)

    failed = [name for name, passed in results.items() if passed is False]
    skipped = [name for name, passed in results.items() if passed is None]
    for name in failed:
        log = logs / f"{name.replace(' ', '-')}.log"
        lines = log.read_text(encoding="utf-8", errors="replace").splitlines()
        print(f"\n--- {name} (last {TAIL_LINES} lines; complete log: {log.as_posix()}) ---")
        print("\n".join(lines[-TAIL_LINES:]))
    if failed or skipped:
        print(
            f"\nNothing pushed: {len(failed)} failed, {len(skipped)} skipped. Fix every failure "
            "(.vorch/workflows/push-workflow.md) and run again."
        )
        return 1
    if args.no_push:
        print(f"\nEvery check passed for {commit[:12]}; not pushed (--no-push).")
        return 0

    contained, remote = remote_is_behind(root, commit)
    if not contained:
        print(f"error: {REMOTE}/{PRIMARY_BRANCH} moved during the checks; nothing pushed.")
        return 1
    pushed = subprocess.run(
        ["git", "push", REMOTE, f"{commit}:refs/heads/{PRIMARY_BRANCH}"], cwd=root, check=False
    )
    if pushed.returncode != 0:
        print("error: the push was refused; nothing pushed.")
        return 1
    print(f"\npushed {commit[:12]} to {REMOTE}/{PRIMARY_BRANCH}.")
    head = git("rev-parse", PRIMARY_BRANCH, cwd=root).stdout.strip()
    if head != commit:
        print(
            f"note: {PRIMARY_BRANCH} moved on to {head[:12]} meanwhile; it goes with the next run."
        )
    print("Now watch the CI run to green (.vorch/workflows/push-workflow.md, step 4).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
