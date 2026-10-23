"""Expected domain rejections and uncertain committed mutations."""
from __future__ import annotations



class NotFoundError(ValueError):
    """A requested domain entity does not exist."""


class PostMutationError(ValueError):
    """Persistence completed, but a later step could not finish the command."""
