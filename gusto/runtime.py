"""Prepare and verify versioned runtimes before user-service activation.

Installation metadata, interpreter checks and package readiness live here;
service registration and activation policy belong to service and update.
The module stays standard-library only until web preflight is requested.
"""
from __future__ import annotations

import importlib
import json
import os
import re
import shutil
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any


CURRENT_FILENAME = "current.json"
STATE_FILENAME = "install-state.json"
STATE_SCHEMA = 2


class RuntimePreparationError(RuntimeError):
    """Installation metadata, an interpreter or a prepared runtime is invalid."""


@dataclass(frozen=True)
class ManagedPaths:
    app_root: Path
    versions: Path
    python_runtimes: Path
    wrappers: Path
    current: Path
    state: Path

    def version(self, version: str) -> Path:
        validate_version(version)
        candidate = (self.versions / version).resolve()
        if not candidate.is_relative_to(self.versions):
            raise RuntimePreparationError("Ungültiger Versionspfad.")
        return candidate


def default_app_root(
    platform_name: str | None = None,
    environ: dict[str, str] | None = None,
    home: str | Path | None = None,
) -> Path:
    """Return Gusto's normal, unprivileged application root."""
    platform_name = platform_name or sys.platform
    environ = os.environ if environ is None else environ
    user_home = Path.home() if home is None else Path(home)
    if platform_name.startswith("win"):
        base = Path(environ.get("LOCALAPPDATA") or
                    user_home / "AppData" / "Local")
        return (base / "Programs" / "Gusto").expanduser().resolve()
    if platform_name == "darwin":
        return (user_home / "Library" / "Application Support" /
                "Gusto" / "app").resolve()
    return (user_home / ".local" / "opt" / "gusto").resolve()


def managed_paths(app_root: str | Path | None = None) -> ManagedPaths:
    """Use an explicit root, the executing managed runtime, or the user default."""
    if app_root is None:
        runtime = Path(sys.prefix).resolve()
        candidate = runtime.parent.parent
        if (runtime.parent.name == "versions"
                and (candidate / STATE_FILENAME).is_file()
                and (candidate / CURRENT_FILENAME).is_file()):
            app_root = candidate
        else:
            app_root = default_app_root()
    root = Path(app_root).expanduser().resolve()
    home = Path.home().resolve()
    if root == Path(root.anchor).resolve() or root == home:
        raise RuntimePreparationError("Der App-Pfad ist zu breit für eine verwaltete Installation.")
    return ManagedPaths(
        app_root=root,
        versions=(root / "versions").resolve(),
        python_runtimes=(root / "python").resolve(),
        wrappers=(root / "bin").resolve(),
        current=(root / CURRENT_FILENAME).resolve(),
        state=(root / STATE_FILENAME).resolve(),
    )


def validate_version(version: str) -> str:
    """Accept a conservative SemVer value suitable as one directory name."""
    if not re.fullmatch(
        r"(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)"
        r"(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?"
        r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?",
        version,
    ):
        raise RuntimePreparationError(f"Ungültige Release-Version: {version!r}")
    return version


def version_key(version: str) -> tuple[tuple[int, int, int], tuple[tuple[int, Any], ...]]:
    """Return a SemVer comparison key (build metadata is ignored)."""
    validate_version(version)
    core = version.split("+", 1)[0]
    numeric, separator, prerelease = core.partition("-")
    major, minor, patch = (int(value) for value in numeric.split("."))
    if not separator:
        pre_key: tuple[tuple[int, Any], ...] = ((2, ""),)
    else:
        fields: list[tuple[int, Any]] = []
        for value in prerelease.split("."):
            fields.append((0, int(value)) if value.isdigit() else (1, value))
        pre_key = tuple(fields)
    return (major, minor, patch), pre_key


