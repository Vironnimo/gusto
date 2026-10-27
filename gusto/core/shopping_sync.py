"""Store generations keep pre-restore offline shopping states out of a restored store."""
from __future__ import annotations

import json
from pathlib import Path
import uuid

from gusto.core import storage


def generation_path() -> Path:
    return storage.data_dir() / "shopping-generation.json"


def shopping_generation() -> str:
    path = generation_path()
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return "initial"
    except (OSError, UnicodeError, ValueError) as error:
        raise ValueError("Der Wiederherstellungsstand der Einkaufsliste ist nicht lesbar.") from error
    generation = value.get("generation") if isinstance(value, dict) else None
    if not isinstance(generation, str) or not generation:
        raise ValueError("Der Wiederherstellungsstand der Einkaufsliste ist ungültig.")
    return generation


def reset_generation_unlocked() -> str:
    """Invalidate previous offline snapshots while the shopping lock is held."""
    generation = uuid.uuid4().hex
    storage.write_json(generation_path(), {"generation": generation})
    return generation
