"""Core logic of the recipe system.

All domain logic lives here. The server API and web routes adapt this module;
the normal CLI is an HTTP client of that server. No feature exists in only one
surface.

Data model:
  recipes/<slug>.md   pure Markdown content of a recipe (NO frontmatter)
  data/recipes.json   metadata of ALL recipes (source for list/search/filter)
  data/categories.json tag facets, assignment, and display order
  images/<slug>/      image files owned and stored by Gusto
  archive/<slug>/     reversible recipe archive (Markdown, metadata, images)
  data/favorites.json shared shopping needs and ranked preferred products
  images/_favorites/  preferred-product images owned and stored by Gusto
  data/log.json       cooking log: what was cooked when

The slug links both:  data/recipes.json[*].slug  <->  recipes/<slug>.md

User-facing strings (error messages, the German section headers in recipe
content) stay German on purpose; identifiers and comments are English.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sys
import tempfile
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field, asdict, fields
from datetime import date, datetime, timedelta, timezone
from functools import wraps
from pathlib import Path


# --- Paths (instance settings, overridable via GUSTO_HOME) ------------------

SETTINGS_FILENAME = "gusto.settings.json"
_AUTO_SETTINGS = object()
_LOCK_TIMEOUT_SECONDS = 30.0
_THREAD_LOCKS: dict[str, threading.Lock] = {}
_THREAD_LOCKS_GUARD = threading.Lock()
_MUTATION_CONTEXT = threading.local()
_CHANGE_HISTORY_LIMIT = 256


def default_data_root(platform_name: str | None = None,
                      environ: dict[str, str] | None = None,
                      home: str | Path | None = None) -> Path:
    """Return the normal per-user data directory for the operating system."""
    platform_name = platform_name or sys.platform
    environ = os.environ if environ is None else environ
    user_home = Path.home() if home is None else Path(home)

    if platform_name.startswith("win"):
        base = Path(environ.get("LOCALAPPDATA") or user_home / "AppData" / "Local")
        return (base / "Gusto").expanduser().resolve()
    if platform_name == "darwin":
        return (user_home / "Library" / "Application Support" / "Gusto").resolve()
    base = Path(environ.get("XDG_DATA_HOME") or user_home / ".local" / "share")
    return (base / "gusto").expanduser().resolve()


def _legacy_data_root() -> Path:
    """Location used before Gusto adopted per-user platform data directories."""
    return Path(__file__).resolve().parent.parent


def default_settings_path(package_root: str | Path | None = None,
                          prefix: str | Path | None = None) -> Path:
    """Return the settings file belonging to this source or installed instance."""
    package_root = (_legacy_data_root() if package_root is None
                    else Path(package_root))
    if (package_root / "pyproject.toml").is_file():
        return package_root / SETTINGS_FILENAME
    install_root = Path(sys.prefix) if prefix is None else Path(prefix)
    return install_root / SETTINGS_FILENAME


def _settings_data_root(settings_path: Path, default: Path) -> Path | None:
    if not settings_path.is_file():
        return None
    try:
        settings = json.loads(settings_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(
            f"Ungültige Gusto-Settings in '{settings_path}': {error}"
        ) from error
    if not isinstance(settings, dict):
        raise ValueError(
            f"Ungültige Gusto-Settings in '{settings_path}': JSON-Objekt erwartet."
        )
    configured = settings.get("data_dir")
    if not isinstance(configured, str) or not configured.strip():
        raise ValueError(
            f"Ungültige Gusto-Settings in '{settings_path}': "
            "'data_dir' muss ein nicht-leerer Text sein."
        )
    configured_path = Path(os.path.expandvars(configured)).expanduser()
    if not configured_path.is_absolute():
        configured_path = default.parent / configured_path
    return configured_path.resolve()


def contains_gusto_data(root: Path) -> bool:
    """Return whether a directory contains a recognizable Gusto store."""
    data = root / "data"
    if any((data / name).is_file() for name in (
            "recipes.json", "categories.json", "log.json",
            "shopping_list.json", "favorites.json")):
        return True
    recipes = root / "recipes"
    return recipes.is_dir() and next(recipes.glob("*.md"), None) is not None


def _resolve_data_root(environ: dict[str, str] | None = None,
                       default: Path | None = None,
                       legacy: Path | None = None,
                       settings_path: Path | None | object = _AUTO_SETTINGS,
                       ) -> tuple[Path, str, Path]:
    environ = os.environ if environ is None else environ
    configured = environ.get("GUSTO_HOME")
    default = default_data_root(environ=environ) if default is None else default
    if configured:
        return Path(configured).expanduser().resolve(), "environment", default

    if settings_path is _AUTO_SETTINGS:
        settings_path = default_settings_path()
    if settings_path is not None:
        settings_root = _settings_data_root(Path(settings_path), default)
        if settings_root is not None:
            return settings_root, "settings", default

    legacy = _legacy_data_root() if legacy is None else legacy
    if (legacy != default and contains_gusto_data(legacy)
            and not contains_gusto_data(default)):
        return legacy, "legacy", default
    return default, "platform_default", default


def project_root() -> Path:
    return _resolve_data_root()[0]


def storage_info() -> dict[str, str]:
    """Explain the active data root for CLI users and installation tooling."""
    path, source, default = _resolve_data_root()
    info = {
        "path": os.fspath(path),
        "source": source,
        "platform_default": os.fspath(default),
    }
    settings_path = default_settings_path()
    if settings_path.is_file():
        info["settings_path"] = os.fspath(settings_path)
    return info


def recipes_dir() -> Path:
    return project_root() / "recipes"


def data_dir() -> Path:
    return project_root() / "data"


def index_path() -> Path:
    return data_dir() / "recipes.json"


def log_path() -> Path:
    return data_dir() / "log.json"


def categories_path() -> Path:
    return data_dir() / "categories.json"


def recipe_file(slug: str) -> Path:
    return recipes_dir() / f"{slug}.md"


def shopping_path() -> Path:
    return data_dir() / "shopping_list.json"


def favorites_path() -> Path:
    return data_dir() / "favorites.json"


def changes_path() -> Path:
    return data_dir() / "changes.json"


def images_dir() -> Path:
    return project_root() / "images"


def recipe_images_dir(slug: str) -> Path:
    return images_dir() / slug


def archive_dir() -> Path:
    return project_root() / "archive"


def archive_recipe_dir(slug: str) -> Path:
    return archive_dir() / slug


def archive_recipe_file(slug: str) -> Path:
    return archive_recipe_dir(slug) / "recipe.md"


def archive_metadata_path(slug: str) -> Path:
    return archive_recipe_dir(slug) / "metadata.json"


def archive_images_dir(slug: str) -> Path:
    return archive_recipe_dir(slug) / "images"


def favorite_images_dir() -> Path:
    return images_dir() / "_favorites"


# --- Cross-process mutation locks ------------------------------------------

def _resource_lock_path(resource: str) -> Path:
    """Stable advisory-lock file for one mutable source-of-truth area."""
    return data_dir() / f".gusto-{resource}.lock"


def _thread_lock(path: Path) -> threading.Lock:
    """Return the process-local companion of an OS-level resource lock."""
    key = os.fspath(path.resolve())
    with _THREAD_LOCKS_GUARD:
        return _THREAD_LOCKS.setdefault(key, threading.Lock())


def _acquire_file_lock(path: Path):
    """Acquire one byte as an advisory lock on Windows or POSIX."""
    path.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + _LOCK_TIMEOUT_SECONDS
    try:
        descriptor = os.open(
            path, os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o600,
        )
    except FileExistsError:
        # The process which atomically created a new lock file writes its one
        # lockable byte before exposing the handle to mutation code. Wait out
        # that tiny initialization window instead of racing a second write on
        # Windows (which sporadically raises PermissionError).
        while True:
            try:
                if path.stat().st_size >= 1:
                    break
            except FileNotFoundError:
                pass
            if time.monotonic() >= deadline:
                raise ValueError(
                    f"Gusto-Sperrdatei konnte nicht initialisiert werden: {path}"
                )
            time.sleep(0.01)
        handle = path.open("r+b", buffering=0)
    else:
        try:
            os.write(descriptor, b"\0")
        except Exception:
            os.close(descriptor)
            raise
        handle = os.fdopen(descriptor, "r+b", buffering=0)

    while True:
        try:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return handle
        except OSError as error:
            if time.monotonic() >= deadline:
                handle.close()
                raise ValueError(
                    "Gusto-Daten sind seit 30 Sekunden durch einen anderen "
                    "Schreibvorgang gesperrt."
                ) from error
            time.sleep(0.02)


def _release_file_lock(handle) -> None:
    try:
        handle.seek(0)
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    except OSError:
        # Closing the descriptor releases either native advisory lock anyway;
        # do not strand the process-local lock or mask a completed mutation.
        pass
    finally:
        handle.close()


@contextmanager
def _mutation_locks(*resources: str):
    """Serialize mutations that share a source of truth.

    A stable sorted order makes multi-resource operations such as
    shopping_add_recipe deadlock-safe. Reads stay lock-free because persisted
    files are replaced atomically and therefore expose either the old or new
    complete state.
    """
    paths = sorted({_resource_lock_path(name) for name in resources},
                   key=lambda path: os.fspath(path))
    local_locks: list[threading.Lock] = []
    file_handles = []
    try:
        for path in paths:
            lock = _thread_lock(path)
            lock.acquire()
            local_locks.append(lock)
        for path in paths:
            file_handles.append(_acquire_file_lock(path))
        yield
    finally:
        for handle in reversed(file_handles):
            _release_file_lock(handle)
        for lock in reversed(local_locks):
            lock.release()


def _locked_mutation(*resources: str, event_resources=None):
    """Decorate one complete read-modify-write Core transaction."""
    def decorate(function):
        @wraps(function)
        def locked(*args, **kwargs):
            return _execute_locked_mutation(
                resources, lambda: function(*args, **kwargs),
                event_resources=event_resources,
            )
        return locked
    return decorate


def _execute_locked_mutation(resources, function, *, event_resources=None):
    """Run one dynamic mutation and emit its event after business locks."""
    with _mutation_locks(*resources):
        previous = getattr(_MUTATION_CONTEXT, "changed", None)
        _MUTATION_CONTEXT.changed = False
        try:
            result = function()
            changed = _MUTATION_CONTEXT.changed
        finally:
            if previous is None:
                try:
                    del _MUTATION_CONTEXT.changed
                except AttributeError:
                    pass
            else:
                _MUTATION_CONTEXT.changed = previous
    if changed:
        _record_change(event_resources or resources)
    return result


def _mark_mutation() -> None:
    """Remember that the active Core transaction changed persisted state."""
    if hasattr(_MUTATION_CONTEXT, "changed"):
        _MUTATION_CONTEXT.changed = True


def _load_change_journal() -> dict:
    path = changes_path()
    if not path.exists():
        return {
            "revision": 0,
            "resources": [],
            "events": [],
            "compacted_revision": 0,
            "compacted_resources": [],
        }
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"Ungültiges Gusto-Änderungsjournal: {error}") from error
    if not isinstance(state, dict):
        raise ValueError("Ungültiges Gusto-Änderungsjournal: JSON-Objekt erwartet.")
    revision = state.get("revision")
    resources = state.get("resources")
    events = state.get("events", [])
    compacted_revision = state.get("compacted_revision", 0)
    compacted_resources = state.get("compacted_resources", [])
    if (isinstance(revision, bool) or not isinstance(revision, int)
            or revision < 0 or not isinstance(resources, list)
            or not all(isinstance(item, str) for item in resources)
            or not isinstance(events, list)
            or isinstance(compacted_revision, bool)
            or not isinstance(compacted_revision, int)
            or compacted_revision < 0
            or not isinstance(compacted_resources, list)
            or not all(isinstance(item, str) for item in compacted_resources)):
        raise ValueError("Ungültiges Gusto-Änderungsjournal.")
    # Safely migrate a journal written before compacted resources were
    # persisted. A broad refresh is preferable to silently missing a change.
    retained_revisions = [
        event.get("revision") for event in events
        if isinstance(event, dict) and isinstance(event.get("revision"), int)
    ]
    if compacted_revision == 0 and revision:
        first_retained = min(retained_revisions, default=revision + 1)
        if first_retained > 1:
            compacted_revision = first_retained - 1
            compacted_resources = [
                "catalog", "favorites", "log", "shopping",
            ]
    return {
        "revision": revision,
        "resources": list(resources),
        "events": list(events),
        "compacted_revision": compacted_revision,
        "compacted_resources": list(compacted_resources),
    }


def change_state() -> dict:
    """Return the latest persisted change revision and affected resources."""
    state = _load_change_journal()
    return {
        "revision": state["revision"],
        "resources": state["resources"],
    }


def change_events(after_revision: int = 0) -> list[dict]:
    """Return retained change events newer than ``after_revision``."""
    if (isinstance(after_revision, bool) or not isinstance(after_revision, int)
            or after_revision < 0):
        raise ValueError("Die Änderungsrevision muss eine nichtnegative ganze Zahl sein.")
    state = _load_change_journal()
    events = []
    cursor = after_revision
    if after_revision < state["compacted_revision"]:
        events.append({
            "revision": state["compacted_revision"],
            "resources": list(state["compacted_resources"]),
        })
        cursor = state["compacted_revision"]
    for event in state["events"]:
        if (isinstance(event, dict)
                and isinstance(event.get("revision"), int)
                and event["revision"] > cursor
                and isinstance(event.get("resources"), list)):
            events.append({
                "revision": event["revision"],
                "resources": list(event["resources"]),
            })
    if not events and state["revision"] > after_revision:
        events.append({
            "revision": state["revision"],
            "resources": list(state["resources"]),
        })
    return events


def _record_change(resources) -> dict:
    """Persist one monotone change event under its own short lock."""
    normalized = sorted({
        resource for resource in resources
        if resource in {"catalog", "log", "favorites", "shopping"}
    })
    if not normalized:
        return change_state()
    with _mutation_locks("events"):
        state = _load_change_journal()
        event = {
            "revision": state["revision"] + 1,
            "resources": normalized,
        }
        combined = [*state["events"], event]
        dropped = combined[:-_CHANGE_HISTORY_LIMIT]
        events = combined[-_CHANGE_HISTORY_LIMIT:]
        compacted_revision = state["compacted_revision"]
        compacted_resources = set(state["compacted_resources"])
        if dropped:
            compacted_revision = dropped[-1]["revision"]
            for dropped_event in dropped:
                compacted_resources.update(dropped_event["resources"])
        _write_json(changes_path(), {
            **event,
            "events": events,
            "compacted_revision": compacted_revision,
            "compacted_resources": sorted(compacted_resources),
        }, track_mutation=False)
    return event


# --- Data model -------------------------------------------------------------

@dataclass
class RecipeImage:
    id: str
    filename: str
    role: str = "gallery"
    caption: str = ""
    created_at: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "RecipeImage":
        if not isinstance(data, dict):
            raise ValueError(
                "Ungültiges Bild in einem Rezept-Eintrag: JSON-Objekt erwartet."
            )
        allowed = {f.name for f in fields(cls)}
        filtered = {key: value for key, value in data.items() if key in allowed}
        # api.py und check() lesen id/filename als Strings; ein gebrochener
        # Typ im Speicher muss als Datenfehler auflaufen, nicht als Crash.
        if (not isinstance(filtered.get("id"), str)
                or not isinstance(filtered.get("filename"), str)):
            raise ValueError(
                "Ungültiges Bild in einem Rezept-Eintrag: "
                "'id' und 'filename' müssen Texte sein."
            )
        return cls(**filtered)


@dataclass
class Recipe:
    slug: str
    title: str
    tags: list[str] = field(default_factory=list)
    duration_min: int | None = None
    servings: int | None = None
    last_cooked: str | None = None  # ISO "YYYY-MM-DD" or None
    images: list[RecipeImage] = field(default_factory=list)
    cover_image_id: str | None = None

    @property
    def path(self) -> Path:
        return recipe_file(self.slug)

    def content(self) -> str:
        """Pure Markdown content from the .md file."""
        p = self.path
        return p.read_text(encoding="utf-8") if p.exists() else ""

    @property
    def cover_image(self) -> RecipeImage | None:
        if self.cover_image_id:
            match = next((image for image in self.images
                          if image.id == self.cover_image_id), None)
            if match is not None:
                return match
        return self.images[0] if self.images else None

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Recipe":
        allowed = {f.name for f in fields(cls)}
        values = {k: v for k, v in d.items() if k in allowed}
        # api.py sortiert nach title und check() liest images; ein nicht
        # ladbarer Eintrag muss als Datenfehler sichtbar werden, statt beim
        # Lesen mit raw KeyError/AttributeError den ganzen Aufruf zu crashen.
        if (not isinstance(values.get("title"), str)
                or not values["title"].strip()):
            raise ValueError("'title' muss ein nicht-leerer Text sein.")
        images = values.get("images", [])
        if not isinstance(images, list):
            raise ValueError("'images' muss eine Liste sein.")
        values["images"] = [RecipeImage.from_dict(image) for image in images]
        return cls(**values)


@dataclass
class ArchivedRecipe:
    """A complete reversible recipe snapshot outside the active catalog."""
    recipe: Recipe
    archived_at: str

    @property
    def slug(self) -> str:
        return self.recipe.slug

    @property
    def title(self) -> str:
        return self.recipe.title

    @property
    def images(self) -> list[RecipeImage]:
        return self.recipe.images

    @property
    def cover_image(self) -> RecipeImage | None:
        return self.recipe.cover_image

    def content(self) -> str:
        path = archive_recipe_file(self.slug)
        return path.read_text(encoding="utf-8") if path.exists() else ""

    def to_dict(self, *, include_content: bool = False) -> dict:
        result = self.recipe.to_dict()
        result["archived_at"] = self.archived_at
        if include_content:
            result["content"] = self.content()
        return result


@dataclass
class FavoriteProduct:
    """One concrete product in a household preference ranking."""
    id: str
    name: str
    brand: str = ""
    store: str = ""
    note: str = ""
    image_filename: str = ""
    created_at: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "FavoriteProduct":
        allowed = {f.name for f in fields(cls)}
        return cls(**{key: value for key, value in data.items() if key in allowed})


@dataclass
class ShoppingNeed:
    """A reusable shopping need with exact aliases and ranked products."""
    id: str
    name: str
    aliases: list[str] = field(default_factory=list)
    products: list[FavoriteProduct] = field(default_factory=list)
    created_at: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "ShoppingNeed":
        allowed = {f.name for f in fields(cls)}
        values = {key: value for key, value in data.items() if key in allowed}
        values["aliases"] = list(values.get("aliases", []))
        values["products"] = [FavoriteProduct.from_dict(product)
                              for product in values.get("products", [])]
        return cls(**values)


# --- Load / save ------------------------------------------------------------

def load_recipes() -> list[Recipe]:
    p = index_path()
    if not p.exists():
        return []
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(
            f"Ungültiger Rezeptkatalog in '{p.name}': {error}"
        ) from error
    if not isinstance(raw, list):
        raise ValueError(
            f"Ungültiger Rezeptkatalog in '{p.name}': JSON-Liste erwartet."
        )
    recipes = []
    for entry in raw:
        if not isinstance(entry, dict):
            raise ValueError(
                f"Ungültiger Rezeptkatalog in '{p.name}': "
                "jeder Eintrag muss ein JSON-Objekt sein."
            )
        try:
            recipes.append(Recipe.from_dict(entry))
        except ValueError as error:
            raise ValueError(
                f"Ungültiger Rezeptkatalog in '{p.name}': {error}"
            ) from error
    return recipes


def _save_recipes_unlocked(recipes: list[Recipe]) -> None:
    _write_json(index_path(), [r.to_dict() for r in recipes])


@_locked_mutation("catalog")
def save_recipes(recipes: list[Recipe]) -> None:
    """Replace the full catalog through the public locked save primitive."""
    _save_recipes_unlocked(recipes)


def get(slug: str) -> Recipe | None:
    return next((r for r in load_recipes() if r.slug == slug), None)


def _load_archived_entry(path: Path) -> ArchivedRecipe:
    try:
        raw = json.loads((path / "metadata.json").read_text(encoding="utf-8"))
        version = raw["version"]
        recipe = Recipe.from_dict(raw["recipe"])
        archived_at = raw["archived_at"]
    except (OSError, json.JSONDecodeError, KeyError, TypeError) as error:
        raise ValueError(
            f"Ungültiger Archiveintrag '{path.name}': {error}"
        ) from error
    if version != 1:
        raise ValueError(
            f"Ungültiger Archiveintrag '{path.name}': "
            f"nicht unterstützte Version '{version}'."
        )
    if recipe.slug != path.name:
        raise ValueError(
            f"Ungültiger Archiveintrag '{path.name}': "
            f"Metadaten gehören zu '{recipe.slug}'."
        )
    if (not isinstance(archived_at, str) or not archived_at
            or _parse_iso(archived_at) is None):
        raise ValueError(
            f"Ungültiger Archiveintrag '{path.name}': archived_at ist ungültig."
        )
    return ArchivedRecipe(recipe=recipe, archived_at=archived_at)


def load_archive() -> list[ArchivedRecipe]:
    root = archive_dir()
    if not root.exists():
        return []
    entries = [
        _load_archived_entry(path)
        for path in sorted(root.iterdir(), key=lambda item: item.name)
        if path.is_dir() and not path.name.startswith(".")
    ]
    return sorted(entries, key=lambda entry: entry.archived_at, reverse=True)


def get_archived(slug: str) -> ArchivedRecipe | None:
    if (not isinstance(slug, str) or not slug
            or Path(slug).name != slug or slug.startswith(".")):
        return None
    path = archive_recipe_dir(slug)
    return _load_archived_entry(path) if path.is_dir() else None


def recipe_references() -> dict[str, dict]:
    """Titles and lifecycle state for cross-domain slug references."""
    references = {
        recipe.slug: {
            "slug": recipe.slug,
            "title": recipe.title,
            "archived": False,
        }
        for recipe in load_recipes()
    }
    for entry in load_archive():
        references.setdefault(entry.slug, {
            "slug": entry.slug,
            "title": entry.title,
            "archived": True,
        })
    return references


def _write_json(path: Path, data, *, track_mutation: bool = True) -> bool:
    """Write changed JSON atomically; return whether the file was replaced."""
    path.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    if path.is_file():
        try:
            if path.read_text(encoding="utf-8") == content:
                return False
        except OSError:
            pass
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.", suffix=".tmp",
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
    if track_mutation:
        _mark_mutation()
    return True


def _write_text(path: Path, content: str) -> bool:
    """Write changed UTF-8 text atomically; return whether it was replaced."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file():
        try:
            if path.read_text(encoding="utf-8") == content:
                return False
        except OSError:
            pass
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.", suffix=".tmp",
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
    _mark_mutation()
    return True


