#!/usr/bin/env bash
set -Eeuo pipefail

# Public one-shot bootstrap for the Gusto application. The installed
# application is updated later with `gusto update`, never by rerunning this.
release_base="${GUSTO_RELEASE_BASE:-https://github.com/Vironnimo/gusto/releases/latest/download}"
python_bin="${PYTHON_BIN:-python3}"
installer_args=()

while (($#)); do
  case "$1" in
    --release-base)
      [[ $# -ge 2 ]] || { echo "Fehler: --release-base braucht eine URL." >&2; exit 2; }
      release_base="${2%/}"
      shift 2
      ;;
    --install-dir|--data-dir|--host|--port|--manifest-url)
      [[ $# -ge 2 ]] || { echo "Fehler: $1 braucht einen Wert." >&2; exit 2; }
      installer_args+=("$1" "$2")
      shift 2
      ;;
    --dry-run|--repair|--json)
      installer_args+=("$1")
      shift
      ;;
    -h|--help)
      echo "Gusto installieren: ./install.sh [--install-dir PFAD] [--data-dir PFAD]"
      echo "Danach erfolgen Updates ausschließlich mit: gusto update"
      exit 0
      ;;
    *)
      echo "Fehler: unbekannte Option '$1'." >&2
      exit 2
      ;;
  esac
done

if [[ "${EUID:-$(id -u)}" -eq 0 ]]; then
  echo "Fehler: Gusto wird als normaler Benutzer und ohne sudo installiert." >&2
  exit 1
fi
command -v "$python_bin" >/dev/null 2>&1 || {
  echo "Fehler: Python 3 wurde nicht gefunden." >&2
  exit 1
}
"$python_bin" -c 'import sys; raise SystemExit(sys.version_info < (3, 10))' || {
  echo "Fehler: Gusto braucht Python 3.10 oder neuer." >&2
  exit 1
}

temporary="$(mktemp -d "${TMPDIR:-/tmp}/gusto-install.XXXXXXXX")"
trap 'rm -rf -- "$temporary"' EXIT
manifest="$temporary/gusto-release.json"

download() {
  local source="$1" destination="$2"
  "$python_bin" - "$source" "$destination" <<'PY'
import shutil, sys, urllib.parse, urllib.request
from pathlib import Path
source, destination = sys.argv[1:]
parsed = urllib.parse.urlparse(source)
if parsed.scheme == "https":
    request = urllib.request.Request(source, headers={"User-Agent": "Gusto-Installer/1"})
    with urllib.request.urlopen(request, timeout=30) as response, open(destination, "wb") as out:
        shutil.copyfileobj(response, out)
elif parsed.scheme == "file":
    shutil.copyfile(urllib.request.url2pathname(parsed.path), destination)
elif parsed.scheme:
    raise SystemExit("Fehler: Release-URLs müssen HTTPS oder lokale Pfade sein.")
else:
    shutil.copyfile(Path(source).expanduser(), destination)
PY
}

download "$release_base/gusto-release.json" "$manifest"
readarray -t release_fields < <("$python_bin" - "$manifest" <<'PY'
import json, sys
from pathlib import Path
value = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
if value.get("schema_version") != 1:
    raise SystemExit("Fehler: unbekanntes Release-Manifest.")
for key in ("archive", "checksum", "sha256"):
    item = value.get(key)
    if not isinstance(item, str) or not item:
        raise SystemExit(f"Fehler: Manifest-Feld {key!r} fehlt.")
print(value["archive"])
print(value["checksum"])
print(value["sha256"].lower())
PY
)
archive_name="${release_fields[0]}"
checksum_name="${release_fields[1]}"
expected="${release_fields[2]}"
[[ "$archive_name" != */* && "$checksum_name" != */* ]] || {
  echo "Fehler: Release-Assets müssen einfache Dateinamen sein." >&2
  exit 1
}
archive="$temporary/$archive_name"
checksum="$temporary/$checksum_name"
download "$release_base/$archive_name" "$archive"
download "$release_base/$checksum_name" "$checksum"

"$python_bin" - "$archive" "$checksum" "$expected" <<'PY'
import hashlib, sys
from pathlib import Path
archive = Path(sys.argv[1])
checksum = Path(sys.argv[2])
expected = sys.argv[3].lower()
line = checksum.read_text(encoding="utf-8").strip().split()
if not line or line[0].lower() != expected:
    raise SystemExit("Fehler: Manifest und Checksum-Datei stimmen nicht überein.")
actual = hashlib.sha256(archive.read_bytes()).hexdigest()
if actual != expected:
    raise SystemExit(f"Fehler: SHA-256-Prüfung fehlgeschlagen ({actual}).")
PY

extract="$temporary/release"
mkdir -- "$extract"
"$python_bin" - "$archive" "$extract" <<'PY'
import stat, sys, zipfile
from pathlib import Path
archive, destination = Path(sys.argv[1]), Path(sys.argv[2]).resolve()
with zipfile.ZipFile(archive) as bundle:
    for info in bundle.infolist():
        name = info.filename.replace("\\", "/")
        parts = Path(name).parts
        if (not name or name.startswith("/") or any(p in ("", ".", "..") for p in parts)
                or ((info.external_attr >> 16) & 0o170000) == stat.S_IFLNK):
            raise SystemExit(f"Fehler: unsicherer ZIP-Eintrag: {name!r}")
        target = (destination / Path(*parts)).resolve()
        if destination not in target.parents and target != destination:
            raise SystemExit(f"Fehler: ZIP-Eintrag verlässt das Ziel: {name!r}")
    bundle.extractall(destination)
PY

mapfile -t installers < <(find "$extract" -type f -name install.py -print)
[[ "${#installers[@]}" -eq 1 ]] || {
  echo "Fehler: Release enthält nicht genau einen Installer." >&2
  exit 1
}
"$python_bin" "${installers[0]}" \
  --manifest-url "$release_base/gusto-release.json" "${installer_args[@]}"
