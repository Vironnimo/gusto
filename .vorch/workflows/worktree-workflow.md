# Worktree Workflow

A larger or uncertain task runs in its own git worktree: a separate directory on its own branch, so several agents can work, commit and merge in parallel without touching each other or the primary checkout. Small, low-risk changes go directly on `main` instead (`AGENTS.md` -> Git).

## Create

From any checkout:

```bash
python scripts/worktree.py create <task-name>
```

It creates `.worktrees/<task-name>` on a new branch `<task-name>` from `main`, runs the project's setup for a fresh checkout, and prints the absolute `path` (and the ports or data directory, where the project assigns them). A task name is lowercase letters, digits and hyphens.

## Work inside the worktree

The working directory decides which checkout a command acts on. Change into the printed path before you run anything, or use absolute paths and `git -C <path>`; a script path that points into the worktree does not make a process run there. Every subagent you delegate to gets the worktree path as its working directory and the instruction to stay inside it.

Commit inside the worktree as on `main`: one logical unit per commit, through the commit hook, once the tests covering the change pass.

## Merge

Once everything is committed:

```bash
python scripts/worktree.py merge <task-name>
```

No user confirmation is needed. Use a generous shell timeout: the command waits while another session's merge holds the merge lock. It then:

1. reports a conflict with `main` before touching anything;
2. prepares the merged tree's dependencies in its own private landing checkout, then makes the merge commit (`--no-ff`) there through the static commit hook;
3. fast-forwards `main` in the primary checkout to that commit, and remakes the merge when `main` moved meanwhile;
4. removes the worktree and its branch.

It runs no tests; the complete suites run at push (`push-workflow.md`). A merge that fails or is killed leaves `main` as it was; run it again.

## Conflicts

When the merge reports conflicts, `main` is unchanged. In your worktree, bring `main` into your branch, resolve, and commit:

```bash
git rebase main
```

Then run the merge again. When the merge commit fails the commit hook, the report names the problems: fix them in the worktree the same way and retry.

## Abandon

```bash
python scripts/worktree.py delete <task-name>
```

It refuses while the worktree has uncommitted changes or commits that `main` lacks; `--force` discards them.

## Gotchas

- **Stashes are shared by all worktrees.** An unqualified `git stash pop` can consume another session's stash. Set work aside with a temporary commit or a saved patch instead.
- **One branch per worktree.** A branch can be checked out in one worktree at a time; `create` always makes a fresh branch.
- **The primary checkout stays on `main`.** The merge refuses otherwise. Never switch it to another branch while sessions run.
- **Never hand-merge into `main`** while sessions run: all merges go through the merge command so the lock serializes them.
- **Uncommitted files are local.** Untracked plans and scratch files in a worktree are not visible in the primary checkout and disappear with the worktree.
- **A leading `cd <primary checkout> && ` may be dropped.** Claude Code removes a leading `cd <session directory> && ` as redundant, comparing against the session's recorded directory, which can lag behind the shell's actual directory right after a `cd` into or out of a worktree; the rest of the command then runs where the shell already is. Use absolute paths and `git -C <path>`, and check with `pwd` when it matters.
- **Never link folders between checkouts.** A junction or symlink from a worktree into another checkout (such as a shared `node_modules`) is followed by Git for Windows' `git worktree remove`, which deletes the target's contents. Each worktree installs its own dependencies.
- **Run merge and delete from the primary checkout** (`python <primary>/scripts/worktree.py merge <name>` with the absolute path, or after changing there). Started from inside the worktree, the merge still lands, but on Windows the shell's handle on the directory can keep it from being removed; the command then says so, and `delete <name> --force` from the primary checkout finishes the cleanup.
