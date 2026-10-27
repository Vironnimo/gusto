"""Portable, verified snapshots and guarded whole-household restoration."""
from __future__ import annotations

import hashlib
import io
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import uuid
import zipfile
import zlib
from pathlib import Path, PurePosixPath

from gusto.core import storage

RESOURCES = ("catalog", "log", "favorites", "shopping")
DATA_FILES = ("recipes.json", "categories.json", "log.json", "shopping_list.json", "favorites.json")
TREES = ("recipes", "images", "archive")
MAX_ARCHIVE_BYTES = 64 * 1024 * 1024
MAX_FILE_BYTES = 32 * 1024 * 1024
MAX_TOTAL_BYTES = 128 * 1024 * 1024
MAX_FILES = 25_000
MAX_MANIFEST_BYTES = 2 * 1024 * 1024
_COMPONENT = re.compile(r"[^<>:\\|?*\x00-\x1f/]+")
_DEVICE = re.compile(r"(?i)^(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?$")


def _is_link(path: Path) -> bool:
    try:
        attributes = getattr(path.lstat(), "st_file_attributes", 0)
    except FileNotFoundError:
        return False
    return path.is_symlink() or bool(attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))


def _portable_component(name: str) -> bool:
    return bool(name and len(name) <= 255 and _COMPONENT.fullmatch(name)
                and name not in {".", ".."} and not name.startswith(".")
                and not name.endswith((".", " ")) and not _DEVICE.fullmatch(name))


def _allowed(name: str) -> bool:
    parts = name.split("/")
    if not all(_portable_component(part) for part in parts):
        return False
    if len(parts) == 2 and parts[0] == "data":
        return parts[1] in DATA_FILES
    if len(parts) == 2 and parts[0] == "recipes":
        return parts[1].endswith(".md")
    if len(parts) == 3 and parts[0] == "images":
        return True
    if len(parts) == 3 and parts[0] == "archive":
        return parts[2] in {"recipe.md", "metadata.json"}
    return len(parts) == 4 and parts[0] == "archive" and parts[2] == "images"


def _snapshot(root: Path) -> dict[str, bytes]:
    files: dict[str, bytes] = {}
    total = 0
    if _is_link(root / "data"):
        raise ValueError("Das Datenverzeichnis ist ein symbolischer Link.")
    for relative in (*TREES, *(f"data/{name}" for name in DATA_FILES)):
        target = root / relative
        if _is_link(target):
            raise ValueError(f"Symbolischer Link im Datenbestand: {relative}")
        if not target.exists():
            continue
        if ((relative in TREES and not target.is_dir())
                or (relative not in TREES and not target.is_file())):
            raise ValueError(f"Ungültiger Pfad im Datenbestand: {relative}")
        candidates = [target] if target.is_file() else target.rglob("*")
        for path in candidates:
            name = path.relative_to(root).as_posix()
            if _is_link(path):
                raise ValueError(f"Symbolischer Link im Datenbestand: {name}")
            if path.is_dir():
                continue
            if not path.is_file() or not _allowed(name):
                raise ValueError(f"Nicht unterstützte Datei im Datenbestand: {name}")
            size = path.stat().st_size
            total += size
            if size > MAX_FILE_BYTES or total > MAX_TOTAL_BYTES or len(files) >= MAX_FILES:
                raise ValueError("Der Datenbestand überschreitet die Backup-Grenzen.")
            files[name] = path.read_bytes()
    folded = [name.casefold() for name in files]
    if len(set(folded)) != len(folded):
        raise ValueError("Dateinamen im Datenbestand kollidieren auf anderen Systemen.")
    return files


def _fingerprint(files: dict[str, bytes]) -> str:
    digest = hashlib.sha256()
    for name, data in sorted(files.items()):
        digest.update(name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(hashlib.sha256(data).digest())
    return digest.hexdigest()


def _archive(files: dict[str, bytes]) -> bytes:
    manifest = {"version": 1, "files": [
        {"path": name, "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}
        for name, data in sorted(files.items())
    ]}
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False).encode("utf-8"))
        for name, data in sorted(files.items()):
            archive.writestr(name, data)
    value = output.getvalue()
    if len(value) > MAX_ARCHIVE_BYTES:
        raise ValueError("Das Backup überschreitet die Größenbegrenzung.")
    return value


