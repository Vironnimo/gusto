"""Build a transferable Gusto release bundle without repository access."""
from __future__ import annotations

import argparse
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
BUNDLE_FILES = (
    Path("install.py"),
    Path("deploy/gusto.service"),
    Path("deploy/install-systemd.sh"),
    Path("deploy/install-windows-task.ps1"),
)
INSTALLATION_GUIDE = """Gusto installieren
==================

Dieses Release-Paket funktioniert ohne GitHub-Zugang und ohne Source-Checkout.
Python 3.10 oder neuer sowie eine Internetverbindung für die Web-Abhängigkeiten
werden benötigt.

Windows (PowerShell):
  py -3 install.py

  Danach eine neue Konsole öffnen. Start:
  gusto serve

  Optionaler Autostart:
  powershell -ExecutionPolicy Bypass -File .\\deploy\\install-windows-task.ps1

  Der Autostart verwendet einen fensterlosen Launcher. Diagnoseausgaben stehen
  unter %LOCALAPPDATA%\\Gusto\\gusto-autostart.log. Derselbe Befehl aktualisiert
  auch eine bereits vorhandene Gusto-Aufgabe.

Linux / Raspberry Pi (als normaler Benutzer, nicht mit sudo):
  python3 install.py

  Start:
  "$HOME/.local/opt/gusto/bin/gusto" serve

  Optionaler systemd-Autostart:
  ./deploy/install-systemd.sh

Nutzdaten liegen getrennt von der Anwendung unter %LOCALAPPDATA%\\Gusto auf
Windows beziehungsweise ~/.local/share/gusto auf Linux. GUSTO_HOME kann den
Datenpfad überschreiben.
"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Übertragbares Gusto-Release-Paket bauen.",
    )
    parser.add_argument(
        "--output-dir", type=Path, default=ROOT / "dist",
        help="Zielordner (Standard: dist im Projektordner).",
    )
    return parser


def build_release(output_dir: Path) -> Path:
    output_dir = output_dir.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="gusto-release-") as temporary:
        temporary_dir = Path(temporary)
        wheel_dir = temporary_dir / "wheel"
        wheel_dir.mkdir()
        result = subprocess.run(
            [sys.executable, "-m", "pip", "wheel", "--no-deps",
             "--wheel-dir", os.fspath(wheel_dir), os.fspath(ROOT)],
            cwd=ROOT,
        )
        if result.returncode:
            raise SystemExit(result.returncode)

        wheels = list(wheel_dir.glob("gusto-*.whl"))
        if len(wheels) != 1:
            raise RuntimeError(f"Genau ein Gusto-Wheel erwartet, gefunden: {wheels}")
        wheel = wheels[0]
        version = wheel.name.split("-", 2)[1]
        bundle_name = f"gusto-{version}"
        staging = temporary_dir / bundle_name
        staging.mkdir()

        shutil.copy2(wheel, staging / wheel.name)
        for relative_path in BUNDLE_FILES:
            destination = staging / relative_path
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / relative_path, destination)
        (staging / "INSTALLATION.txt").write_text(
            INSTALLATION_GUIDE, encoding="utf-8",
        )

        archive = output_dir / f"{bundle_name}-release.zip"
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
            for path in sorted(staging.rglob("*")):
                if path.is_file():
                    archive_name = path.relative_to(temporary_dir).as_posix()
                    info = zipfile.ZipInfo.from_file(path, archive_name)
                    info.create_system = 3
                    permissions = 0o755 if path.suffix == ".sh" else 0o644
                    info.external_attr = (stat.S_IFREG | permissions) << 16
                    bundle.writestr(
                        info, path.read_bytes(), compress_type=zipfile.ZIP_DEFLATED,
                    )
        return archive


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        archive = build_release(args.output_dir)
    except (OSError, RuntimeError) as error:
        print(f"Fehler: Release-Paket konnte nicht gebaut werden: {error}",
              file=sys.stderr)
        return 1
    print()
    print(f"Release-Paket: {archive}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