def _atomic_json(path: Path, value: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f".{os.getpid()}.tmp")
    try:
        temporary.write_text(
            json.dumps(value, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, path)
    finally:
        # A failed replace is exactly the case where the target is temporarily
        # locked, so the half-written sibling must not survive the attempt.
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def _read_json_object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise RuntimePreparationError(f"{label} fehlt: {path}") from error
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RuntimePreparationError(f"{label} ist nicht lesbar: {error}") from error
    if not isinstance(value, dict):
        raise RuntimePreparationError(f"{label} muss ein JSON-Objekt sein.")
    return value


def read_state(paths: ManagedPaths) -> dict[str, Any]:
    state = _read_json_object(paths.state, "Installationszustand")
    if state.get("schema_version") != STATE_SCHEMA:
        raise RuntimePreparationError("Unbekannte Version des Installationszustands.")
    for field in ("data_dir", "host", "port"):
        if field not in state:
            raise RuntimePreparationError(f"Installationszustand enthält kein {field!r}.")
    host = state.get("host")
    if not isinstance(host, str) or not host or "/" in host or " " in host:
        raise RuntimePreparationError("Installationszustand enthält keinen gültigen Host.")
    port = state.get("port")
    if (isinstance(port, bool) or not isinstance(port, int)
            or not 1 <= port <= 65535):
        raise RuntimePreparationError("Installationszustand enthält keinen gültigen Port.")
    python_runtime = state.get("python_runtime")
    if (not isinstance(python_runtime, dict)
            or python_runtime.get("kind") not in {"managed", "system"}
            or not isinstance(python_runtime.get("version"), str)
            or not isinstance(python_runtime.get("path"), str)):
        raise RuntimePreparationError(
            "Installationszustand enthält keine gültige Python-Runtime."
        )
    try:
        validate_python_version(str(python_runtime["version"]))
        if python_runtime["kind"] == "managed":
            _python_runtime_marker(
                str(python_runtime["version"]),
                str(python_runtime.get("sha256") or ""),
            )
    except RuntimePreparationError as error:
        raise RuntimePreparationError(
            "Installationszustand enthält keine gültige Python-Runtime."
        ) from error
    return state


def write_state(paths: ManagedPaths, state: dict[str, object]) -> None:
    value = dict(state)
    value["schema_version"] = STATE_SCHEMA
    _atomic_json(paths.state, value)


def read_current(paths: ManagedPaths, *, require_runtime: bool = True) -> dict[str, Any]:
    current = _read_json_object(paths.current, "Aktiver Versionszeiger")
    version = current.get("version")
    if not isinstance(version, str):
        raise RuntimePreparationError("Aktiver Versionszeiger enthält keine Version.")
    runtime = paths.version(validate_version(version))
    stored_path = current.get("runtime")
    if stored_path and Path(str(stored_path)).resolve() != runtime:
        raise RuntimePreparationError("Aktiver Versionszeiger verweist aus dem App-Root.")
    if require_runtime and not runtime.is_dir():
        raise RuntimePreparationError(f"Aktive Runtime fehlt: {runtime}")
    return {**current, "version": version, "runtime": os.fspath(runtime)}


def write_current(
    paths: ManagedPaths,
    version: str,
    *,
    previous_version: str | None = None,
) -> None:
    runtime = paths.version(version)
    value: dict[str, object] = {
        "schema_version": STATE_SCHEMA,
        "version": version,
        "runtime": os.fspath(runtime),
    }
    if previous_version:
        validate_version(previous_version)
        value["previous_version"] = previous_version
    _atomic_json(paths.current, value)


def runtime_python(runtime: Path, platform_name: str | None = None) -> Path:
    platform_name = platform_name or sys.platform
    return (runtime / "Scripts" / "python.exe" if platform_name.startswith("win")
            else runtime / "bin" / "python")


def validate_python_version(version: str) -> str:
    if not isinstance(version, str) or not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise RuntimePreparationError(f"Ungültige Python-Version: {version!r}")
    return version


def managed_python_root(paths: ManagedPaths, version: str) -> Path:
    validate_python_version(version)
    candidate = (paths.python_runtimes / version).resolve()
    if not candidate.is_relative_to(paths.python_runtimes):
        raise RuntimePreparationError("Ungültiger Pfad der verwalteten Python-Runtime.")
    return candidate


def base_python(root: Path, platform_name: str | None = None) -> Path:
    platform_name = platform_name or sys.platform
    return (root / "python.exe" if platform_name.startswith("win")
            else root / "bin" / "python")


def _python_runtime_marker(version: str, sha256: str) -> dict[str, object]:
    if (not isinstance(sha256, str) or len(sha256) != 64
            or any(value not in "0123456789abcdef" for value in sha256.lower())):
        raise RuntimePreparationError("Ungültige SHA-256 der Python-Runtime.")
    return {
        "schema_version": 1,
        "kind": "managed",
        "version": validate_python_version(version),
        "sha256": sha256.lower(),
        "architecture": "x86_64",
    }


def managed_python_usable(root: Path, version: str, sha256: str) -> bool:
    try:
        marker = _read_json_object(
            root / ".gusto-python-runtime.json", "Python-Runtime-Markierung",
        )
    except RuntimePreparationError:
        return False
    if marker != _python_runtime_marker(version, sha256):
        return False
    python = base_python(root, "win32")
    if not python.is_file():
        return False
    try:
        result = subprocess.run(
            [
                os.fspath(python), "-I", "-c",
                "import struct,sys; print('.'.join(map(str,sys.version_info[:3]))); "
                "print(struct.calcsize('P') * 8)",
            ],
            text=True, encoding="utf-8", errors="replace", capture_output=True,
        )
    except OSError:
        return False
    return result.returncode == 0 and result.stdout.splitlines() == [version, "64"]


def install_managed_python(
    paths: ManagedPaths,
    source: Path,
    version: str,
    sha256: str,
    *,
    replace: bool = False,
) -> Path:
    """Stage and publish one app-owned, registry-free Windows Python."""
    source = source.expanduser().resolve()
    source_python = base_python(source, "win32")
    if not source_python.is_file():
        raise RuntimePreparationError(f"Python-Runtime-Quelle ist unvollständig: {source}")
    for item in source.rglob("*"):
        if item.is_symlink():
            raise RuntimePreparationError(
                f"Python-Runtime enthält einen symbolischen Link: {item}"
            )
    probe = subprocess.run(
        [
            os.fspath(source_python), "-I", "-c",
            "import struct,sys; print('.'.join(map(str,sys.version_info[:3]))); "
            "print(struct.calcsize('P') * 8)",
        ],
        text=True, encoding="utf-8", errors="replace", capture_output=True,
    )
    if probe.returncode or probe.stdout.splitlines() != [version, "64"]:
        raise RuntimePreparationError(
            "Python-Runtime-Quelle meldet nicht die erwartete Version/Architektur."
        )

    target = managed_python_root(paths, version)
    if target.exists() and managed_python_usable(target, version, sha256):
        return base_python(target, "win32")
    if target.exists() and not replace:
        raise RuntimePreparationError(
            f"Vorhandene Python-Runtime ist nicht verifiziert: {target}."
        )
    paths.python_runtimes.mkdir(parents=True, exist_ok=True)
    staging = paths.python_runtimes / f".staging-{version}-{os.getpid()}"
    if staging.exists():
        shutil.rmtree(staging)
    try:
        shutil.copytree(source, staging)
        _atomic_json(
            staging / ".gusto-python-runtime.json",
            _python_runtime_marker(version, sha256),
        )
        if not managed_python_usable(staging, version, sha256):
            raise RuntimePreparationError("Kopierte Python-Runtime hat die Prüfung nicht bestanden.")
        if target.exists():
            shutil.rmtree(target)
        os.replace(staging, target)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return base_python(target, "win32")


def managed_python_state(
    paths: ManagedPaths,
    version: str,
    sha256: str,
) -> dict[str, str]:
    root = managed_python_root(paths, version)
    marker = _python_runtime_marker(version, sha256)
    return {
        "kind": "managed",
        "version": str(marker["version"]),
        "path": os.fspath(root),
        "sha256": str(marker["sha256"]),
    }


def system_python_state(executable: str | Path | None = None) -> dict[str, str]:
    executable = Path(
        executable or getattr(sys, "_base_executable", sys.executable)
    ).expanduser().resolve()
    return {
        "kind": "system",
        "version": ".".join(str(value) for value in sys.version_info[:3]),
        "path": os.fspath(executable),
    }


def state_base_python(paths: ManagedPaths, state: dict[str, Any]) -> Path:
    runtime = state.get("python_runtime")
    if not isinstance(runtime, dict):
        raise RuntimePreparationError("Installationszustand enthält keine Python-Runtime.")
    kind = runtime.get("kind")
    version = runtime.get("version")
    path = runtime.get("path")
    if not isinstance(version, str) or not isinstance(path, str):
        raise RuntimePreparationError("Installationszustand enthält eine ungültige Python-Runtime.")
    if kind == "managed":
        sha256 = runtime.get("sha256")
        expected = managed_python_root(paths, version)
        if Path(path).expanduser().resolve() != expected:
            raise RuntimePreparationError("Python-Runtime verweist aus dem App-Root.")
        if not isinstance(sha256, str) or not managed_python_usable(
            expected, version, sha256,
        ):
            raise RuntimePreparationError("Verwaltete Python-Runtime ist nicht verwendbar.")
        return base_python(expected, "win32")
    if kind == "system":
        executable = Path(path).expanduser().resolve()
        if executable.is_relative_to(paths.app_root) or not executable.is_file():
            raise RuntimePreparationError("System-Python der Installation ist nicht verwendbar.")
        return executable
    raise RuntimePreparationError(f"Unbekannte Python-Runtime-Art: {kind!r}")


def create_virtual_environment(base: Path, target: Path) -> None:
    result = subprocess.run(
        [os.fspath(base), "-I", "-m", "venv", os.fspath(target)],
        text=True, encoding="utf-8", errors="replace", capture_output=True,
    )
    if result.returncode:
        detail = (result.stderr or result.stdout or "").strip()
        raise RuntimePreparationError(
            "Virtuelle Umgebung konnte nicht erstellt werden"
            + (f": {detail}" if detail else ".")
        )


def runtime_command(runtime: Path, platform_name: str | None = None) -> Path:
    platform_name = platform_name or sys.platform
    return (runtime / "Scripts" / "gusto.exe" if platform_name.startswith("win")
            else runtime / "bin" / "gusto")


def runtime_autostart(runtime: Path) -> Path:
    return runtime / "Scripts" / "gusto-autostart.exe"


def write_settings(
    runtime: Path,
    data_root: Path,
    server_url: str | None = None,
) -> Path:
    settings_path = runtime / "gusto.settings.json"
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = settings_path.with_suffix(".json.tmp")
    value = {"data_dir": os.fspath(data_root.resolve())}
    if server_url is not None:
        value["server_url"] = server_url
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, settings_path)
    return settings_path


