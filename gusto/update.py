"""Verified side-by-side release updates for managed Gusto installations."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import time
import urllib.parse
import urllib.request
import venv
import zipfile
from collections.abc import Callable
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from . import service


DEFAULT_MANIFEST_URL = (
    "https://github.com/Vironnimo/gusto/releases/latest/download/"
    "gusto-release.json"
)


class UpdateError(RuntimeError):
    """Raised when a release cannot be verified or activated."""

    def __init__(
        self,
        message: str,
        *,
        result: dict[str, object] | None = None,
    ) -> None:
        super().__init__(message)
        self.result = result or {"ok": False, "status": "failed", "error": message}


def fetch_bytes(location: str, *, timeout: float = 30.0) -> bytes:
    """Read a local/file fixture or an HTTPS release asset."""
    local = Path(location).expanduser()
    if local.is_file():
        return local.resolve().read_bytes()
    parsed = urllib.parse.urlparse(location)
    if parsed.scheme in {"", "file"}:
        path = Path(urllib.request.url2pathname(parsed.path)
                    if parsed.scheme == "file" else location)
        return path.expanduser().resolve().read_bytes()
    if parsed.scheme != "https":
        raise UpdateError("Release-URLs müssen HTTPS- oder lokale Fixture-Pfade sein.")
    request = urllib.request.Request(
        location, headers={"User-Agent": "Gusto-Updater/1"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def resolve_asset(manifest_location: str, asset: str) -> str:
    parsed = urllib.parse.urlparse(asset)
    if parsed.scheme:
        return asset
    local_manifest = Path(manifest_location).expanduser()
    if local_manifest.is_file():
        return os.fspath((local_manifest.resolve().parent / asset).resolve())
    manifest_parsed = urllib.parse.urlparse(manifest_location)
    if manifest_parsed.scheme:
        return urllib.parse.urljoin(manifest_location, asset)
    return os.fspath((Path(manifest_location).resolve().parent / asset).resolve())


def load_manifest(
    location: str,
    *,
    downloader: Callable[[str], bytes] = fetch_bytes,
) -> dict[str, Any]:
    try:
        value = json.loads(downloader(location).decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise UpdateError(f"Release-Manifest ist nicht lesbar: {error}") from error
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        raise UpdateError("Release-Manifest hat ein unbekanntes Format.")
    version = value.get("version")
    archive = value.get("archive")
    checksum = value.get("sha256")
    if not isinstance(version, str) or not isinstance(archive, str):
        raise UpdateError("Release-Manifest enthält keine Version oder kein Archiv.")
    try:
        service.validate_version(version)
    except service.ServiceError as error:
        raise UpdateError(str(error)) from error
    if (not isinstance(checksum, str) or len(checksum) != 64
            or any(character not in "0123456789abcdefABCDEF"
                   for character in checksum)):
        raise UpdateError("Release-Manifest enthält keine gültige SHA-256-Prüfsumme.")
    return {
        **value,
        "version": version,
        "archive": resolve_asset(location, archive),
        "sha256": checksum.lower(),
    }


def verify_archive(data: bytes, expected_sha256: str) -> str:
    actual = hashlib.sha256(data).hexdigest()
    if actual != expected_sha256.lower():
        raise UpdateError(
            f"SHA-256-Prüfung fehlgeschlagen (erwartet {expected_sha256}, "
            f"erhalten {actual})."
        )
    return actual


def safe_extract(archive: Path, destination: Path) -> None:
    """Extract a ZIP after rejecting traversal, links and absolute entries."""
    destination = destination.resolve()
    destination.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as bundle:
        entries: list[tuple[zipfile.ZipInfo, Path]] = []
        for info in bundle.infolist():
            name = info.filename.replace("\\", "/")
            if not name or name.startswith("/") or "\0" in name:
                raise UpdateError(f"Unsicherer ZIP-Eintrag: {info.filename!r}")
            parts = Path(name).parts
            if (any(part in {"", ".", ".."} for part in parts)
                    or (parts and parts[0].endswith(":"))):
                raise UpdateError(f"Unsicherer ZIP-Eintrag: {info.filename!r}")
            mode = (info.external_attr >> 16) & 0o170000
            if mode == 0o120000:
                raise UpdateError(f"Symlink im Release-Archiv ist nicht erlaubt: {name}")
            target = (destination / Path(*parts)).resolve()
            if not target.is_relative_to(destination):
                raise UpdateError(f"ZIP-Eintrag verlässt das Ziel: {name}")
            entries.append((info, target))
        for info, target in entries:
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with bundle.open(info) as source, target.open("wb") as output:
                shutil.copyfileobj(source, output)
            permissions = (info.external_attr >> 16) & 0o777
            if permissions:
                target.chmod(permissions)


def find_release_payload(extracted: Path) -> tuple[Path, Path]:
    installers = list(extracted.rglob("install.py"))
    wheels = list(extracted.rglob("gusto-*.whl"))
    if len(installers) != 1 or len(wheels) != 1:
        raise UpdateError(
            "Release-ZIP muss genau einen Installer und ein Gusto-Wheel enthalten."
        )
    return installers[0].parent, wheels[0]


def write_runtime_settings(
    runtime: Path,
    data_dir: Path,
    *,
    server_url_value: str | None = None,
) -> Path:
    settings = runtime / "gusto.settings.json"
    temporary = settings.with_suffix(".json.tmp")
    value = {"data_dir": os.fspath(data_dir.resolve())}
    if server_url_value is not None:
        value["server_url"] = server_url_value
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, settings)
    return settings


def verified_runtime(runtime: Path, version: str) -> bool:
    try:
        marker = json.loads(
            (runtime / ".gusto-runtime.json").read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError):
        return False
    return (
        marker == {"version": version, "verified": True}
        and service.runtime_python(runtime).is_file()
    )


def recover_staging(paths: service.ManagedPaths, version: str) -> list[Path]:
    """Remove abandoned staging directories left by interrupted updates."""
    service.validate_version(version)
    removed: list[Path] = []
    if not paths.versions.is_dir():
        return removed
    for candidate in paths.versions.glob(f".staging-{version}-*"):
        candidate = candidate.resolve()
        if candidate.parent != paths.versions or not candidate.is_dir():
            continue
        shutil.rmtree(candidate)
        removed.append(candidate)
    return removed


@contextmanager
def update_lock(paths: service.ManagedPaths):
    """Hold one crash-safe per-app update lock across staging and activation."""
    lock_path = paths.app_root / ".update.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(
            lock_path, os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o600,
        )
    except FileExistsError:
        deadline = time.monotonic() + 2.0
        while lock_path.stat().st_size < 1:
            if time.monotonic() >= deadline:
                raise UpdateError(
                    "Die Gusto-Update-Sperre ist nicht initialisiert."
                )
            time.sleep(0.01)
        handle = lock_path.open("r+b", buffering=0)
    else:
        try:
            os.write(descriptor, b"\0")
        except Exception:
            os.close(descriptor)
            raise
        handle = os.fdopen(descriptor, "r+b", buffering=0)
    handle.seek(0)
    try:
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as error:
        handle.close()
        raise UpdateError(
            "Ein anderes Gusto-Update läuft bereits.",
            result={
                "ok": False,
                "status": "update_locked",
                "error": "Ein anderes Gusto-Update läuft bereits.",
            },
        ) from error
    try:
        yield
    finally:
        try:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()


def install_runtime(
    paths: service.ManagedPaths,
    version: str,
    wheel: Path,
    data_dir: Path,
    *,
    server_url_value: str | None = None,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    environment_builder: Callable[[Path], None] | None = None,
) -> Path:
    """Build in staging, verify, then atomically publish one runtime."""
    runtime = paths.version(version)
    paths.versions.mkdir(parents=True, exist_ok=True)
    recover_staging(paths, version)
    if runtime.exists():
        if verified_runtime(runtime, version):
            return runtime
        shutil.rmtree(runtime)
    if environment_builder is None:
        environment_builder = lambda target: venv.EnvBuilder(
            with_pip=True,
        ).create(target)
    staging = Path(tempfile.mkdtemp(
        prefix=f".staging-{version}-", dir=paths.versions,
    )).resolve()
    try:
        environment_builder(staging)
        python = service.runtime_python(staging)
        result = runner(
            [
                os.fspath(python), "-m", "pip", "install",
                f"gusto[web] @ {wheel.resolve().as_uri()}",
            ],
            text=True, encoding="utf-8", errors="replace", capture_output=True,
        )
        if result.returncode:
            raise UpdateError(
                "Installation des Release-Wheels fehlgeschlagen: "
                + (result.stderr or result.stdout or f"Exit-Code {result.returncode}").strip()
            )
        write_runtime_settings(
            staging, data_dir, server_url_value=server_url_value,
        )
        smoke = runner(
            [
                os.fspath(python), "-c",
                "import gusto,sys; print(gusto.__version__); "
                "raise SystemExit(gusto.__version__ != sys.argv[1])",
                version,
            ],
            text=True, encoding="utf-8", errors="replace", capture_output=True,
        )
        if smoke.returncode:
            raise UpdateError(
                "Import-/Versionsprüfung der neuen Runtime fehlgeschlagen: "
                + (smoke.stderr or smoke.stdout or
                   f"Exit-Code {smoke.returncode}").strip()
            )
        marker = staging / ".gusto-runtime.json"
        marker.write_text(
            json.dumps({"version": version, "verified": True},
                       ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(staging, runtime)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return runtime


def cleanup_versions(
    paths: service.ManagedPaths,
    keep: set[str],
) -> list[str]:
    """Keep current and previous verified runtimes, remove older siblings."""
    removed: list[str] = []
    if not paths.versions.is_dir():
        return removed
    for candidate in paths.versions.iterdir():
        if not candidate.is_dir() or candidate.name in keep:
            continue
        try:
            service.validate_version(candidate.name)
        except service.ServiceError:
            continue
        try:
            marker = json.loads(
                (candidate / ".gusto-runtime.json").read_text(encoding="utf-8")
            )
        except (OSError, json.JSONDecodeError):
            continue
        if marker != {"version": candidate.name, "verified": True}:
            continue
        shutil.rmtree(candidate)
        removed.append(candidate.name)
    return sorted(removed, key=service.version_key)


def _perform_update_locked(
    paths: service.ManagedPaths,
    state: dict[str, Any],
    current_version: str,
    manifest: dict[str, Any],
    base_result: dict[str, object],
    *,
    downloader: Callable[[str], bytes],
    runtime_installer: Callable[..., Path],
    service_installer: Callable[..., dict[str, object]],
    service_controller: Callable[..., dict[str, object]],
    health_waiter: Callable[..., dict[str, Any]],
) -> dict[str, object]:
    latest = str(manifest["version"])
    archive_bytes = downloader(str(manifest["archive"]))
    verify_archive(archive_bytes, str(manifest["sha256"]))
    data_dir = Path(str(state["data_dir"])).expanduser().resolve()
    new_runtime: Path | None = None
    switched = False
    rollback_ok = False
    with tempfile.TemporaryDirectory(prefix="gusto-update-") as temporary:
        temp = Path(temporary)
        archive = temp / "release.zip"
        archive.write_bytes(archive_bytes)
        extracted = temp / "release"
        safe_extract(archive, extracted)
        _, wheel = find_release_payload(extracted)
        new_runtime = runtime_installer(
            paths,
            latest,
            wheel,
            data_dir,
            server_url_value=service.server_url(state),
        )
        try:
            service_controller("stop", app_root=paths.app_root)
            service.write_current(
                paths, latest, previous_version=current_version,
            )
            switched = True
            service_installer(paths, state)
            health_waiter(
                service.health_url(state),
                expected_version=latest,
                expected_data_path=data_dir,
            )
        except Exception as activation_error:
            try:
                if switched:
                    service.write_current(paths, current_version)
                service_installer(paths, state)
                health_waiter(
                    service.health_url(state),
                    expected_version=current_version,
                    expected_data_path=data_dir,
                )
                rollback_ok = True
            except Exception as rollback_error:
                message = (
                    "Update-Aktivierung fehlgeschlagen und Rollback ist "
                    f"fehlgeschlagen: {activation_error}; {rollback_error}"
                )
                raise UpdateError(
                    message,
                    result={
                        "ok": False,
                        "status": "rollback_failed",
                        "error": message,
                        "rollback": False,
                        "previous_version": current_version,
                        "attempted_version": latest,
                    },
                ) from activation_error
            if new_runtime is not None:
                shutil.rmtree(new_runtime, ignore_errors=True)
            message = (
                "Update-Aktivierung fehlgeschlagen; vorige Version wurde "
                f"wiederhergestellt: {activation_error}"
            )
            raise UpdateError(
                message,
                result={
                    "ok": False,
                    "status": "update_failed",
                    "error": message,
                    "rollback": True,
                    "current_version": current_version,
                    "attempted_version": latest,
                },
            ) from activation_error

    removed = cleanup_versions(paths, {latest, current_version})
    return {
        **base_result,
        "status": "updated",
        "previous_version": current_version,
        "current_version": latest,
        "update_available": False,
        "rollback": rollback_ok,
        "removed_versions": removed,
    }


def run_update(
    *,
    app_root: str | Path | None = None,
    manifest_url: str | None = None,
    check: bool = False,
    dry_run: bool = False,
    downloader: Callable[[str], bytes] = fetch_bytes,
    runtime_installer: Callable[..., Path] = install_runtime,
    service_installer: Callable[..., dict[str, object]] = service.install_user_service,
    service_controller: Callable[..., dict[str, object]] = service.service_action,
    health_waiter: Callable[..., dict[str, Any]] = service.wait_for_health,
) -> dict[str, object]:
    """Check or atomically activate the latest verified public release."""
    paths = service.managed_paths(app_root)
    state = service.read_state(paths)
    current = service.read_current(paths)
    current_version = str(current["version"])
    location = manifest_url or str(
        state.get("manifest_url") or DEFAULT_MANIFEST_URL
    )
    manifest = load_manifest(location, downloader=downloader)
    latest = str(manifest["version"])
    available = service.version_key(latest) > service.version_key(current_version)
    base_result: dict[str, object] = {
        "ok": True,
        "status": "update_available" if available else "current",
        "current_version": current_version,
        "latest_version": latest,
        "update_available": available,
        "manifest_url": location,
    }
    if check or not available:
        return base_result
    if dry_run:
        return {**base_result, "status": "dry_run"}

    with update_lock(paths):
        # Another process may have completed the same release before this
        # caller acquired the lock.
        locked_current = service.read_current(paths)
        locked_version = str(locked_current["version"])
        if service.version_key(latest) <= service.version_key(locked_version):
            return {
                **base_result,
                "status": "current",
                "current_version": locked_version,
                "update_available": False,
            }
        locked_result = {
            **base_result,
            "current_version": locked_version,
        }
        return _perform_update_locked(
            paths,
            state,
            locked_version,
            manifest,
            locked_result,
            downloader=downloader,
            runtime_installer=runtime_installer,
            service_installer=service_installer,
            service_controller=service_controller,
            health_waiter=health_waiter,
        )