# --- Create -----------------------------------------------------------------

def slugify(title: str) -> str:
    s = title.strip().lower()
    s = s.replace("ä", "ae").replace("ö", "oe").replace("ü", "ue").replace("ß", "ss")
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s or "recipe"


@_locked_mutation("catalog")
def add_recipe(title: str, tags=None, duration_min=None, servings=None,
               content: str | None = None, slug: str | None = None) -> Recipe:
    title = _recipe_title(title)
    tags = _recipe_tags(tags or [])
    _positive_recipe_number(duration_min, "Die Dauer")
    _positive_recipe_number(servings, "Die Portionszahl")
    if slug is not None and (
            not isinstance(slug, str) or not slug
            or Path(slug).name != slug or slug.startswith(".")):
        raise ValueError(
            "Der Rezept-Slug muss ein nicht-leerer Name ohne Pfadanteile "
            "und ohne führenden Punkt sein."
        )
    slug = slug or slugify(title)
    recipes = load_recipes()
    if any(r.slug == slug for r in recipes):
        raise ValueError(f"Es gibt bereits ein Rezept mit dem Slug '{slug}'.")
    if archive_recipe_dir(slug).exists():
        raise ValueError(
            f"Der Slug '{slug}' liegt im Archiv. Stelle das Rezept wieder her "
            "oder lösche den Archiveintrag endgültig."
        )
    recipes_dir().mkdir(parents=True, exist_ok=True)
    recipe_content = (
        _content_with_title(content, title) if content is not None
        else _template(title)
    )
    markdown_path = recipe_file(slug)
    previous_content = (
        markdown_path.read_text(encoding="utf-8")
        if markdown_path.is_file() else None
    )
    _write_text(markdown_path, recipe_content)
    r = Recipe(slug=slug, title=title, tags=tags,
               duration_min=duration_min, servings=servings)
    recipes.append(r)
    try:
        _save_recipes_unlocked(recipes)
    except Exception:
        # Without the index entry the .md would stay behind as an orphaned
        # file (a hard check error), so roll the Markdown back as well.
        if previous_content is None:
            markdown_path.unlink(missing_ok=True)
        else:
            _write_text(markdown_path, previous_content)
        raise
    return r


