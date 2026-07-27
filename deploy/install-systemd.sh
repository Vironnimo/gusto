#!/usr/bin/env bash
set -Eeuo pipefail

app_root="${1:-${GUSTO_APP_ROOT:-$HOME/.local/opt/gusto}}"
[[ "${EUID:-$(id -u)}" -ne 0 ]] || {
  echo "Fehler: Gusto-systemd-Integration läuft ohne sudo als Benutzer." >&2
  exit 1
}
command -v systemctl >/dev/null 2>&1 || {
  echo "Fehler: systemctl wurde nicht gefunden." >&2
  exit 1
}
python_bin="${PYTHON_BIN:-python3}"
readarray -t values < <("$python_bin" - "$app_root" <<'PY'
import json, sys
from pathlib import Path
root = Path(sys.argv[1]).expanduser().resolve()
current = json.loads((root / "current.json").read_text(encoding="utf-8"))
state = json.loads((root / "install-state.json").read_text(encoding="utf-8"))
print(Path(current["runtime"]) / "bin" / "python")
print(Path(state["data_dir"]).expanduser().resolve())
print(state.get("host", "0.0.0.0"))
print(int(state.get("port", 8000)))
PY
)
runtime_python="${values[0]}"
data_dir="${values[1]}"
host="${values[2]}"
port="${values[3]}"
unit_dir="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
unit_path="$unit_dir/gusto.service"
mkdir -p -- "$unit_dir"
"$python_bin" - "$unit_path" "$runtime_python" "$data_dir" "$host" "$port" <<'PY'
import sys
from pathlib import Path
unit, python, data, host, port = sys.argv[1:]
def q(value):
    if any(c in value for c in "\0\r\n"):
        raise SystemExit("Steuerzeichen in systemd-Wert")
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%") + '"'
Path(unit).write_text(
    "[Unit]\nDescription=Gusto Rezeptserver\nAfter=network-online.target\n"
    "Wants=network-online.target\n\n[Service]\nType=simple\n"
    f"WorkingDirectory={q(data)}\n"
    f"ExecStart={q(python)} -m gusto serve --host {q(host)} --port {port}\n"
    "Restart=on-failure\nRestartSec=3\n\n[Install]\nWantedBy=default.target\n",
    encoding="utf-8",
)
PY
systemctl --user daemon-reload
systemctl --user enable --now gusto.service
echo "Gusto läuft als systemd-User-Service."
