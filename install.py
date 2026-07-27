"""Install Gusto as a versioned, always-running current-user application."""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import venv
from pathlib import Path


ROOT = Path(__file__).resolve().parent
SETTINGS_FILENAME = "gusto.settings.json"
RECOGNIZABLE_DATA_FILES = (
    "recipes.json", "categories.json", "log.json",
    "shopping_list.json", "favorites.json",
)
DEFAULT_MANIFEST_URL = (
    "https://github.com/Vironnimo/gusto/releases/latest/download/"
    "gusto-release.json"
)


def default_install_dir(
    platform_name: str | None = None,
    environ: dict[str, str] | None = None,
    home: str | Path | None = None,
) -> Path:
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


def default_data_dir(
    platform_name: str | None = None,
    environ: dict[str, str] | None = None,
    home: str | Path | None = None,
) -> Path:
    platform_name = platform_name or sys.platform
    environ = os.environ if environ is None else environ
    user_home = Path.home() if home is None else Path(home)
    if platform_name.startswith("win"):
        base = Path(environ.get("LOCALAPPDATA") or
                    user_home / "AppData" / "Local")
        return (base / "Gusto").expanduser().resolve()
    if platform_name == "darwin":
        return (user_home / "Library" / "Application Support" / "Gusto").resolve()
    base = Path(environ.get("XDG_DATA_HOME") or user_home / ".local" / "share")
    return (base / "gusto").expanduser().resolve()


def venv_python(venv_dir: Path, platform_name: str | None = None) -> Path:
    platform_name = platform_name or sys.platform
    return (venv_dir / "Scripts" / "python.exe"
            if platform_name.startswith("win") else venv_dir / "bin" / "python")


def gusto_command(venv_dir: Path, platform_name: str | None = None) -> Path:
    platform_name = platform_name or sys.platform
    return (venv_dir / "Scripts" / "gusto.exe"
            if platform_name.startswith("win") else venv_dir / "bin" / "gusto")


def ensure_virtual_environment(
    venv_dir: Path,
    platform_name: str | None = None,
) -> bool:
    if venv_python(venv_dir, platform_name).is_file():
        return False
    venv.EnvBuilder(with_pip=True).create(venv_dir)
    return True


def append_path_entry(current: str, directory: Path) -> tuple[str, bool]:
    directory_text = os.fspath(directory.resolve())
    target = os.path.normcase(os.path.normpath(directory_text))
    entries = [entry for entry in current.split(os.pathsep) if entry]
    for entry in entries:
        candidate = os.path.expandvars(entry.strip().strip('"'))
        if os.path.normcase(os.path.normpath(candidate)) == target:
            return current, False
    separator = "" if not current or current.endswith(os.pathsep) else os.pathsep
    return current + separator + directory_text, True


def add_windows_user_path(
    directory: Path,
    platform_name: str | None = None,
) -> bool:
    platform_name = platform_name or sys.platform
    if not platform_name.startswith("win"):
        return False
    import ctypes
    import winreg
    with winreg.CreateKeyEx(
        winreg.HKEY_CURRENT_USER, "Environment",
        access=winreg.KEY_READ | winreg.KEY_SET_VALUE,
    ) as key:
        try:
            current, value_type = winreg.QueryValueEx(key, "Path")
        except FileNotFoundError:
            current, value_type = "", winreg.REG_EXPAND_SZ
        updated, changed = append_path_entry(current, directory)
        if not changed:
            return False
        winreg.SetValueEx(key, "Path", 0, value_type, updated)
    try:
        result = ctypes.c_ulong()
        ctypes.windll.user32.SendMessageTimeoutW(
            0xFFFF, 0x001A, 0, ctypes.c_wchar_p("Environment"),
            0x0002, 5000, ctypes.byref(result),
        )
    except (AttributeError, OSError, ctypes.ArgumentError):
        pass
    return True