def _template(title: str) -> str:
    return f"# {title}\n\n## Zutaten\n\n- \n\n## Zubereitung\n\n1. \n"


# Slugs come straight from the title, so the title length is what keeps the
# atomic writer's ".<slug>.md.<tmp>" name inside the Windows filename limit.
MAX_RECIPE_TITLE_LENGTH = 150


def _recipe_title(value: str) -> str:
    """Return a normalized non-empty, single-line recipe title."""
    if (not isinstance(value, str) or not value.strip()
            or "\n" in value or "\r" in value):
        raise ValueError(
            "Das Rezept braucht einen nicht-leeren, einzeiligen Titel."
        )
    title = value.strip()
    # Der Slug entsteht aus dem Titel; der atomare Schreiber nutzt einen
    # Temporärnamen ".<slug>.md.<tmp>", der oberhalb des Windows-Limits
    # (255 Zeichen pro Dateinamen) zum OSError beim Schreiben führen würde.
    if len(title) > MAX_RECIPE_TITLE_LENGTH:
        raise ValueError(
            f"Der Titel darf höchstens {MAX_RECIPE_TITLE_LENGTH} Zeichen lang sein."
        )
    return title


def _recipe_tags(values) -> list[str]:
    """Return one-line recipe tags, case-insensitively deduplicated."""
    if (not isinstance(values, list)
            or not all(isinstance(value, str) and value.strip()
                       and "\n" not in value and "\r" not in value
                       for value in values)):
        raise ValueError(
            "Rezept-Tags müssen eine Liste nicht-leerer, einzeiliger Texte sein."
        )
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        tag = value.strip()
        if tag.lower() not in seen:
            seen.add(tag.lower())
            result.append(tag)
    return result


def _content_with_title(content: str, title: str) -> str:
    """Make the first Markdown line the canonical recipe title."""
    normalized = (content or "").replace("\r\n", "\n").replace("\r", "\n")
    first, separator, remainder = normalized.partition("\n")
    if re.fullmatch(r"#(?:\s.*)?", first):
        return f"# {title}" + (separator + remainder if separator else "\n")
    if not normalized:
        return f"# {title}\n"
    return f"# {title}\n\n{normalized.lstrip(chr(10))}"


def _markdown_title(content: str) -> str | None:
    first = content.replace("\r\n", "\n").replace("\r", "\n").partition("\n")[0]
    match = re.fullmatch(r"#\s+(.+?)\s*", first)
    return match.group(1) if match else None


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


# --- Categories (facets) ----------------------------------------------------
# Per recipe, tags are deliberately a flat list in data/recipes.json. Which tag
# belongs to which category lives centrally in data/categories.json:
#   { "<key>": {"label": str, "tags": [str, ...]}, ... }
# The order in the JSON is the display order. A tag without a category counts
# as "uncategorized" and ends up in the shared "Sonstige" group.

_CATEGORY_KEY_PATTERN = re.compile(r"[a-z0-9][a-z0-9_-]*")


def _category_key(value: str) -> str:
    if (not isinstance(value, str)
            or not _CATEGORY_KEY_PATTERN.fullmatch(value.strip())):
        raise ValueError(
            "Der Kategorie-Schlüssel muss mit einem Kleinbuchstaben oder einer "
            "Ziffer beginnen und darf nur a-z, 0-9, '_' und '-' enthalten."
        )
    key = value.strip()
    if key in {"other", "__other__"}:
        raise ValueError(
            f"Der Kategorie-Schlüssel '{key}' ist für die Facette Sonstige reserviert."
        )
    return key


def _category_label(value: str) -> str:
    if (not isinstance(value, str) or not value.strip()
            or "\n" in value or "\r" in value):
        raise ValueError(
            "Die Tag-Kategorie braucht eine nicht-leere, einzeilige Bezeichnung."
        )
    return value.strip()


def _category_tags(values) -> list[str]:
    if (not isinstance(values, list)
            or not all(isinstance(value, str) and value.strip()
                       and "\n" not in value and "\r" not in value
                       for value in values)):
        raise ValueError(
            "Kategorie-Tags müssen eine Liste nicht-leerer, einzeiliger Texte sein."
        )
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        tag = value.strip()
        normalized = tag.lower()
        if normalized in seen:
            raise ValueError(f"Tag '{tag}' ist in einer Kategorie doppelt vorhanden.")
        seen.add(normalized)
        result.append(tag)
    return result


def _validated_categories(value) -> dict[str, dict]:
    if not isinstance(value, dict):
        raise ValueError("Ungültige Tag-Kategorien: JSON-Objekt erwartet.")
    result: dict[str, dict] = {}
    owners: dict[str, str] = {}
    for raw_key, raw_category in value.items():
        key = _category_key(raw_key)
        if key in result:
            raise ValueError(
                f"Tag-Kategorie '{key}' ist nach Normalisierung doppelt vorhanden."
            )
        if not isinstance(raw_category, dict):
            raise ValueError(f"Tag-Kategorie '{key}' muss ein JSON-Objekt sein.")
        unknown = set(raw_category) - {"label", "tags"}
        if unknown:
            raise ValueError(
                f"Tag-Kategorie '{key}' enthält unbekannte Felder: "
                + ", ".join(sorted(unknown)) + "."
            )
        label = _category_label(raw_category.get("label"))
        tags = _category_tags(raw_category.get("tags"))
        for tag in tags:
            normalized = tag.lower()
            previous = owners.get(normalized)
            if previous is not None:
                raise ValueError(
                    f"Tag '{tag}' gehört zugleich zu '{previous}' und '{key}'."
                )
            owners[normalized] = key
        result[key] = {"label": label, "tags": tags}
    return result


def load_categories() -> dict[str, dict]:
    """Category definition from data/categories.json (order preserved).
    If the file is missing the result is {} -- then every tag is uncategorized."""
    path = categories_path()
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"Ungültige Tag-Kategorien: {error}") from error
    return _validated_categories(value)


def _category_dict(key: str, category: dict) -> dict:
    return {"key": key, "label": category["label"], "tags": list(category["tags"])}


def _category_position(value: int | None, maximum: int) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= maximum:
        raise ValueError(f"Die Position muss zwischen 1 und {maximum} liegen.")
    return value


def _requested_tags(values: list[str]) -> list[str]:
    if (not isinstance(values, list)
            or not all(isinstance(value, str) and value.strip()
                       and "\n" not in value and "\r" not in value
                       for value in values)):
        raise ValueError("Tags müssen eine Liste nicht-leerer, einzeiliger Texte sein.")
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        tag = value.strip()
        if tag.lower() not in seen:
            seen.add(tag.lower())
            result.append(tag)
    return result


@_locked_mutation("catalog")
def add_category(key: str, label: str, position: int | None = None) -> dict:
    key = _category_key(key)
    label = _category_label(label)
    categories = load_categories()
    if key in categories:
        raise ValueError(f"Tag-Kategorie '{key}' existiert bereits.")
    position = _category_position(position, len(categories) + 1)
    entries = list(categories.items())
    entries.insert(len(entries) if position is None else position - 1, (
        key, {"label": label, "tags": []},
    ))
    updated = dict(entries)
    _write_json(categories_path(), updated)
    return _category_dict(key, updated[key])


@_locked_mutation("catalog")
def update_category(key: str, *, label: str | None = None,
                    position: int | None = None) -> dict:
    key = _category_key(key)
    if label is None and position is None:
        raise ValueError("Wähle --label und/oder --position.")
    categories = load_categories()
    if key not in categories:
        raise ValueError(f"Keine Tag-Kategorie mit Schlüssel '{key}'.")
    if label is not None:
        categories[key]["label"] = _category_label(label)
    position = _category_position(position, len(categories))
    if position is not None:
        entry = (key, categories.pop(key))
        entries = list(categories.items())
        entries.insert(position - 1, entry)
        categories = dict(entries)
    _write_json(categories_path(), categories)
    return _category_dict(key, categories[key])


@_locked_mutation("catalog")
def remove_category(key: str) -> dict:
    key = _category_key(key)
    categories = load_categories()
    category = categories.pop(key, None)
    if category is None:
        raise ValueError(f"Keine Tag-Kategorie mit Schlüssel '{key}'.")
    used = {tag.lower(): tag for recipe in load_recipes() for tag in recipe.tags}
    now_uncategorized = [
        used[tag.lower()] for tag in category["tags"] if tag.lower() in used
    ]
    _write_json(categories_path(), categories)
    return {
        **_category_dict(key, category),
        "removed": True,
        "now_uncategorized": now_uncategorized,
    }


@_locked_mutation("catalog")
def assign_category_tags(key: str, tags: list[str]) -> dict:
    key = _category_key(key)
    requested = _requested_tags(tags)
    if not requested:
        raise ValueError("Mindestens ein Tag muss zugeordnet werden.")
    categories = load_categories()
    if key not in categories:
        raise ValueError(f"Keine Tag-Kategorie mit Schlüssel '{key}'.")

    spellings = {
        tag.lower(): tag
        for category in categories.values()
        for tag in category["tags"]
    }
    for recipe in load_recipes():
        for tag in recipe.tags:
            spellings.setdefault(tag.lower(), tag)
    target = categories[key]["tags"]
    target_names = {tag.lower(): tag for tag in target}
    for requested_tag in requested:
        normalized = requested_tag.lower()
        for category_key, category in categories.items():
            if category_key != key:
                category["tags"] = [
                    tag for tag in category["tags"] if tag.lower() != normalized
                ]
        if normalized not in target_names:
            canonical = spellings.get(normalized, requested_tag)
            target.append(canonical)
            target_names[normalized] = canonical
    _write_json(categories_path(), categories)
    return _category_dict(key, categories[key])


