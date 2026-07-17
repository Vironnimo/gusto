"""Cross-platform Gusto installer for Windows, Linux, and macOS."""
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

from gusto import core


ROOT = Path(__file__).resolve().parent


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


def migrate_checkout_data(source: Path, destination: Path) -> bool:
    """Copy an old checkout store once, without overwriting an active store."""
    if source.resolve() == destination.resolve():
        return False
    if (not core.contains_gusto_data(source)
            or core.contains_gusto_data(destination)):
        return False
    for name in ("recipes", "images", "data"):
        source_dir = source / name
        if source_dir.is_dir():
            shutil.copytree(source_dir, destination / name, dirs_exist_ok=True)
    return True


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Gusto plattformübergreifend in einer virtuellen Umgebung installieren.",
    )
    parser.add_argument(
        "--venv", type=Path, default=ROOT / ".venv",
        help="Ziel der virtuellen Umgebung (Standard: .venv im Projektordner).",
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

    venv_dir = args.venv.expanduser().resolve()
    python = venv_python(venv_dir)
    command = gusto_command(venv_dir)
    project = os.fspath(ROOT) + ("" if args.cli_only else "[web]")
    install = [os.fspath(python), "-m", "pip", "install"]
    if args.editable:
        install.append("--editable")
    install.append(project)

    print(f"Projekt: {ROOT}")
    print(f"Umgebung: {venv_dir}")
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
        source_has_data = core.contains_gusto_data(ROOT)
        destination_has_data = core.contains_gusto_data(data_root)
        migrated = migrate_checkout_data(ROOT, data_root)
        for name in ("recipes", "images", "data"):
            (data_root / name).mkdir(parents=True, exist_ok=True)
    except OSError as error:
        print(f"Fehler: Datenordner konnte nicht vorbereitet werden: {error}",
              file=sys.stderr)
        return 1

    print()
    print("Gusto ist installiert.")
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
