"""Hermetic checks for installing the bundled Gusto agent skill."""
from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from gusto import __version__, skill_install  # noqa: E402


SOURCE = ROOT / "skill" / "gusto"
checks = 0


def check(condition, message):
    global checks
    assert condition, message
    checks += 1


def expect_valueerror(function, *args, **kwargs):
    try:
        function(*args, **kwargs)
    except ValueError as error:
        return str(error)
    raise AssertionError(f"expected ValueError from {function.__name__}")


def snapshot(path: Path) -> dict[str, bytes]:
    return {
        item.relative_to(path).as_posix(): item.read_bytes()
        for item in path.rglob("*") if item.is_file()
    }


def main():
    with tempfile.TemporaryDirectory(prefix="gusto-skill-test-") as temporary:
        root = Path(temporary)
        home = root / "user"
        vbot = home / ".vbot"

        missing = expect_valueerror(
            skill_install.install_skill, "vbot", user_home=home, source=SOURCE,
        )
        check("nicht gefunden" in missing and not (vbot / "skills").exists(),
              "missing vBot home must fail without creating host directories")

        vbot.mkdir(parents=True)
        dry_run = skill_install.install_skill(
            "vbot", dry_run=True, user_home=home, source=SOURCE,
        )
        # Use the installer's canonical path. Python 3.10 on Windows may resolve
        # the temporary user profile through its equivalent 8.3 short name.
        destination = Path(dry_run["destination"])
        check(dry_run["status"] == "dry_run"
              and dry_run["overwritten"] is False
              and dry_run["gusto_version"] == __version__
              and not destination.exists(),
              "dry-run must report the exact plan without writing")

        installed = skill_install.install_skill(
            "vbot", user_home=home, source=SOURCE,
        )
        check(installed["status"] == "installed"
              and installed["overwritten"] is False
              and snapshot(destination) == snapshot(SOURCE),
              "first install must copy the complete canonical skill")

        (destination / "SKILL.md").write_text("locally changed", encoding="utf-8")
        (destination / "stale.txt").write_text("stale", encoding="utf-8")
        replaced = skill_install.install_skill(
            "vbot", user_home=home, source=SOURCE,
        )
        check(replaced["overwritten"] is True
              and snapshot(destination) == snapshot(SOURCE)
              and not (destination / "stale.txt").exists(),
              "repeat install must replace, not merge, an existing skill")
        check(not list((vbot / "skills").glob(".gusto-skill-*")),
              "successful replacement must leave no staging or backup directory")

        before_failed_replace = snapshot(destination)
        real_replace = skill_install.os.replace
        failed_once = False

        def fail_publish(source, target):
            nonlocal failed_once
            source_path = Path(source)
            if (not failed_once
                    and source_path.name.startswith(".gusto-skill-")
                    and Path(target) == destination):
                failed_once = True
                raise OSError("forced publish failure")
            return real_replace(source, target)

        with patch.object(skill_install.os, "replace", side_effect=fail_publish):
            failed = expect_valueerror(
                skill_install.install_skill,
                "vbot", user_home=home, source=SOURCE,
            )
        check("forced publish failure" in failed
              and snapshot(destination) == before_failed_replace
              and not list((vbot / "skills").glob(".gusto-skill-*")),
              "failed publication must restore the complete previous skill")

        incomplete = root / "incomplete"
        incomplete.mkdir()
        (incomplete / "SKILL.md").write_text("x", encoding="utf-8")
        error = expect_valueerror(
            skill_install.install_skill,
            "vbot", user_home=home, source=incomplete,
        )
        check("unvollständig" in error and snapshot(destination) == snapshot(SOURCE),
              "an incomplete source must be rejected before replacing the target")

        runtime = root / "runtime"
        packaged = runtime / "share" / "gusto" / "skill" / "gusto"
        shutil.copytree(SOURCE, packaged)
        resolved = skill_install.bundled_skill_path(
            prefix=runtime, source_root=root / "not-a-checkout",
        )
        check(resolved == packaged.resolve(),
              "installed runtimes must resolve their wheel-owned skill payload")

        unsupported = expect_valueerror(
            skill_install.install_skill,
            "codex", user_home=home, source=SOURCE,
        )
        check("Unterstützt: vbot" in unsupported,
              "unsupported hosts must fail with the currently valid choice")

    print(f"OK - {checks} skill installation checks passed")


if __name__ == "__main__":
    main()