@_locked_mutation("catalog")
def unassign_category_tags(tags: list[str]) -> dict:
    requested = {tag.lower() for tag in _requested_tags(tags)}
    if not requested:
        raise ValueError("Mindestens ein Tag muss entfernt werden.")
    categories = load_categories()
    removed: list[str] = []
    for category in categories.values():
        retained = []
        for tag in category["tags"]:
            if tag.lower() in requested:
                removed.append(tag)
            else:
                retained.append(tag)
        category["tags"] = retained
    _write_json(categories_path(), categories)
    return {"unassigned": removed}


@_locked_mutation("catalog")
def move_category_tag(key: str, tag: str, position: int) -> dict:
    key = _category_key(key)
    requested = _category_tags([tag])[0]
    categories = load_categories()
    if key not in categories:
        raise ValueError(f"Keine Tag-Kategorie mit Schlüssel '{key}'.")
    current = categories[key]["tags"]
    index = next(
        (index for index, value in enumerate(current)
         if value.lower() == requested.lower()),
        None,
    )
    if index is None:
        raise ValueError(f"Tag '{requested}' gehört nicht zur Kategorie '{key}'.")
    position = _category_position(position, len(current))
    value = current.pop(index)
    current.insert(position - 1, value)
    _write_json(categories_path(), categories)
    return _category_dict(key, categories[key])


def _tag_to_category(categories: dict | None = None) -> dict[str, str]:
    """Inverse mapping tag(lowercase) -> category key."""
    categories = load_categories() if categories is None else categories
    return {t.lower(): key
            for key, cat in categories.items()
            for t in cat.get("tags", [])}


def tag_category(tag: str, categories: dict | None = None) -> str | None:
    """Category key of a tag, or None if uncategorized."""
    return _tag_to_category(categories).get(tag.lower())


def uncategorized_tags(tags: list[str]) -> list[str]:
    """Return distinct tags that have no named facet assignment."""
    mapping = _tag_to_category()
    return sorted(
        {tag for tag in tags if tag.lower() not in mapping}, key=str.lower,
    )


def tag_groups(only_used: bool = True) -> list[dict]:
    """Categories with their tags in display order -- for the tag bar and
    `gusto tags`. Returns [{"key", "label", "tags": [...]}, ...].

    only_used=True: only tags that occur in recipes; empty categories are
    dropped; actually-used tags without a category come last as a "Sonstige"
    group. only_used=False: all defined categories/tags."""
    categories = load_categories()
    used_names = sorted({t for r in load_recipes() for t in r.tags},
                        key=str.lower) if only_used else None
    used_lower = {t.lower() for t in used_names} if used_names is not None else None

    groups: list[dict] = []
    covered: set[str] = set()
    for key, cat in categories.items():
        covered |= {t.lower() for t in cat.get("tags", [])}
        tags = [t for t in cat.get("tags", [])
                if used_lower is None or t.lower() in used_lower]
        if tags or used_lower is None:
            groups.append({"key": key, "label": cat.get("label", key), "tags": tags})

    if used_names is not None:
        leftover = [t for t in used_names if t.lower() not in covered]
        if leftover:
            groups.append({"key": "other", "label": "Sonstige", "tags": leftover})
    return groups


# --- Search -----------------------------------------------------------------

def search(query: str = "", match: str = "any", tags: list[str] | None = None,
           max_time: int | None = None) -> list[Recipe]:
    """Filter over metadata (tags, max_time) and, for `query`, additionally
    search the title, tags AND the Markdown content (so the ingredients too).

    Tag filter (facets): multiple `tags` are grouped by their category. A recipe
    matches if it has AT LEAST ONE of the selected tags in EVERY selected
    category -- i.e. OR within a category and AND across categories. Tags without
    a category form one shared group (OR among themselves)."""
    if not isinstance(query, str):
        raise ValueError("Der Suchbegriff muss ein Text sein.")
    if match not in ("any", "all"):
        raise ValueError("Der Suchmodus muss 'any' oder 'all' sein.")
    _positive_recipe_number(max_time, "Die maximale Dauer")
    terms = [t.lower() for t in query.split()]
    groups = _group_tags(tags or [])
    hits = []
    for r in load_recipes():
        if groups and not _matches_tags(r, groups):
            continue
        if max_time is not None and (r.duration_min is None or r.duration_min > max_time):
            continue
        if terms:
            haystack = f"{r.title} {' '.join(r.tags)} {r.content()}".lower()
            ok = (all(t in haystack for t in terms) if match == "all"
                  else any(t in haystack for t in terms))
            if not ok:
                continue
        hits.append(r)
    return hits


def _group_tags(tags: list[str]) -> dict[str, set[str]]:
    """Group selected tags by category: key -> {tag-lowercase, ...}.
    Tags without a category go together under "__other__"."""
    mapping = _tag_to_category()
    groups: dict[str, set[str]] = {}
    for t in tags:
        tl = t.lower()
        groups.setdefault(mapping.get(tl, "__other__"), set()).add(tl)
    return groups


def _matches_tags(r: Recipe, groups: dict[str, set[str]]) -> bool:
    """OR within a group, AND across groups."""
    recipe_tags = {t.lower() for t in r.tags}
    return all(recipe_tags & selected for selected in groups.values())


# --- Cooking log ------------------------------------------------------------

def load_log(days: int | None = None) -> list[dict]:
    _positive_recipe_number(days, "Die Anzahl der Tage")
    p = log_path()
    entries: list[dict] = []
    if p.exists():
        try:
            entries = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ValueError(
                f"Ungültiges Kochprotokoll in '{p.name}': {error}"
            ) from error
        if (not isinstance(entries, list)
                or not all(isinstance(entry, dict) for entry in entries)):
            raise ValueError(
                f"Ungültiges Kochprotokoll in '{p.name}': "
                "JSON-Liste von Objekten erwartet."
            )
        # 'date' treibt Filter und Sortierung; ein gebrochener Eintrag würde
        # sonst mit raw KeyError den Loader (und check()) crashen, statt als
        # Datenfehler gemeldet zu werden.
        for entry in entries:
            if (not isinstance(entry.get("date"), str)
                    or not isinstance(entry.get("slug"), str)):
                raise ValueError(
                    f"Ungültiges Kochprotokoll in '{p.name}': "
                    "jeder Eintrag braucht ein Textfeld 'date' und 'slug'."
                )
    if days is not None:
        cutoff = (date.today() - timedelta(days=days)).isoformat()
        entries = [e for e in entries if e["date"] >= cutoff]
    return sorted(entries, key=lambda e: e["date"])


@_locked_mutation("catalog", "log")
def log_cooked(slug: str, when: str | None = None) -> None:
    """Record a valid ISO calendar date no later than today."""
    recipes = load_recipes()
    if not any(r.slug == slug for r in recipes):
        raise ValueError(f"Kein Rezept mit Slug '{slug}'.")
    when = when or date.today().isoformat()
    if not isinstance(when, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", when):
        raise ValueError("Das Kochdatum muss im Format YYYY-MM-DD angegeben werden.")
    try:
        cooked_on = date.fromisoformat(when)
    except ValueError as error:
        raise ValueError("Das Kochdatum ist kein gültiges Kalenderdatum.") from error
    if cooked_on > date.today():
        raise ValueError("Das Kochdatum darf nicht in der Zukunft liegen.")
    entries = load_log()
    entries.append({"date": when, "slug": slug})
    _write_json(log_path(), sorted(entries, key=lambda e: e["date"]))
    changed_last_cooked = False
    for r in recipes:
        if r.slug == slug and (r.last_cooked is None or when > r.last_cooked):
            r.last_cooked = when
            changed_last_cooked = True
    try:
        _save_recipes_unlocked(recipes)
    except Exception:
        # The log entry must never persist without its last_cooked update.
        if changed_last_cooked:
            _write_json(
                log_path(), sorted(entries[:-1], key=lambda e: e["date"]),
                track_mutation=False,
            )
        raise


# --- Suggestions ------------------------------------------------------------

def suggest(days: int = 7, limit: int | None = None) -> list[Recipe]:
    """Candidates for the next meal: everything NOT cooked in the last `days`
    days -- longest-not-cooked first.

    Deliberately simple and rule-based: the actual decision is made by a human
    or an agent that additionally uses `log`, `search` & `list`.
    """
    if limit is not None and (
            isinstance(limit, bool) or not isinstance(limit, int) or limit < 0):
        raise ValueError("Das Vorschlagslimit muss eine nichtnegative ganze Zahl sein.")
    recent = {e["slug"] for e in load_log(days=days)}
    remaining = [r for r in load_recipes() if r.slug not in recent]
    remaining.sort(key=lambda r: r.last_cooked or "")  # None/"" = longest ago
    return remaining[:limit] if limit is not None else remaining


# --- Update / archive -------------------------------------------------------

@_locked_mutation("catalog")
def update_recipe(slug: str, title: str | None = None, tags=None,
                  duration_min=None, servings=None,
                  content: str | None = None, *,
                  clear_duration: bool = False,
                  clear_servings: bool = False) -> Recipe:
    """Update a recipe. None means 'leave unchanged' (exception: tags=[] clears
    the tags). Explicit clear flags remove optional numeric metadata; `content`
    overwrites the .md file."""
    if clear_duration and duration_min is not None:
        raise ValueError("Die Dauer kann nicht gleichzeitig gesetzt und entfernt werden.")
    if clear_servings and servings is not None:
        raise ValueError("Die Portionszahl kann nicht gleichzeitig gesetzt und entfernt werden.")
    _positive_recipe_number(duration_min, "Die Dauer")
    _positive_recipe_number(servings, "Die Portionszahl")
    recipes = load_recipes()
    target = next((r for r in recipes if r.slug == slug), None)
    if target is None:
        raise ValueError(f"Kein Rezept mit Slug '{slug}'.")
    if title is not None:
        target.title = _recipe_title(title)
    if tags is not None:
        target.tags = _recipe_tags(tags)
    if clear_duration:
        target.duration_min = None
    elif duration_min is not None:
        target.duration_min = duration_min
    if clear_servings:
        target.servings = None
    elif servings is not None:
        target.servings = servings
    markdown_path = recipe_file(slug)
    old_content: str | None = None
    next_content: str | None = None
    if content is not None or title is not None:
        if content is None:
            if not markdown_path.is_file():
                raise ValueError(
                    f"Markdown-Datei für Rezept '{slug}' fehlt."
                )
            old_content = markdown_path.read_text(encoding="utf-8")
            next_content = _content_with_title(old_content, target.title)
        else:
            old_content = (
                markdown_path.read_text(encoding="utf-8")
                if markdown_path.is_file() else None
            )
            next_content = _content_with_title(content, target.title)
        _write_text(markdown_path, next_content)
    try:
        _save_recipes_unlocked(recipes)
    except Exception:
        if next_content is not None:
            if old_content is None:
                markdown_path.unlink(missing_ok=True)
            else:
                _write_text(markdown_path, old_content)
        raise
    return target


@_locked_mutation("catalog")
def archive_recipe(slug: str) -> ArchivedRecipe:
    """Move recipe-owned Markdown, metadata and images into the archive."""
    recipes = load_recipes()
    recipe = next((item for item in recipes if item.slug == slug), None)
    if recipe is None:
        raise ValueError(f"Kein Rezept mit Slug '{slug}'.")
    source_markdown = recipe_file(slug)
    if not source_markdown.is_file():
        raise ValueError(
            f"Rezept '{slug}' kann nicht archiviert werden: "
            "Die Markdown-Datei fehlt."
        )
    target = archive_recipe_dir(slug)
    if target.exists():
        raise ValueError(f"Rezept '{slug}' liegt bereits im Archiv.")

    root = archive_dir()
    root.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{slug}.archive.", dir=root))
    archived_at = _now_iso()
    source_images = recipe_images_dir(slug)
    final_created = False
    markdown_moved = False
    images_moved = False
    try:
        source_markdown.replace(temporary / "recipe.md")
        markdown_moved = True
        if source_images.exists():
            source_images.replace(temporary / "images")
            images_moved = True
        _write_json(temporary / "metadata.json", {
            "version": 1,
            "archived_at": archived_at,
            "recipe": recipe.to_dict(),
        })
        temporary.replace(target)
        final_created = True
        _save_recipes_unlocked([
            item for item in recipes if item.slug != slug
        ])
    except Exception:
        rollback = target if final_created else temporary
        if markdown_moved and (rollback / "recipe.md").exists():
            source_markdown.parent.mkdir(parents=True, exist_ok=True)
            (rollback / "recipe.md").replace(source_markdown)
        if images_moved and (rollback / "images").exists():
            source_images.parent.mkdir(parents=True, exist_ok=True)
            (rollback / "images").replace(source_images)
        if rollback.exists():
            shutil.rmtree(rollback)
        raise
    return ArchivedRecipe(recipe=recipe, archived_at=archived_at)


def delete_recipe(slug: str) -> ArchivedRecipe:
    """Backward-compatible name: deleting now means reversible archiving."""
    return archive_recipe(slug)


@_locked_mutation("catalog")
def restore_archived_recipe(slug: str) -> Recipe:
    """Restore one archived snapshot to the active catalog."""
    entry = get_archived(slug)
    if entry is None:
        raise ValueError(f"Kein archiviertes Rezept mit Slug '{slug}'.")
    if get(slug) is not None:
        raise ValueError(f"Rezept '{slug}' ist bereits aktiv.")
    target_markdown = recipe_file(slug)
    target_images = recipe_images_dir(slug)
    if target_markdown.exists() or target_images.exists():
        raise ValueError(
            f"Rezept '{slug}' kann nicht wiederhergestellt werden: "
            "Aktive Dateien mit diesem Slug sind bereits vorhanden."
        )

    source = archive_recipe_dir(slug)
    root = archive_dir()
    temporary = root / f".{slug}.restore.{uuid.uuid4().hex}"
    source.replace(temporary)
    markdown_moved = False
    images_moved = False
    try:
        target_markdown.parent.mkdir(parents=True, exist_ok=True)
        (temporary / "recipe.md").replace(target_markdown)
        markdown_moved = True
        if (temporary / "images").exists():
            target_images.parent.mkdir(parents=True, exist_ok=True)
            (temporary / "images").replace(target_images)
            images_moved = True
        recipes = load_recipes()
        recipes.append(entry.recipe)
        _save_recipes_unlocked(recipes)
    except Exception:
        if markdown_moved and target_markdown.exists():
            target_markdown.replace(temporary / "recipe.md")
        if images_moved and target_images.exists():
            target_images.replace(temporary / "images")
        if temporary.exists():
            temporary.replace(source)
        raise
    shutil.rmtree(temporary)
    return entry.recipe


@_locked_mutation("catalog", "shopping", event_resources=("catalog",))
def purge_archived_recipe(slug: str) -> ArchivedRecipe:
    """Permanently remove one archived recipe snapshot.

    The snapshot is first moved into a hidden transaction folder under the
    archive root and only then removed recursively. An interrupted purge
    therefore never leaves a half-deleted visible archive entry behind; the
    leftover folder is reported by ``check`` as ``stale_archive_transactions``.
    """
    entry = get_archived(slug)
    if entry is None:
        raise ValueError(f"Kein archiviertes Rezept mit Slug '{slug}'.")
    visible_sources = [
        item for item in shopping_load()
        if not item.deleted and item.source == slug
    ]
    if visible_sources:
        count = len(visible_sources)
        verb = "verweist" if count == 1 else "verweisen"
        raise ValueError(
            f"{count} sichtbare Einkaufsposten {verb} noch auf '{slug}'. "
            "Entferne diese Einträge vor dem endgültigen Löschen."
        )
    temporary = archive_dir() / f".{slug}.purge.{uuid.uuid4().hex}"
    archive_recipe_dir(slug).replace(temporary)
    shutil.rmtree(temporary)
    _mark_mutation()
    return entry


# --- Recipe images ----------------------------------------------------------

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}


