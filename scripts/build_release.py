"""Build verified, stable-named Gusto release assets."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
ARCHIVE_NAME = "gusto-release.zip"
MANIFEST_NAME = "gusto-release.json"
CHECKSUM_NAME = ARCHIVE_NAME + ".sha256"
BUNDLE_FILES = (
    Path("install.py"),
    Path("install.ps1"),
    Path("install.sh"),
    Path("deploy/gusto.service"),
    Path("deploy/install-systemd.sh"),
    Path("deploy/install-windows-task.ps1"),
)
BUNDLE_DIRECTORIES = (Path("skill/gusto"),)


def project_version() -> str:
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'^version\s*=\s*"([^"]+)"$', text, re.MULTILINE)
    if not match:
        raise RuntimeError("Projektversion fehlt.")
    return match.group(1)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Gusto-Release-Assets bauen.")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "dist")
    return parser


def build_release(output_dir: Path) -> Path:
    output_dir = output_dir.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    version = project_version()
    with tempfile.TemporaryDirectory(prefix="gusto-release-") as temporary:
        temporary_dir = Path(temporary)
        wheel_dir = temporary_dir / "wheel"
        wheel_dir.mkdir()
        result = subprocess.run(
            [sys.executable, "-m", "pip", "wheel", "--no-deps",
             "--no-build-isolation",
             "--wheel-dir", os.fspath(wheel_dir), os.fspath(ROOT)],
            cwd=ROOT,
        )
        if result.returncode:
            raise RuntimeError("Wheel-Build fehlgeschlagen.")
        wheels = list(wheel_dir.glob("gusto-*.whl"))
        if len(wheels) != 1:
            raise RuntimeError("Genau ein Gusto-Wheel erwartet.")

        staging = temporary_dir / f"gusto-{version}"
        staging.mkdir()
        shutil.copy2(wheels[0], staging / wheels[0].name)
        for relative in BUNDLE_FILES:
            destination = staging / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / relative, destination)
        for relative in BUNDLE_DIRECTORIES:
            shutil.copytree(
                ROOT / relative, staging / relative,
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
            )
        (staging / "INSTALLATION.txt").write_text(
            "Gusto wird als laufende Benutzer-App ohne Adminrechte installiert.\n\n"
            "Windows: powershell -ExecutionPolicy Bypass -File .\\install.ps1\n"
            "Linux:   ./install.sh\n\n"
            "Der normale Updateweg ist anschließend: gusto update\n"
            "Die Nutzdaten bleiben getrennt von den versionierten Runtimes.\n"
            "Der passende Agent-Skill ist im Release und in der Runtime "
            "enthalten. vBot-Installation: gusto install-skill vbot\n"
            "Der App-Installer verändert Agent-Hosts nicht automatisch.\n",
            encoding="utf-8",
        )

        archive = output_dir / ARCHIVE_NAME
        temporary_archive = archive.with_suffix(".zip.tmp")
        with zipfile.ZipFile(
            temporary_archive, "w", compression=zipfile.ZIP_DEFLATED,
        ) as bundle:
            for path in sorted(staging.rglob("*")):
                if not path.is_file():
                    continue
                name = path.relative_to(temporary_dir).as_posix()
                info = zipfile.ZipInfo.from_file(path, name)
                info.create_system = 3
                executable = path.suffix == ".sh" or path.name == "install.sh"
                permissions = 0o755 if executable else 0o644
                info.external_attr = (stat.S_IFREG | permissions) << 16
                bundle.writestr(
                    info, path.read_bytes(), compress_type=zipfile.ZIP_DEFLATED,
                )
        os.replace(temporary_archive, archive)

    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    checksum = output_dir / CHECKSUM_NAME
    checksum.write_text(f"{digest}  {ARCHIVE_NAME}\n", encoding="utf-8")
    manifest = {
        "schema_version": 1,
        "version": version,
        "archive": ARCHIVE_NAME,
        "checksum": CHECKSUM_NAME,
        "sha256": digest,
        "minimum_python": "3.10",
    }
    (output_dir / MANIFEST_NAME).write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    # Stable bootstrap filenames are published beside the manifest so the
    # one-shot commands and the release payload always come from one release.
    shutil.copy2(ROOT / "install.ps1", output_dir / "install.ps1")
    shutil.copy2(ROOT / "install.sh", output_dir / "install.sh")
    return archive


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        archive = build_release(args.output_dir)
    except (OSError, RuntimeError) as error:
        print(f"Fehler: Release konnte nicht gebaut werden: {error}",
              file=sys.stderr)
        return 1
    print(f"Release: {archive}")
    print(f"Manifest: {archive.parent / MANIFEST_NAME}")
    print(f"SHA-256: {archive.parent / CHECKSUM_NAME}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
