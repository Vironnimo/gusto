"""Shared CLI presentation and one-call remote dispatch."""
from __future__ import annotations

import json

from . import client


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
    return client.command(
        operation,
        arguments,
        attachments=attachments,
        server_url=getattr(args, "server", None),
    )
