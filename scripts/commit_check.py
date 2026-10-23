#!/usr/bin/env python
"""Check a commit: format, lint and type-check the staged files.

Git runs this through ``.githooks/pre-commit`` and ``.githooks/pre-merge-commit``
once a clone has enabled the tracked hooks with ``git config core.hooksPath
.githooks``. It runs in every checkout, the merge commits of ``scripts/worktree.py
merge`` included. It runs no tests: agents run the tests covering their change,
and ``scripts/push.py`` runs every check before main reaches the remote.

Several agents may work in one checkout at once, so the check touches only what
this commit stages:

- Formatter and linter fixes are applied and re-staged only for files whose whole
  change is staged. A partially staged file is checked as it is in the working tree
  and blocks the commit when it needs a fix, so unstaged work in the same checkout
  (another session's edits included) is never rewritten or swept into the commit.
- A project-wide check (a type checker) blocks on problems in staged files and in
  files without uncommitted changes; problems in files with unstaged or untracked
  work in progress are reported without blocking.

Staged domain maps and ``.vorch/PROJECT.md`` are checked with
``scripts/domain_maps.py``: non-ASCII prose and References entries that name no
file block; a file above its size budget that the change makes larger is reported
without blocking.

Configure the checks for the stack in ``CHECKS`` and ``PROJECT_CHECKS`` below.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from scripts import domain_maps  # noqa: E402

IGNORED_ROOTS: tuple[str, ...] = ()
"""Path prefixes the check never looks at (vendored code, archives, generated files)."""


@dataclass(frozen=True)
class FileCheck:
    """A formatter or linter that runs on the staged files it covers."""

    name: str
    suffixes: frozenset[str]
    check: Callable[[list[str]], list[str]]
    """Command that reports problems without changing files; exit code != 0 blocks."""
    fix: Callable[[list[str]], list[str]] | None = None
    """Command that fixes what it can in place; run only on fully staged files."""
    prefixes: tuple[str, ...] = ()
    """Limit to paths under these prefixes; empty means anywhere."""


@dataclass(frozen=True)
class ProjectCheck:
    """A check over the whole project, such as a type checker, whose errors name files."""

    name: str
    command: list[str]
    triggers: frozenset[str]
    """Run when a staged file has one of these suffixes or names."""
    error_path: re.Pattern[str] = field(default=re.compile(r"^(?P<path>[^:\s][^:]*):\d+"))
    """Finds the file an output line reports about; lines without one never block."""


def _python(*args: str) -> Callable[[list[str]], list[str]]:
    return lambda files: [sys.executable, "-m", *args, *files]


def _npx(*args: str) -> Callable[[list[str]], list[str]]:
    npx = shutil.which("npx") or "npx"
    return lambda files: [npx, *args, *files]


# <<FILL: the checks for this stack; delete the examples that do not apply. Every
# tool must be pinned in the project's dependencies so the hook and CI agree.>>
CHECKS: list[FileCheck] = []

PROJECT_CHECKS: list[ProjectCheck] = []


def git(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


def _paths(*args: str) -> set[str]:
    return {path for path in git(*args).stdout.split("\0") if path}


def staged_paths() -> list[str]:
    """Return the staged added, copied, modified and renamed files."""
    staged = _paths("diff", "--cached", "--name-only", "--diff-filter=ACMR", "-z")
    return sorted(
        path
        for path in staged
        if not path.startswith(IGNORED_ROOTS) and (PROJECT_ROOT / path).is_file()
    )


def unstaged_paths() -> set[str]:
    """Return the files with unstaged changes or untracked: work in progress."""
    changed = _paths("diff", "--name-only", "-z")
    untracked = _paths("ls-files", "--others", "--exclude-standard", "-z")
    return changed | untracked


def _matches(path: str, suffixes: frozenset[str], prefixes: tuple[str, ...]) -> bool:
    pure = PurePosixPath(path)
    covered = pure.suffix in suffixes or pure.name in suffixes
    return covered and (not prefixes or path.startswith(prefixes))


def run(command: Sequence[str]) -> tuple[int, str]:
    try:
        result = subprocess.run(
            list(command),
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except OSError as exc:
        return 1, f"{command[0]} could not run: {exc}"
    return result.returncode, (result.stdout + result.stderr).strip()


def run_file_check(check: FileCheck, staged: list[str], partial: set[str]) -> list[str]:
    """Run one file check; return the blocking problems."""
    files = [path for path in staged if _matches(path, check.suffixes, check.prefixes)]
    if not files:
        return []
    complete = [path for path in files if path not in partial]
    if check.fix is not None and complete:
        run(check.fix(complete))
        git("add", "--", *complete)
    code, output = run(check.check(files))
    if code == 0:
        return []
    problems = [f"{check.name}:\n{output}"]
    partially = [path for path in files if path in partial]
    if partially:
        problems.append(
            f"{check.name}: partially staged files are checked as they are in the working "
            "tree and never fixed automatically: " + ", ".join(partially)
        )
    return problems


def run_project_check(
    check: ProjectCheck, staged: list[str], in_progress: set[str]
) -> tuple[list[str], list[str]]:
    """Run one project-wide check; return blocking and reported-only problems."""
    if not any(_matches(path, check.triggers, ()) for path in staged):
        return [], []
    code, output = run(check.command)
    if code == 0:
        return [], []
    blocking: list[str] = []
    reported: list[str] = []
    for line in output.splitlines():
        match = check.error_path.match(line)
        # A note belongs to the error it follows; alone it never blocks.
        if match is None or ": note:" in line:
            continue
        path = PurePosixPath(match.group("path").replace("\\", "/")).as_posix()
        if path in in_progress and path not in staged:
            reported.append(line)
        else:
            blocking.append(line)
    if not blocking and not reported:
        blocking.append(output)
    return (
        [f"{check.name}:\n" + "\n".join(blocking)] if blocking else [],
        [f"{check.name} (work in progress, not blocking):\n" + "\n".join(reported)]
        if reported
        else [],
    )


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")
    staged = staged_paths()
    print(f"Commit check: {len(staged)} staged files")
    if not staged:
        return 0
    in_progress = unstaged_paths()
    partial = in_progress & set(staged)

    blocking: list[str] = []
    reported: list[str] = []
    for check in CHECKS:
        blocking += run_file_check(check, staged, partial)
    for project_check in PROJECT_CHECKS:
        blocks, reports = run_project_check(project_check, staged, in_progress)
        blocking += blocks
        reported += reports
    for finding in domain_maps.staged_findings(PROJECT_ROOT, staged):
        (blocking if finding.blocking else reported).append(f"domain maps: {finding}")

    for problem in reported:
        print(problem)
    if blocking:
        print("\nCommit blocked:")
        for problem in blocking:
            print(problem)
        print(
            "\nFix the problems, stage the files completely, and commit again. "
            "Never bypass this check with --no-verify."
        )
        return 1
    print("Commit check passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
