# Bundled skill delivery

Owner: `gusto/skill_install.py`; `cli.md` owns the shipped skill's content and operating instructions. Install/update place the payload in their runtime but never touch agent-host skills automatically.

`bundled_skill_path()` chooses `skill/gusto/` from a checkout with `pyproject.toml`, otherwise `<sys.prefix>/share/gusto/skill/gusto`. The source belongs to the executing Gusto version, not whichever unrelated installation is active on the machine. Missing payload is an installation-repair/update error.

The delivery registry supports `vbot`. Its `~/.vbot` must already exist and be a directory; Gusto does not initialize a host. `install_skill()` validates all required files and rejects source symlinks, copies into a staging directory under the host's `skills/`, validates again, backs up the previous `gusto` destination and atomically publishes the replacement. This is full replacement, not merging: obsolete and locally edited files are overwritten. A failed publish restores the backup; a failed restore reports its remaining location. After successful publication backup cleanup is best-effort and never triggers destructive rollback.

The result reports `installed|dry_run`, Gusto version, source/destination, whether an existing destination was replaced and the copied files. Dry-run resolves and validates without creating the host directory or staging files. No service/API access is involved.

Packaging lists skill files explicitly in `pyproject.toml`. New required files need matching entries in `REQUIRED_SKILL_FILES`, package data and `tests/test_packaging.py`; otherwise a checkout can pass while a wheel lacks instructions. `tests/test_skill_install.py` covers replacement, rollback and host/source safety; packaging tests install the built wheel and use that runtime's bundled payload.