def create_backup() -> bytes:
    """Return one consistent, portable ZIP snapshot of all household resources."""
    with storage._mutation_locks(*RESOURCES):
        files = _snapshot(storage.project_root())
    with tempfile.TemporaryDirectory(prefix="gusto-backup-check-") as directory:
        staged = Path(directory)
        _stage(files, staged)
        _validate(staged)
    value = _archive(files)
    if _unpack(value) != files:
        raise ValueError("Das Backup konnte nicht verifiziert werden.")
    return value


def _unpack(data: bytes) -> dict[str, bytes]:
    if not isinstance(data, bytes) or len(data) > MAX_ARCHIVE_BYTES:
        raise ValueError("Das Backup überschreitet die Größenbegrenzung.")
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            entries = archive.infolist()
            if len(entries) > MAX_FILES + 1:
                raise ValueError("Das Backup enthält zu viele Dateien.")
            names = [entry.filename for entry in entries]
            if len(names) != len(set(name.casefold() for name in names)):
                raise ValueError("Das Backup enthält doppelte Dateinamen.")
            if "manifest.json" not in names:
                raise ValueError("Die Backup-Dateiliste fehlt.")
            for entry in entries:
                mode = (entry.external_attr >> 16) & 0o170000
                if (entry.is_dir() or mode not in (0, stat.S_IFREG)
                        or (entry.filename != "manifest.json" and not _allowed(entry.filename))):
                    raise ValueError(f"Ungültiger Backup-Pfad: {entry.filename}")
                limit = MAX_MANIFEST_BYTES if entry.filename == "manifest.json" else MAX_FILE_BYTES
                if entry.file_size > limit or entry.flag_bits & 1:
                    raise ValueError("Eine Backup-Datei ist zu groß oder verschlüsselt.")
            if sum(e.file_size for e in entries if e.filename != "manifest.json") > MAX_TOTAL_BYTES:
                raise ValueError("Das entpackte Backup ist zu groß.")
            manifest = json.loads(archive.read("manifest.json").decode("utf-8"))
            if (not isinstance(manifest, dict) or type(manifest.get("version")) is not int
                    or manifest.get("version") != 1 or not isinstance(manifest.get("files"), list)):
                raise ValueError("Das Backup-Format wird nicht unterstützt.")
            records = manifest["files"]
            if len(records) != len(entries) - 1:
                raise ValueError("Die Backup-Dateiliste stimmt nicht mit dem Archiv überein.")
            files: dict[str, bytes] = {}
            for record in records:
                if not isinstance(record, dict):
                    raise ValueError("Die Backup-Dateiliste ist ungültig.")
                name, size, checksum = record.get("path"), record.get("size"), record.get("sha256")
                if (not isinstance(name, str) or name not in names or name == "manifest.json"
                        or name in files or isinstance(size, bool) or not isinstance(size, int)
                        or not isinstance(checksum, str)):
                    raise ValueError("Die Backup-Dateiliste ist ungültig.")
                info = archive.getinfo(name)
                if size != info.file_size or size > MAX_FILE_BYTES:
                    raise ValueError(f"Die Größe der Backup-Datei stimmt nicht: {name}")
                payload = archive.read(info)
                if hashlib.sha256(payload).hexdigest() != checksum:
                    raise ValueError(f"Die Prüfsumme der Backup-Datei stimmt nicht: {name}")
                files[name] = payload
            return files
    except (zipfile.BadZipFile, UnicodeError, json.JSONDecodeError, KeyError,
            EOFError, NotImplementedError, zlib.error, RuntimeError) as error:
        raise ValueError("Das Backup ist beschädigt oder nicht lesbar.") from error


def _stage(files: dict[str, bytes], root: Path) -> None:
    for name, data in files.items():
        destination = root.joinpath(*PurePosixPath(name).parts)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)
    for name in TREES:
        (root / name).mkdir(exist_ok=True)


