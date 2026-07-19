"""Install Gusto from a source checkout or a self-contained release bundle."""
from __future__ import annotations

import argparse
import json
import os
import shlex
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


def default_install_dir(platform_name: str | None = None,
                        environ: dict[str, str] | None = None,
                        home: str | Path | None = None) -> Path:
    """Return the per-user application directory for the operating system."""
    platform_name = platform_name or sys.platform
    environ = os.environ if environ is None else environ
    user_home = Path.home() if home is None else Path(home)

    if platform_name.startswith("win"):
        base = Path(environ.get("LOCALAPPDATA")
                    or user_home / "AppData" / "Local")
        return (base / "Programs" / "Gusto").expanduser().resolve()
    if platform_name == "darwin":
        return (user_home / "Library" / "Application Support"
                / "Gusto" / "app").resolve()
    return (user_home / ".local" / "opt" / "gusto").resolve()


def venv_python(venv_dir: Path, platform_name: str | None = None) -> Path:
    platform_name = platform_name or sys.platform
    if platform_name.startswith("win"):
        return venv_dir / "Scripts" / "python.exe"
    return venv_dir / "bin" / "python"


def gusto_command(venv_dir: Path, platform_name: str | None = None) -> Path:
    platform_name = platform_name or sys.platform
    if platform_name.startswith("win"):
        return venv_dir / "Scripts" / "gusto.exe"
    return venv_dir / "bin" / "gusto"


def display_command(arguments: list[str]) -> str:
    if sys.platform.startswith("win"):
        return subprocess.list2cmdline(arguments)
    return shlex.join(arguments)


def contains_gusto_data(root: Path) -> bool:
    """Return whether a directory contains a recognizable Gusto store."""
    data = root / "data"
    if any((data / name).is_file() for name in RECOGNIZABLE_DATA_FILES):
        return True
    recipes = root / "recipes"
    return recipes.is_dir() and next(recipes.glob("*.md"), None) is not None


def migrate_checkout_data(source: Path, destination: Path) -> bool:
    """Copy an old checkout store once, without overwriting an active store."""
    if source.resolve() == destination.resolve():
        return False
    if not contains_gusto_data(source) or contains_gusto_data(destination):
        return False
    for name in ("recipes", "images", "data"):
        source_dir = source / name
        if source_dir.is_dir():
            shutil.copytree(source_dir, destination / name, dirs_exist_ok=True)
    return True


def write_instance_settings(install_dir: Path, data_root: Path) -> Path:
    """Persist the data directory beside the installed application runtime."""
    settings_path = install_dir / SETTINGS_FILENAME
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = settings_path.with_suffix(settings_path.suffix + ".tmp")
    content = json.dumps(
        {"data_dir": os.fspath(data_root.resolve())},
        ensure_ascii=False, indent=2,
    ) + "\n"
    temporary_path.write_text(content, encoding="utf-8")
    os.replace(temporary_path, settings_path)
    return settings_path


def installation_source(root: Path = ROOT) -> tuple[Path, str]:
    """Find the bundled wheel, falling back to a complete source checkout."""
    wheels = sorted(root.glob("gusto-*.whl"))
    if len(wheels) > 1:
        names = ", ".join(wheel.name for wheel in wheels)
        raise ValueError(f"mehrere Gusto-Wheels gefunden: {names}")
    if wheels:
        return wheels[0], "Release-Paket"
    if (root / "pyproject.toml").is_file() and (root / "gusto").is_dir():
        return root, "Projekt-Checkout"
    raise ValueError(
        "weder ein Gusto-Wheel noch ein vollständiger Projekt-Checkout gefunden"
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Gusto plattformübergreifend in einer virtuellen Umgebung installieren.",
    )
    parser.add_argument(
        "--venv", type=Path,
        help="Installationsordner (Standard: plattformüblicher Benutzer-App-Ordner).",
    )
    parser.add_argument(
        "--cli-only", action="store_true",
        help="Nur die abhängigkeitfreie CLI ohne Web-Oberfläche installieren.",
    )
    parser.add_argument(
        "--editable", action="store_true",
        help="Entwicklungsinstallation erstellen; für normale Installationen nicht nötig.",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Geplante Befehle und Pfade anzeigen, ohne etwas zu verändern.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if sys.version_info < (3, 10):
        print("Fehler: Gusto braucht Python 3.10 oder neuer.", file=sys.stderr)
        return 1

    default_venv = ROOT / ".venv" if args.editable else default_install_dir()
    venv_dir = (args.venv or default_venv).expanduser().resolve()
    python = venv_python(venv_dir)
    command = gusto_command(venv_dir)
    try:
        source, source_kind = installation_source()
    except ValueError as error:
        print(f"Fehler: {error}.", file=sys.stderr)
        return 1
    if args.editable and source_kind == "Release-Paket":
        print("Fehler: --editable ist nur in einem Projekt-Checkout möglich.",
              file=sys.stderr)
        return 2
    project = os.fspath(source) + ("" if args.cli_only else "[web]")
    install = [os.fspath(python), "-m", "pip", "install"]
    if args.editable:
        install.append("--editable")
    install.append(project)

    print(f"Quelle: {source_kind} ({source})")
    print(f"Installation: {venv_dir}")
    print("Variante: " + ("CLI" if args.cli_only else "CLI + Web"))
    if args.dry_run:
        print("Geplant:")
        print(f"  {display_command([sys.executable, '-m', 'venv', os.fspath(venv_dir)])}")
        print(f"  {display_command(install)}")
        return 0

    try:
        venv.EnvBuilder(with_pip=True).create(venv_dir)
    except Exception as error:
        print(f"Fehler: Virtuelle Umgebung konnte nicht erstellt werden: {error}",
              file=sys.stderr)
        return 1

    result = subprocess.run(install, cwd=venv_dir)
    if result.returncode:
        return result.returncode

    home_result = subprocess.run(
        [os.fspath(python), "-m", "gusto", "home", "--json"],
        cwd=venv_dir, text=True, encoding="utf-8", capture_output=True,
    )
    if home_result.returncode:
        print(home_result.stderr, file=sys.stderr, end="")
        return home_result.returncode
    data_root = Path(json.loads(home_result.stdout)["path"])
    try:
        settings_path = write_instance_settings(venv_dir, data_root)
        source_has_data = contains_gusto_data(ROOT)
        destination_has_data = contains_gusto_data(data_root)
        migrated = migrate_checkout_data(ROOT, data_root)
        for name in ("recipes", "images", "data"):
            (data_root / name).mkdir(parents=True, exist_ok=True)
    except OSError as error:
        print(f"Fehler: Datenordner konnte nicht vorbereitet werden: {error}",
              file=sys.stderr)
        return 1

    print()
    print("Gusto ist installiert.")
    print(f"Settings: {settings_path}")
    print(f"Daten: {data_root}")
    if migrated:
        print("Bestehende Checkout-Daten wurden dorthin kopiert; das Original bleibt erhalten.")
    elif (source_has_data and destination_has_data
          and ROOT.resolve() != data_root.resolve()):
        print("Hinweis: Checkout und Benutzerdatenordner enthalten bereits Daten.")
        print("Sie wurden nicht automatisch zusammengeführt; beide Bestände "
              "bleiben unverändert.")
    print("Start:")
    print(f"  {display_command([os.fspath(command), 'serve'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
