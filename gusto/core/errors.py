"""Expected domain rejections and uncertain committed mutations."""
from __future__ import annotations



class NotFoundError(ValueError):
    """A requested domain entity does not exist."""


class RevisionConflictError(ValueError):
    """An edit was based on an older version of a recipe."""

    def __init__(self, current_revision: str, message: str | None = None):
        super().__init__(message or (
            "Das Rezept wurde zwischenzeitlich geändert. Es wurde nichts gespeichert. "
            "Lade das Rezept erneut und prüfe deine Änderungen."
        ))
        self.current_revision = current_revision


class PostMutationError(ValueError):
    """Persistence completed, but a later step could not finish the command."""
