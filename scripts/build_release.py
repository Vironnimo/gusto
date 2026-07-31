"""Build direct, verified Gusto release assets without an outer bundle ZIP."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
MANIFEST_NAME = "gusto-release.json"
INSTALLER_NAME = "install.py"
WINDOWS_PYTHON_VERSION = "3.13.14"
WINDOWS_PYTHON_ASSET = "python-runtime-windows-x64.nupkg"
WINDOWS_PYTHON_URL = (
    "https://www.nuget.org/api/v2/package/python/"
    f"{WINDOWS_PYTHON_VERSION}"
)
WINDOWS_PYTHON_SHA256 = (
    "9ac15cfa6cab1115c83d48f2af55c554efa4d1bb044bbc4ab1c9d17ad426e16c"
)


def project_version() -> str:
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'^version\s*=\s*"([^"]+)"$', text, re.MULTILINE)
    if not match:
        raise RuntimeError("Projektversion fehlt.")
    return match.group(1)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download_windows_python(destination: Path) -> None:
    request = urllib.request.Request(
        WINDOWS_PYTHON_URL,
        headers={"User-Agent": "Gusto-Release-Builder/1"},
    )
    with (
        urllib.request.urlopen(request, timeout=60) as response,
        destination.open("wb") as output,
    ):
        shutil.copyfileobj(response, output)


def validate_windows_python_package(path: Path) -> None:
    try:
        with zipfile.ZipFile(path) as package:
            names = {name.replace("\\", "/") for name in package.namelist()}
    except (OSError, zipfile.BadZipFile) as error:
        raise RuntimeError(f"Windows-Python-Paket ist nicht lesbar: {error}") from error
    for required in ("tools/python.exe", "tools/pythonw.exe", "tools/LICENSE.txt"):
        if required not in names:
            raise RuntimeError(
                f"Windows-Python-Paket enthält {required!r} nicht."
            )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Gusto-Release-Assets bauen.")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "dist")
    parser.add_argument(
        "--windows-python-source", type=Path, help=argparse.SUPPRESS,
    )
    return parser


def build_release(
    output_dir: Path,
    *,
    windows_python_source: Path | None = None,
) -> Path:
    """Build one wheel plus independently hashed bootstrap/runtime assets."""
    output_dir = output_dir.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    for stale in (
        *output_dir.glob("gusto-*.whl"),
        output_dir / "gusto-release.zip",
        output_dir / "gusto-release.zip.sha256",
    ):
        stale.unlink(missing_ok=True)
    version = project_version()
    with tempfile.TemporaryDirectory(prefix="gusto-release-") as temporary:
        wheel_dir = Path(temporary) / "wheel"
        wheel_dir.mkdir()
        result = subprocess.run(
            [sys.executable, "-m", "pip", "wheel", "--no-deps",
             "--no-build-isolation", "--wheel-dir", os.fspath(wheel_dir),
             os.fspath(ROOT)],
            cwd=ROOT,
        )
        if result.returncode:
            raise RuntimeError("Wheel-Build fehlgeschlagen.")
        wheels = list(wheel_dir.glob("gusto-*.whl"))
        if len(wheels) != 1:
            raise RuntimeError("Genau ein Gusto-Wheel erwartet.")
        wheel = output_dir / wheels[0].name
        shutil.copy2(wheels[0], wheel)

    installer = output_dir / INSTALLER_NAME
    shutil.copy2(ROOT / INSTALLER_NAME, installer)
    for bootstrap in ("install.ps1", "install.sh"):
        shutil.copy2(ROOT / bootstrap, output_dir / bootstrap)
    windows_bootstrap = output_dir / "install.ps1"
    linux_bootstrap = output_dir / "install.sh"

    python_asset = output_dir / WINDOWS_PYTHON_ASSET
    if windows_python_source is None:
        temporary_python = python_asset.with_suffix(".download")
        download_windows_python(temporary_python)
        actual = sha256(temporary_python)
        if actual != WINDOWS_PYTHON_SHA256:
            temporary_python.unlink(missing_ok=True)
            raise RuntimeError(
                "SHA-256 des offiziellen Windows-Python-Pakets stimmt nicht: "
                f"{actual}."
            )
        os.replace(temporary_python, python_asset)
    else:
        shutil.copy2(windows_python_source.resolve(), python_asset)
    validate_windows_python_package(python_asset)

    manifest = {
        "schema_version": 2,
        "version": version,
        "minimum_system_python": "3.10",
        "assets": {
            "wheel": {
                "name": wheel.name,
                "sha256": sha256(wheel),
            },
            "installer": {
                "name": installer.name,
                "sha256": sha256(installer),
            },
            "windows_bootstrap": {
                "name": windows_bootstrap.name,
                "sha256": sha256(windows_bootstrap),
            },
            "linux_bootstrap": {
                "name": linux_bootstrap.name,
                "sha256": sha256(linux_bootstrap),
            },
            "windows_python_x64": {
                "name": python_asset.name,
                "sha256": sha256(python_asset),
                "version": WINDOWS_PYTHON_VERSION,
                "layout": "tools",
            },
        },
    }
    manifest_path = output_dir / MANIFEST_NAME
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return manifest_path


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        manifest = build_release(
            args.output_dir,
            windows_python_source=args.windows_python_source,
        )
    except (OSError, RuntimeError) as error:
        print(f"Fehler: Release konnte nicht gebaut werden: {error}",
              file=sys.stderr)
        return 1
    value = json.loads(manifest.read_text(encoding="utf-8"))
    print(f"Manifest: {manifest}")
    for name, asset in value["assets"].items():
        print(f"{name}: {manifest.parent / asset['name']} ({asset['sha256']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
