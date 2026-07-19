#!/usr/bin/env bash
set -Eeuo pipefail

usage() {
  cat <<'EOF'
Optionalen Linux-systemd-Autostart für Gusto einrichten:

  ./deploy/install-systemd.sh [--install-dir PFAD] [--data-dir PFAD]
                                [--port N] [--dry-run]

Die normale plattformübergreifende Installation übernimmt install.py. Dieser
Helper ergänzt ausschließlich den Linux-Autostart und funktioniert auch auf
einem Raspberry Pi.
EOF
}

project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
python_bin="${PYTHON_BIN:-python3}"
data_arg="${GUSTO_HOME:-}"
install_arg=""
port=8000
dry_run=0

while (($#)); do
  case "$1" in
    --install-dir)
      [[ $# -ge 2 ]] || { echo "Fehler: --install-dir braucht einen Pfad." >&2; exit 2; }
      install_arg="$2"
      shift 2
      ;;
    --data-dir)
      [[ $# -ge 2 ]] || { echo "Fehler: --data-dir braucht einen Pfad." >&2; exit 2; }
      data_arg="$2"
      shift 2
      ;;
    --port)
      [[ $# -ge 2 && "$2" =~ ^[0-9]{1,5}$ ]] || {
        echo "Fehler: --port braucht eine Zahl zwischen 1 und 65535." >&2
        exit 2
      }
      ((10#$2 >= 1 && 10#$2 <= 65535)) || {
        echo "Fehler: --port braucht eine Zahl zwischen 1 und 65535." >&2
        exit 2
      }
      port="$2"
      shift 2
      ;;
    --dry-run)
      dry_run=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Fehler: unbekannte Option '$1'." >&2
      usage >&2
      exit 2
      ;;
  esac
done

command -v "$python_bin" >/dev/null 2>&1 || {
  echo "Fehler: '$python_bin' wurde nicht gefunden." >&2
  exit 1
}
"$python_bin" -c 'import sys; raise SystemExit(sys.version_info < (3, 10))' || {
  echo "Fehler: Gusto braucht Python 3.10 oder neuer." >&2
  exit 1
}
[[ "$(uname -s)" == "Linux" ]] || {
  echo "Fehler: Dieser Helper ist ausschließlich für Linux/systemd." >&2
  exit 1
}
command -v systemctl >/dev/null 2>&1 || {
  echo "Fehler: systemctl wurde nicht gefunden." >&2
  exit 1
}

if [[ -n "$data_arg" ]]; then
  data_dir="$("$python_bin" -c 'from pathlib import Path; import sys; print(Path(sys.argv[1]).expanduser().resolve())' "$data_arg")"
else
  data_dir="${XDG_DATA_HOME:-$HOME/.local/share}/gusto"
  data_dir="$("$python_bin" -c 'from pathlib import Path; import sys; print(Path(sys.argv[1]).expanduser().resolve())' "$data_dir")"
fi

if [[ -n "$install_arg" ]]; then
  install_dir="$("$python_bin" -c 'from pathlib import Path; import sys; print(Path(sys.argv[1]).expanduser().resolve())' "$install_arg")"
else
  install_dir="$("$python_bin" -c 'from pathlib import Path; print((Path.home() / ".local" / "opt" / "gusto").resolve())')"
fi
service_user="$(id -un)"
service_python="$install_dir/bin/python"
service_template="$project_dir/deploy/gusto.service"
service_preview="$("$python_bin" - "$service_template" "$service_user" "$data_dir" "$service_python" "$port" <<'PY'
from pathlib import Path
import sys

template, user, data, python, port = sys.argv[1:]

def unit_value(value: str) -> str:
    if any(character in value for character in "\0\r\n"):
        raise SystemExit("Fehler: systemd-Werte dürfen keine Steuerzeichen enthalten.")
    return value.replace("%", "%%").replace("\\", "\\\\").replace('"', '\\"')

text = Path(template).read_text(encoding="utf-8")
for marker, value in {
    "@GUSTO_USER@": user,
    "@GUSTO_HOME@": data,
    "@GUSTO_PYTHON@": python,
    "@GUSTO_PORT@": port,
}.items():
    text = text.replace(marker, unit_value(value))
print(text, end="")
PY
)"

echo "Quelle:       $project_dir"
echo "Installation: $install_dir"
echo "Daten:        $data_dir"
echo "Port:         $port"

if ((dry_run)); then
  echo
  "$python_bin" "$project_dir/install.py" --dry-run --venv "$install_dir"
  echo
  echo "Geplante systemd-Unit:"
  printf '%s\n' "$service_preview"
  exit 0
fi

if [[ "${EUID:-$(id -u)}" -eq 0 ]]; then
  echo "Fehler: Bitte als normaler Benutzer und ohne 'sudo' starten." >&2
  echo "Der Helper fragt nur für systemd nach Administratorrechten." >&2
  exit 1
fi

if [[ -n "$data_arg" ]]; then
  GUSTO_HOME="$data_dir" "$python_bin" "$project_dir/install.py" --venv "$install_dir"
else
  "$python_bin" "$project_dir/install.py" --venv "$install_dir"
fi
mkdir -p -- "$data_dir/recipes" "$data_dir/images" "$data_dir/data"

command -v sudo >/dev/null 2>&1 || {
  echo "Fehler: sudo wird für den systemd-Autostart benötigt." >&2
  exit 1
}
service_tmp="$(mktemp)"
trap 'rm -f -- "$service_tmp"' EXIT
printf '%s\n' "$service_preview" >"$service_tmp"
sudo install -m 0644 "$service_tmp" /etc/systemd/system/gusto.service
sudo systemctl daemon-reload
sudo systemctl enable --now gusto.service
sudo systemctl --no-pager --full status gusto.service

host_name="$(hostname)"
echo
echo "Der optionale Linux-Autostart ist aktiv. Öffne:"
echo "  http://${host_name}.local:${port}"
