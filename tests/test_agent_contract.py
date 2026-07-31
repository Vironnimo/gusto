"""Durable checks for the fresh-agent command and documentation contract."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from gusto.cli import build_parser  # noqa: E402


EXPECTED_COMMANDS = {
    "list", "search", "tags",
    "categories list", "categories add", "categories set",
    "categories remove", "categories assign", "categories unassign",
    "categories tag-move", "install-skill", "home", "show", "new", "edit",
    "content set", "cooked", "log", "suggest", "check", "set", "delete",
    "archive list", "archive show", "archive restore", "archive purge",
    "image list", "image add", "image set", "image cover", "image remove",
    "favorites list", "favorites show", "favorites match", "favorites add",
    "favorites set", "favorites remove", "favorites alias-add",
    "favorites alias-remove", "favorites product-add",
    "favorites product-set", "favorites product-move",
    "favorites product-remove", "shopping list", "shopping add",
    "shopping add-many", "shopping add-recipe", "shopping check",
    "shopping uncheck", "shopping remove", "shopping remove-done",
    "shopping clear", "serve", "status", "start", "stop", "restart",
    "update", "uninstall",
}
checks = 0


def check(condition, message):
    global checks
    assert condition, message
    checks += 1


def leaf_parsers(parser: argparse.ArgumentParser, prefix=()):
    subparsers = next(
        (action for action in parser._actions
         if isinstance(action, argparse._SubParsersAction)),
        None,
    )
    if subparsers is None:
        yield " ".join(prefix), parser
        return
    for name, child in subparsers.choices.items():
        yield from leaf_parsers(child, (*prefix, name))


def main():
    commands = dict(leaf_parsers(build_parser()))
    check(set(commands) == EXPECTED_COMMANDS,
          "agent command surface changed without updating its explicit contract")
    for name, parser in commands.items():
        check(any("--json" in action.option_strings for action in parser._actions),
              f"agent-facing command lacks --json: {name}")

    skill_root = ROOT / "skill/gusto"
    skill = (skill_root / "SKILL.md").read_text(encoding="utf-8")
    cli_reference = (skill_root / "references/cli.md").read_text(encoding="utf-8")
    installation = (
        skill_root / "references/installation.md"
    ).read_text(encoding="utf-8")
    telegram = (skill_root / "references/telegram.md").read_text(encoding="utf-8")
    normalized_installation = " ".join(installation.split())

    check(len(skill.encode("utf-8")) <= 12_000,
          "main skill became too large for a focused fresh-agent entry point")
    for required in (
        "Route the task before acting",
        "Non-negotiable operating contract",
        "home --json",
        "server_data_path",
        "Never silently switch",
        "categories assign",
        "archive purge",
        "install-skill vbot",
        "references/telegram.md",
    ):
        check(required in skill, f"main skill lost critical guidance: {required}")
    check("chk:" not in skill and "chk:" in telegram and "run:done" in telegram,
          "host-specific Telegram detail must stay routed out of the main skill")

    for name in EXPECTED_COMMANDS:
        check(name in cli_reference,
              f"full CLI reference does not name command: {name}")
    for required in (
        "~/.vbot/skills/gusto",
        "fully replaced",
        "--app-root PATH",
        '"status": "installed"',
        '"overwritten": true',
    ):
        check(required in cli_reference,
              f"CLI reference lost a result/safety contract: {required}")
    for required in (
        "matching agent-skill payload",
        "gusto install-skill vbot",
        "does not silently modify an agent host",
        "does not remove `~/.vbot/skills/gusto`",
    ):
        check(required in normalized_installation,
              f"installation reference lost skill lifecycle guidance: {required}")

    for guide_name in ("CLAUDE.md", "AGENTS.override.md"):
        guide = (ROOT / guide_name).read_text(encoding="utf-8")
        for required in (
            "gusto categories assign",
            "gusto install-skill vbot",
            "gusto/skill_install.py",
        ):
            check(required in guide,
                  f"{guide_name} is stale: missing {required}")
        check("sort the tag into `categories.json`" not in guide,
              f"{guide_name} still instructs agents to bypass the service")

    print(f"OK - {checks} fresh-agent contract checks passed")


if __name__ == "__main__":
    main()
