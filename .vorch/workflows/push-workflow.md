# Push Workflow

How to push `main` or an explicitly selected release tag. Commits and merges run static checks only, so `main` may hold a commit that breaks a test elsewhere. The push is where every such failure is found and fixed: `python scripts/push.py` checks the commit `main` points to completely and pushes exactly that commit only when every check passes. Nothing red reaches the remote.

A push is finished only when the CI run it starts is green. Never use a plain `git push`.

## Steps

### 1. Run the push command

From the primary checkout:

```bash
python scripts/push.py
```

It checks the commit in a private checkout, not the working tree, so other sessions' uncommitted work does not affect the result, and runs every check listed in `.vorch/PROJECT.md` -> Testing (static checks and the complete suites). A run takes several minutes; use a shell timeout of at least 30 minutes. An interrupted check pushes nothing; interruption during publication requires inspecting the remote refs before retrying.

Exit code 0 means the commit is pushed, or the remote's `main` already had it. For a new push, continue with step 4; otherwise inspect the existing CI run for that commit.

Release tags also use this wrapper: `python scripts/push.py --tag v<version>` (the complete release procedure is `release-workflow.md`). The named local tag must be annotated, point to the selected `main` commit and match the version in that commit's `pyproject.toml`. The wrapper captures the tag object and commit before checking, reruns every check even when `main` is already remote, and publishes only those two refs atomically. An identical remote tag is an idempotent success after checks; a different remote tag or a local tag changed during the checks is refused. The wrapper never creates, moves or overwrites a tag. Watch release CI as described in the release workflow.

### 2. Find the cause of every failure

The report marks each step PASS or FAIL and shows the end of each failing step's output and the path of its complete log. Fix every failure, whatever commit caused it, not only those of your own work.

For each one, reproduce it directly (the single test, the single checker), then find the commit that caused it: `git log -- <path>` and `git blame` for the code and the test involved, `git bisect run` when the breaking commit is not obvious.

A test that failed and passed on a rerun still blocks the push: a rerun does not show whether the cause is test isolation, timing or application behavior. Investigate and fix the first failure.

### 3. Fix, commit, run again

- Fix the code, not the test, unless the test is wrong: it asserts behavior that was changed on purpose, or fails by its own construction.
- When the intended behavior is unclear, ask the user instead of guessing.
- Commit each fix separately on `main` as `fix(<scope>): <what>`, once the tests covering it pass. A larger fix goes through a worktree like any other task.

Then run `python scripts/push.py` again. Repeat steps 2 and 3 until it pushes.

### 4. Watch CI to the end

Find the CI run the push started and check that it ran on the pushed commit:

```bash
gh run list --branch=main --event=push --limit 3 --json databaseId,workflowName,headSha,status
```

Follow it job by job, not only its end: check the jobs every minute or two (`gh run view <run-id> --json status,jobs`). As soon as one job fails, read its failures (`gh run view --job <job-id> --log-failed`) and start fixing while the other jobs still run; keep checking them, since they may add failures. Do not end your task before the run finished.

A red run is part of the push: fix every failure as in steps 2 and 3, whatever commit caused it, run `python scripts/push.py` again once the run has ended and every failure is fixed, and watch the new run. Repeat until a run is green. A run cancelled because a newer push superseded it says nothing; watch the newer one.

### 5. Report

Tell the user which commit was pushed, the green CI run, and what you fixed on the way: each failure, the commit that caused it, and the commit that fixed it.

## Gotchas

- **Remote moved:** when the remote's `main` has commits that local `main` lacks, the command pushes nothing. Tell the user and stop; do not bring those commits in on your own, and never force-push.
- **Only the checked commit is pushed:** commits that reach `main` while the command runs go with the next run. Tags are published only with explicit `--tag`; Git's `push.followTags` cannot add incidental tags.
- **Checks only:** `python scripts/push.py --no-push` runs every check without pushing, even when `main` is already remote. Combine it with `--tag v<version>` to validate that release selection without publishing. No CI run starts.
- **Push failure after checks:** a lost response can leave the remote outcome unknown. Inspect its refs with `git ls-remote --heads --tags origin` before retrying. A server without atomic-push support rejects the push; never bypass the wrapper.
- **Leftover checkouts:** a killed run can leave `.worktrees/.push-*` behind; the next run removes it.
