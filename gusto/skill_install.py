"""Install the Gusto agent skill into supported local agent hosts."""
from __future__ import annotations

import os
import shutil
import stat
import sys
import tempfile
import uuid
from pathlib import Path

from . import __version__


REQUIRED_SKILL_FILES = (
    Path("SKILL.md"),
    Path("references/cli.md"),
    Path("references/installation.md"),
    Path("references/telegram.md"),
)


def bundled_skill_path(*, prefix: str | Path | None = None,
                       source_root: str | Path | None = None) -> Path:
    """Locate the skill belonging to this checkout or installed runtime."""
    if source_root is None:
        source_root = Path(__file__).resolve().parent.parent
    source_root = Path(source_root)
    checkout = source_root / "skill" / "gusto"
    if (source_root / "pyproject.toml").is_file() and checkout.is_dir():
        return checkout.resolve()

    runtime = Path(sys.prefix if prefix is None else prefix)
    installed = runtime / "share" / "gusto" / "skill" / "gusto"
    if installed.is_dir():
        return installed.resolve()
    raise ValueError(
        "Der zu dieser Gusto-Version gehörende Agent-Skill fehlt in der Runtime. "
        "Repariere oder aktualisiere die Gusto-Installation."
    )


def _skill_files(source: Path) -> list[str]:
    for required in REQUIRED_SKILL_FILES:
        if not (source / required).is_file():
            raise ValueError(
                f"Der gebündelte Gusto-Skill ist unvollständig: {required.as_posix()} fehlt."
            )
    files: list[str] = []
    for path in sorted(source.rglob("*")):
        if path.is_symlink():
            raise ValueError(
                "Der gebündelte Gusto-Skill enthält einen unsicheren symbolischen "
                f"Link: {path.relative_to(source).as_posix()}."
            )
        if path.is_file():
            files.append(path.relative_to(source).as_posix())
    return files


def _exists(path: Path) -> bool:
    return path.exists() or path.is_symlink()


def _is_reparse_point(path: Path) -> bool:
    try:
        attributes = getattr(path.lstat(), "st_file_attributes", 0)
    except OSError:
        return False
    return bool(attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))


def _remove_path(path: Path) -> None:
    if not _exists(path):
        return
    if path.is_symlink():
        path.unlink()
    elif _is_reparse_point(path):
        os.rmdir(path)
    elif path.is_dir():
        shutil.rmtree(path)
    else:
        path.unlink()


def _vbot_home(user_home: str | Path | None = None) -> Path:
    home = Path.home() if user_home is None else Path(user_home)
    candidate = home.expanduser().resolve() / ".vbot"
    if not candidate.exists():
        raise ValueError(
            f"vBot-Standardverzeichnis nicht gefunden: {candidate}. "
            "Installiere oder initialisiere vBot zuerst."
        )
    if not candidate.is_dir():
        raise ValueError(f"vBot-Standardverzeichnis ist kein Ordner: {candidate}.")
    return candidate


_HOST_HOMES = {
    "vbot": _vbot_home,
}


def supported_hosts() -> tuple[str, ...]:
    """Return stable CLI host choices from the single delivery registry."""
    return tuple(_HOST_HOMES)


def install_skill(host: str, *, dry_run: bool = False,
                  user_home: str | Path | None = None,
                  source: str | Path | None = None) -> dict:
    """Install the bundled Gusto skill for one explicit local agent host."""
    resolve_home = _HOST_HOMES.get(host)
    if resolve_home is None:
        raise ValueError(
            f"Nicht unterstützter Agent-Host '{host}'. Unterstützt: "
            + ", ".join(supported_hosts()) + "."
        )
    skill_source = (
        bundled_skill_path() if source is None else Path(source).expanduser().resolve()
    )
    if not skill_source.is_dir():
        raise ValueError(f"Gebündelter Gusto-Skill nicht gefunden: {skill_source}.")
    files = _skill_files(skill_source)
    host_home = resolve_home(user_home)
    skills_dir = host_home / "skills"
    destination = skills_dir / "gusto"
    overwritten = _exists(destination)
    if overwritten and not (destination.is_dir() or destination.is_symlink()):
        raise ValueError(
            f"Skill-Ziel existiert, ist aber kein Ordner: {destination}."
        )

    result = {
        "status": "dry_run" if dry_run else "installed",
        "host": host,
        "gusto_version": __version__,
        "source": os.fspath(skill_source),
        "destination": os.fspath(destination),
        "overwritten": overwritten,
        "files": files,
    }
    if dry_run:
        return result

    staging: Path | None = None
    backup: Path | None = None
    try:
        skills_dir.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix=".gusto-skill-", dir=skills_dir))
        shutil.copytree(skill_source, staging, dirs_exist_ok=True)
        _skill_files(staging)
        if overwritten:
            backup = skills_dir / f".gusto-skill-backup-{uuid.uuid4().hex}"
            os.replace(destination, backup)
        os.replace(staging, destination)
        staging = None
        if backup is not None:
            _remove_path(backup)
            backup = None
    except (OSError, shutil.Error) as error:
        restore_error: OSError | None = None
        if backup is not None and _exists(backup):
            try:
                if _exists(destination):
                    _remove_path(destination)
                os.replace(backup, destination)
                backup = None
            except OSError as failed_restore:
                restore_error = failed_restore
        message = f"Gusto-Skill konnte nicht installiert werden: {error}"
        if restore_error is not None and backup is not None:
            message += (
                f"; der bisherige Skill blieb zur Wiederherstellung unter {backup}: "
                f"{restore_error}"
            )
        raise ValueError(message) from error
    finally:
        if staging is not None:
            _remove_path(staging)
    return result
