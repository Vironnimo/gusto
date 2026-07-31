#!/usr/bin/env bash
set -Eeuo pipefail

# Public one-shot bootstrap. Linux uses a suitable system Python; Windows ships
# an app-private runtime through install.ps1.
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
    request = urllib.request.Request(source, headers={"User-Agent": "Gusto-Installer/2"})
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
import json, re, sys
from pathlib import Path
value = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
if value.get("schema_version") != 2:
    raise SystemExit("Fehler: unbekanntes Release-Manifest.")
assets = value.get("assets")
if not isinstance(assets, dict):
    raise SystemExit("Fehler: Release-Manifest enthält keine Assets.")
for key in ("wheel", "installer"):
    asset = assets.get(key)
    if not isinstance(asset, dict):
        raise SystemExit(f"Fehler: Manifest-Asset {key!r} fehlt.")
    name, digest = asset.get("name"), asset.get("sha256")
    if (not isinstance(name, str) or not name or Path(name).name != name
            or not isinstance(digest, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", digest)):
        raise SystemExit(f"Fehler: Manifest-Asset {key!r} ist ungültig.")
    print(name)
    print(digest.lower())
PY
)
[[ "${#release_fields[@]}" -eq 4 ]] || {
  echo "Fehler: Release-Manifest enthält keine gültigen Installer-Assets." >&2
  exit 1
}
wheel_name="${release_fields[0]}"
wheel_sha="${release_fields[1]}"
installer_name="${release_fields[2]}"
installer_sha="${release_fields[3]}"
wheel="$temporary/$wheel_name"
installer="$temporary/$installer_name"
download "$release_base/$wheel_name" "$wheel"
download "$release_base/$installer_name" "$installer"

"$python_bin" - "$wheel" "$wheel_sha" "$installer" "$installer_sha" <<'PY'
import hashlib, sys
from pathlib import Path
for raw_path, expected in zip(sys.argv[1::2], sys.argv[2::2]):
    path = Path(raw_path)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if digest != expected:
        raise SystemExit(f"Fehler: SHA-256-Prüfung für {path.name!r} fehlgeschlagen.")
PY

"$python_bin" "$installer" \
  --manifest-url "$release_base/gusto-release.json" "${installer_args[@]}"