def _valid_image_header(path: Path, suffix: str) -> bool:
    """Validate the format and positive dimensions without optional libraries."""
    with path.open("rb") as handle:
        header = handle.read(32)

        if suffix == ".png":
            return (header.startswith(b"\x89PNG\r\n\x1a\n")
                    and header[12:16] == b"IHDR"
                    and int.from_bytes(header[16:20], "big") > 0
                    and int.from_bytes(header[20:24], "big") > 0)

        if suffix == ".gif":
            return (header[:6] in {b"GIF87a", b"GIF89a"}
                    and int.from_bytes(header[6:8], "little") > 0
                    and int.from_bytes(header[8:10], "little") > 0)

        if suffix == ".webp":
            if not (header.startswith(b"RIFF") and header[8:12] == b"WEBP"):
                return False
            chunk = header[12:16]
            if chunk == b"VP8X" and len(header) >= 30:
                width = 1 + int.from_bytes(header[24:27], "little")
                height = 1 + int.from_bytes(header[27:30], "little")
                return width > 0 and height > 0
            if chunk == b"VP8L" and len(header) >= 25 and header[20] == 0x2F:
                bits = int.from_bytes(header[21:25], "little")
                return (bits & 0x3FFF) + 1 > 0 and ((bits >> 14) & 0x3FFF) + 1 > 0
            if chunk == b"VP8 " and len(header) >= 30 and header[23:26] == b"\x9d\x01\x2a":
                width = int.from_bytes(header[26:28], "little") & 0x3FFF
                height = int.from_bytes(header[28:30], "little") & 0x3FFF
                return width > 0 and height > 0
            return False

        if suffix in {".jpg", ".jpeg"}:
            if header[:2] != b"\xff\xd8":
                return False
            handle.seek(2)
            sof_markers = {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7,
                           0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}
            while True:
                byte = handle.read(1)
                if not byte:
                    return False
                if byte != b"\xff":
                    continue
                marker_byte = handle.read(1)
                while marker_byte == b"\xff":
                    marker_byte = handle.read(1)
                if not marker_byte:
                    return False
                marker = marker_byte[0]
                if marker in {0x01, 0xD8} or 0xD0 <= marker <= 0xD7:
                    continue
                if marker in {0xD9, 0xDA}:
                    return False
                length_bytes = handle.read(2)
                if len(length_bytes) != 2:
                    return False
                length = int.from_bytes(length_bytes, "big")
                if length < 2:
                    return False
                if marker in sof_markers:
                    dimensions = handle.read(5)
                    return (len(dimensions) == 5
                            and int.from_bytes(dimensions[1:3], "big") > 0
                            and int.from_bytes(dimensions[3:5], "big") > 0)
                handle.seek(length - 2, 1)

    return False


def recipe_image_path(slug: str, image: RecipeImage) -> Path:
    return recipe_images_dir(slug) / image.filename


def archived_recipe_image_path(slug: str, image: RecipeImage) -> Path:
    return archive_images_dir(slug) / image.filename


def get_archived_recipe_image(slug: str, image_id: str) -> RecipeImage | None:
    entry = get_archived(slug)
    if entry is None:
        return None
    return next((image for image in entry.images if image.id == image_id), None)


def list_recipe_images(slug: str) -> tuple[list[RecipeImage], str | None]:
    recipe = get(slug)
    if recipe is None:
        raise ValueError(f"Kein Rezept mit Slug '{slug}'.")
    cover = recipe.cover_image
    return recipe.images, cover.id if cover is not None else None


def get_recipe_image(slug: str, image_id: str) -> RecipeImage | None:
    recipe = get(slug)
    if recipe is None:
        return None
    return next((image for image in recipe.images if image.id == image_id), None)


@_locked_mutation("catalog")
def add_recipe_image(slug: str, source: str | Path, role: str = "gallery",
                     caption: str = "", cover: bool = False) -> RecipeImage:
    recipes = load_recipes()
    recipe = next((item for item in recipes if item.slug == slug), None)
    if recipe is None:
        raise ValueError(f"Kein Rezept mit Slug '{slug}'.")

    source_path = Path(source).expanduser()
    if not source_path.is_file():
        raise ValueError(f"Bilddatei nicht gefunden: '{source_path}'.")
    suffix = source_path.suffix.lower()
    if suffix not in IMAGE_EXTENSIONS:
        allowed = ", ".join(sorted(IMAGE_EXTENSIONS))
        raise ValueError(f"Nicht unterstütztes Bildformat '{suffix}' ({allowed}).")
    if not _valid_image_header(source_path, suffix):
        raise ValueError(
            f"Datei ist kein gültiges {suffix.lstrip('.').upper()}-Bild: '{source_path}'."
        )

    clean_role = role.strip() or "gallery"
    clean_caption = caption.strip()
    _no_newlines(clean_role, "Die Bildrolle")
    _no_newlines(clean_caption, "Die Bildbeschreibung")
    image = RecipeImage(
        id=uuid.uuid4().hex, filename="", role=clean_role,
        caption=clean_caption, created_at=_now_iso(),
    )
    make_cover = cover or recipe.cover_image is None
    image.filename = f"{image.id}{suffix}"
    destination = recipe_image_path(slug, image)
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source_path, destination)

    recipe.images.append(image)
    if make_cover:
        recipe.cover_image_id = image.id
    try:
        _save_recipes_unlocked(recipes)
    except Exception:
        destination.unlink(missing_ok=True)
        raise
    return image


@_locked_mutation("catalog")
def update_recipe_image(slug: str, image_id: str, role: str | None = None,
                        caption: str | None = None) -> RecipeImage:
    recipes = load_recipes()
    recipe = next((item for item in recipes if item.slug == slug), None)
    if recipe is None:
        raise ValueError(f"Kein Rezept mit Slug '{slug}'.")
    image = next((item for item in recipe.images if item.id == image_id), None)
    if image is None:
        raise ValueError(f"Kein Bild mit id '{image_id}' bei Rezept '{slug}'.")
    if role is not None:
        clean_role = role.strip() or "gallery"
        _no_newlines(clean_role, "Die Bildrolle")
        image.role = clean_role
    if caption is not None:
        clean_caption = caption.strip()
        _no_newlines(clean_caption, "Die Bildbeschreibung")
        image.caption = clean_caption
    _save_recipes_unlocked(recipes)
    return image


@_locked_mutation("catalog")
def set_recipe_cover(slug: str, image_id: str) -> RecipeImage:
    recipes = load_recipes()
    recipe = next((item for item in recipes if item.slug == slug), None)
    if recipe is None:
        raise ValueError(f"Kein Rezept mit Slug '{slug}'.")
    image = next((item for item in recipe.images if item.id == image_id), None)
    if image is None:
        raise ValueError(f"Kein Bild mit id '{image_id}' bei Rezept '{slug}'.")
    recipe.cover_image_id = image.id
    _save_recipes_unlocked(recipes)
    return image


@_locked_mutation("catalog")
def remove_recipe_image(slug: str, image_id: str) -> str | None:
    recipes = load_recipes()
    recipe = next((item for item in recipes if item.slug == slug), None)
    if recipe is None:
        raise ValueError(f"Kein Rezept mit Slug '{slug}'.")
    image = next((item for item in recipe.images if item.id == image_id), None)
    if image is None:
        raise ValueError(f"Kein Bild mit id '{image_id}' bei Rezept '{slug}'.")

    recipe.images = [item for item in recipe.images if item.id != image_id]
    remaining_ids = {item.id for item in recipe.images}
    if recipe.cover_image_id == image_id or recipe.cover_image_id not in remaining_ids:
        recipe.cover_image_id = recipe.images[0].id if recipe.images else None
    _save_recipes_unlocked(recipes)
    recipe_image_path(slug, image).unlink(missing_ok=True)
    folder = recipe_images_dir(slug)
    if folder.exists() and not any(folder.iterdir()):
        folder.rmdir()
    return recipe.cover_image_id


