"""Shared CLI presentation and one-call remote dispatch."""
from __future__ import annotations

import json
import io
import os
import re
import shlex
from contextlib import redirect_stdout

from . import client

OUTPUT_BUDGET = 10_000
PAGE_BUDGET = 7_000


def nonnegative(value: str) -> int:
    import argparse
    try:
        number = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError("Eine ganze Zahl ab 0 angeben.") from None
    if number < 0:
        raise argparse.ArgumentTypeError("Eine ganze Zahl ab 0 angeben.")
    return number


def add_paging(parser, *, limit: bool = True) -> None:
    if limit:
        parser.add_argument("--limit", type=nonnegative,
                            help="Höchstens N Einträge. Ohne Angabe: Text bis 40 je Seite, JSON vollständig.")
    parser.add_argument("--offset", type=nonnegative, default=0,
                        help="Einträge vor dieser Position überspringen (ab 0). Ohne Angabe: erste Seite.")
    parser.add_argument("--text-offset", type=nonnegative, default=0,
                        help="Zeichen innerhalb eines überlangen Eintrags überspringen; aus Weiter übernehmen. Ohne Angabe: 0.")


def command_line(args, *, replace: dict | None = None, command: list[str] | None = None) -> str:
    """Render a copyable command, preserving the selected endpoint and filters."""
    values = list(getattr(args, "_argv", [])) if command is None else list(command)
    if command is not None:
        for index, value in enumerate(values[1:], 1):
            if value.startswith("-"):
                values.insert(index, "--")
                break
    if command is not None and getattr(args, "server", None):
        values = ["--server", args.server, *values]
    for flag, value in (replace or {}).items():
        terminator = values.index("--") if "--" in values else len(values)
        positional = values[terminator:]
        cleaned = []
        skip = False
        for part in values[:terminator]:
            if skip:
                skip = False
            elif part == flag:
                skip = True
            elif not part.startswith(flag + "="):
                cleaned.append(part)
        values = cleaned
        if value is not None:
            values.extend([flag, str(value)])
        values.extend(positional)
    def quote(value):
        if os.name == "nt":
            return value if re.fullmatch(r"[\w./:@%+=,\\-]+", value) else "'" + value.replace("'", "''") + "'"
        return shlex.quote(value)
    return "gusto " + " ".join(quote(str(value)) for value in values)


def page(args, values: list, title: str, render) -> None:
    """Bound readable lists; preserve the original JSON unless slicing is requested."""
    offset = getattr(args, "offset", 0)
    limit = getattr(args, "limit", None)
    if args.json:
        if isinstance(values, list) and (offset or limit is not None):
            values = values[offset:None if limit is None else offset + limit]
        dump(values)
        return
    limit = 40 if limit is None else limit
    if limit == 0:
        print("Hinweis: --limit 0 fordert keine Einträge an.")
    end = min(len(values), offset + limit)
    rendered = []
    used = 0
    next_offset = None
    next_text = 0
    text_offset = getattr(args, "text_offset", 0)
    for index in range(offset, end):
        output = io.StringIO()
        with redirect_stdout(output):
            render(values[index])
        item = "\n".join("  " + line for line in output.getvalue().rstrip("\n").split("\n")) + "\n"
        start = text_offset if index == offset else 0
        if start > len(item):
            raise ValueError("--text-offset liegt hinter dem Eintrag. Mit --text-offset 0 erneut lesen.")
        remainder = item[start:]
        if rendered and used + len(remainder) > PAGE_BUDGET:
            next_offset = index
            break
        if len(remainder) > PAGE_BUDGET:
            rendered.append(remainder[:PAGE_BUDGET])
            next_offset, next_text = index, start + PAGE_BUDGET
            break
        rendered.append(remainder)
        used += len(remainder)
    if next_offset is None and end < len(values) and limit:
        next_offset = end
    print(f"{title}: {len(values)} insgesamt; Einträge {offset + 1 if rendered else 0}–{offset + len(rendered) if rendered else 0}.")
    for index, item in enumerate(rendered):
        # A chunk starting within a field still belongs to its data block.
        prefix = "  " if index == 0 and text_offset else ""
        print(prefix + item.rstrip("\n"))
    if next_offset is not None:
        print("Weiter: " + command_line(args, replace={"--offset": next_offset,
                                                       "--text-offset": next_text or None}))


