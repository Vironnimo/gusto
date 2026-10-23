#!/usr/bin/env python
"""Manage task worktrees so several agents can work and merge in parallel.

Commands (``.vorch/workflows/worktree-workflow.md`` describes the workflow):

- ``create <name>``: a new branch ``<name>`` from ``main`` in ``.worktrees/<name>``,
  with a marker file, an optional port, and the project's setup commands.
- ``list``: the managed worktrees with branch, port, and pending work.
- ``merge <name>``: merge the branch into ``main`` and remove the worktree. Merges
  are serialized by a lock. The merge commit is made in a private landing checkout,
  where the commit hook checks it; ``main`` only fast-forwards to a checked commit,
  so a failed or killed merge leaves ``main`` as it was.
- ``delete <name>``: remove an abandoned worktree and its branch.

The script works from any checkout: it finds the primary checkout through Git's
common directory. Configure it for the project below.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
from collections.abc import Iterator, Sequence
from contextlib import contextmanager, suppress
from datetime import datetime, timezone
from pathlib import Path

# --- Project configuration -------------------------------------------------------

PRIMARY_BRANCH = "main"
WORKTREES_DIR_NAME = ".worktrees"
MARKER_FILE_NAME = ".vorch-worktree"
"""JSON in each worktree's root (git-ignored): name, branch, port. Dev tooling reads it
to pick the worktree's port or data directory."""

_VENV_PYTHON = ".venv/Scripts/python.exe" if sys.platform == "win32" else ".venv/bin/python"

SETUP_COMMANDS: list[list[str]] = [
    [sys.executable, "-m", "venv", ".venv"],
    [_VENV_PYTHON, "-m", "pip", "install", "-q", "-e", ".[web,test,dev]"],
]
"""Commands run in a new task or merged landing checkout, in order.
The caller removes the checkout after failure; main stays unchanged.
They get VORCH_WORKTREE_NAME, VORCH_WORKTREE_PATH, VORCH_WORKTREE_PORT and
VORCH_PRIMARY_ROOT in their environment; a relative executable is resolved
inside the new worktree. Install each worktree's own dependencies
(for example ``["npm", "ci", "--prefix", "webui"]``); never link folders of another
checkout into a worktree, and never reinstall an editable package into an
environment that other checkouts share."""

PORT_BASE: int | None = 8100
"""When set, each worktree gets its own block of ports starting above this one."""
PORTS_PER_WORKTREE = 10

MERGE_WAIT_TIMEOUT_SECONDS = 30 * 60
LANDING_ATTEMPTS = 3
"""How often a merge is remade when main moved while its commit was checked."""

# ---------------------------------------------------------------------------------

NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
CONFLICT_EXIT_CODE = 2


class WorktreeError(Exception):
    """A failure the command reports and exits on."""