# --- Consistency ------------------------------------------------------------

def check() -> dict:
    """Check hard data-integrity errors and non-blocking organization warnings."""
    # Corrupted data files must be reported as integrity errors instead of
    # crashing the whole diagnostic with a raw loader exception.
    invalid_data_files: list[str] = []
    try:
        recipes = load_recipes()
    except ValueError as error:
        recipes = []
        invalid_data_files.append(str(error))
    indexed = {r.slug for r in recipes}
    present = {p.stem for p in recipes_dir().glob("*.md")} if recipes_dir().exists() else set()
    duplicate_recipe_slugs = sorted({
        recipe.slug for recipe in recipes
        if sum(item.slug == recipe.slug for item in recipes) > 1
    })
    uncategorized = uncategorized_tags([
        tag for recipe in recipes for tag in recipe.tags
    ])
    title_mismatches: list[str] = []
    for recipe in recipes:
        path = recipe_file(recipe.slug)
        if path.is_file() and _markdown_title(path.read_text(encoding="utf-8")) != recipe.title:
            title_mismatches.append(recipe.slug)
    image_root = images_dir()
    image_folders = ({path.name for path in image_root.iterdir()
                      if path.is_dir() and path.name != "_favorites"}
                     if image_root.exists() else set())
    orphaned_image_folders = sorted(image_folders - indexed)
    missing_image_files: list[str] = []
    orphaned_image_files: list[str] = []
    invalid_cover_images: list[str] = []
    for recipe in recipes:
        referenced = {image.filename for image in recipe.images}
        folder = recipe_images_dir(recipe.slug)
        present_images = ({path.name for path in folder.iterdir() if path.is_file()}
                          if folder.exists() else set())
        missing_image_files.extend(
            f"{recipe.slug}/{filename}" for filename in sorted(referenced - present_images)
        )
        orphaned_image_files.extend(
            f"{recipe.slug}/{filename}" for filename in sorted(present_images - referenced)
        )
        if ((recipe.images and recipe.cover_image_id is None)
                or (recipe.cover_image_id is not None
                    and recipe.cover_image_id not in {image.id for image in recipe.images})):
            invalid_cover_images.append(recipe.slug)
    try:
        favorite_needs = favorites_load()
    except ValueError as error:
        favorite_needs = []
        invalid_data_files.append(str(error))
    referenced_favorite_images = {
        product.image_filename
        for need in favorite_needs
        for product in need.products
        if product.image_filename
    }
    favorite_root = favorite_images_dir()
    present_favorite_images = ({path.name for path in favorite_root.iterdir()
                                if path.is_file()}
                               if favorite_root.exists() else set())
    alias_owners: dict[str, list[str]] = {}
    for need in favorite_needs:
        for label in [need.name, *need.aliases]:
            alias_owners.setdefault(normalize_shopping_text(label), []).append(need.name)
    duplicate_favorite_aliases = sorted(
        label for label, owners in alias_owners.items()
        if label and len(set(owners)) > 1
    )

    archive_root = archive_dir()
    archive_folders = (
        [path for path in archive_root.iterdir() if path.is_dir()]
        if archive_root.exists() else []
    )
    orphaned_archive_root_files = (
        sorted(path.name for path in archive_root.iterdir() if path.is_file())
        if archive_root.exists() else []
    )
    stale_archive_transactions = sorted(
        path.name for path in archive_folders if path.name.startswith(".")
    )
    archived_entries: list[ArchivedRecipe] = []
    invalid_archive_entries: list[str] = []
    missing_archive_files: list[str] = []
    archived_title_mismatches: list[str] = []
    missing_archive_image_files: list[str] = []
    orphaned_archive_image_files: list[str] = []
    invalid_archive_cover_images: list[str] = []
    for folder in archive_folders:
        if folder.name.startswith("."):
            continue
        try:
            entry = _load_archived_entry(folder)
        except ValueError:
            invalid_archive_entries.append(folder.name)
            continue
        archived_entries.append(entry)
        markdown = archive_recipe_file(entry.slug)
        if not markdown.is_file():
            missing_archive_files.append(entry.slug)
        elif _markdown_title(markdown.read_text(encoding="utf-8")) != entry.title:
            archived_title_mismatches.append(entry.slug)
        referenced = {image.filename for image in entry.images}
        folder_images = archive_images_dir(entry.slug)
        present_images = (
            {path.name for path in folder_images.iterdir() if path.is_file()}
            if folder_images.exists() else set()
        )
        missing_archive_image_files.extend(
            f"{entry.slug}/{filename}"
            for filename in sorted(referenced - present_images)
        )
        orphaned_archive_image_files.extend(
            f"{entry.slug}/{filename}"
            for filename in sorted(present_images - referenced)
        )
        image_ids = {image.id for image in entry.images}
        if ((entry.images and entry.recipe.cover_image_id is None)
                or (entry.recipe.cover_image_id is not None
                    and entry.recipe.cover_image_id not in image_ids)):
            invalid_archive_cover_images.append(entry.slug)

    archived_slugs = {entry.slug for entry in archived_entries}
    known_slugs = indexed | archived_slugs
    active_archive_conflicts = sorted(indexed & archived_slugs)
    try:
        shopping_items = shopping_load()
    except ValueError as error:
        shopping_items = []
        invalid_data_files.append(str(error))
    unresolved_shopping_sources = sorted({
        item.source for item in shopping_items
        if not item.deleted and item.source and item.source not in known_slugs
    })
    try:
        log_entries = load_log()
    except ValueError as error:
        log_entries = []
        invalid_data_files.append(str(error))
    unresolved_log_references = sorted({
        entry.get("slug") for entry in log_entries
        if entry.get("slug") and entry.get("slug") not in known_slugs
    })

    result = {
        "recipe_count": len(indexed),
        "archive_count": len(archived_entries),
        "duplicate_recipe_slugs": duplicate_recipe_slugs,
        "orphaned_files": sorted(present - indexed),   # .md without index entry
        "missing_files": sorted(indexed - present),    # index entry without .md
        "title_mismatches": sorted(title_mismatches),
        "uncategorized_tags": uncategorized,           # tags in no category
        "orphaned_image_folders": orphaned_image_folders,
        "orphaned_image_files": orphaned_image_files,
        "missing_image_files": missing_image_files,
        "invalid_cover_images": sorted(invalid_cover_images),
        "invalid_archive_entries": sorted(invalid_archive_entries),
        "orphaned_archive_root_files": orphaned_archive_root_files,
        "stale_archive_transactions": stale_archive_transactions,
        "missing_archive_files": sorted(missing_archive_files),
        "archived_title_mismatches": sorted(archived_title_mismatches),
        "missing_archive_image_files": missing_archive_image_files,
        "orphaned_archive_image_files": orphaned_archive_image_files,
        "invalid_archive_cover_images": sorted(invalid_archive_cover_images),
        "active_archive_conflicts": active_archive_conflicts,
        "unresolved_shopping_sources": unresolved_shopping_sources,
        "unresolved_log_references": unresolved_log_references,
        "favorite_need_count": len(favorite_needs),
        "duplicate_favorite_aliases": duplicate_favorite_aliases,
        "orphaned_favorite_image_files": sorted(
            present_favorite_images - referenced_favorite_images
        ),
        "missing_favorite_image_files": sorted(
            referenced_favorite_images - present_favorite_images
        ),
        "invalid_data_files": invalid_data_files,
    }
    hard_error_fields = [
        "duplicate_recipe_slugs",
        "orphaned_files",
        "missing_files",
        "title_mismatches",
        "orphaned_image_folders",
        "orphaned_image_files",
        "missing_image_files",
        "invalid_cover_images",
        "invalid_archive_entries",
        "orphaned_archive_root_files",
        "stale_archive_transactions",
        "missing_archive_files",
        "archived_title_mismatches",
        "missing_archive_image_files",
        "orphaned_archive_image_files",
        "invalid_archive_cover_images",
        "active_archive_conflicts",
        "unresolved_shopping_sources",
        "duplicate_favorite_aliases",
        "orphaned_favorite_image_files",
        "missing_favorite_image_files",
        "invalid_data_files",
    ]
    warning_fields = ["uncategorized_tags", "unresolved_log_references"]
    result["errors"] = [
        {"code": field, "items": result[field]}
        for field in hard_error_fields if result[field]
    ]
    result["warnings"] = [
        {"code": field, "items": result[field]}
        for field in warning_fields if result[field]
    ]
    result["ok"] = not result["errors"]
    return result


# ===========================================================================
# Shopping list  (feature: shopping list + PWA, see docs/plan-einkaufsliste-pwa.md)
# ===========================================================================
#
# Storage:  data/shopping_list.json   (shape: {"items": [ <item-dict>, ... ]})
# An item is sync-capable: `updated_at` (UTC ISO) drives "last writer wins" on
# sync, `deleted` is a tombstone so deletions propagate between devices.
# NOTHING is ever hard-deleted.


@dataclass
class ShoppingItem:
    """One entry on the shopping list (sync-capable)."""
    id: str
    text: str                       # e.g. "200 g Spaghetti" (v1: whole ingredient line)
    quantity: str = ""              # optional, v1 usually empty (text holds the amount)
    checked: bool = False           # ticked off?
    source: str | None = None       # slug of the recipe the ingredient came from
    created_at: str = ""            # UTC ISO, e.g. "2026-06-23T18:00:00Z"
    updated_at: str = ""            # UTC ISO -- drives "last writer wins" on sync
    deleted: bool = False           # tombstone for sync (never hard-delete)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "ShoppingItem":
        allowed = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in allowed})


def _parse_iso(value: str) -> datetime | None:
    """Parse an ISO timestamp used by the shopping sync.

    Both the former second-precision format and the current millisecond format
    are accepted so existing data remains compatible.
    """
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _version_key(value: str) -> tuple:
    """Comparable sync version; valid ISO values beat malformed legacy data."""
    parsed = _parse_iso(value)
    return (1, parsed) if parsed is not None else (0, value)


def _now_iso(after: str | None = None) -> str:
    """Current UTC timestamp with milliseconds and a literal ``Z``.

    When ``after`` belongs to the same item, the result is guaranteed to be
    newer. This prevents a rapid add-then-toggle sequence from producing two
    indistinguishable versions while remaining compatible with old timestamps.
    """
    now = datetime.now(timezone.utc)
    previous = _parse_iso(after or "")
    if previous is not None and now <= previous:
        now = previous + timedelta(milliseconds=1)
    return now.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def new_id() -> str:
    """New unique item id (uuid4 hex)."""
    return uuid.uuid4().hex


# --- Preferred products -----------------------------------------------------

def normalize_shopping_text(value: str) -> str:
    """Deterministic favorite matching: case and whitespace only.

    Quantities, punctuation and words deliberately stay untouched. A label is
    matched only after it has explicitly become a need name or alias.
    """
    lowered = re.sub(r"\s+", " ", (value or "").strip()).lower()
    return lowered.replace("ß", "ss")