def recipe_result(args, recipe: dict, *, heading: str = "Rezept", archived: bool = False,
                  mutation: bool = False) -> None:
    """Show enough saved state to continue without a verification read."""
    print(f"{heading}: {text(recipe.get('title', recipe['slug']))}")
    print(f"Slug: {recipe['slug']}")
    print(f"Dauer: {str(recipe['duration_min']) + ' Minuten' if recipe.get('duration_min') else 'unbekannt'}")
    print(f"Portionen: {recipe.get('servings') or 'unbekannt'}")
    tags = ", ".join(text(tag) for tag in recipe.get("tags", [])) or "keine"
    if len(tags) <= 800:
        print("Tags: " + tags)
    else:
        print("Tags: siehe Rezeptdetails unten.")
    if recipe.get("revision"):
        print(f"Revision: {recipe['revision']}")
    if recipe.get("archived_at"):
        print(f"Archiviert am: {text(recipe['archived_at'])}")
    images = recipe.get("images", [])
    if images:
        print(f"Bilder: {len(images)}; Top-Bild: {recipe.get('cover_image_id') or 'keines'}")
    for warning in recipe.get("warnings", []):
        print("Hinweis: " + text(warning["message"]))
    if "content" not in recipe:
        return
    # Include unusually long metadata in the paged data block instead of losing it.
    content = (("Tags: " + tags + "\n\n") if len(tags) > 800 else "") + recipe["content"]
    offset = 0 if mutation else getattr(args, "offset", 0)
    if offset > len(content):
        raise ValueError(f"--offset liegt hinter dem Rezeptinhalt ({len(content)} Zeichen).")
    chunk = content[offset:offset + 5_000]
    rendered = "\n".join("    " + text(line) for line in chunk.split("\n"))
    while len(rendered) > 6_000:
        chunk = chunk[:max(1, len(chunk) * 6_000 // (len(rendered) + 1))]
        rendered = "\n".join("    " + text(line) for line in chunk.split("\n"))
    print(f"Rezeptdetails (Zeichen {offset}–{offset + len(chunk)} von {len(content)}):")
    print(rendered)
    if offset + len(chunk) < len(content):
        cmd = ["archive", "show", recipe["slug"]] if archived else ["show", recipe["slug"]]
        print("Weiter: " + command_line(args, command=cmd, replace={"--offset": offset + len(chunk)}))


def run_presented(args) -> None:
    """Keep unusually large mutation receipts bounded without repeating a write."""
    followups = {
        "new": ["show", getattr(args, "slug", "")],
        "set": ["show", getattr(args, "slug", "")],
        "content": ["show", getattr(args, "slug", "")],
        "categories": ["categories", "list"],
        "image": ["image", "list", getattr(args, "slug", "")],
        "shopping": ["shopping", "list"],
        "favorites": ["favorites", "show", getattr(args, "need", "")],
    }
    followup = followups.get(args.command)
    if args.json or followup is None:
        args.func(args)
        return
    output = io.StringIO()
    try:
        with redirect_stdout(output):
            args.func(args)
    finally:
        result = output.getvalue()
        if len(result) <= OUTPUT_BUDGET:
            print(result, end="")
        else:
            # The result confirms the mutation first. The rest is a read of the
            # resulting collection/item, never a replay of the original command.
            if args.command == "new":
                slug_line = next((line for line in result.splitlines() if line.startswith("Slug: ")), None)
                followup = ["show", slug_line[6:]] if slug_line else ["list"]
            if args.command == "favorites" and not getattr(args, "need", None):
                followup = ["favorites", "list"]
            print(result[:8_000].rstrip())
            print("Hinweis: Die Ausgabe ist begrenzt; lies den vollständigen gespeicherten Zustand weiter.")
            print("Weiter: " + command_line(args, command=followup))


def dump(data) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2))


def text(value: object) -> str:
    """Keep stored field values on one line, outside CLI message structure."""
    return "".join(
        character if character.isprintable() else ascii(character)[1:-1]
        for character in str(value)
    )


def remote(args, operation: str, arguments: dict | None = None, *,
            attachments: list[dict] | None = None):
    result = client.command(
        operation,
        arguments,
        attachments=attachments,
        server_url=getattr(args, "server", None),
    )
    if not args.json and not getattr(args, "_target_printed", False):
        print("Hinweis: Service " + text(client.resolve_server_url(getattr(args, "server", None))))
        args._target_printed = True
    return result
