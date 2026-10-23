# Release Workflow

How to publish a Gusto release when the user asks for one. A release is a version tag `v<version>`; `.github/workflows/release.yml` turns it into public assets that `install.ps1`, `install.sh` and `gusto update` consume. What the pipeline builds and checks: `lifecycle.md`.

## Steps

1. **Start from a pushed, green `main`.** The primary checkout is on `main` without uncommitted work, everything is pushed through `.vorch/workflows/push-workflow.md`, and the CI run of that push is green.
2. **Choose the version** (`MAJOR.MINOR.PATCH`). Fixes raise the patch number, new user-visible capabilities the minor number. Ask the user when the choice is not obvious from the changes since the last tag (`git log --oneline $(git describe --tags --abbrev=0)..main`).
3. **Bump the version in both places:** `version` in `pyproject.toml` and `__version__` in `gusto/__init__.py`. `tests/test_packaging.py` fails when they disagree; run it. Version strings inside example output in `skill/gusto/references/cli.md` are illustrative and stay unless the output shape changes.
4. **Commit and push** the bump as `chore(release): <version>` through the push workflow, and wait for its CI run to be green.
5. **Tag and push the tag**, on exactly that commit:
   ```bash
   git tag -a v<version> -m "Gusto <version>" <commit>
   python scripts/push.py --tag v<version>
   ```
   The wrapper requires an annotated tag of the selected `main` commit whose name matches the committed package version. It reruns every check even when that commit is already remote, then publishes the captured commit and tag object atomically, without overwriting a remote tag or including any other tags. `--no-push` checks the same selection without publishing it. A local tag changed during the checks is refused; do not move a tag to make a failed release pass.
6. **Watch the release run to its end** (`gh run list --workflow release.yml`, `gh run watch <id>`). It validates that the tag equals the package version, reruns the complete quality gates, builds the assets once, smoke-installs them on fresh Windows and Linux runners, attests provenance and only then publishes. A failed run publishes nothing; fix the cause on `main`, then release a new patch version. Never move or reuse a published tag.
7. **Verify the result:** the GitHub release lists the manifest and its assets, and `gusto update --check --json` on an installed instance reports the new version. Report the release URL to the user.
