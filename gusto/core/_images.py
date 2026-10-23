"""Image format validation and uncommitted-copy cleanup."""
from __future__ import annotations

from pathlib import Path
import gusto.core.storage as storage


IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}


def validate_source(source: str | Path) -> tuple[Path, str]:
    """Validate one owned-image import before either domain copies its file."""
    source_path = Path(source).expanduser()
    if not source_path.is_file():
        raise ValueError(f"Bilddatei nicht gefunden: '{source_path}'.")
    suffix = source_path.suffix.lower()
    if suffix not in IMAGE_EXTENSIONS:
        allowed = ", ".join(sorted(IMAGE_EXTENSIONS))
        raise ValueError(f"Nicht unterstütztes Bildformat '{suffix}' ({allowed}).")
    if not _valid_image_header(source_path, suffix):
        raise ValueError(
            f"Datei ist kein gültiges {suffix.lstrip('.').upper()}-Bild: '{source_path}'."
        )
    return source_path, suffix


def _valid_image_header(path: Path, suffix: str) -> bool:
    """Validate the format and positive dimensions without optional libraries."""
    with path.open("rb") as handle:
        header = handle.read(32)

        if suffix == ".png":
            return (header.startswith(b"\x89PNG\r\n\x1a\n")
                    and header[12:16] == b"IHDR"
                    and int.from_bytes(header[16:20], "big") > 0
                    and int.from_bytes(header[20:24], "big") > 0)

        if suffix == ".gif":
            return (header[:6] in {b"GIF87a", b"GIF89a"}
                    and int.from_bytes(header[6:8], "little") > 0
                    and int.from_bytes(header[8:10], "little") > 0)

        if suffix == ".webp":
            if not (header.startswith(b"RIFF") and header[8:12] == b"WEBP"):
                return False
            chunk = header[12:16]
            if chunk == b"VP8X" and len(header) >= 30:
                width = 1 + int.from_bytes(header[24:27], "little")
                height = 1 + int.from_bytes(header[27:30], "little")
                return width > 0 and height > 0
            if chunk == b"VP8L" and len(header) >= 25 and header[20] == 0x2F:
                bits = int.from_bytes(header[21:25], "little")
                return (bits & 0x3FFF) + 1 > 0 and ((bits >> 14) & 0x3FFF) + 1 > 0
            if chunk == b"VP8 " and len(header) >= 30 and header[23:26] == b"\x9d\x01\x2a":
                width = int.from_bytes(header[26:28], "little") & 0x3FFF
                height = int.from_bytes(header[28:30], "little") & 0x3FFF
                return width > 0 and height > 0
            return False

        if suffix in {".jpg", ".jpeg"}:
            if header[:2] != b"\xff\xd8":
                return False
            handle.seek(2)
            sof_markers = {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7,
                           0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}
            while True:
                byte = handle.read(1)
                if not byte:
                    return False
                if byte != b"\xff":
                    continue
                marker_byte = handle.read(1)
                while marker_byte == b"\xff":
                    marker_byte = handle.read(1)
                if not marker_byte:
                    return False
                marker = marker_byte[0]
                if marker in {0x01, 0xD8} or 0xD0 <= marker <= 0xD7:
                    continue
                if marker in {0xD9, 0xDA}:
                    return False
                length_bytes = handle.read(2)
                if len(length_bytes) != 2:
                    return False
                length = int.from_bytes(length_bytes, "big")
                if length < 2:
                    return False
                if marker in sof_markers:
                    dimensions = handle.read(5)
                    return (len(dimensions) == 5
                            and int.from_bytes(dimensions[1:3], "big") > 0
                            and int.from_bytes(dimensions[3:5], "big") > 0)
                handle.seek(length - 2, 1)

    return False


def _discard_imported_image(path: Path) -> None:
    """Remove an uncommitted copy; a failed cleanup leaves a storage change."""
    try:
        path.unlink(missing_ok=True)
    except OSError:
        storage.mark_mutation()
        raise
