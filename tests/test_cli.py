"""Hermetic smoke checks for the agent-facing JSON CLI."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
HOME = Path(tempfile.mkdtemp(prefix="gusto-cli-test-"))
ENV = {**os.environ, "RECIPE_HOME": os.fspath(HOME)}
checks = 0


def check(condition, message):
    global checks
    assert condition, message
    checks += 1


def run(*arguments, expect=0):
    result = subprocess.run(
        [sys.executable, "-m", "recipe", *arguments], cwd=ROOT, env=ENV,
        text=True, encoding="utf-8", capture_output=True,
    )
    check(result.returncode == expect,
          f"{' '.join(arguments)} returned {result.returncode}: {result.stderr}")
    return result


def as_json(*arguments):
    return json.loads(run(*arguments, "--json").stdout)


def main():
    created = as_json(
        "new", "Test Suppe", "--tags", "vegan,schnell",
        "--duration", "25", "--servings", "4",
    )
    slug = created["slug"]
    check(slug == "test-suppe", "new --json must return the generated slug")

    listed = as_json("list")
    check(len(listed) == 1 and listed[0]["title"] == "Test Suppe",
          "list --json must return recipes")

    shown = as_json("show", slug)
    check(shown["content"].startswith("# Test Suppe"),
          "show --json must include Markdown content")

    changed = as_json("set", slug, "--title", "Neue Suppe", "--duration", "30")
    check(changed["title"] == "Neue Suppe" and changed["duration_min"] == 30,
          "set --json must return updated metadata")

    cooked = as_json("cooked", slug, "--date", "2026-07-13")
    check(cooked == {"slug": slug, "date": "2026-07-13", "ok": True},
          "cooked --json must confirm the log entry")
    check(as_json("log") == [{"date": "2026-07-13", "slug": slug}],
          "log --json must return the written entry")

    item = as_json("shopping", "add", "Milch", "--quantity", "1 L")
    check(item["text"] == "Milch" and item["quantity"] == "1 L",
          "shopping add --json must return the new item")
    checked = as_json("shopping", "check", item["id"])
    check(checked["checked"] is True, "shopping check --json must set checked")
    check(as_json("shopping", "list", "--pending") == [],
          "shopping list --pending --json must hide checked items")

    consistency = as_json("check")
    check(consistency["recipe_count"] == 1 and not consistency["missing_files"],
          "check --json must expose consistency state")

    missing = run("show", "does-not-exist", "--json", expect=1)
    check("Kein Rezept" in missing.stderr, "CLI errors must be written to stderr")

    deleted = as_json("delete", slug)
    check(deleted == {"slug": slug, "deleted": True},
          "delete --json must confirm deletion")

    print(f"OK - {checks} CLI checks passed (RECIPE_HOME={HOME})")


if __name__ == "__main__":
    main()
