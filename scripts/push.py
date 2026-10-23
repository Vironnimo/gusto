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
   fast-forward. ``--tag v<version>`` also publishes exactly one annotated tag
   of that commit, atomically with main. No other tags are pushed.

``--no-push`` runs the checks only, even when main is already remote. Configure
the steps for the project below.
"""

from __future__ import annotations

import argparse
import errno
import re
import shutil
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
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
    Step("domain maps", [PY, "scripts/domain_maps.py", "--check"], requires=("venv",)),
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
    fetched = git("fetch", "-q", "--no-tags", REMOTE, PRIMARY_BRANCH, cwd=root)
    if fetched.returncode != 0:
        raise SystemExit(
            f"error: fetching {REMOTE}/{PRIMARY_BRANCH} failed: {fetched.stderr.strip()}"
        )
    remote = git("rev-parse", "FETCH_HEAD^{commit}", cwd=root).stdout.strip()
    contained = git("merge-base", "--is-ancestor", remote, commit, cwd=root).returncode == 0
    return contained, remote


def release_tag(root: Path, name: str, commit: str) -> str:
    """Capture an annotated release tag of the selected, version-matching commit."""
    if not re.fullmatch(r"v[0-9]+\.[0-9]+\.[0-9]+", name):
        raise ValueError(f"tag {name!r} must have the form vMAJOR.MINOR.PATCH")
    resolved = git("rev-parse", "--verify", f"refs/tags/{name}", cwd=root)
    if resolved.returncode:
        raise ValueError(f"local tag {name!r} does not exist")
    tag = resolved.stdout.strip()
    if git("cat-file", "-t", tag, cwd=root).stdout.strip() != "tag":
        raise ValueError(f"tag {name!r} must be annotated")
    target = git("rev-parse", "--verify", f"{tag}^{{commit}}", cwd=root)
    if target.returncode or target.stdout.strip() != commit:
        raise ValueError(f"tag {name!r} must point to {PRIMARY_BRANCH} at {commit[:12]}")
    project = git("show", f"{commit}:pyproject.toml", cwd=root)
    section = re.search(r"(?ms)^\[project\]\s*\n(.*?)(?=^\[|\Z)", project.stdout)
    version = re.search(
        r'''(?m)^version\s*=\s*["']([0-9]+\.[0-9]+\.[0-9]+)["']\s*(?:#.*)?$''',
        section.group(1) if section else "",
    )
    if project.returncode or not version or name != f"v{version.group(1)}":
        expected = f"v{version.group(1)}" if version else "a numeric project version"
        raise ValueError(f"tag {name!r} does not match {expected} in committed pyproject.toml")
    return tag


def remote_tag(root: Path, name: str, tag: str) -> bool:
    """Return whether the same tag is already published; refuse a different object."""
    reference = f"refs/tags/{name}"
    result = git("ls-remote", "--refs", REMOTE, reference, cwd=root)
    if result.returncode:
        raise ValueError(f"reading {REMOTE}/{reference} failed: {result.stderr.strip()}")
    published = dict(line.split(maxsplit=1)[::-1] for line in result.stdout.splitlines())
    current = published.get(reference)
    if current is not None and current != tag:
        raise ValueError(f"{REMOTE}/{reference} already names another object; tags are never overwritten")
    return current == tag


class PushInProgress(Exception):
    """Another invocation holds this repository's push lock."""


@contextmanager
def push_lock(worktrees: Path):
    """Serialize selection, FETCH_HEAD, checking and publication for this repo.

    The lock file stays in place: replacing it could let two processes lock
    different files with the same name. The OS releases the lock after a crash.
    """
    worktrees.mkdir(parents=True, exist_ok=True)
    with (worktrees / ".push.lock").open("a+b") as handle:
        try:
            if sys.platform == "win32":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            if error.errno in (errno.EACCES, errno.EAGAIN):
                raise PushInProgress from error
            raise
        try:
            yield
        finally:
            if sys.platform == "win32":
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def remove_checkout(root: Path, checkout: Path) -> bool:
    """Remove only this run's checkout; Git refusal never authorizes deletion."""
    removed = git("worktree", "remove", "--force", str(checkout), cwd=root)
    if removed.returncode:
        print(f"error: private checkout preserved at {checkout}: {removed.stderr.strip()}")
        return False
    return True


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
    parser.add_argument("--no-push", action="store_true",
                        help="run every check without publishing main or a tag")
    parser.add_argument(
        "--tag", metavar="vMAJOR.MINOR.PATCH",
        help="also publish this annotated tag of main after checking its package version; "
             "omit to publish only main",
    )
    args = parser.parse_args(argv)

    root = primary_root()
    try:
        with push_lock(root / WORKTREES_DIR_NAME):
            return run(args, root)
    except PushInProgress:
        print("error: another push check owns this repository; retry after it finishes.")
        return 1


