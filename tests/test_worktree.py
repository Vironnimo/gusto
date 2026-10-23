"""Protect main while preparing and checking a merge in its private checkout."""
from __future__ import annotations

import io
import json
import os
import shlex
import sys
import tempfile
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import gusto  # noqa: E402, F401 - keep every fixture under the checkout's .tmp
from scripts import worktree  # noqa: E402


checks = 0


def check(condition, message):
    global checks
    assert condition, message
    checks += 1


with tempfile.TemporaryDirectory(prefix="gusto-worktree-test-") as directory:
    sandbox = Path(directory)
    root = sandbox / "repo with spaces"
    root.mkdir()
    environment = {key: value for key, value in os.environ.items()
                   if not key.startswith("GIT_")}
    environment.update({
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_TERMINAL_PROMPT": "0",
        "GUSTO_HOME": str(sandbox / "store"),
    })
    with patch.dict(os.environ, environment, clear=True):
        worktree.git_out("init", "-q", "-b", "main", cwd=root)
        worktree.git_out("config", "user.name", "Fixture User", cwd=root)
        worktree.git_out("config", "user.email", "fixture@example.invalid", cwd=root)
        worktree.git_out("config", "commit.gpgsign", "false", cwd=root)
        (root / ".gitignore").write_text(
            ".venv/\n.worktrees/\n.vorch-worktree\n", encoding="utf-8",
        )
        (root / "dependency.txt").write_text("base-pin", encoding="utf-8")
        worktree.git_out("add", ".", cwd=root)
        worktree.git_out("commit", "-qm", "base", cwd=root)
        trees = worktree.Worktrees(root)
        source = trees.path("task")
        worktree.git_out("worktree", "add", "-b", "task", str(source), cwd=root)
        (source / worktree.MARKER_FILE_NAME).write_text(
            json.dumps({"name": "task", "branch": "task", "port": None}),
            encoding="utf-8",
        )
        (source / "dependency.txt").write_text("task-pin", encoding="utf-8")
        worktree.git_out("add", "dependency.txt", cwd=source)
        worktree.git_out("commit", "-qm", "task dependency", cwd=source)
        source_commit = worktree.git_out("rev-parse", "HEAD", cwd=source)
        (root / "main-only.txt").write_text("main's concurrent work", encoding="utf-8")
        worktree.git_out("add", "main-only.txt", cwd=root)
        worktree.git_out("commit", "-qm", "main work", cwd=root)
        before = worktree.git_out("rev-parse", "main", cwd=root)

        # Distinct existing environments must never be borrowed by the landing.
        for checkout in (root, source):
            (checkout / ".venv").mkdir()
            (checkout / ".venv/owner").write_text(checkout.name, encoding="utf-8")

        setup_script = root / ".git/fixture-setup.py"
        trace = sandbox / "setup-trace.json"
        setup_script.write_text('''\
import json, os, sys
from pathlib import Path
root = Path.cwd()
assert (root / "dependency.txt").read_text() == "task-pin"
assert (root / "main-only.txt").is_file()
assert root == Path(os.environ["VORCH_WORKTREE_PATH"])
assert root != Path(os.environ["VORCH_PRIMARY_ROOT"])
assert not (root / ".venv/owner").exists()
(root / ".venv").mkdir(exist_ok=True)
state = {"path": str(root), "pin": (root / "dependency.txt").read_text()}
(root / ".venv/prepared.json").write_text(json.dumps(state))
Path(sys.argv[1]).write_text(json.dumps(state))
''', encoding="utf-8")
        setup = [[sys.executable, str(setup_script), str(trace)]]
        hook_script = root / ".git/fixture-hook.py"
        hook_script.write_text('''\
import json, os, sys
from pathlib import Path
root = Path.cwd()
state = json.loads((root / ".venv/prepared.json").read_text())
assert Path(state["path"]) == root
assert state["pin"] == (root / "dependency.txt").read_text() == "task-pin"
assert (root / "main-only.txt").is_file()
print("Commit check: prepared merged dependencies", flush=True)
if os.environ.get("WORKTREE_TEST_HOOK_FAIL") == "1":
    print("Commit blocked: fixture rejection", flush=True)
    sys.exit(1)
print("Commit check passed.", flush=True)
''', encoding="utf-8")
        hooks = root / ".git/hooks"
        hook = hooks / "pre-commit"
        hook.write_text(
            "#!/bin/sh\nexec " + shlex.quote(Path(sys.executable).as_posix())
            + " " + shlex.quote(hook_script.as_posix()) + "\n", encoding="utf-8",
        )
        hook.chmod(0o755)
        worktree.git_out("config", "core.hooksPath", str(hooks), cwd=root)

        def merge(setup_commands, *, reject_hook=False):
            output = io.StringIO()
            with patch.object(worktree, "primary_root", return_value=root), \
                    patch.object(worktree, "SETUP_COMMANDS", setup_commands), \
                    patch.dict(os.environ, {"WORKTREE_TEST_HOOK_FAIL": str(int(reject_hook))}), \
                    redirect_stdout(output):
                result = worktree.main(["merge", "task", "-m", "test: checked merge"])
            return result, output.getvalue()

        landing = trees.dir / ".landing-task"
        for label, commands, reject in (
            ("setup", [[sys.executable, "-c", "import sys; sys.exit(7)"]], False),
            ("hook", setup, True),
        ):
            result, output = merge(commands, reject_hook=reject)
            check(result == 1 and "main is unchanged" in output,
                  f"{label} failure must report failure without landing")
            check(worktree.git_out("rev-parse", "main", cwd=root) == before
                  and (root / "dependency.txt").read_text() == "base-pin",
                  f"{label} failure must preserve main and its files")
            check(source.is_dir()
                  and worktree.git_out("rev-parse", "task", cwd=root) == source_commit,
                  f"{label} failure must preserve the task for retry")
            check(not landing.exists()
                  and str(landing).replace("\\", "/") not in worktree.git_out(
                      "worktree", "list", "--porcelain", cwd=root),
                  f"{label} failure must remove the private landing checkout")
        check("Commit blocked" in output and trace.is_file(),
              "the hook must run after setup and expose its rejection report")

        result, output = merge(setup)
        state = json.loads(trace.read_text())
        check(result == 0 and "status: merged" in output
              and "Commit check passed." in output,
              "successful merge must expose the successful commit check")
        check(Path(state["path"]) == landing and state["pin"] == "task-pin",
              "setup must install from the merged tree into its own checkout")
        check(len(worktree.git_out("rev-list", "--parents", "-n", "1", "main",
                                   cwd=root).split()) == 3
              and worktree.git("merge-base", "--is-ancestor", source_commit, "main",
                               cwd=root).returncode == 0,
              "main must advance to the checked merge containing the task")
        check(not source.exists() and not landing.exists()
              and worktree.git("rev-parse", "--verify", "refs/heads/task",
                               cwd=root).returncode != 0,
              "success must remove the task, its branch, and the private landing")
        check((root / ".venv/owner").read_text() == root.name
              and not worktree.git_out("status", "--porcelain", cwd=root),
              "landing setup must leave the primary environment and checkout clean")

print(f"OK - {checks} worktree merge checks passed")
