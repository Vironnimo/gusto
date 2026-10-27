"""Data roots, atomic files, mutation locks and change revisions."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import time
from contextlib import contextmanager
from functools import wraps
from pathlib import Path
import gusto.core.errors as errors


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
    return Path(__file__).resolve().parents[2]


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
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
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
    # Both native locks work on empty files; Windows permits locking beyond
    # EOF. Never initialize or replace the inode: a crash between creation
    # and writing a sentinel byte must not strand every later mutation.
    descriptor = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
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


def locked_mutation(*resources: str, event_resources=None):
    """Decorate one complete read-modify-write Core transaction."""
    def decorate(function):
        @wraps(function)
        def locked(*args, **kwargs):
            return execute_locked_mutation(
                resources, lambda: function(*args, **kwargs),
                event_resources=event_resources,
            )
        return locked
    return decorate


def execute_locked_mutation(resources, function, *, event_resources=None):
    """Run one dynamic mutation and emit its event after business locks."""
    mutation_error: Exception | None = None
    with _mutation_locks(*resources):
        previous = getattr(_MUTATION_CONTEXT, "writes", None)
        _MUTATION_CONTEXT.writes = 0
        try:
            result = function()
            changed = bool(_MUTATION_CONTEXT.writes)
        except Exception as error:
            changed = bool(_MUTATION_CONTEXT.writes)
            if not changed:
                raise
            # Cleanup and other post-save steps can fail after a durable
            # change. Publish that change and report its uncertain extent;
            # returning an ordinary rejection would invite unsafe retries.
            mutation_error = error
        finally:
            if previous is None:
                try:
                    del _MUTATION_CONTEXT.writes
                except AttributeError:
                    pass
            else:
                _MUTATION_CONTEXT.writes = previous
    if changed:
        try:
            _record_change(event_resources or resources)
        except (OSError, ValueError) as error:
            if mutation_error is not None:
                raise errors.PostMutationError(
                    "Die Änderung wurde zumindest teilweise gespeichert, "
                    f"aber der Befehl konnte nicht abgeschlossen werden: {mutation_error}. "
                    "Auch das Änderungsjournal konnte nicht aktualisiert "
                    f"werden: {error}"
                ) from mutation_error
            raise errors.PostMutationError(
                "Die Änderung wurde gespeichert, aber das Änderungsjournal "
                f"konnte nicht aktualisiert werden: {error}"
            ) from error
    if mutation_error is not None:
        raise errors.PostMutationError(
            "Die Änderung wurde zumindest teilweise gespeichert, aber der "
            f"Befehl konnte nicht abgeschlossen werden: {mutation_error}"
        ) from mutation_error
    return result


def mark_mutation() -> None:
    """Count confirmed writes so a complete rollback can restore its checkpoint."""
    if hasattr(_MUTATION_CONTEXT, "writes"):
        _MUTATION_CONTEXT.writes += 1


def mutation_checkpoint() -> int:
    return getattr(_MUTATION_CONTEXT, "writes", 0)


def rollback_completed(checkpoint: int) -> None:
    """Forget only the writes that the caller has successfully rolled back."""
    if hasattr(_MUTATION_CONTEXT, "writes"):
        _MUTATION_CONTEXT.writes = checkpoint


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
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
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
            or not 0 <= compacted_revision <= revision
            or not isinstance(compacted_resources, list)
            or not all(isinstance(item, str) for item in compacted_resources)):
        raise ValueError("Ungültiges Gusto-Änderungsjournal.")
    previous_revision = compacted_revision
    for event in events:
        if (not isinstance(event, dict)
                or isinstance(event.get("revision"), bool)
                or not isinstance(event.get("revision"), int)
                or not previous_revision < event["revision"] <= revision
                or not isinstance(event.get("resources"), list)
                or not all(isinstance(item, str) for item in event["resources"])):
            raise ValueError("Ungültiges Gusto-Änderungsjournal.")
        previous_revision = event["revision"]
    # Safely migrate a journal written before compacted resources were
    # persisted. A broad refresh is preferable to silently missing a change.
    retained_revisions = [event["revision"] for event in events]
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
        write_json(changes_path(), {
            **event,
            "events": events,
            "compacted_revision": compacted_revision,
            "compacted_resources": sorted(compacted_resources),
        }, track_mutation=False)
    return event


def _replace_atomic(temporary: Path, path: Path) -> None:
    """Replace a complete file, allowing short-lived Windows reader handles."""
    deadline = time.monotonic() + 1.0
    while True:
        try:
            temporary.replace(path)
            return
        except OSError as error:
            # Windows readers do not generally grant FILE_SHARE_DELETE.
            # A concurrent catalog/SSE read can therefore block the atomic
            # rename briefly even though the writer holds its resource lock.
            if (os.name != "nt" or getattr(error, "winerror", None) not in {5, 32, 33}
                    or time.monotonic() >= deadline):
                raise
            time.sleep(0.01)


def write_json(path: Path, data, *, track_mutation: bool = True) -> bool:
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
        _replace_atomic(temporary, path)
        if track_mutation:
            mark_mutation()
    finally:
        temporary.unlink(missing_ok=True)
    return True


def write_text(path: Path, content: str) -> bool:
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
        _replace_atomic(temporary, path)
        mark_mutation()
    finally:
        temporary.unlink(missing_ok=True)
    return True