def remove_windows_user_path(
    directory: Path,
    platform_name: str | None = None,
) -> bool:
    """Remove only the exact stable Gusto wrapper directory from user PATH."""
    platform_name = platform_name or sys.platform
    if not platform_name.startswith("win"):
        return False
    import winreg
    target = os.path.normcase(os.path.normpath(os.fspath(directory.resolve())))
    with winreg.CreateKeyEx(
        winreg.HKEY_CURRENT_USER, "Environment",
        access=winreg.KEY_READ | winreg.KEY_SET_VALUE,
    ) as key:
        try:
            current, value_type = winreg.QueryValueEx(key, "Path")
        except FileNotFoundError:
            return False
        kept = []
        removed = False
        for entry in current.split(os.pathsep):
            candidate = os.path.expandvars(entry.strip().strip('"'))
            if (candidate and
                    os.path.normcase(os.path.normpath(candidate)) == target):
                removed = True
            elif entry:
                kept.append(entry)
        if removed:
            winreg.SetValueEx(key, "Path", 0, value_type, os.pathsep.join(kept))
        return removed


def remove_windows_app_registration() -> None:
    import winreg
    try:
        winreg.DeleteKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Uninstall\Gusto",
        )
    except FileNotFoundError:
        pass
    programs = Path(os.environ.get("APPDATA") or
                    Path.home() / "AppData" / "Roaming")
    shortcut = (
        programs / "Microsoft" / "Windows" / "Start Menu" /
        "Programs" / "Gusto.url"
    )
    shortcut.unlink(missing_ok=True)


def contains_gusto_data(root: Path) -> bool:
    data = root / "data"
    if any((data / name).is_file() for name in RECOGNIZABLE_DATA_FILES):
        return True
    recipes = root / "recipes"
    return recipes.is_dir() and next(recipes.glob("*.md"), None) is not None


def migrate_checkout_data(source: Path, destination: Path) -> bool:
    if source.resolve() == destination.resolve():
        return False
    if not contains_gusto_data(source) or contains_gusto_data(destination):
        return False
    for name in ("recipes", "images", "archive", "data"):
        source_dir = source / name
        if source_dir.is_dir():
            shutil.copytree(source_dir, destination / name, dirs_exist_ok=True)
    return True


def write_instance_settings(
    install_dir: Path,
    data_root: Path,
    server_url: str | None = None,
) -> Path:
    settings_path = install_dir / SETTINGS_FILENAME
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


def validate_install_targets(
    app_root: Path,
    data_root: Path,
    *,
    managed: bool,
    legacy: bool,
) -> None:
    """Protect custom paths before Gusto claims or later removes them."""
    app_root = app_root.resolve()
    data_root = data_root.resolve()
    if (app_root == data_root or app_root.is_relative_to(data_root)
            or data_root.is_relative_to(app_root)):
        raise ValueError(
            "App- und Datenpfad müssen getrennte, nicht verschachtelte "
            "Verzeichnisse sein."
        )
    if managed or legacy or not app_root.exists():
        return
    try:
        has_content = next(app_root.iterdir(), None) is not None
    except NotADirectoryError as error:
        raise ValueError(f"App-Pfad ist kein Verzeichnis: {app_root}") from error
    if has_content:
        raise ValueError(
            f"Der App-Pfad ist nicht leer und nicht als Gusto-Installation "
            f"erkennbar: {app_root}"
        )


def installation_source(root: Path = ROOT) -> tuple[Path, str]:
    wheels = sorted(root.glob("gusto-*.whl"))
    if len(wheels) > 1:
        raise ValueError("mehrere Gusto-Wheels gefunden")
    if wheels:
        return wheels[0], "Release-Paket"
    if (root / "pyproject.toml").is_file() and (root / "gusto").is_dir():
        return root, "Projekt-Checkout"
    raise ValueError(
        "weder ein Gusto-Wheel noch ein vollständiger Projekt-Checkout gefunden"
    )


def source_version(source: Path) -> str:
    if source.suffix == ".whl":
        match = re.match(r"gusto-([^-]+)-", source.name)
        if not match:
            raise ValueError(f"Version im Wheel-Namen nicht erkennbar: {source.name}")
        return match.group(1)
    init = (source / "gusto" / "__init__.py").read_text(encoding="utf-8")
    match = re.search(r'__version__\s*=\s*"([^"]+)"', init)
    if not match:
        raise ValueError("Projektversion ist nicht erkennbar")
    return match.group(1)