def favorites_load() -> list[ShoppingNeed]:
    """Load the shared household preference catalog."""
    path = favorites_path()
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(
            f"Ungültige Favoriten in '{path.name}': {error}"
        ) from error
    if not isinstance(data, dict):
        raise ValueError(
            f"Ungültige Favoriten in '{path.name}': JSON-Objekt erwartet."
        )
    needs = data.get("needs", [])
    if (not isinstance(needs, list)
            or not all(isinstance(need, dict) for need in needs)):
        raise ValueError(
            f"Ungültige Favoriten in '{path.name}': "
            "'needs' muss eine Liste von Objekten sein."
        )
    return [ShoppingNeed.from_dict(need) for need in needs]


def _favorites_save_unlocked(needs: list[ShoppingNeed]) -> None:
    _write_json(favorites_path(), {"needs": [need.to_dict() for need in needs]})


@_locked_mutation("favorites")
def favorites_save(needs: list[ShoppingNeed]) -> None:
    """Replace the full preference catalog through its public locked save."""
    _favorites_save_unlocked(needs)


def favorite_get_need(identifier: str) -> ShoppingNeed | None:
    """Find a need by opaque id or exact normalized canonical name."""
    normalized = normalize_shopping_text(identifier)
    return next((need for need in favorites_load()
                 if need.id == identifier
                 or normalize_shopping_text(need.name) == normalized), None)


def favorite_match(text: str, needs: list[ShoppingNeed] | None = None) -> ShoppingNeed | None:
    """Return the need whose name or explicit alias matches ``text`` exactly."""
    normalized = normalize_shopping_text(text)
    if not normalized:
        return None
    for need in favorites_load() if needs is None else needs:
        labels = [need.name, *need.aliases]
        if normalized in {normalize_shopping_text(label) for label in labels}:
            return need
    return None


def _favorite_need(needs: list[ShoppingNeed], identifier: str) -> ShoppingNeed:
    normalized = normalize_shopping_text(identifier)
    need = next((item for item in needs
                 if item.id == identifier
                 or normalize_shopping_text(item.name) == normalized), None)
    if need is None:
        raise ValueError(f"Kein Einkaufsbedarf mit id oder Name '{identifier}'.")
    return need


def _favorite_product(need: ShoppingNeed, product_id: str) -> FavoriteProduct:
    product = next((item for item in need.products if item.id == product_id), None)
    if product is None:
        raise ValueError(
            f"Kein Lieblingsprodukt mit id '{product_id}' bei '{need.name}'."
        )
    return product


def _favorite_label_owner(label: str, needs: list[ShoppingNeed],
                          except_need_id: str | None = None) -> ShoppingNeed | None:
    normalized = normalize_shopping_text(label)
    if not normalized:
        return None
    for need in needs:
        if need.id == except_need_id:
            continue
        if normalized in {normalize_shopping_text(value)
                          for value in [need.name, *need.aliases]}:
            return need
    return None


@_locked_mutation("favorites")
def favorite_add_need(name: str, aliases: list[str] | None = None) -> ShoppingNeed:
    name = (name or "").strip()
    if not name:
        raise ValueError("Der Einkaufsbedarf braucht einen Namen.")
    _no_newlines(name, "Der Name eines Einkaufsbedarfs")
    needs = favorites_load()
    owner = _favorite_label_owner(name, needs)
    if owner is not None:
        raise ValueError(f"'{name}' gehört bereits zu '{owner.name}'.")

    clean_aliases: list[str] = []
    seen = {normalize_shopping_text(name)}
    for alias in aliases or []:
        cleaned = alias.strip()
        normalized = normalize_shopping_text(cleaned)
        if not normalized or normalized in seen:
            continue
        owner = _favorite_label_owner(cleaned, needs)
        if owner is not None:
            raise ValueError(f"'{cleaned}' gehört bereits zu '{owner.name}'.")
        seen.add(normalized)
        clean_aliases.append(cleaned)

    need = ShoppingNeed(
        id=new_id(), name=name, aliases=clean_aliases, products=[],
        created_at=_now_iso(),
    )
    needs.append(need)
    _favorites_save_unlocked(needs)
    return need


@_locked_mutation("favorites")
def favorite_update_need(identifier: str, name: str) -> ShoppingNeed:
    needs = favorites_load()
    need = _favorite_need(needs, identifier)
    name = (name or "").strip()
    if not name:
        raise ValueError("Der Einkaufsbedarf braucht einen Namen.")
    _no_newlines(name, "Der Name eines Einkaufsbedarfs")
    owner = _favorite_label_owner(name, needs, except_need_id=need.id)
    if owner is not None:
        raise ValueError(f"'{name}' gehört bereits zu '{owner.name}'.")

    old_name = need.name
    if normalize_shopping_text(old_name) != normalize_shopping_text(name):
        if normalize_shopping_text(old_name) not in {
                normalize_shopping_text(alias) for alias in need.aliases}:
            need.aliases.append(old_name)
    need.name = name
    need.aliases = [alias for alias in need.aliases
                    if normalize_shopping_text(alias) != normalize_shopping_text(name)]
    _favorites_save_unlocked(needs)
    return need


@_locked_mutation("favorites")
def favorite_add_alias(identifier: str, alias: str) -> ShoppingNeed:
    needs = favorites_load()
    need = _favorite_need(needs, identifier)
    alias = (alias or "").strip()
    if not alias:
        raise ValueError("Der Alias darf nicht leer sein.")
    _no_newlines(alias, "Ein Alias")
    normalized = normalize_shopping_text(alias)
    own_labels = {normalize_shopping_text(value)
                  for value in [need.name, *need.aliases]}
    if normalized in own_labels:
        return need
    owner = _favorite_label_owner(alias, needs, except_need_id=need.id)
    if owner is not None:
        raise ValueError(f"'{alias}' gehört bereits zu '{owner.name}'.")
    need.aliases.append(alias)
    _favorites_save_unlocked(needs)
    return need


@_locked_mutation("favorites")
def favorite_remove_alias(identifier: str, alias: str) -> ShoppingNeed:
    needs = favorites_load()
    need = _favorite_need(needs, identifier)
    normalized = normalize_shopping_text(alias)
    original_count = len(need.aliases)
    need.aliases = [value for value in need.aliases
                    if normalize_shopping_text(value) != normalized]
    if len(need.aliases) == original_count:
        raise ValueError(f"Kein Alias '{alias}' bei '{need.name}'.")
    _favorites_save_unlocked(needs)
    return need


def _copy_favorite_image(source: str | Path) -> tuple[str, Path]:
    source_path = Path(source).expanduser()
    if not source_path.is_file():
        raise ValueError(f"Bilddatei nicht gefunden: '{source_path}'.")
    suffix = source_path.suffix.lower()
    if suffix not in IMAGE_EXTENSIONS:
        allowed = ", ".join(sorted(IMAGE_EXTENSIONS))
        raise ValueError(f"Nicht unterstütztes Bildformat '{suffix}' ({allowed}).")
    if not _valid_image_header(source_path, suffix):
        raise ValueError(
            f"Datei ist kein gültiges {suffix.lstrip('.').upper()}-Bild: '{source_path}'."
        )
    filename = f"{new_id()}{suffix}"
    destination = favorite_images_dir() / filename
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source_path, destination)
    return filename, destination


def favorite_image_path(filename: str) -> Path:
    return favorite_images_dir() / Path(filename).name


def favorite_image_is_referenced(filename: str) -> bool:
    return any(product.image_filename == filename
               for need in favorites_load() for product in need.products)


@_locked_mutation("favorites")
def favorite_add_product(identifier: str, name: str, *, brand: str = "",
                         store: str = "", note: str = "",
                         image: str | Path | None = None) -> FavoriteProduct:
    needs = favorites_load()
    need = _favorite_need(needs, identifier)
    name = (name or "").strip()
    if not name:
        raise ValueError("Das Lieblingsprodukt braucht einen Namen.")
    _no_newlines(name, "Der Produktname")
    brand = (brand or "").strip()
    if not brand:
        raise ValueError("Das Lieblingsprodukt braucht eine Marke.")
    _no_newlines(brand, "Die Marke")
    _no_newlines(store.strip(), "Das Geschäft")
    _no_newlines(note.strip(), "Die Notiz")
    product = FavoriteProduct(
        id=new_id(), name=name, brand=brand, store=store.strip(),
        note=note.strip(), created_at=_now_iso(),
    )
    copied: Path | None = None
    if image:
        product.image_filename, copied = _copy_favorite_image(image)
    need.products.append(product)
    try:
        _favorites_save_unlocked(needs)
    except Exception:
        if copied is not None:
            copied.unlink(missing_ok=True)
        raise
    return product


@_locked_mutation("favorites")
def favorite_update_product(identifier: str, product_id: str, *,
                            name: str | None = None, brand: str | None = None,
                            store: str | None = None, note: str | None = None,
                            image: str | Path | None = None,
                            remove_image: bool = False) -> FavoriteProduct:
    if image and remove_image:
        raise ValueError("Bild kann nicht gleichzeitig ersetzt und entfernt werden.")
    needs = favorites_load()
    need = _favorite_need(needs, identifier)
    product = _favorite_product(need, product_id)
    if name is not None:
        cleaned_name = name.strip()
        if not cleaned_name:
            raise ValueError("Das Lieblingsprodukt braucht einen Namen.")
        _no_newlines(cleaned_name, "Der Produktname")
        product.name = cleaned_name
    if brand is not None:
        cleaned_brand = brand.strip()
        if not cleaned_brand:
            raise ValueError("Das Lieblingsprodukt braucht eine Marke.")
        _no_newlines(cleaned_brand, "Die Marke")
        product.brand = cleaned_brand
    if store is not None:
        cleaned_store = store.strip()
        _no_newlines(cleaned_store, "Das Geschäft")
        product.store = cleaned_store
    if note is not None:
        cleaned_note = note.strip()
        _no_newlines(cleaned_note, "Die Notiz")
        product.note = cleaned_note

    old_filename = product.image_filename
    copied: Path | None = None
    if image:
        product.image_filename, copied = _copy_favorite_image(image)
    elif remove_image:
        product.image_filename = ""
    try:
        _favorites_save_unlocked(needs)
    except Exception:
        if copied is not None:
            copied.unlink(missing_ok=True)
        raise
    if old_filename and old_filename != product.image_filename:
        favorite_image_path(old_filename).unlink(missing_ok=True)
    return product


@_locked_mutation("favorites")
def favorite_move_product(identifier: str, product_id: str,
                          position: int) -> ShoppingNeed:
    needs = favorites_load()
    need = _favorite_need(needs, identifier)
    product = _favorite_product(need, product_id)
    if (isinstance(position, bool) or not isinstance(position, int)
            or not 1 <= position <= len(need.products)):
        raise ValueError(f"Position muss zwischen 1 und {len(need.products)} liegen.")
    need.products.remove(product)
    need.products.insert(position - 1, product)
    _favorites_save_unlocked(needs)
    return need


@_locked_mutation("favorites")
def favorite_remove_product(identifier: str, product_id: str) -> ShoppingNeed:
    needs = favorites_load()
    need = _favorite_need(needs, identifier)
    product = _favorite_product(need, product_id)
    need.products = [item for item in need.products if item.id != product.id]
    _favorites_save_unlocked(needs)
    if product.image_filename:
        favorite_image_path(product.image_filename).unlink(missing_ok=True)
    folder = favorite_images_dir()
    if folder.exists() and not any(folder.iterdir()):
        folder.rmdir()
    return need


@_locked_mutation("favorites")
def favorite_remove_need(identifier: str) -> ShoppingNeed:
    needs = favorites_load()
    need = _favorite_need(needs, identifier)
    _favorites_save_unlocked([item for item in needs if item.id != need.id])
    for product in need.products:
        if product.image_filename:
            favorite_image_path(product.image_filename).unlink(missing_ok=True)
    folder = favorite_images_dir()
    if folder.exists() and not any(folder.iterdir()):
        folder.rmdir()
    return need