def run(args: argparse.Namespace, root: Path) -> int:
    selected = git("rev-parse", "--verify", f"refs/heads/{PRIMARY_BRANCH}^{{commit}}", cwd=root)
    if selected.returncode:
        print(f"error: local {PRIMARY_BRANCH} does not name a commit; nothing pushed.")
        return 1
    commit = selected.stdout.strip()
    tag = None
    if args.tag:
        try:
            tag = release_tag(root, args.tag, commit)
            remote_tag(root, args.tag, tag)
        except ValueError as error:
            print(f"error: {error}; nothing checked or pushed.")
            return 1
    contained, remote = remote_is_behind(root, commit)
    if not contained:
        print(
            f"error: {REMOTE}/{PRIMARY_BRANCH} ({remote[:12]}) has commits that local "
            f"{PRIMARY_BRANCH} lacks; nothing was checked or pushed. Tell the user; never "
            "force-push."
        )
        return 1
    if remote == commit and not args.no_push and tag is None:
        print(f"{REMOTE}/{PRIMARY_BRANCH} already has {commit[:12]}; nothing to push.")
        return 0

    worktrees = root / WORKTREES_DIR_NAME
    stamp = time.strftime("%Y%m%d-%H%M%S")
    checkout = Path(tempfile.mkdtemp(prefix=f".push-{stamp}-", dir=worktrees))
    logs = worktrees / ".push-logs" / checkout.name.removeprefix(".push-")
    logs.mkdir(parents=True, exist_ok=False)
    added = git("worktree", "add", "--detach", str(checkout), commit, cwd=root)
    if added.returncode != 0:
        # Only an untouched, empty reservation is safe to discard. An incomplete
        # Git checkout remains available for inspection rather than forced removal.
        try:
            checkout.rmdir()
        except OSError:
            pass
        print(f"error: the private checkout could not be created: {added.stderr.strip()}")
        return 1

    target = f"{commit[:12]}" + (f" with tag {args.tag} ({tag[:12]})" if tag else "")
    print(f"checking {target} in {checkout.as_posix()}", flush=True)
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
        cleaned = remove_checkout(root, checkout)

    if not cleaned:
        print("Nothing pushed: inspect the preserved checkout before retrying.")
        return 1

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
        print(f"\nEvery check passed for {target}; not pushed (--no-push).")
        return 0

    contained, remote = remote_is_behind(root, commit)
    if not contained:
        print(f"error: {REMOTE}/{PRIMARY_BRANCH} moved during the checks; nothing pushed.")
        return 1
    tag_published = False
    if tag:
        current = git("rev-parse", "--verify", f"refs/tags/{args.tag}", cwd=root)
        if current.returncode or current.stdout.strip() != tag:
            print(f"error: local tag {args.tag!r} changed during the checks; nothing pushed.")
            return 1
        try:
            tag_published = remote_tag(root, args.tag, tag)
        except ValueError as error:
            print(f"error: {error}; nothing pushed.")
            return 1
    if remote == commit and (tag is None or tag_published):
        print(f"\nEvery check passed; {REMOTE} already has {target}. Nothing pushed.")
        return 0
    refspecs = [f"{commit}:refs/heads/{PRIMARY_BRANCH}"]
    if tag:
        refspecs.append(f"{tag}:refs/tags/{args.tag}")
    pushed = git("push", "--atomic", "--no-follow-tags", REMOTE, *refspecs, cwd=root)
    if pushed.returncode != 0:
        print(pushed.stderr.strip())
        print(f"error: the push did not complete successfully; verify {REMOTE}'s refs before retrying.")
        return 1
    print(f"\npushed {commit[:12]} to {REMOTE}/{PRIMARY_BRANCH}.")
    if tag:
        print(f"published tag {args.tag} ({tag[:12]}).")
    head = git("rev-parse", PRIMARY_BRANCH, cwd=root).stdout.strip()
    if head != commit:
        print(
            f"note: {PRIMARY_BRANCH} moved on to {head[:12]} meanwhile; it goes with the next run."
        )
    workflow = "release-workflow.md, step 6" if tag else "push-workflow.md, step 4"
    print(f"Now watch the CI run to green (.vorch/workflows/{workflow}).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