def load_service_module(source: Path):
    if source.suffix == ".whl":
        sys.path.insert(0, os.fspath(source))
    from gusto import service
    return service


def runtime_is_usable(runtime: Path, version: str) -> bool:
    python = venv_python(runtime)
    if not python.is_file():
        return False
    try:
        marker = json.loads(
            (runtime / ".gusto-runtime.json").read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError):
        return False
    if marker != {"version": version, "verified": True}:
        return False
    smoke = subprocess.run(
        [
            os.fspath(python), "-c",
            "import gusto,sys;"
            "from gusto.cli import _missing_web_dependencies;"
            "raise SystemExit(gusto.__version__ != sys.argv[1] or "
            "bool(_missing_web_dependencies()))",
            version,
        ],
        text=True, encoding="utf-8", errors="replace", capture_output=True,
    )
    return smoke.returncode == 0


def install_source_runtime(
    source: Path,
    runtime: Path,
    version: str,
    data_root: Path,
    server_url: str,
) -> None:
    ensure_virtual_environment(runtime)
    python = venv_python(runtime)
    project = (
        f"gusto[web] @ {source.resolve().as_uri()}"
        if source.suffix == ".whl" else os.fspath(source) + "[web]"
    )
    install = subprocess.run(
        [os.fspath(python), "-m", "pip", "install", project],
        text=True, encoding="utf-8", errors="replace",
    )
    if install.returncode:
        raise RuntimeError("Wheel-Installation fehlgeschlagen")
    write_instance_settings(runtime, data_root, server_url)
    smoke = subprocess.run(
        [
            os.fspath(python), "-c",
            "import gusto,sys;from gusto.cli import _missing_web_dependencies;"
            "raise SystemExit(gusto.__version__ != sys.argv[1] or "
            "bool(_missing_web_dependencies()))",
            version,
        ],
        text=True, encoding="utf-8", errors="replace",
    )
    if smoke.returncode:
        raise RuntimeError("Import-/Web-Runtime-Prüfung fehlgeschlagen")
    (runtime / ".gusto-runtime.json").write_text(
        json.dumps({"version": version, "verified": True},
                   ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def write_wrappers(app_root: Path, bootstrap_python: Path) -> Path:
    """Create a stable command which resolves current.json on every call."""
    wrappers = app_root / "bin"
    wrappers.mkdir(parents=True, exist_ok=True)
    if sys.platform.startswith("win"):
        wrapper = wrappers / "gusto.cmd"
        text = (
            "@echo off\r\n"
            "setlocal\r\n"
            "for /f \"usebackq delims=\" %%P in (`powershell.exe -NoProfile "
            "-NonInteractive -Command "
            "\"(Get-Content -Raw -LiteralPath '%~dp0..\\current.json' | "
            "ConvertFrom-Json).runtime\"`) do set \"GUSTO_RUNTIME=%%P\"\r\n"
            "\"%GUSTO_RUNTIME%\\Scripts\\gusto.exe\" %*\r\n"
            "exit /b %ERRORLEVEL%\r\n"
        )
    else:
        wrapper = wrappers / "gusto"
        text = (
            f"#!{os.fspath(bootstrap_python)}\n"
            "import json, os, sys\n"
            "from pathlib import Path\n"
            "root = Path(__file__).resolve().parent.parent\n"
            "runtime = Path(json.loads((root / 'current.json').read_text("
            "encoding='utf-8'))['runtime'])\n"
            "os.execv(str(runtime / 'bin' / 'gusto'), "
            "[str(runtime / 'bin' / 'gusto'), *sys.argv[1:]])\n"
        )
    temporary = wrapper.with_suffix(wrapper.suffix + ".tmp")
    temporary.write_text(text, encoding="utf-8", newline="")
    os.replace(temporary, wrapper)
    if not sys.platform.startswith("win"):
        wrapper.chmod(0o755)
        user_bin = Path.home() / ".local" / "bin"
        user_bin.mkdir(parents=True, exist_ok=True)
        link = user_bin / "gusto"
        if link.is_symlink():
            try:
                owned = link.resolve() == wrapper.resolve()
            except OSError:
                owned = False
            if not owned:
                raise RuntimeError(
                    f"Bestehender fremder Symlink blockiert Gusto: {link}"
                )
            link.unlink(missing_ok=True)
            link.symlink_to(wrapper)
        elif link.exists():
            raise RuntimeError(
                f"Bestehende Datei blockiert den Gusto-Befehl: {link}"
            )
        else:
            link.symlink_to(wrapper)
    return wrapper


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Gusto als laufende Benutzer-App installieren.",
    )
    parser.add_argument("--install-dir", "--venv", dest="install_dir", type=Path)
    parser.add_argument("--data-dir", type=Path)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--manifest-url", default=DEFAULT_MANIFEST_URL)
    parser.add_argument("--repair", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--json", action="store_true")
    return parser


def _print_result(result: dict[str, object], *, json_output: bool) -> None:
    if json_output:
        print(json.dumps(result, ensure_ascii=False))
        return
    print(f"Gusto {result['version']} ist installiert und läuft.")
    print(f"App:   {result['application_path']}")
    print(f"Daten: {result['data_path']}")
    print(f"Öffnen: {result['url']}")
    print("Updates: gusto update")


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8")
            except OSError:
                pass
    args = build_parser().parse_args(argv)
    if sys.version_info < (3, 10):
        print("Fehler: Gusto braucht Python 3.10 oder neuer.", file=sys.stderr)
        return 1
    if not 1 <= args.port <= 65535:
        print("Fehler: --port muss zwischen 1 und 65535 liegen.", file=sys.stderr)
        return 2
    try:
        source, source_kind = installation_source()
        version = source_version(source)
        service = load_service_module(source)
        service.validate_version(version)
    except (ValueError, OSError, RuntimeError) as error:
        print(f"Fehler: {error}.", file=sys.stderr)
        return 1

    app_root = (args.install_dir or default_install_dir()).expanduser().resolve()
    data_dir = (args.data_dir or
                Path(os.environ.get("GUSTO_HOME") or default_data_dir())
                ).expanduser().resolve()
    paths = service.managed_paths(app_root)
    has_state = paths.state.is_file()
    has_current = paths.current.is_file()
    if has_state != has_current:
        print(
            "Fehler: Der App-Root enthält nur einen Teil der "
            "Gusto-Installationsmarkierung; aus Sicherheitsgründen wird "
            "nichts verändert.",
            file=sys.stderr,
        )
        return 1
    managed_exists = has_state and has_current
    legacy_exists = (
        not managed_exists and (app_root / "pyvenv.cfg").is_file()
    )
    try:
        validate_install_targets(
            app_root, data_dir, managed=managed_exists, legacy=legacy_exists,
        )
    except ValueError as error:
        print(f"Fehler: {error}", file=sys.stderr)
        return 1
    state = {
        "schema_version": 1,
        "data_dir": os.fspath(data_dir),
        "host": args.host,
        "port": args.port,
        "manifest_url": args.manifest_url,
    }
    result: dict[str, object] = {
        "ok": True,
        "status": "dry_run" if args.dry_run else "installed",
        "version": version,
        "source": source_kind,
        "application_path": os.fspath(app_root),
        "data_path": os.fspath(data_dir),
        "url": service.server_url(state),
        "autostart": "windows_task" if sys.platform.startswith("win")
        else "systemd_user",
    }
    if args.dry_run:
        _print_result(result, json_output=args.json)
        return 0
    if sys.platform.startswith("linux") and hasattr(os, "geteuid") and os.geteuid() == 0:
        print(
            "Fehler: Gusto wird als normaler Benutzer und ohne sudo installiert.",
            file=sys.stderr,
        )
        return 1

    if managed_exists and not args.repair:
        print(
            "Fehler: Gusto ist bereits installiert. Für Updates 'gusto update' "
            "verwenden; für Reparatur install.py --repair.",
            file=sys.stderr,
        )
        return 1

    legacy_backup: Path | None = None
    # A pre-versioned virtualenv occupied the complete app root. Move it aside
    # until the new service has passed its health check.
    if legacy_exists:
        legacy_settings = app_root / SETTINGS_FILENAME
        if args.data_dir is None and legacy_settings.is_file():
            try:
                configured = json.loads(
                    legacy_settings.read_text(encoding="utf-8")
                ).get("data_dir")
                if configured:
                    data_dir = Path(configured).expanduser().resolve()
                    state["data_dir"] = os.fspath(data_dir)
            except (OSError, json.JSONDecodeError, AttributeError):
                pass
        try:
            validate_install_targets(
                app_root, data_dir, managed=False, legacy=True,
            )
        except ValueError as error:
            print(f"Fehler: {error}", file=sys.stderr)
            return 1
        legacy_backup = app_root.with_name(
            app_root.name + f".legacy-{os.getpid()}"
        )
        if legacy_backup.exists():
            print(f"Fehler: temporäres Legacy-Ziel existiert: {legacy_backup}",
                  file=sys.stderr)
            return 1
        app_root.rename(legacy_backup)

    payload_version = version
    repair_current = None
    rebuild_runtime = False
    if managed_exists and args.repair:
        try:
            state = service.read_state(paths)
            data_dir = Path(str(state["data_dir"])).expanduser().resolve()
            validate_install_targets(
                app_root, data_dir, managed=True, legacy=False,
            )
            repair_current = service.read_current(paths, require_runtime=False)
            version = str(repair_current["version"])
            runtime = Path(str(repair_current["runtime"]))
            rebuild_runtime = not runtime_is_usable(runtime, version)
            if rebuild_runtime and payload_version != version:
                raise RuntimeError(
                    f"Die aktive Version {version} ist beschädigt, das "
                    f"Reparaturpaket enthält aber {payload_version}. "
                    "Eine Reparatur darf kein verdecktes Update durchführen."
                )
        except Exception as error:
            print(f"Fehler: Reparatur nicht möglich: {error}", file=sys.stderr)
            return 1
    else:
        runtime = paths.version(version)
    migrated = False
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
        for name in ("recipes", "images", "archive", "data"):
            (data_dir / name).mkdir(parents=True, exist_ok=True)
        migrated = migrate_checkout_data(ROOT, data_dir)
        if managed_exists and args.repair:
            assert repair_current is not None
            if rebuild_runtime:
                try:
                    service.remove_user_service(paths)
                except Exception:
                    pass
                shutil.rmtree(runtime, ignore_errors=True)
                install_source_runtime(
                    source, runtime, version, data_dir,
                    service.server_url(state),
                )
        else:
            if runtime.exists():
                raise RuntimeError(f"Versionsordner existiert bereits: {runtime}")
            install_source_runtime(
                source, runtime, version, data_dir,
                service.server_url(state),
            )
            service.write_state(paths, state)
            service.write_current(paths, version)
        write_instance_settings(
            runtime, Path(str(state["data_dir"])), service.server_url(state),
        )
        write_wrappers(app_root, Path(sys.executable).resolve())
        if sys.platform.startswith("win"):
            add_windows_user_path(paths.wrappers)
        service.install_user_service(paths, state)
        service.wait_for_health(
            service.health_url(state),
            expected_version=version,
            expected_data_path=state["data_dir"],
        )
        if legacy_backup is not None:
            shutil.rmtree(legacy_backup)
        result.update({
            "status": "repaired" if args.repair else "installed",
            "version": version,
            "data_path": os.fspath(data_dir),
            "url": service.server_url(state),
        })
    except Exception as error:
        if not managed_exists:
            try:
                service.remove_user_service(paths)
            except Exception:
                pass
            if sys.platform.startswith("win"):
                try:
                    remove_windows_user_path(paths.wrappers)
                    remove_windows_app_registration()
                except Exception:
                    pass
            else:
                link = Path.home() / ".local" / "bin" / "gusto"
                try:
                    if (link.is_symlink()
                            and link.resolve() == (paths.wrappers / "gusto").resolve()):
                        link.unlink()
                except OSError:
                    pass
        if legacy_backup is not None and legacy_backup.exists():
            shutil.rmtree(app_root, ignore_errors=True)
            legacy_backup.rename(app_root)
        elif not managed_exists:
            shutil.rmtree(app_root, ignore_errors=True)
        print(f"Fehler: Installation fehlgeschlagen: {error}", file=sys.stderr)
        return 1

    result["data_migrated"] = migrated
    _print_result(result, json_output=args.json)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
