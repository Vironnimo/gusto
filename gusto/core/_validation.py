"""Shared scalar store validation."""
from __future__ import annotations



def _positive_recipe_number(value: int | None, label: str) -> None:
    """Validate optional recipe counts shared by CLI and web."""
    if value is not None and (
            isinstance(value, bool) or not isinstance(value, int) or value < 1):
        raise ValueError(f"{label} muss eine positive ganze Zahl sein.")


def _no_newlines(value: str, label: str) -> None:
    """Reject embedded line breaks in single-line free-text fields.

    Store hygiene only: matching already normalizes whitespace, so persisted
    line breaks change nothing except rendering dirt in CLI lists and web rows.
    """
    if "\n" in value or "\r" in value:
        raise ValueError(f"{label} darf keine Zeilenumbrüche enthalten.")
