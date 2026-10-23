# Development

The development domain owns the developer harness: the quality gates and their runner, the browser and PWA checks, the commit hook, the push check, worktrees, CI for pull requests and `main`, and the static-check configuration in `pyproject.toml`. Release building and the release pipeline belong to `lifecycle.md`.

## Overview

Gusto has no pytest suite. Every gate is a standalone script that runs its checks top to bottom and exits non-zero on the first failure; `scripts/run_quality.py` lists the gates and runs them in sequence. The same gates run locally, in the push check and in CI, so a change that passes `python scripts/run_quality.py all` locally is expected to pass the push.

The Vorch tooling (`scripts/commit_check.py`, `scripts/commit_authorship.py`, `scripts/domain_maps.py`, `scripts/worktree.py`, `scripts/push.py`, `.githooks/`) is shared with other repositories through the Vorch templates; project-specific configuration sits at the top of each script.

## Interfaces

- `scripts/run_quality.py scripts|browser|all` - the gate list. `SCRIPT_CHECKS` holds every `tests/test_*.py`; `BROWSER_CHECKS` holds `scripts/browser_check.py` and `scripts/pwa_check.py`. A new test file must be added to `SCRIPT_CHECKS`; `tests/test_ci.py` checks that CI and the runner cover every gate.
- `scripts/browser_check.py` and `scripts/pwa_check.py` start their own server on a free port with a fresh store under `.tmp/browser-*/` or `.tmp/pwa-*/`, verify that `/api/v1/health` reports that store as `data_path`, and drive headless Chromium through Playwright. They need `python -m playwright install chromium` for the installed Playwright version; a missing browser build fails at launch with "Executable doesn't exist".
- `pyproject.toml` extras: `web` (service runtime), `test` (`httpx2`, `playwright`, `pyyaml`), `dev` (static-check tools, pinned exactly). `[tool.ruff.lint]` selects only error rules (`E4`, `E7`, `E9`, `F`); there is deliberately no formatter.

## Commit hook

`.githooks/pre-commit` and `pre-merge-commit` call `scripts/commit_authorship.py identity` and then `scripts/commit_check.py`; `commit-msg` strips attribution lines; `pre-push` refuses pushed commits with an AI tool identity or attribution. `.claude/settings.json` enables the hooks (`core.hooksPath`) at every Claude Code session start and pins the commit identity.

`commit_check.py` runs `ruff check` (with `--fix` for completely staged files) on staged `.py` files and the domain-map checks of `scripts/domain_maps.py` on staged maps and `.vorch/PROJECT.md`. The tools run from the checkout's `.venv` (`project_python`), falling back to the interpreter running the hook. Before linting, `pinned_tool_problems` compares every `name==version` pin in `pyproject.toml` with the installed version and blocks with the install command when they differ; a fresh clone without `pip install -e ".[web,test,dev]"` therefore cannot commit Python files.

Side-effect imports of `gusto` in tests and scripts (they configure the checkout's temp directory) carry `# noqa: E402, F401`; removing the `F401` part lets `ruff check --fix` delete the import and breaks temp isolation.

## Push check and CI

`scripts/push.py` checks out the commit `main` points to in a private checkout under `.worktrees/`, creates its own `.venv`, installs `.[web,test,dev]`, runs `ruff check .`, the script gates, `playwright install chromium` and the browser gates, and pushes exactly that commit as a fast-forward only when all passed. The complete local run takes about two minutes, most of it `tests/test_cli.py` and the two browser checks.

`.github/workflows/quality.yml` runs on pull requests, pushes to `main`, manual dispatch and as a reusable workflow for `release.yml`. Jobs: `static` (ruff and `commit_authorship.py history` over the full history), `script-tests` (every script gate on Windows and Linux with Python 3.10 and 3.14, plus syntax checks of JavaScript, shell and PowerShell), and `browser` (Chromium browser and PWA checks on both systems). Third-party actions are pinned to full commit SHAs; `tests/test_ci.py` enforces that and the job structure `release.yml` depends on. Dependabot proposes action updates as pull requests.

## Worktrees

`scripts/worktree.py create <name>` creates `.worktrees/<name>` on a new branch from `main`, writes the git-ignored `.vorch-worktree` marker with a port from the block starting at 8100 (10 ports per worktree), and runs the setup: a new `.venv` inside the worktree and an editable install of `.[web,test,dev]`. Playwright browsers live in the per-user cache and are shared. The checkout's `gusto.settings.json` points every checkout at the same `gusto-dev` store, so a worktree that starts a server must set `GUSTO_HOME` (`.vorch/workflows/live-testing-workflow.md`).

## Constraints & Gotchas

- Every gate must leave real data alone: tests set `GUSTO_HOME` to a fresh directory under `.tmp/` before importing Core or starting a server. A test that forgets it writes to the shared `gusto-dev` store.
- Gates run sequentially in one process each; a gate that hangs blocks the push check and CI (CI jobs time out after 30 minutes). There is no per-check timeout.
- `tests/test_agent_contract.py` locks the CLI command set to the skill's CLI reference and checks that `AGENTS.md` routes Gusto operation to `skill/gusto/SKILL.md` and `CLAUDE.md` only imports `AGENTS.md`.

## References

Read these only when your task matches - not by default.

- Writing or changing a test or a quality gate -> `development/testing.md`
