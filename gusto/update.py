"""Verified side-by-side release updates for managed Gusto installations."""
from __future__ import annotations

import hashlib
import json
import os
import random
import shutil
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from collections.abc import Callable
from contextlib import contextmanager
from pathlib import Path
from typing import IO, Any

from . import service


DEFAULT_MANIFEST_URL = (
    "https://github.com/Vironnimo/gusto/releases/latest/download/"
    "gusto-release.json"
)

MAX_FETCH_RETRIES = 3
RETRY_BASE_DELAY_SECONDS = 0.5
RETRY_MAX_DELAY_SECONDS = 4.0
# 500 counts only because release downloads are idempotent GET requests.
RETRYABLE_HTTP_CODES = frozenset({429, 500, 502, 503, 504})


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


def _transient_download_error(error: Exception) -> bool:
    """Report whether a failed release download deserves another attempt."""
    if isinstance(error, urllib.error.HTTPError):
        return error.code in RETRYABLE_HTTP_CODES
    return isinstance(error, (urllib.error.URLError, TimeoutError, OSError))


def _retry_delay(attempt: int) -> float:
    """Return the capped exponential backoff with jitter for one retry."""
    backoff = min(
        RETRY_BASE_DELAY_SECONDS * 2 ** attempt, RETRY_MAX_DELAY_SECONDS,
    )
    return random.uniform(backoff / 2, backoff)


def fetch_bytes(
    location: str,
    *,
    timeout: float = 30.0,
    opener: Callable[..., Any] = urllib.request.urlopen,
) -> bytes:
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
    attempt = 0
    while True:
        try:
            with opener(request, timeout=timeout) as response:
                return response.read()
        except Exception as error:
            if (attempt >= MAX_FETCH_RETRIES
                    or not _transient_download_error(error)):
                raise UpdateError(
                    f"Release-Download fehlgeschlagen ({location}): {error}"
                ) from error
            time.sleep(_retry_delay(attempt))
            attempt += 1


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
    if not isinstance(value, dict) or value.get("schema_version") != 2:
        raise UpdateError("Release-Manifest hat ein unbekanntes Format.")
    version = value.get("version")
    if not isinstance(version, str):
        raise UpdateError("Release-Manifest enthält keine Version.")
    try:
        service.validate_version(version)
    except service.ServiceError as error:
        raise UpdateError(str(error)) from error
    assets = value.get("assets")
    if not isinstance(assets, dict):
        raise UpdateError("Release-Manifest enthält keine Assets.")
    resolved: dict[str, dict[str, str]] = {}
    for key in ("wheel", "installer", "windows_python_x64"):
        asset = assets.get(key)
        if not isinstance(asset, dict):
            raise UpdateError(f"Release-Manifest enthält Asset {key!r} nicht.")
        name = asset.get("name")
        checksum = asset.get("sha256")
        if (not isinstance(name, str) or not name or Path(name).name != name
                or not isinstance(checksum, str) or len(checksum) != 64
                or any(character not in "0123456789abcdefABCDEF"
                       for character in checksum)):
            raise UpdateError(f"Release-Asset {key!r} ist ungültig.")
        resolved[key] = {
            **asset,
            "name": name,
            "url": resolve_asset(location, name),
            "sha256": checksum.lower(),
        }
    python_asset = resolved["windows_python_x64"]
    python_version = python_asset.get("version")
    if python_asset.get("layout") != "tools" or not isinstance(
        python_version, str,
    ):
        raise UpdateError("Windows-Python-Asset ist unvollständig.")
    try:
        service.validate_python_version(python_version)
    except service.ServiceError as error:
        raise UpdateError(str(error)) from error
    return {
        **value,
        "version": version,
        "assets": resolved,
    }