def verified_runtime(runtime: Path, version: str, python_version: str) -> bool:
    """Check recorded preparation and interpreter presence without executing it."""
    try:
        marker = json.loads(
            (runtime / ".gusto-runtime.json").read_text(encoding="utf-8")
        )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return False
    return (
        marker == {
            "version": version,
            "verified": True,
            "python_runtime": python_version,
        }
        and runtime_python(runtime).is_file()
    )


def runtime_is_usable(
    runtime: Path,
    version: str,
    python_version: str,
) -> bool:
    """Installer repair requires a live version and complete web-dependency check."""
    if not verified_runtime(runtime, version, python_version):
        return False
    smoke = _probe_package(
        runtime, version, web_dependencies=True, capture_output=True,
    )
    return smoke.returncode == 0


def _probe_package(
    runtime: Path,
    version: str,
    *,
    web_dependencies: bool,
    capture_output: bool,
    runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
) -> subprocess.CompletedProcess[str]:
    program = "import gusto,sys; "
    if web_dependencies:
        program += (
            "from gusto.runtime import missing_web_dependencies; "
            "raise SystemExit(gusto.__version__ != sys.argv[1] or "
            "bool(missing_web_dependencies()))"
        )
    else:
        program += (
            "print(gusto.__version__); "
            "raise SystemExit(gusto.__version__ != sys.argv[1])"
        )
    options: dict[str, object] = {
        "text": True, "encoding": "utf-8", "errors": "replace",
    }
    if capture_output:
        options["capture_output"] = True
    return (runner or subprocess.run)(
        [os.fspath(runtime_python(runtime)), "-I", "-c", program, version],
        **options,
    )