# --- Ingredient parsing -----------------------------------------------------

def parse_ingredients(content: str) -> list[str]:
    """Extract ingredient lines from the recipe Markdown.

    Rule (v1): all bullet items ('-' or '*') in the section under the heading
    '## Zutaten', up to the next '##' heading. For each bullet the WHOLE line
    (without the bullet char and surrounding whitespace) as one entry. Empty
    bullets are skipped. If there is no ingredient section the result is [].
    """
    ingredients: list[str] = []
    in_section = False
    for line in content.splitlines():
        s = line.strip()
        if s == "## Zutaten":
            in_section = True
            continue
        if not in_section:
            continue
        if s.startswith("## "):       # next heading ends the section
            break
        if s.startswith("-") or s.startswith("*"):
            entry = s[1:].strip()
            if entry:                 # skip empty bullets
                ingredients.append(entry)
    return ingredients


# --- Load / save ------------------------------------------------------------

def shopping_load() -> list[ShoppingItem]:
    """Load all items from data/shopping_list.json -- INCLUDING tombstones
    (deleted=True). If the file does not exist the result is []."""
    p = shopping_path()
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(
            f"Ungültige Einkaufsliste in '{p.name}': {error}"
        ) from error
    if not isinstance(data, dict):
        raise ValueError(
            f"Ungültige Einkaufsliste in '{p.name}': JSON-Objekt erwartet."
        )
    items = data.get("items", [])
    if (not isinstance(items, list)
            or not all(isinstance(item, dict) for item in items)):
        raise ValueError(
            f"Ungültige Einkaufsliste in '{p.name}': "
            "'items' muss eine Liste von Objekten sein."
        )
    return [ShoppingItem.from_dict(item) for item in items]


def _shopping_save_unlocked(items: list[ShoppingItem]) -> None:
    """Write items atomically to data/shopping_list.json (uses _write_json).
    File shape: {"items": [ <item-dict>, ... ]}. Tombstones are kept."""
    _write_json(shopping_path(), {"items": [i.to_dict() for i in items]})


@_locked_mutation("shopping")
def shopping_save(items: list[ShoppingItem]) -> None:
    """Replace the full shopping state through its public locked save."""
    _shopping_save_unlocked(items)


# --- Modify -----------------------------------------------------------------

def _shopping_append(entries: list[tuple[str, str]],
                     source: str | None = None) -> list[ShoppingItem]:
    """Append already validated entries while the shopping lock is held."""
    items = shopping_load()
    new_items: list[ShoppingItem] = []
    for text, quantity in entries:
        now = _now_iso()
        new_items.append(ShoppingItem(
            id=new_id(), text=text, quantity=quantity, checked=False,
            source=source, created_at=now, updated_at=now, deleted=False,
        ))
    items.extend(new_items)
    _shopping_save_unlocked(items)
    return new_items


def _shopping_text(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Ein Einkaufslisten-Eintrag braucht einen Text.")
    _no_newlines(value, "Der Text eines Einkaufslisten-Eintrags")
    return value.strip()


def _shopping_quantity(value: str) -> None:
    """Validate the optional single-line quantity label (text has its own
    validator; matching normalizes whitespace, so this is store hygiene)."""
    if (not isinstance(value, str) or "\n" in value or "\r" in value):
        raise ValueError(
            "Die Menge eines Einkaufslisten-Eintrags muss ein "
            "einzeiliger Text sein."
        )


def shopping_add(text: str, quantity: str = "", source: str | None = None) -> ShoppingItem:
    """Create, save and return a new item. Sets id (new_id()), created_at and
    updated_at (= _now_iso()), checked=False, deleted=False. A source must name
    an existing recipe and makes the item participate in that recipe's active
    import guard."""
    resources = ("shopping",) if source is None else ("catalog", "shopping")
    def add():
        if source is not None and get(source) is None:
            raise ValueError(f"Kein Rezept mit Slug '{source}'.")
        _shopping_quantity(quantity)
        return _shopping_append([(_shopping_text(text), quantity)], source=source)[0]
    return _execute_locked_mutation(
        resources, add, event_resources=("shopping",),
    )


@_locked_mutation("shopping")
def shopping_add_many(texts: list[str]) -> list[ShoppingItem]:
    """Add a non-empty group of free-text entries in one transaction."""
    if not isinstance(texts, list) or not texts:
        raise ValueError("Mindestens ein Einkaufslisten-Eintrag ist erforderlich.")
    entries = [(_shopping_text(text), "") for text in texts]
    return _shopping_append(entries)


@_locked_mutation("catalog", "shopping", event_resources=("shopping",))
def shopping_add_recipe(slug: str) -> list[ShoppingItem]:
    """Put all ingredients of a recipe (parse_ingredients on its .md) onto the
    list, source=slug. Returns the newly added items. ValueError if the recipe
    is unknown, has no importable ingredients, or already has visible items on
    the list."""
    r = get(slug)
    if r is None:
        raise ValueError(f"Kein Rezept mit Slug '{slug}'.")
    ingredients = parse_ingredients(r.content())
    if not ingredients:
        raise ValueError(f"Rezept '{slug}' enthält keine importierbaren Zutaten.")
    existing = [
        item for item in shopping_load()
        if item.source == slug and not item.deleted
    ]
    if existing:
        verb = "steht" if len(existing) == 1 else "stehen"
        raise ValueError(
            f"{len(existing)} Einkaufsposten aus '{slug}' {verb} bereits "
            "auf der Einkaufsliste."
        )
    entries = [(ingredient, "") for ingredient in ingredients]
    return _shopping_append(entries, source=slug)


def shopping_list(include_done: bool = True,
                  include_deleted: bool = False) -> list[ShoppingItem]:
    """Visible items. Tombstones (deleted) hidden by default; include_done=False
    additionally hides done (checked) ones. Order: by created_at ascending
    (insertion order)."""
    items = [i for i in shopping_load() if include_deleted or not i.deleted]
    if not include_done:
        items = [i for i in items if not i.checked]
    items.sort(key=lambda i: i.created_at)
    return items


@_locked_mutation("shopping")
def shopping_toggle(item_id: str, checked: bool | None = None) -> ShoppingItem:
    """Set the done checkmark. checked=None toggles; otherwise the value is set.
    Updates updated_at only for a state transition and returns the item.
    ValueError if the id is unknown or the item is a tombstone."""
    items = shopping_load()
    target = next((i for i in items if i.id == item_id and not i.deleted), None)
    if target is None:
        raise ValueError(f"Kein Einkauf-Item mit id '{item_id}'.")
    next_checked = (not target.checked) if checked is None else checked
    if target.checked == next_checked:
        return target
    target.checked = next_checked
    target.updated_at = _now_iso(target.updated_at)
    _shopping_save_unlocked(items)
    return target


@_locked_mutation("shopping")
def shopping_remove(item_id: str) -> None:
    """Mark item as a tombstone: deleted=True + update updated_at (do NOT hard-
    delete from the file, so the deletion syncs). Repeating the removal is a
    no-op. ValueError if the id is unknown."""
    items = shopping_load()
    target = next((i for i in items if i.id == item_id), None)
    if target is None:
        raise ValueError(f"Kein Einkauf-Item mit id '{item_id}'.")
    if target.deleted:
        return
    target.deleted = True
    target.updated_at = _now_iso(target.updated_at)
    _shopping_save_unlocked(items)


@_locked_mutation("shopping")
def shopping_remove_done() -> int:
    """Tombstone every checked visible item and return the removed count."""
    items = shopping_load()
    count = 0
    for item in items:
        if item.checked and not item.deleted:
            item.deleted = True
            item.updated_at = _now_iso(item.updated_at)
            count += 1
    if count:
        _shopping_save_unlocked(items)
    return count


@_locked_mutation("shopping")
def shopping_clear() -> int:
    """Tombstone every visible item and return the removed count."""
    items = shopping_load()
    count = 0
    for item in items:
        if not item.deleted:
            item.deleted = True
            item.updated_at = _now_iso(item.updated_at)
            count += 1
    if count:
        _shopping_save_unlocked(items)
    return count


# --- Sync -------------------------------------------------------------------

@_locked_mutation("shopping")
def shopping_merge(remote_items: list[dict]) -> list[ShoppingItem]:
    """Full-state sync: merge remote_items (raw item dicts from the client) into
    the local list, save the result and return it (incl. tombstones).

    Rule "last writer wins": per id the version with the later ISO timestamp
    wins. Old second-precision and new millisecond timestamps are both accepted.
    On a tie the local version stays. Ids that exist only remotely are taken
    over; tombstones (deleted=True) propagate like any other change.
    Every taken-over item needs non-empty text, and a visible item's source
    must still name a known active or archived recipe. Tombstones keep their
    deliberately unvalidated sources so that a purge of the referenced recipe
    never blocks a legitimate sync.
    """
    # Local state (incl. tombstones) as the source of truth, indexed by id.
    merged: dict[str, ShoppingItem] = {i.id: i for i in shopping_load()}
    known_sources: set[str] | None = None

    if not isinstance(remote_items, list):
        raise ValueError("Der Einkaufslisten-Stand muss eine Liste sein.")

    for raw in remote_items:
        if not isinstance(raw, dict):
            raise ValueError("Jeder Einkaufslisten-Eintrag muss ein Objekt sein.")
        try:
            remote = ShoppingItem.from_dict(raw)
        except TypeError as error:
            raise ValueError("Ein Einkaufslisten-Eintrag ist unvollständig.") from error
        if not isinstance(remote.id, str) or not remote.id:
            raise ValueError("Jeder Einkaufslisten-Eintrag braucht eine id.")
        if not isinstance(remote.text, str):
            raise ValueError("Der Text eines Einkaufslisten-Eintrags ist ungültig.")
        if not remote.text.strip():
            raise ValueError("Ein Einkaufslisten-Eintrag braucht einen Text.")
        if not isinstance(remote.quantity, str):
            raise ValueError("Die Menge eines Einkaufslisten-Eintrags ist ungültig.")
        # Ohne die Statusfelder blieben leere Zeitstempel stehen und eine nur
        # implizit abwesende Tombstone-Markierung würde ein gelöschtes Item
        # beim nächsten Sync wiederbeleben; deshalb wird Anwesenheit geprüft.
        if (not isinstance(raw.get("checked"), bool)
                or not isinstance(raw.get("deleted"), bool)):
            raise ValueError(
                "Der Status eines Einkaufslisten-Eintrags ist ungültig oder fehlt."
            )
        if remote.source is not None and not isinstance(remote.source, str):
            raise ValueError("Die Quelle eines Einkaufslisten-Eintrags ist ungültig.")
        if (not isinstance(raw.get("created_at"), str) or not raw["created_at"]
                or not isinstance(raw.get("updated_at"), str) or not raw["updated_at"]):
            raise ValueError(
                "Der Zeitstempel eines Einkaufslisten-Eintrags ist ungültig oder fehlt."
            )
        local = merged.get(remote.id)
        # id only remote -> take it over.
        # id on both sides -> later updated_at wins;
        # on a tie the local version stays.
        if local is None or _version_key(remote.updated_at) > _version_key(local.updated_at):
            if not remote.deleted and remote.source:
                # Only taken-over versions are persisted, so a losing stale
                # version may still reference a since-purged recipe.
                if known_sources is None:
                    known_sources = set(recipe_references())
                if remote.source not in known_sources:
                    raise ValueError(
                        f"Die Quelle '{remote.source}' eines "
                        "Einkaufslisten-Eintrags ist kein bekanntes Rezept."
                    )
            merged[remote.id] = remote
    # ids that exist only locally stay unchanged.

    result = list(merged.values())
    _shopping_save_unlocked(result)
    return result