def verify_asset(data: bytes, expected_sha256: str, label: str = "Asset") -> str:
    actual = hashlib.sha256(data).hexdigest()
    if actual != expected_sha256.lower():
        raise UpdateError(
            f"SHA-256-Prüfung für {label} fehlgeschlagen "
            f"(erwartet {expected_sha256}, "
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
            if (any(part in {"", ".", ".."} or ":" in part for part in parts)):
                raise UpdateError(f"Unsicherer ZIP-Eintrag: {info.filename!r}")
            mode = (info.external_attr >> 16) & 0o170000
            if mode == 0o120000:
                raise UpdateError(f"Symlink im Python-Paket ist nicht erlaubt: {name}")
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


def extract_python_runtime(package: Path, destination: Path) -> None:
    """Extract only the verified NuGet package's tools/ runtime subtree."""
    extracted = destination / "package"
    safe_extract(package, extracted)
    tools = extracted / "tools"
    if not service.base_python(tools, "win32").is_file():
        raise UpdateError("Python-Runtime-Paket enthält tools/python.exe nicht.")
    shutil.move(os.fspath(tools), os.fspath(destination / "runtime"))


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


def verified_runtime(runtime: Path, version: str, python_version: str) -> bool:
    try:
        marker = json.loads(
            (runtime / ".gusto-runtime.json").read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError):
        return False
    return (
        marker == {
            "version": version,
            "verified": True,
            "python_runtime": python_version,
        }
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


LOCK_INIT_WAIT_SECONDS = 2.0
# A crash between the exclusive create and the first written byte leaves an
# empty lock file. Only a file that stayed empty for this safe period counts as
# abandoned; a live initializer stays empty for microseconds.
LOCK_INIT_STALE_SECONDS = 10.0


def _open_lock_handle(lock_path: Path) -> IO[bytes]:
    """Open the lock file, reclaiming an abandoned initialization once."""
    deadline = time.monotonic() + LOCK_INIT_WAIT_SECONDS
    while True:
        try:
            descriptor = os.open(
                lock_path, os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o600,
            )
        except FileExistsError:
            try:
                info = lock_path.stat()
            except FileNotFoundError:
                # Another waiter reclaimed the stale file first.
                continue
            if info.st_size >= 1:
                return lock_path.open("r+b", buffering=0)
            if time.time() - info.st_mtime > LOCK_INIT_STALE_SECONDS:
                # No process can hold a lock on an empty file, and no live
                # initializer leaves one empty that long, so this is the crash
                # leftover that would otherwise block every later update.
                lock_path.unlink(missing_ok=True)
                continue
            if time.monotonic() >= deadline:
                raise UpdateError(
                    "Die Gusto-Update-Sperre ist nicht initialisiert."
                )
            time.sleep(0.01)
        else:
            try:
                os.write(descriptor, b"\0")
            except Exception:
                os.close(descriptor)
                raise
            return os.fdopen(descriptor, "r+b", buffering=0)


@contextmanager
def update_lock(paths: service.ManagedPaths):
    """Hold one crash-safe per-app update lock across staging and activation."""
    lock_path = paths.app_root / ".update.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = _open_lock_handle(lock_path)
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
    base_python: Path,
    python_version: str,
    server_url_value: str | None = None,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    environment_builder: Callable[[Path], None] | None = None,
) -> Path:
    """Build in staging, verify, then atomically publish one runtime."""
    runtime = paths.version(version)
    paths.versions.mkdir(parents=True, exist_ok=True)
    recover_staging(paths, version)
    if runtime.exists():
        if verified_runtime(runtime, version, python_version):
            return runtime
        shutil.rmtree(runtime)
    if environment_builder is None:
        environment_builder = lambda target: service.create_virtual_environment(
            base_python, target,
        )
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
            json.dumps({
                "version": version,
                "verified": True,
                "python_runtime": python_version,
            },
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
        if (not isinstance(marker, dict)
                or marker.get("version") != candidate.name
                or marker.get("verified") is not True
                or not isinstance(marker.get("python_runtime"), str)):
            continue
        shutil.rmtree(candidate)
        removed.append(candidate.name)
    return sorted(removed, key=service.version_key)


def cleanup_python_runtimes(paths: service.ManagedPaths) -> list[str]:
    """Remove managed Python versions not referenced by a retained Gusto venv."""
    referenced: set[str] = set()
    if paths.versions.is_dir():
        for runtime in paths.versions.iterdir():
            if not runtime.is_dir():
                continue
            try:
                marker = json.loads(
                    (runtime / ".gusto-runtime.json").read_text(encoding="utf-8")
                )
            except (OSError, json.JSONDecodeError):
                continue
            python_version = marker.get("python_runtime") if isinstance(
                marker, dict,
            ) else None
            if isinstance(python_version, str):
                referenced.add(python_version)
    removed: list[str] = []
    if not paths.python_runtimes.is_dir():
        return removed
    for candidate in paths.python_runtimes.iterdir():
        if not candidate.is_dir() or candidate.name in referenced:
            continue
        try:
            service.validate_python_version(candidate.name)
        except service.ServiceError:
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
    assets = manifest["assets"]
    if not isinstance(assets, dict):
        raise UpdateError("Release-Manifest enthält keine Assets.")
    wheel_asset = assets["wheel"]
    if not isinstance(wheel_asset, dict):
        raise UpdateError("Release-Manifest enthält kein Wheel.")
    wheel_bytes = downloader(str(wheel_asset["url"]))
    verify_asset(wheel_bytes, str(wheel_asset["sha256"]), "Gusto-Wheel")
    data_dir = Path(str(state["data_dir"])).expanduser().resolve()
    new_runtime: Path | None = None
    new_python_root: Path | None = None
    switched = False
    rollback_ok = False
    original_state = json.loads(json.dumps(state))
    updated_state = json.loads(json.dumps(state))
    with tempfile.TemporaryDirectory(prefix="gusto-update-") as temporary:
        temp = Path(temporary)
        wheel = temp / str(wheel_asset["name"])
        wheel.write_bytes(wheel_bytes)
        python_state = state["python_runtime"]
        if not isinstance(python_state, dict):
            raise UpdateError("Installationszustand enthält keine Python-Runtime.")
        python_version = str(python_state["version"])
        if python_state.get("kind") == "managed":
            python_asset = assets["windows_python_x64"]
            if not isinstance(python_asset, dict):
                raise UpdateError("Release enthält keine Windows-Python-Runtime.")
            release_python_version = str(python_asset["version"])
            release_python_sha256 = str(python_asset["sha256"])
            installed_sha256 = str(python_state.get("sha256") or "")
            if (release_python_version == python_version
                    and release_python_sha256 != installed_sha256):
                raise UpdateError(
                    "Das Release ersetzt dieselbe Python-Version mit anderem "
                    "Inhalt; dafür ist eine neue Python-Version erforderlich."
                )
            if (release_python_version != python_version
                    or release_python_sha256 != installed_sha256):
                package_bytes = downloader(str(python_asset["url"]))
                verify_asset(
                    package_bytes, release_python_sha256, "Python-Runtime",
                )
                package = temp / str(python_asset["name"])
                package.write_bytes(package_bytes)
                extracted = temp / "python"
                extracted.mkdir()
                extract_python_runtime(package, extracted)
                try:
                    base_python = service.install_managed_python(
                        paths,
                        extracted / "runtime",
                        release_python_version,
                        release_python_sha256,
                    )
                except service.ServiceError as error:
                    # Still pre-activation, so a plain conversion without
                    # rollback keeps the CLI JSON contract intact.
                    raise UpdateError(str(error)) from error
                new_python_root = service.managed_python_root(
                    paths, release_python_version,
                )
                updated_state["python_runtime"] = service.managed_python_state(
                    paths, release_python_version, release_python_sha256,
                )
                python_version = release_python_version
            else:
                try:
                    base_python = service.state_base_python(paths, state)
                except service.ServiceError as error:
                    raise UpdateError(
                        "Die installierte Python-Runtime ist beschädigt; bitte "
                        "den öffentlichen Installer im Reparaturmodus starten."
                    ) from error
        else:
            try:
                base_python = service.state_base_python(paths, state)
            except service.ServiceError as error:
                raise UpdateError(str(error)) from error

        try:
            new_runtime = runtime_installer(
                paths,
                latest,
                wheel,
                data_dir,
                base_python=base_python,
                python_version=python_version,
                server_url_value=service.server_url(updated_state),
            )
        except service.ServiceError as error:
            # Nothing has been switched yet, so this stays a plain
            # conversion without rollback; the venv build and runtime checks
            # must not escape as a raw ServiceError under --json.
            if new_python_root is not None:
                cleanup_python_runtimes(paths)
            raise UpdateError(str(error)) from error
        except Exception:
            if new_python_root is not None:
                cleanup_python_runtimes(paths)
            raise

        try:
            service_controller("stop", app_root=paths.app_root)
            service.write_current(
                paths, latest, previous_version=current_version,
            )
            switched = True
            if updated_state != state:
                service.write_state(paths, updated_state)
            service_installer(paths, updated_state)
            health_waiter(
                service.health_url(updated_state),
                expected_version=latest,
                expected_data_path=data_dir,
            )
        except Exception as activation_error:
            try:
                # Stop the failed instance explicitly before restoring the
                # previous runtime. On Linux `enable --now` never restarts an
                # already active unit, so without this stop the rollback could
                # leave the failed version serving while the pointer claims the
                # restored one. The stop also matches the Windows activation
                # path, which always stops a running task before re-registering.
                service_controller("stop", app_root=paths.app_root)
                if switched:
                    service.write_current(paths, current_version)
                if updated_state != state:
                    service.write_state(paths, original_state)
                service_installer(paths, original_state)
                health_waiter(
                    service.health_url(original_state),
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
            if new_python_root is not None:
                cleanup_python_runtimes(paths)
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
    removed_python = cleanup_python_runtimes(paths)
    return {
        **base_result,
        "status": "updated",
        "previous_version": current_version,
        "current_version": latest,
        "update_available": False,
        "rollback": rollback_ok,
        "removed_versions": removed,
        "python_runtime": updated_state["python_runtime"],
        "removed_python_runtimes": removed_python,
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
    try:
        paths = service.managed_paths(app_root)
        state = service.read_state(paths)
        current = service.read_current(paths)
    except service.ServiceError as error:
        # The CLI maps only UpdateError and ValueError/OSError for the
        # --json contract; a raw ServiceError would escape as a traceback
        # instead of the structured {ok:false, error} object.
        raise UpdateError(str(error)) from error
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
        try:
            locked_current = service.read_current(paths)
        except service.ServiceError as error:
            raise UpdateError(str(error)) from error
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
