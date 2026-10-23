# Development

The development domain owns the developer harness: the quality gates and their runner, the browser and PWA checks, the commit hook, the push check, worktrees, CI for pull requests and `main`, and the static-check configuration in `pyproject.toml`. Release building and the release pipeline belong to `lifecycle.md`.

## Overview

Gusto has no pytest suite. Each gate is a standalone script; the Python test scripts stop on a failed assertion, while the browser scripts collect failed checks. `scripts/run_quality.py` runs the gates sequentially and stops on a failed gate. Local runs, the push check and CI use this runner.

The Vorch tooling (`scripts/commit_check.py`, `scripts/commit_authorship.py`, `scripts/domain_maps.py`, `scripts/worktree.py`, `scripts/push.py`, `.githooks/`) is adapted from the Vorch templates; project-specific configuration sits at the top of each script.

## Interfaces

- `scripts/run_quality.py scripts|browser|all` runs a group; a registered path such as `tests/test_recipes.py` runs one gate with the same isolation and timeout. `SCRIPT_CHECKS` holds every `tests/test_*.py`; `BROWSER_CHECKS` holds `scripts/browser_check.py` and `scripts/pwa_check.py`. A new test file must be added to `SCRIPT_CHECKS`; `tests/test_ci.py` checks that the runner covers every gate.
- `scripts/domain_maps.py` reports map sizes, routing and punctuation defects. `--check` returns 1 on blocking findings, as used by push and CI; size budgets stay advisory. The commit hook checks only the staged maps, project context and glossary.
- `scripts/browser_check.py` and `scripts/pwa_check.py` start their own server on a free port with a fresh store under `.tmp/browser-*/` or `.tmp/pwa-*/`, verify that `/api/v1/health` reports that store as `data_path`, and drive headless Chromium through Playwright. They need `python -m playwright install chromium` for the installed Playwright version; a missing browser build fails at launch with "Executable doesn't exist".
- `pyproject.toml` extras: `web` (service runtime), `test` (`httpx2`, `playwright`, `pyyaml`, plus `setuptools` and `wheel` for the packaging gate's build without isolation), `dev` (static-check tools, pinned exactly). Build-system requirements alone exist only in pip's temporary build environment; they do not prepare the gate's interpreter. `[tool.ruff.lint]` selects only error rules (`E4`, `E7`, `E9`, `F`); there is deliberately no formatter.

## Commit hook

`.githooks/pre-commit` and `pre-merge-commit` call `scripts/commit_authorship.py identity` and then `scripts/commit_check.py`; `commit-msg` strips attribution lines; `pre-push` refuses pushed commits with an AI tool identity or attribution. `.claude/settings.json` enables the hooks (`core.hooksPath`) at every Claude Code session start and pins the commit identity.

`commit_check.py` runs `ruff check` (with `--fix` for completely staged files) on staged `.py` files and the domain-map checks of `scripts/domain_maps.py` on staged maps, `.vorch/PROJECT.md` and `.vorch/GLOSSARY.md`. The tools run from the checkout's `.venv` (`project_python`), falling back to the interpreter running the hook. Before linting, `pinned_tool_problems` compares every `name==version` pin in `pyproject.toml` with the installed version and blocks with the install command when they differ. A partially staged file is checked as it appears in the working tree, without applying fixes or staging its remaining changes.

Side-effect imports of `gusto` in tests and scripts (they configure the checkout's temp directory) carry `# noqa: E402, F401`; removing the `F401` part lets `ruff check --fix` delete the import and breaks temp isolation.

## Push check and CI

`scripts/push.py` checks out the commit `main` points to in a private checkout under `.worktrees/`, creates its own `.venv`, installs `.[web,test,dev]`, runs `ruff check .`, the domain-map check, the script gates, `playwright install chromium` and the browser gates, and pushes exactly that commit as a fast-forward only when all passed. Dependency failure skips dependent steps. A remote ahead of the checked commit prevents the push.

A repository-wide OS lock serializes selection, checks and publication; an overlapping invocation fails before fetching or changing checkouts. Each run reserves a unique checkout and log directory and removes only its own checkout through Git. Logs and abandoned or unrecognized checkouts are retained; cleanup refusal blocks publication and never falls back to recursive deletion. Recovery: `push-workflow.md` -> Gotchas.

`--no-push` runs all checks even when the remote already has `main`. Releases add `--tag v<version>`: the wrapper requires an annotated tag of the checked commit matching its package version, and publishes only that tag together with `main` atomically. `tests/test_push.py` guards this using a local throwaway remote. Release procedure: `.vorch/workflows/release-workflow.md`.

`.github/workflows/quality.yml` runs on pull requests, pushes to `main`, manual dispatch and as a reusable workflow for `release.yml`. Jobs: `static` (ruff, domain maps and `commit_authorship.py history` over the full history), `script-tests` (every script gate on Windows and Linux with Python 3.10 and 3.14, plus Python, JavaScript, shell and PowerShell syntax checks), and `browser` (Chromium browser and PWA checks on both systems). The additional language syntax checks run in CI, not in `push.py`. Third-party actions are pinned to full commit SHAs; `tests/test_ci.py` enforces that and the job structure `release.yml` depends on. Dependabot proposes action updates as pull requests.

## Worktrees

`scripts/worktree.py create <name>` creates `.worktrees/<name>` on a new branch from `main`, writes the git-ignored `.vorch-worktree` marker with a port starting at 8110 (10-port increments above `PORT_BASE=8100`), and runs the setup: a new `.venv` inside the worktree and an editable install of `.[web,test,dev]`. Playwright browsers live in the per-user cache and are shared. The checkout's `gusto.settings.json` points every checkout at the same `gusto-dev` store, so a worktree that starts a server must set `GUSTO_HOME` (`.vorch/workflows/live-testing-workflow.md`).

`merge` prepares the merged dependencies in its private landing checkout before running the commit hook. Setup or hook failure leaves `main` unchanged and removes that landing checkout; success prints the hook's report before fast-forwarding `main`. `tests/test_worktree.py` owns this safety contract using throwaway Git repositories and local test identities.

## Constraints & Gotchas

- The quality runner gives each gate temporary home, AppData and default store paths under `.tmp/`, retaining access to the installed Playwright browser cache. Direct `python tests/test_*.py` invocations bypass this safety net; store-owning gates still set their own `GUSTO_HOME`, and lifecycle gates use explicit temporary roots.
- Each gate has a 180-second deadline, reports its duration, and fails with exit 124 on timeout; the runner kills the gate's process tree. Individual assertions have no runner-enforced timeout. CI additionally limits whole script and browser jobs to 30 minutes.
- `tests/test_agent_contract.py` locks the CLI command set to the skill's CLI reference and checks that `AGENTS.md` routes Gusto operation to `skill/gusto/SKILL.md` and `CLAUDE.md` only imports `AGENTS.md`.

## References

Read these only when your task matches - not by default.

- Writing or changing a test or a quality gate -> `development/testing.md`