def _install_package(
    runtime: Path,
    source: Path,
    *,
    capture_output: bool,
    runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
) -> subprocess.CompletedProcess[str]:
    project = (
        f"gusto[web] @ {source.resolve().as_uri()}"
        if source.suffix == ".whl" else os.fspath(source) + "[web]"
    )
    options: dict[str, object] = {
        "text": True, "encoding": "utf-8", "errors": "replace",
    }
    if capture_output:
        options["capture_output"] = True
    return (runner or subprocess.run)(
        [os.fspath(runtime_python(runtime)), "-m", "pip", "install", project],
        **options,
    )


def prepare_source_runtime(
    source: Path,
    runtime: Path,
    version: str,
    data_root: Path,
    server_url: str,
    *,
    base_python: Path,
    python_version: str,
    json_output: bool = False,
) -> None:
    """Install/repair a payload with the installer's full web-readiness policy."""
    # A present interpreter alone does not make a usable venv: a partially
    # removed runtime can still contain Scripts/python.exe. Rebuild whenever
    # the venv marker is missing instead of installing into a broken venv.
    if not (runtime_python(runtime).is_file()
            and (runtime / "pyvenv.cfg").is_file()):
        create_virtual_environment(base_python, runtime)
    install = _install_package(runtime, source, capture_output=json_output)
    if install.returncode:
        detail = (install.stderr or install.stdout or "").strip()
        raise RuntimePreparationError(
            "Wheel-Installation fehlgeschlagen"
            + (f": {detail}" if detail else "")
        )
    write_settings(runtime, data_root, server_url)
    smoke = _probe_package(
        runtime, version, web_dependencies=True, capture_output=False,
    )
    if smoke.returncode:
        raise RuntimePreparationError("Import-/Web-Runtime-Prüfung fehlgeschlagen")
    _write_verified_marker(runtime, version, python_version)