def git(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


def git_out(*args: str, cwd: Path | None = None) -> str:
    result = git(*args, cwd=cwd)
    if result.returncode != 0:
        raise WorktreeError(result.stderr.strip() or f"git {' '.join(args)} failed")
    return result.stdout.strip()


def primary_root() -> Path:
    """Return the primary checkout, whichever checkout runs this script."""
    here = Path(__file__).resolve().parent
    common = Path(git_out("rev-parse", "--path-format=absolute", "--git-common-dir", cwd=here))
    return common.parent


def show(**fields: object) -> None:
    for key, value in fields.items():
        print(f"{key}: {value}")


def posix(path: Path) -> str:
    return path.resolve().as_posix()


# --- Locks -------------------------------------------------------------------------


def _try_lock(handle) -> bool:
    handle.seek(0, os.SEEK_END)
    if handle.tell() == 0:
        handle.write(b"0")
        handle.flush()
    handle.seek(0)
    try:
        if sys.platform == "win32":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return False
    return True


def _unlock(handle) -> None:
    handle.seek(0)
    with suppress(OSError):
        if sys.platform == "win32":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


@contextmanager
def file_lock(path: Path, timeout: float, waiting: str) -> Iterator[None]:
    """Hold an exclusive advisory lock on *path*; the OS releases it if the process dies."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as handle:
        deadline = time.monotonic() + timeout
        announced = False
        while not _try_lock(handle):
            if time.monotonic() >= deadline:
                raise WorktreeError(f"{path.name} stayed busy for {int(timeout)} s; try again")
            if not announced:
                print(waiting, flush=True)
                announced = True
            time.sleep(0.5)
        try:
            yield
        finally:
            _unlock(handle)


# --- Worktree records --------------------------------------------------------------


class Worktrees:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.dir = root / WORKTREES_DIR_NAME

    def path(self, name: str) -> Path:
        if not NAME_PATTERN.match(name):
            raise WorktreeError("a task name is lowercase letters, digits and hyphens")
        return self.dir / name

    def marker(self, path: Path) -> dict[str, object]:
        try:
            data = json.loads((path / MARKER_FILE_NAME).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def managed(self) -> list[Path]:
        if not self.dir.is_dir():
            return []
        return sorted(
            path
            for path in self.dir.iterdir()
            if path.is_dir()
            and not path.name.startswith(".")
            and (path / MARKER_FILE_NAME).is_file()
        )

    def require(self, name: str) -> tuple[Path, str]:
        """Return a managed worktree's path and checked-out branch."""
        path = self.path(name)
        if not (path / MARKER_FILE_NAME).is_file():
            raise WorktreeError(f"no managed worktree '{name}' in {posix(self.dir)}")
        branch = git_out("branch", "--show-current", cwd=path)
        if not branch:
            raise WorktreeError(f"worktree '{name}' has no branch checked out")
        return path, branch

    def pending(self, path: Path, branch: str) -> tuple[list[str], int]:
        """Return the worktree's uncommitted entries and its commits that main lacks."""
        status = [
            line
            for line in git_out("status", "--porcelain", cwd=path).splitlines()
            if line and not line.endswith(MARKER_FILE_NAME)
        ]
        ahead = int(git_out("rev-list", "--count", f"{PRIMARY_BRANCH}..{branch}", cwd=self.root))
        return status, ahead

    def free_port(self) -> int | None:
        if PORT_BASE is None:
            return None
        used = {self.marker(path).get("port") for path in self.managed()}
        for index in range(1, 500):
            port = PORT_BASE + index * PORTS_PER_WORKTREE
            if port in used:
                continue
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
                if probe.connect_ex(("127.0.0.1", port)) != 0:
                    return port
        raise WorktreeError("no free port block found")

    def remove(self, path: Path, branch: str, *, force: bool) -> None:
        """Remove the worktree directory and its branch.

        The directory goes with its ignored and untracked files, so callers check for
        pending work first. *force* also deletes a branch that main does not contain.
        """
        if Path.cwd().resolve().is_relative_to(path.resolve()):
            os.chdir(self.root)
        removed = git("worktree", "remove", "--force", str(path), cwd=self.root)
        if removed.returncode != 0:
            raise WorktreeError(
                f"the worktree could not be removed: {removed.stderr.strip()}\n"
                f"hint: leave the directory, then run from {posix(self.root)}: "
                f"python scripts/worktree.py delete {path.name} --force"
            )
        git("worktree", "prune", cwd=self.root)
        deleted = git("branch", "-D" if force else "-d", branch, cwd=self.root)
        if deleted.returncode != 0:
            print(f"note: branch '{branch}' kept: {deleted.stderr.strip()}")


# --- Commands ----------------------------------------------------------------------


def _setup_checkout(trees: Worktrees, path: Path, name: str,
                    port: int | None = None) -> None:
    """Install this checkout's dependencies before its commit hook can run."""
    env = {
        **os.environ,
        "VORCH_WORKTREE_NAME": name,
        "VORCH_WORKTREE_PATH": str(path),
        "VORCH_WORKTREE_PORT": "" if port is None else str(port),
        "VORCH_PRIMARY_ROOT": str(trees.root),
    }
    for command in SETUP_COMMANDS:
        print(f"setup: {' '.join(command)}", flush=True)
        local = path / command[0]
        executable = str(local) if local.is_file() else shutil.which(command[0]) or command[0]
        try:
            result = subprocess.run([executable, *command[1:]], cwd=path, env=env, check=False)
        except OSError as error:
            raise WorktreeError(f"setup command could not run: {command}: {error}") from error
        if result.returncode != 0:
            raise WorktreeError(f"setup command failed (exit {result.returncode}): {command}")


def create(trees: Worktrees, args: argparse.Namespace) -> int:
    name: str = args.name
    path = trees.path(name)
    if path.exists():
        raise WorktreeError(f"worktree '{name}' already exists: {posix(path)}")
    if (
        git("rev-parse", "--verify", "--quiet", f"refs/heads/{name}", cwd=trees.root).returncode
        == 0
    ):
        raise WorktreeError(f"branch '{name}' already exists; choose another task name")

    git("worktree", "prune", cwd=trees.root)
    with file_lock(trees.dir / ".create.lock", 60, "waiting for another create..."):
        git_out("worktree", "add", "-b", name, str(path), PRIMARY_BRANCH, cwd=trees.root)
        port = trees.free_port()
        marker = {
            "name": name,
            "branch": name,
            "port": port,
            "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        (path / MARKER_FILE_NAME).write_text(json.dumps(marker, indent=2) + "\n", encoding="utf-8")

    try:
        _setup_checkout(trees, path, name, port)
    except WorktreeError as error:
        trees.remove(path, name, force=True)
        raise WorktreeError(f"{error}; the worktree was removed") from error

    show(name=name, branch=name, path=posix(path), **({"port": port} if port is not None else {}))
    return 0


def list_worktrees(trees: Worktrees, _args: argparse.Namespace) -> int:
    paths = trees.managed()
    if not paths:
        show(status="empty")
        return 0
    for index, path in enumerate(paths):
        if index:
            print()
        branch = git_out("branch", "--show-current", cwd=path) or "?"
        status, ahead = trees.pending(path, branch) if branch != "?" else ([], 0)
        port = trees.marker(path).get("port")
        show(
            name=path.name,
            branch=branch,
            path=posix(path),
            **({"port": port} if port is not None else {}),
            uncommitted=len(status),
            commits=ahead,
        )
    return 0


def _merge_conflicts(root: Path, branch: str) -> list[str]:
    """Return the paths merging *branch* into main conflicts on, without touching main."""
    result = git(
        "merge-tree",
        "--write-tree",
        "--name-only",
        "--no-messages",
        PRIMARY_BRANCH,
        branch,
        cwd=root,
    )
    if result.returncode != 1:
        return []  # 0: clean; anything else: this Git cannot tell, the merge will
    return [line for line in result.stdout.splitlines()[1:] if line.strip()]


def _fast_forward(root: Path, commit: str) -> subprocess.CompletedProcess[str]:
    result = git("merge", "--ff-only", "-q", commit, cwd=root)
    for _ in range(5):
        if result.returncode == 0 or "index.lock" not in result.stderr:
            break
        time.sleep(1)
        result = git("merge", "--ff-only", "-q", commit, cwd=root)
    return result


@contextmanager
def _landing_checkout(trees: Worktrees, name: str) -> Iterator[Path]:
    landing = trees.dir / f".landing-{name}"
    if landing.exists():
        git("worktree", "remove", "--force", str(landing), cwd=trees.root)
        shutil.rmtree(landing, ignore_errors=True)
    git("worktree", "prune", cwd=trees.root)
    git_out("worktree", "add", "--detach", str(landing), PRIMARY_BRANCH, cwd=trees.root)
    try:
        yield landing
    finally:
        git("worktree", "remove", "--force", str(landing), cwd=trees.root)
        shutil.rmtree(landing, ignore_errors=True)
        git("worktree", "prune", cwd=trees.root)


def _land(trees: Worktrees, name: str, branch: str, message: str) -> str:
    """Make, check and land the merge commit; return the commit main now points to."""
    root = trees.root
    with _landing_checkout(trees, name) as landing:
        for _attempt in range(LANDING_ATTEMPTS):
            base = git_out("rev-parse", PRIMARY_BRANCH, cwd=root)
            git_out("reset", "-q", "--hard", base, cwd=landing)
            merged = git("merge", "--no-ff", "--no-commit", branch, cwd=landing)
            if merged.returncode != 0:
                conflicts = git_out("diff", "--name-only", "--diff-filter=U", cwd=landing)
                git("merge", "--abort", cwd=landing)
                for path in conflicts.splitlines():
                    print(f"conflicted: {path}")
                raise WorktreeError(
                    f"merging '{branch}' into {PRIMARY_BRANCH} failed; main is unchanged: "
                    f"{merged.stderr.strip()}\nhint: in the worktree run `git rebase "
                    f"{PRIMARY_BRANCH}`, resolve, commit, and merge again"
                )
            if git("rev-parse", "-q", "--verify", "MERGE_HEAD", cwd=landing).returncode != 0:
                return base  # main already contains the branch

            try:
                _setup_checkout(trees, landing, name)
            except WorktreeError as error:
                raise WorktreeError(f"{error}; main is unchanged") from error
            print("checking the merge commit (no output until done)...", flush=True)
            committed = git("commit", "-m", message, cwd=landing)
            report = (committed.stdout + committed.stderr).strip()
            if report:
                print(report)
            commit = git_out("rev-parse", "HEAD", cwd=landing)
            if committed.returncode != 0 or commit == base:
                raise WorktreeError(
                    "the merge commit failed the commit check; main is unchanged\n"
                    f"hint: in the worktree run `git rebase {PRIMARY_BRANCH}`, fix the problems "
                    "above, commit, and merge again"
                )

            landed = _fast_forward(root, commit)
            if landed.returncode == 0:
                return commit
            if git_out("rev-parse", PRIMARY_BRANCH, cwd=root) == base:
                raise WorktreeError(
                    f"main could not be fast-forwarded: {landed.stderr.strip()}\n"
                    "hint: uncommitted changes in the primary checkout overlap the merge; "
                    "they must be committed or set aside before the merge can land"
                )
            print("main moved while the merge commit was checked; merging again...", flush=True)
    raise WorktreeError(
        f"main moved during each of {LANDING_ATTEMPTS} checks; main is unchanged by this merge; "
        "run the merge again"
    )


def merge(trees: Worktrees, args: argparse.Namespace) -> int:
    name: str = args.name
    path, branch = trees.require(name)
    current = git_out("branch", "--show-current", cwd=trees.root)
    if current != PRIMARY_BRANCH:
        raise WorktreeError(
            f"the primary checkout is on '{current}', not '{PRIMARY_BRANCH}'; switch it back first"
        )
    status, ahead = trees.pending(path, branch)
    if status:
        for line in status:
            print(f"uncommitted: {line}")
        raise WorktreeError(
            "the worktree has uncommitted or untracked files; commit or remove them"
        )

    if ahead == 0:
        print(f"'{branch}' has no commits that {PRIMARY_BRANCH} lacks; removing the worktree")
        trees.remove(path, branch, force=False)
        show(name=name, status="removed")
        return 0

    conflicts = _merge_conflicts(trees.root, branch)
    if conflicts:
        for conflict in conflicts:
            print(f"conflicted: {conflict}")
        print(
            f"hint: in the worktree run `git rebase {PRIMARY_BRANCH}`, resolve, commit, "
            "and merge again"
        )
        print(f"error: merging '{branch}' into {PRIMARY_BRANCH} conflicts; main is unchanged")
        return CONFLICT_EXIT_CODE

    message = args.message or f"merge: {name}"
    with file_lock(
        trees.dir / ".merge.lock", args.wait_timeout, "waiting for another session's merge..."
    ):
        commit = _land(trees, name, branch, message)

    show(name=name, status="merged", commit=commit[:12], branch=branch)
    trees.remove(path, branch, force=False)
    return 0


def delete(trees: Worktrees, args: argparse.Namespace) -> int:
    name: str = args.name
    path, branch = trees.require(name)
    if not args.force:
        status, ahead = trees.pending(path, branch)
        if status or ahead:
            for line in status:
                print(f"uncommitted: {line}")
            if ahead:
                print(f"unmerged: {ahead} commits that {PRIMARY_BRANCH} lacks")
            raise WorktreeError("the worktree holds work; merge it, or delete with --force")
    trees.remove(path, branch, force=True)
    show(name=name, status="deleted")
    return 0


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("create", help="create a task worktree").add_argument("name")
    commands.add_parser("list", help="list task worktrees")
    merging = commands.add_parser("merge", help="merge a task into main and remove it")
    merging.add_argument("name")
    merging.add_argument("-m", "--message", help="merge commit message (default: merge: <name>)")
    merging.add_argument(
        "--wait-timeout",
        type=float,
        default=MERGE_WAIT_TIMEOUT_SECONDS,
        help="seconds to wait for another merge",
    )
    deleting = commands.add_parser("delete", help="remove a task worktree and its branch")
    deleting.add_argument("name")
    deleting.add_argument(
        "--force", action="store_true", help="discard uncommitted or unmerged work"
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")
    args = parse_args(sys.argv[1:] if argv is None else argv)
    commands = {"create": create, "list": list_worktrees, "merge": merge, "delete": delete}
    try:
        return commands[args.command](Worktrees(primary_root()), args)
    except WorktreeError as exc:
        print(f"error: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