_VALIDATE = r'''
import json, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from gusto.core.catalog.recipes import _recipe_slug
from gusto.core.backup import _portable_component
from gusto.core import checks, favorites, shopping, storage
from gusto.core.catalog import recipes
root = storage.project_root()
index = root / 'data' / 'recipes.json'
sources = []
if index.exists():
    sources.extend(json.loads(index.read_text(encoding='utf-8')))
for path in (root / 'archive').glob('*/metadata.json'):
    sources.append(json.loads(path.read_text(encoding='utf-8'))['recipe'])
for item in sources:
    slug = item['slug']
    if _recipe_slug(slug) != slug:
        raise ValueError('Ungültiger Rezept-Slug im Backup')
    image_ids = set()
    image_names = set()
    for image in item.get('images', []):
        if (not isinstance(image, dict) or not isinstance(image.get('id'), str)
                or not image['id'] or not isinstance(image.get('filename'), str)
                or not _portable_component(image['filename'])):
            raise ValueError('Ungültiger Bilddateiname im Backup')
        image_id = image['id']
        image_name = image['filename'].casefold()
        if image_id in image_ids or image_name in image_names:
            raise ValueError('Doppelte Bildkennung oder Bilddatei im Backup')
        image_ids.add(image_id)
        image_names.add(image_name)
for need in favorites.favorites_load():
    for product in need.products:
        if product.image_filename and not _portable_component(product.image_filename):
            raise ValueError('Ungültiger Produktbilddateiname im Backup')
report = checks.check()
items = shopping.shopping_load()
report['shopping_item_count'] = len(items)
report['shopping_visible_count'] = sum(not item.deleted for item in items)
report['log_count'] = len(recipes.load_log())
print(json.dumps(report, ensure_ascii=False))
'''