def _write_verified_marker(runtime: Path, version: str, python_version: str) -> None:
    """Publish readiness only after the caller's target-interpreter probe passed."""
    (runtime / ".gusto-runtime.json").write_text(
        json.dumps({
            "version": version,
            "verified": True,
            "python_runtime": python_version,
        }, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _recover_staging(paths: ManagedPaths, version: str) -> list[Path]:
    """Remove abandoned staging directories left by interrupted updates."""
    validate_version(version)
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


def prepare_release_runtime(
    paths: ManagedPaths,
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
    """Prepare an inactive release using update's marker and version-probe policy.

    An exact verified marker permits reuse. Fresh builds check isolated package
    import/version; activation separately proves service readiness through health.
    Install/repair deliberately uses the stronger live web-dependency probe.
    """
    runtime = paths.version(version)
    paths.versions.mkdir(parents=True, exist_ok=True)
    _recover_staging(paths, version)
    if runtime.exists():
        if verified_runtime(runtime, version, python_version):
            return runtime
        shutil.rmtree(runtime)
    if environment_builder is None:
        def environment_builder(target):
            return create_virtual_environment(base_python, target)
    # Virtual environments are not relocatable: pip's console/GUI launchers
    # embed the absolute interpreter path. Build at the final version path,
    # which remains inactive until the verified current pointer is switched.
    # The per-app update lock protects this preparation, and an interrupted
    # build has no verified marker so the next attempt safely rebuilds it.
    try:
        environment_builder(runtime)
        result = _install_package(runtime, wheel, capture_output=True, runner=runner)
        if result.returncode:
            raise RuntimePreparationError(
                "Installation des Release-Wheels fehlgeschlagen: "
                + (result.stderr or result.stdout or f"Exit-Code {result.returncode}").strip()
            )
        write_settings(
            runtime, data_dir, server_url_value,
        )
        smoke = _probe_package(
            runtime, version, web_dependencies=False, capture_output=True,
            runner=runner,
        )
        if smoke.returncode:
            raise RuntimePreparationError(
                "Import-/Versionsprüfung der neuen Runtime fehlgeschlagen: "
                + (smoke.stderr or smoke.stdout or
                   f"Exit-Code {smoke.returncode}").strip()
            )
        _write_verified_marker(runtime, version, python_version)
    except Exception:
        shutil.rmtree(runtime, ignore_errors=True)
        raise
    return runtime


_WEB_DEPENDENCIES = (
    ("fastapi", ("fastapi",)),
    ("uvicorn[standard]", ("uvicorn",)),
    ("jinja2", ("jinja2",)),
    ("markdown", ("markdown",)),
    ("python-multipart", ("python_multipart", "multipart.multipart")),
    ("pillow", ("PIL.Image",)),
)


def missing_web_dependencies() -> list[str]:
    """Return all missing requirements, accepting either multipart import name."""
    missing = []
    for requirement, module_names in _WEB_DEPENDENCIES:
        for module_name in module_names:
            try:
                importlib.import_module(module_name)
                break
            except ImportError:
                continue
        else:
            missing.append(requirement)
    return missing


def _web_dependency_error(missing: list[str]) -> str:
    return (
        "Web-Abhängigkeiten fehlen oder sind nicht importierbar: "
        f"{', '.join(missing)}. "
        'Projekt-Checkout: python -m pip install -e ".[web]". '
        "Installierte App: den Installer derselben Gusto-Version im expliziten "
        "Reparaturmodus verwenden (--repair bzw. -Repair beim Windows-Bootstrap)."
    )


def load_web_runtime():
    """Load the foreground server only after its complete dependency preflight."""
    missing = missing_web_dependencies()
    if missing:
        sys.exit(_web_dependency_error(missing))
    importlib.import_module("gusto.web")
    return importlib.import_module("uvicorn")



def cleanup_versions(
    paths: ManagedPaths,
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
            validate_version(candidate.name)
        except RuntimePreparationError:
            continue
        try:
            marker = json.loads(
                (candidate / ".gusto-runtime.json").read_text(encoding="utf-8")
            )
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            continue
        if (not isinstance(marker, dict)
                or marker.get("version") != candidate.name
                or marker.get("verified") is not True
                or not isinstance(marker.get("python_runtime"), str)):
            continue
        shutil.rmtree(candidate)
        removed.append(candidate.name)
    return sorted(removed, key=version_key)


def cleanup_python_runtimes(paths: ManagedPaths) -> list[str]:
    """Remove managed Python versions not referenced by a retained Gusto venv."""
    referenced: set[str] = set()
    if paths.versions.is_dir():
        for runtime in paths.versions.iterdir():
            if not runtime.is_dir():
                continue
            try:
                validate_version(runtime.name)
                version_directory = True
            except RuntimePreparationError:
                version_directory = False
            try:
                marker = json.loads(
                    (runtime / ".gusto-runtime.json").read_text(encoding="utf-8")
                )
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                if version_directory:
                    return []
                continue
            python_version = marker.get("python_runtime") if isinstance(
                marker, dict,
            ) else None
            if version_directory and (
                not isinstance(marker, dict)
                or marker.get("version") != runtime.name
                or marker.get("verified") is not True
                or not isinstance(python_version, str)
            ):
                # A retained runtime with an unreadable reference may still
                # need any managed interpreter. Leave them all until repaired.
                return []
            if version_directory:
                try:
                    validate_python_version(python_version)
                except RuntimePreparationError:
                    return []
            if isinstance(python_version, str):
                referenced.add(python_version)
    removed: list[str] = []
    if not paths.python_runtimes.is_dir():
        return removed
    for candidate in paths.python_runtimes.iterdir():
        if not candidate.is_dir() or candidate.name in referenced:
            continue
        try:
            validate_python_version(candidate.name)
        except RuntimePreparationError:
            continue
        shutil.rmtree(candidate)
        removed.append(candidate.name)
    return sorted(removed, key=version_key)
