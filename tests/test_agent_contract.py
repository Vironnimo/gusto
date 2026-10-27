"""Durable checks for the fresh-agent command and documentation contract."""
from __future__ import annotations

import argparse
import re
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
    "shopping add-many", "shopping add-recipe", "shopping preview-recipe", "shopping check",
    "shopping uncheck", "shopping remove", "shopping remove-done",
    "shopping clear", "serve", "status", "start", "stop", "restart",
    "update", "uninstall", "backup create", "backup preview", "backup restore",
    "backup safety-download",
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
    frontmatter = re.match(r"\A---\n(.*?)\n---\n", skill, re.DOTALL)
    check(frontmatter is not None, "operating skill needs discoverable frontmatter")
    metadata = dict(
        line.split(":", 1) for line in frontmatter.group(1).splitlines() if ":" in line
    )
    check(metadata.get("name", "").strip() == skill_root.name,
          "the installed skill name must match its folder")
    description = metadata.get("description", "").strip()
    check(0 < len(description) <= 250 and "Use when" in description,
          "skill discovery needs a compact task trigger")
    # Guard exact interface identifiers, not headings or editorial sentences.
    # Behavioral meaning is reviewed with the skill; prose remains editable.
    for required in (
        "home --json",
        "server_url",
        "server_reachable",
        "server_data_path",
        "outcome_unknown",
        "recovery_path",
        "--file",
        "categories assign",
        "archive purge",
        "install-skill vbot",
        "references/telegram.md",
        "references/shopping-trip.md",
    ):
        check(required in skill, f"main skill lost critical guidance: {required}")
    trip = (skill_root / "references/shopping-trip.md").read_text(encoding="utf-8")
    check("--store" in trip and "shopping list" in trip and "products" in trip,
          "the store trip reference must retain its CLI and ranking inputs")
    check("done_label" not in skill and "channel_send" in telegram
          and "checklist" in telegram and "done_label" in telegram
          and "shopping check" in telegram and "shopping uncheck" in telegram,
          "host-specific Telegram detail must stay routed out of the main skill")

    documented_calls = re.findall(r"`([^`]+)`", cli_reference, re.DOTALL)
    documented_calls = [" ".join(call.split()) for call in documented_calls]
    documented_calls = [
        verb + (" " + tail if separator else "")
        for call in documented_calls
        for head, separator, tail in [call.partition(" ")]
        for verb in head.split("|")
    ]
    for name in EXPECTED_COMMANDS:
        check(any(re.match(r"(?:gusto )?" + re.escape(name) + r"(?:\s|$)", call)
                  for call in documented_calls),
              f"full CLI reference does not name command: {name}")
    for reference in skill_root.rglob("*.md"):
        content = reference.read_text(encoding="utf-8")
        for relative in re.findall(r"`(references/[^`\s]+\.md)`", content):
            check((skill_root / relative).is_file(),
                  f"broken skill reference in {reference.name}: {relative}")
            check(reference == skill_root / "SKILL.md",
                  "route optional workflows from the body instead of chaining references")
    for required in (
        "~/.vbot/skills/gusto",
        "--app-root PATH",
        '"status": "installed"',
        '"overwritten": true',
    ):
        check(required in cli_reference,
              f"CLI reference lost a result/safety contract: {required}")
    for required in (
        "gusto install-skill vbot",
        "~/.vbot/skills/gusto",
        "--keep-data",
        "--delete-data",
        "gusto update",
    ):
        check(required in normalized_installation,
              f"installation reference lost skill lifecycle guidance: {required}")

    entry = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    check("skill/gusto/SKILL.md" in entry,
          "the repository agent guide must route Gusto operation to the shipped skill")
    check("sort the tag into `categories.json`" not in entry,
          "AGENTS.md still instructs agents to bypass the service")
    check((ROOT / "CLAUDE.md").read_text(encoding="utf-8").strip() == "@AGENTS.md",
          "CLAUDE.md must import AGENTS.md instead of carrying a second guide")
    check(not (ROOT / "AGENTS.override.md").exists(),
          "AGENTS.override.md would shadow AGENTS.md for Codex")

    print(f"OK - {checks} fresh-agent contract checks passed")


if __name__ == "__main__":
    main()
