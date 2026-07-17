#!/usr/bin/env bash
set -Eeuo pipefail

usage() {
  cat <<'EOF'
Gusto auf Linux/Raspberry Pi installieren:

  ./deploy/install.sh [--data-dir PFAD] [--no-service] [--dry-run]

Optionen:
  --data-dir PFAD  Rezepte, Bilder und JSON-Daten dort ablegen.
                   Standard: Projektordner (wie bisher).
  --no-service     Nur virtuelle Umgebung und Gusto installieren.
  --dry-run        Geplante Pfade und die erzeugte systemd-Unit anzeigen.

Optional kann PYTHON_BIN auf einen anderen Python-3.10+-Interpreter zeigen.
EOF
}

project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
python_bin="${PYTHON_BIN:-python3}"
data_arg="${GUSTO_HOME:-$project_dir}"
install_service=1
dry_run=0

while (($#)); do
  case "$1" in
    --data-dir)
      [[ $# -ge 2 ]] || { echo "Fehler: --data-dir braucht einen Pfad." >&2; exit 2; }
      data_arg="$2"
      shift 2
      ;;
    --no-service)
      install_service=0
      shift
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

data_dir="$("$python_bin" -c 'from pathlib import Path; import sys; print(Path(sys.argv[1]).expanduser().resolve())' "$data_arg")"
venv_dir="$project_dir/.venv"
service_user="$(id -un)"
service_python="$venv_dir/bin/python"
service_template="$project_dir/deploy/gusto.service"
service_preview="$("$python_bin" - "$service_template" "$service_user" "$project_dir" "$data_dir" "$service_python" <<'PY'
from pathlib import Path
import sys

template, user, project, data, python = sys.argv[1:]

def unit_value(value: str) -> str:
    return value.replace("%", "%%").replace("\\", "\\\\").replace('"', '\\"')

text = Path(template).read_text(encoding="utf-8")
for marker, value in {
    "@GUSTO_USER@": user,
    "@GUSTO_PROJECT@": project,
    "@GUSTO_HOME@": data,
    "@GUSTO_PYTHON@": python,
}.items():
    text = text.replace(marker, unit_value(value))
print(text, end="")
PY
)"

echo "Projekt: $project_dir"
echo "Daten:   $data_dir"
echo "Python:  $python_bin"

if ((dry_run)); then
  if ((install_service)); then
    echo
    echo "Geplante systemd-Unit:"
    printf '%s\n' "$service_preview"
  fi
  exit 0
fi

if [[ "${EUID:-$(id -u)}" -eq 0 ]]; then
  echo "Fehler: Bitte den Installer als normaler Benutzer und ohne 'sudo' starten." >&2
  echo "Er fragt nur für die systemd-Schritte nach Administratorrechten." >&2
  exit 1
fi

if ! "$python_bin" -m venv "$venv_dir"; then
  echo "Fehler: Die virtuelle Umgebung konnte nicht erstellt werden." >&2
  echo "Auf Raspberry Pi OS hilft meist: sudo apt install python3-venv" >&2
  exit 1
fi

"$service_python" -m pip install -e "$project_dir[web]"
mkdir -p -- "$data_dir/recipes" "$data_dir/images" "$data_dir/data"

if ((!install_service)); then
  echo
  echo "Gusto ist installiert. Start:"
  printf 'GUSTO_HOME=%q %q -m gusto serve\n' "$data_dir" "$service_python"
  exit 0
fi

[[ "$(uname -s)" == "Linux" ]] || {
  echo "Fehler: systemd-Autostart wird nur unter Linux eingerichtet." >&2
  echo "Nutze auf diesem System --no-service." >&2
  exit 1
}
command -v systemctl >/dev/null 2>&1 || {
  echo "Fehler: systemctl wurde nicht gefunden. Nutze --no-service." >&2
  exit 1
}

root_cmd=()
if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
  command -v sudo >/dev/null 2>&1 || {
    echo "Fehler: sudo wird für den systemd-Autostart benötigt." >&2
    exit 1
  }
  root_cmd=(sudo)
fi

service_tmp="$(mktemp)"
trap 'rm -f -- "$service_tmp"' EXIT
printf '%s\n' "$service_preview" >"$service_tmp"
"${root_cmd[@]}" install -m 0644 "$service_tmp" /etc/systemd/system/gusto.service
"${root_cmd[@]}" systemctl daemon-reload
"${root_cmd[@]}" systemctl enable --now gusto.service
"${root_cmd[@]}" systemctl --no-pager --full status gusto.service

host_name="$(hostname)"
echo
echo "Gusto läuft und startet künftig automatisch. Öffne:"
echo "  http://${host_name}.local:8000"
