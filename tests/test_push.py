"""Protect checked commits and release tags using only a local throwaway remote."""
from __future__ import annotations

import io
import os
import sys
import tempfile
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import gusto  # noqa: E402, F401 - allocate every fixture under checkout .tmp
from scripts import push  # noqa: E402


checks = 0


def check(condition, message):
    global checks
    assert condition, message
    checks += 1


with tempfile.TemporaryDirectory(prefix="gusto-push-test-") as directory:
    sandbox = Path(directory).resolve()
    check(sandbox.is_relative_to((ROOT / ".tmp").resolve()),
          "push fixtures must stay under this checkout's .tmp")
    root = sandbox / "repo with spaces"
    remote = sandbox / "local remote.git"
    root.mkdir()
    remote.mkdir()
    environment = {key: value for key, value in os.environ.items()
                   if not key.startswith("GIT_")}
    environment.update({
        "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_TERMINAL_PROMPT": "0", "GUSTO_HOME": str(sandbox / "store"),
    })

    with patch.dict(os.environ, environment, clear=True):
        def git(*arguments, cwd=root):
            result = push.git(*arguments, cwd=cwd)
            assert result.returncode == 0, result.stderr or result.stdout
            return result.stdout.strip()

        def commit(version):
            (root / "pyproject.toml").write_text(
                f'[project]\nname = "fixture"\nversion = "{version}"\n', encoding="utf-8",
            )
            git("add", "pyproject.toml")
            git("commit", "-qm", f"version {version}")
            return git("rev-parse", "HEAD")

        def tag(name):
            git("tag", "-a", name, "-m", name)
            return git("rev-parse", f"refs/tags/{name}")

        def remote_ref(reference):
            result = push.git("rev-parse", "--verify", reference, cwd=remote)
            return result.stdout.strip() if result.returncode == 0 else None

        visited = []

        def run(arguments, *, passed=True, during=None):
            visited.clear()

            def check_step(step, checkout, log):
                assert checkout.resolve().is_relative_to(sandbox)
                visited.append(git("rev-parse", "HEAD", cwd=checkout))
                log.write_text("fixture check\n", encoding="utf-8")
                if during:
                    during()
                return passed

            output = io.StringIO()
            with patch.object(push, "primary_root", return_value=root), \
                    patch.object(push, "STEPS", [push.Step("fixture", ["unused"])]), \
                    patch.object(push, "run_step", side_effect=check_step), \
                    redirect_stdout(output):
                result = push.main(arguments)
            check(not [path for path in (root / ".worktrees").glob(".push-*")
                       if path.name != ".push-logs"],
                  "every attempted check must remove its private checkout")
            return result, output.getvalue()

        git("init", "-q", "-b", "main")
        git("init", "-q", "--bare", "-b", "main", cwd=remote)
        git("config", "user.name", "Fixture User")
        git("config", "user.email", "fixture@example.invalid")
        git("config", "commit.gpgsign", "false")
        git("config", "tag.gpgsign", "false")
        git("config", "push.followTags", "true")
        git("remote", "add", "origin", str(remote))
        check(Path(git("remote", "get-url", "origin")).resolve().is_relative_to(sandbox),
              "the only push destination must be the local fixture remote")
        initial = commit("0.1.0")
        git("push", "-q", "--no-follow-tags", "origin", "main")

        # A dry check still runs when there is nothing new to push.
        result, output = run(["--no-push"])
        check(result == 0 and visited == [initial] and "--no-push" in output
              and remote_ref("refs/heads/main") == initial,
              "--no-push must check the captured commit even when it is already remote")

        git("tag", "v9.9.8")
        tag("v9.9.9")
        for invalid in ("latest", "v8.8.8", "v9.9.8", "v9.9.9"):
            result, output = run(["--tag", invalid])
            check(result == 1 and not visited and invalid in output
                  and remote_ref(f"refs/tags/{invalid}") is None,
                  "malformed, missing, lightweight and version-mismatched tags must be refused")

        initial_tag = tag("v0.1.0")
        result, output = run(["--tag", "v0.1.0", "--no-push"])
        check(result == 0 and visited == [initial] and "v0.1.0" in output
              and remote_ref("refs/tags/v0.1.0") is None,
              "tag dry runs must check the selected commit and publish no tag")
        result, output = run(["--tag", "v0.1.0"], passed=False)
        check(result == 1 and visited == [initial]
              and remote_ref("refs/tags/v0.1.0") is None,
              "a failed exact-commit check must block a release tag")
        result, output = run(["--tag", "v0.1.0"])
        check(result == 0 and visited == [initial]
              and remote_ref("refs/tags/v0.1.0") == initial_tag
              and remote_ref("refs/tags/v9.9.9") is None,
              "publish only the explicit annotated tag even when push.followTags is enabled")
        result, output = run(["--tag", "v0.1.0"])
        check(result == 0 and visited == [initial] and "already" in output,
              "an identical published tag must be idempotent but still checked")

        # The worktree and published commit remain fixed while local main moves.
        selected = commit("0.2.0")
        selected_tag = tag("v0.2.0")
        reject_main = remote / "hooks/update"
        reject_main.write_text(
            '#!/bin/sh\nif [ "$1" = "refs/heads/main" ]; then exit 1; fi\nexit 0\n',
            encoding="utf-8", newline="\n",
        )
        reject_main.chmod(0o755)
        try:
            result, output = run(["--tag", "v0.2.0"])
            check(result == 1 and visited == [selected]
                  and remote_ref("refs/heads/main") == initial
                  and remote_ref("refs/tags/v0.2.0") is None,
                  "a remote rejection of main must also reject the tag atomically")
        finally:
            reject_main.unlink()
        result, output = run(["--tag", "v0.2.0"], during=lambda: commit("0.3.0"))
        check(result == 0 and visited == [selected]
              and remote_ref("refs/heads/main") == selected
              and remote_ref("refs/tags/v0.2.0") == selected_tag
              and git("rev-parse", "main") != selected and "meanwhile" in output,
              "publish the captured commit and tag together, never concurrent main changes")
        result, output = run(["--tag", "v0.1.0"])
        check(result == 1 and not visited and "main" in output,
              "an annotated tag of an older commit must not release the selected main")

        # Conflicting remote and concurrently replaced local tag objects are distinct refusals.
        conflict_tag = tag("remote-conflict")
        tag("v0.3.0")
        git("push", "-q", "--no-follow-tags", "origin", f"{conflict_tag}:refs/tags/v0.3.0")
        result, output = run(["--tag", "v0.3.0"])
        check(result == 1 and not visited
              and remote_ref("refs/heads/main") == selected
              and remote_ref("refs/tags/v0.3.0") == conflict_tag,
              "a conflicting published tag must be preserved and prevent any push")
        next_commit = commit("0.4.0")
        next_tag = tag("v0.4.0")
        result, output = run(["--tag", "v0.4.0"], during=lambda: git(
            "tag", "-f", "-a", "v0.4.0", "-m", "concurrent replacement",
        ))
        check(result == 1 and visited == [next_commit] and "changed during" in output
              and remote_ref("refs/heads/main") == selected
              and remote_ref("refs/tags/v0.4.0") is None,
              "a locally replaced tag must not replace the object captured before checking")
        git("update-ref", "refs/tags/v0.4.0", next_tag)

        # Ordinary main pushes also suppress incidental tags from Git configuration.
        result, output = run([])
        check(result == 0 and visited == [next_commit]
              and remote_ref("refs/heads/main") == next_commit
              and remote_ref("refs/tags/v0.4.0") is None,
              "ordinary pushes must not publish a release tag implicitly")

        def advance_remote():
            later = git("commit-tree", git("rev-parse", "HEAD^{tree}"),
                        "-p", next_commit, "-m", "concurrent remote work")
            git("push", "-q", "--no-follow-tags", "origin", f"{later}:refs/heads/main")

        result, output = run(["--tag", "v0.4.0"], during=advance_remote)
        check(result == 1 and visited == [next_commit] and "moved during" in output
              and remote_ref("refs/tags/v0.4.0") is None,
              "remote main advancing during checks must block the tag without force")
        result, output = run(["--tag", "v0.4.0"])
        check(result == 1 and not visited and "lacks" in output,
              "remote main ahead before checks must preserve the existing safety refusal")

print(f"OK - {checks} push checks passed")