def _validate(root: Path) -> dict:
    package_root = Path(__file__).resolve().parents[2]
    env = {**os.environ, "GUSTO_HOME": os.fspath(root)}
    try:
        result = subprocess.run(
            [sys.executable, "-I", "-c", _VALIDATE, os.fspath(package_root)],
            env=env, text=True, encoding="utf-8", errors="replace", capture_output=True,
            timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ValueError("Der Datenbestand im Backup konnte nicht geprüft werden.") from error
    if result.returncode:
        raise ValueError("Der Datenbestand im Backup ist ungültig.")
    try:
        report = json.loads(result.stdout)
    except (ValueError, TypeError) as error:
        raise ValueError("Der Datenbestand im Backup konnte nicht geprüft werden.") from error
    if not report.get("ok"):
        raise ValueError("Der Datenbestand im Backup ist inkonsistent: " +
                         ", ".join(error["code"] for error in report["errors"]))
    return report


def _counts(files: dict[str, bytes], report: dict) -> dict:
    return {key: report[key] for key in (
        "recipe_count", "archive_count", "favorite_need_count", "shopping_item_count",
        "shopping_visible_count", "log_count",
    )} | {
        "file_count": len(files), "total_bytes": sum(map(len, files.values())),
        "image_count": sum(name.startswith("images/") and not name.startswith("images/_favorites/") for name in files),
        "favorite_image_count": sum(name.startswith("images/_favorites/") for name in files),
        "archive_image_count": sum(name.startswith("archive/") and "/images/" in name for name in files),
    }


def _token(archive: bytes, current: dict[str, bytes]) -> str:
    from gusto.core import shopping_sync
    generation = shopping_sync.generation_path()
    if _is_link(generation):
        raise ValueError("Die Shopping-Generationsdatei ist ein symbolischer Link.")
    generation_hash = hashlib.sha256(generation.read_bytes()).hexdigest() if generation.exists() else "absent"
    return hashlib.sha256((hashlib.sha256(archive).hexdigest() + ":" +
                           _fingerprint(current) + ":" + generation_hash).encode("ascii")).hexdigest()


def preview_restore(archive: bytes) -> dict:
    """Validate an archive and bind a preview to it and the current store."""
    files = _unpack(archive)
    with tempfile.TemporaryDirectory(prefix="gusto-backup-check-") as directory:
        stage = Path(directory)
        _stage(files, stage)
        report = _validate(stage)
    with storage._mutation_locks(*RESOURCES):
        current = _snapshot(storage.project_root())
        token = _token(archive, current)
    return {
        "token": token, "counts": _counts(files, report),
        "warnings": report["warnings"],
        "effects": {
            "added": len(files.keys() - current.keys()),
            "removed": len(current.keys() - files.keys()),
            "replaced": sum(files[name] != current[name] for name in files.keys() & current.keys()),
        },
    }


def read_safety_backup(backup_id: str) -> bytes:
    if not isinstance(backup_id, str) or not re.fullmatch(r"[0-9a-f]{32}", backup_id):
        raise ValueError("Ungültige Sicherungskennung.")
    directory = storage.project_root() / "backups"
    path = directory / f"{backup_id}.zip"
    if _is_link(directory) or _is_link(path) or not path.is_file():
        raise ValueError("Sicherung nicht gefunden.")
    value = path.read_bytes()
    _unpack(value)
    return value


def restore_backup(archive: bytes, token: str) -> dict:
    """Restore only the exact archive and current state shown in the preview."""
    files = _unpack(archive)
    root = storage.project_root()
    root.mkdir(parents=True, exist_ok=True)
    transaction = Path(tempfile.mkdtemp(prefix=".gusto-restore-", dir=root))
    preserve = [False]
    try:
        staged = transaction / "staged"
        staged.mkdir()
        _stage(files, staged)
        report = _validate(staged)

        def commit() -> dict:
            current = _snapshot(root)
            if not isinstance(token, str) or token != _token(archive, current):
                raise ValueError("Das Backup oder der Datenbestand hat sich seit der Vorschau geändert. Erstelle eine neue Vorschau.")
            safety_id = uuid.uuid4().hex
            backups = root / "backups"
            if _is_link(backups):
                raise ValueError("Der Sicherungsordner ist ein symbolischer Link.")
            backups.mkdir(exist_ok=True)
            safety = backups / f"{safety_id}.zip"
            safety_bytes = _archive(current)
            if _unpack(safety_bytes) != current:
                raise ValueError("Die Sicherung vor der Wiederherstellung konnte nicht verifiziert werden.")
            with safety.open("xb") as output:
                output.write(safety_bytes)
            if hashlib.sha256(safety.read_bytes()).digest() != hashlib.sha256(safety_bytes).digest():
                raise ValueError("Die gespeicherte Sicherung konnte nicht verifiziert werden.")
            before = transaction / "before"
            before.mkdir()
            targets = [*TREES, *(f"data/{name}" for name in DATA_FILES),
                       "data/shopping-generation.json"]
            moved: list[tuple[Path, Path, bool]] = []
            checkpoint = storage.mutation_checkpoint()
            try:
                for name in targets:
                    target = root / name
                    replacement = staged / name
                    previous = before / name
                    previous.parent.mkdir(parents=True, exist_ok=True)
                    existed = target.exists()
                    if existed:
                        os.replace(target, previous)
                        storage.mark_mutation()
                    moved.append((target, previous, existed))
                    if replacement.exists():
                        target.parent.mkdir(parents=True, exist_ok=True)
                        os.replace(replacement, target)
                        storage.mark_mutation()
                from gusto.core import shopping_sync
                shopping_sync.reset_generation_unlocked()
            except Exception:
                try:
                    for target, previous, existed in reversed(moved):
                        if target.is_dir():
                            shutil.rmtree(target)
                        elif target.exists():
                            target.unlink()
                        if existed:
                            os.replace(previous, target)
                except Exception as error:
                    preserve[0] = True
                    raise RuntimeError(f"Wiederherstellung unvollständig; vorherige Dateien liegen unter {transaction}: {error}") from error
                storage.rollback_completed(checkpoint)
                raise
            return {"safety_backup_id": safety_id, "counts": _counts(files, report)}

        return storage.execute_locked_mutation(RESOURCES, commit)
    finally:
        if not preserve[0]:
            shutil.rmtree(transaction, ignore_errors=True)
