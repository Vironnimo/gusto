"""Install and exercise the built release exactly as an end user would."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Gebautes Gusto-Release installieren und smoke-testen.",
    )
    parser.add_argument("--release-dir", required=True, type=Path)
    parser.add_argument("--work-dir", required=True, type=Path)
    parser.add_argument("--port", required=True, type=int)
    return parser


def execute(
    command: list[str],
    *,
    environment: dict[str, str],
    log_path: Path,
    expected: tuple[int, ...] = (0,),
) -> subprocess.CompletedProcess[str]:
    printable = subprocess.list2cmdline(command)
    print(f"$ {printable}", flush=True)
    result = subprocess.run(
        command,
        cwd=ROOT,
        env=environment,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
    )
    with log_path.open("a", encoding="utf-8") as log:
        log.write(f"$ {printable}\n")
        log.write(result.stdout)
        log.write(result.stderr)
        log.write(f"\n[exit {result.returncode}]\n\n")
    if result.stdout:
        print(result.stdout, end="")
    if result.stderr:
        print(result.stderr, end="", file=sys.stderr)
    if result.returncode not in expected:
        raise RuntimeError(
            f"Befehl endete mit Status {result.returncode}: {printable}"
        )
    return result


def as_json(result: subprocess.CompletedProcess[str]) -> Any:
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise RuntimeError(
            f"Ungültige JSON-Ausgabe: {result.stdout!r}"
        ) from error


def wait_removed(path: Path, timeout: float = 20) -> None:
    deadline = time.monotonic() + timeout
    while path.exists() and time.monotonic() < deadline:
        time.sleep(0.1)
    if path.exists():
        raise RuntimeError(f"Deinstallation ließ den App-Root zurück: {path}")


def get_json(url: str) -> Any:
    with urllib.request.urlopen(url, timeout=10) as response:
        if response.status != 200:
            raise RuntimeError(f"{url} antwortete mit HTTP {response.status}.")
        return json.load(response)


def get_text(url: str) -> str:
    with urllib.request.urlopen(url, timeout=10) as response:
        if response.status != 200:
            raise RuntimeError(f"{url} antwortete mit HTTP {response.status}.")
        return response.read().decode("utf-8")


def prepare_linux_service_adapter(
    work_dir: Path,
    environment: dict[str, str],
) -> None:
    shim_dir = work_dir / "systemctl-shim"
    shim_dir.mkdir(parents=True)
    shim = shim_dir / "systemctl"
    shutil.copy2(ROOT / "scripts" / "ci_systemctl.py", shim)
    shim.chmod(0o755)
    environment["PATH"] = os.pathsep.join(
        (os.fspath(shim_dir), environment.get("PATH", "")),
    )
    config_home = Path(
        environment.get("XDG_CONFIG_HOME")
        or Path(environment["HOME"]) / ".config"
    )
    environment["GUSTO_CI_UNIT_PATH"] = os.fspath(
        config_home / "systemd/user/gusto.service"
    )
    environment["GUSTO_CI_PID_FILE"] = os.fspath(
        work_dir / "gusto-service.pid"
    )
    environment["GUSTO_CI_SERVICE_LOG"] = os.fspath(
        work_dir / "logs/gusto-service.log"
    )


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if os.environ.get("GITHUB_ACTIONS") != "true":
        print(
            "Fehler: Der Installations-Smoke-Test verändert User-Autostart "
            "und läuft deshalb ausschließlich auf einem frischen "
            "GitHub-Actions-Runner.",
            file=sys.stderr,
        )
        return 2
    if not 1 <= args.port <= 65535:
        print("Fehler: --port muss zwischen 1 und 65535 liegen.",
              file=sys.stderr)
        return 2

    release_dir = args.release_dir.resolve()
    required = {
        "gusto-release.zip",
        "gusto-release.zip.sha256",
        "gusto-release.json",
        "install.ps1",
        "install.sh",
    }
    missing = sorted(name for name in required
                     if not (release_dir / name).is_file())
    if missing:
        print(f"Fehler: Release-Assets fehlen: {', '.join(missing)}",
              file=sys.stderr)
        return 2

    work_dir = args.work_dir.resolve()
    if work_dir.exists():
        shutil.rmtree(work_dir)
    logs = work_dir / "logs"
    logs.mkdir(parents=True)
    app_dir = work_dir / "Gusto App"
    data_dir = work_dir / "Gusto Data"
    fake_home = work_dir / "User Home"
    fake_home.mkdir()
    command_log = logs / "commands.log"
    environment = os.environ.copy()
    environment["PYTHONUTF8"] = "1"
    environment["PIP_DISABLE_PIP_VERSION_CHECK"] = "1"
    environment["PIP_PROGRESS_BAR"] = "off"
    environment["GUSTO_CI_SMOKE"] = "1"
    base_url = f"http://127.0.0.1:{args.port}"

    wrapper: Path | None = None
    install_complete = False
    try:
        if sys.platform.startswith("win"):
            execute(
                [
                    "powershell.exe", "-NoProfile", "-NonInteractive",
                    "-Command",
                    "if (Get-ScheduledTask -TaskName 'Gusto' "
                    "-ErrorAction SilentlyContinue) { exit 9 }",
                ],
                environment=environment,
                log_path=command_log,
            )
            execute(
                [
                    "powershell.exe", "-NoProfile", "-NonInteractive",
                    "-ExecutionPolicy", "Bypass", "-File",
                    os.fspath(release_dir / "install.ps1"),
                    "-ReleaseBase", os.fspath(release_dir),
                    "-InstallDir", os.fspath(app_dir),
                    "-DataDir", os.fspath(data_dir),
                    "-HostName", "127.0.0.1",
                    "-Port", str(args.port),
                    "-Json",
                ],
                environment=environment,
                log_path=command_log,
            )
            wrapper = app_dir / "bin/gusto.cmd"
            execute(
                [
                    "powershell.exe", "-NoProfile", "-NonInteractive",
                    "-Command",
                    "$task=Get-ScheduledTask -TaskName 'Gusto' "
                    "-ErrorAction Stop;"
                    "if($task.Principal.RunLevel -ne 'Limited' "
                    "-or $task.Principal.LogonType -ne 'Interactive'){exit 10};"
                    "if(-not (Test-Path "
                    "'HKCU:\\Software\\Microsoft\\Windows\\CurrentVersion"
                    "\\Uninstall\\Gusto')){exit 11}",
                ],
                environment=environment,
                log_path=command_log,
            )
        else:
            environment["HOME"] = os.fspath(fake_home)
            environment["XDG_DATA_HOME"] = os.fspath(fake_home / ".local/share")
            environment["XDG_CONFIG_HOME"] = os.fspath(fake_home / ".config")
            prepare_linux_service_adapter(work_dir, environment)
            execute(
                [
                    "bash", os.fspath(release_dir / "install.sh"),
                    "--release-base", os.fspath(release_dir),
                    "--install-dir", os.fspath(app_dir),
                    "--data-dir", os.fspath(data_dir),
                    "--host", "127.0.0.1",
                    "--port", str(args.port),
                    "--json",
                ],
                environment=environment,
                log_path=command_log,
            )
            wrapper = app_dir / "bin/gusto"

        install_complete = True
        if not wrapper.is_file():
            raise RuntimeError(f"Stabiler Gusto-Wrapper fehlt: {wrapper}")

        manifest = json.loads(
            (release_dir / "gusto-release.json").read_text(encoding="utf-8")
        )
        version = manifest["version"]
        health = get_json(base_url + "/api/v1/health")
        if health.get("version") != version:
            raise RuntimeError(
                f"Health meldet {health.get('version')!r} statt {version!r}."
            )
        if Path(health["data_path"]).resolve() != data_dir.resolve():
            raise RuntimeError("Health meldet nicht den installierten Datenpfad.")
        home_page = get_text(base_url + "/")
        if "Gusto" not in home_page:
            raise RuntimeError("Installierte Browser-UI enthält die Marke nicht.")
        if "serviceWorker" not in home_page:
            raise RuntimeError("Installierte PWA registriert keinen Service Worker.")
        service_worker = get_text(base_url + "/sw.js")
        if 'const CACHE = "gusto-' not in service_worker:
            raise RuntimeError("Installierter PWA-Service-Worker ist unvollständig.")

        home = as_json(execute(
            [os.fspath(wrapper), "home", "--json"],
            environment=environment,
            log_path=command_log,
        ))
        if not home.get("server_reachable"):
            raise RuntimeError("Installierte CLI erreicht ihren Gusto-Dienst nicht.")
        if Path(home["server_data_path"]).resolve() != data_dir.resolve():
            raise RuntimeError("CLI und Dienst verwenden nicht denselben Datenpfad.")

        skill_environment = environment.copy()
        skill_environment["HOME"] = os.fspath(fake_home)
        skill_environment["USERPROFILE"] = os.fspath(fake_home)
        (fake_home / ".vbot").mkdir(exist_ok=True)
        skill_result = as_json(execute(
            [os.fspath(wrapper), "install-skill", "vbot", "--json"],
            environment=skill_environment,
            log_path=command_log,
        ))
        installed_skill = fake_home / ".vbot/skills/gusto/SKILL.md"
        if (skill_result.get("status") != "installed"
                or skill_result.get("overwritten") is not False
                or not installed_skill.is_file()):
            raise RuntimeError("Installierte CLI liefert den vBot-Skill nicht aus.")
        installed_skill.write_text("stale", encoding="utf-8")
        replaced_skill = as_json(execute(
            [os.fspath(wrapper), "install-skill", "vbot", "--json"],
            environment=skill_environment,
            log_path=command_log,
        ))
        if (replaced_skill.get("overwritten") is not True
                or installed_skill.read_text(encoding="utf-8") == "stale"):
            raise RuntimeError("Installierte CLI ersetzt keinen veralteten vBot-Skill.")

        created = as_json(execute(
            [os.fspath(wrapper), "new", "CI Smoke Rezept", "--json"],
            environment=environment,
            log_path=command_log,
        ))
        slug = created["slug"]
        shown = as_json(execute(
            [os.fspath(wrapper), "show", slug, "--json"],
            environment=environment,
            log_path=command_log,
        ))
        if shown.get("title") != "CI Smoke Rezept":
            raise RuntimeError("CLI-Roundtrip über den installierten Dienst scheiterte.")

        status = as_json(execute(
            [os.fspath(wrapper), "status", "--app-root",
             os.fspath(app_dir), "--json"],
            environment=environment,
            log_path=command_log,
        ))
        if not status.get("running"):
            raise RuntimeError("Installierter User-Dienst läuft nicht.")

        execute(
            [os.fspath(wrapper), "restart", "--app-root",
             os.fspath(app_dir), "--json"],
            environment=environment,
            log_path=command_log,
        )
        if get_json(base_url + "/api/v1/health").get("version") != version:
            raise RuntimeError("Dienst ist nach dem Neustart nicht gesund.")

        update = as_json(execute(
            [
                os.fspath(wrapper), "update", "--check",
                "--app-root", os.fspath(app_dir),
                "--manifest-url",
                os.fspath(release_dir / "gusto-release.json"),
                "--json",
            ],
            environment=environment,
            log_path=command_log,
        ))
        if update.get("status") != "current":
            raise RuntimeError("Frisch installiertes Release gilt nicht als aktuell.")

        execute(
            [os.fspath(wrapper), "uninstall", "--keep-data", "--json"],
            environment=environment,
            log_path=command_log,
        )
        wait_removed(app_dir)
        install_complete = False
        if not data_dir.is_dir():
            raise RuntimeError("App-Deinstallation hat die Nutzdaten entfernt.")
        print(
            f"SMOKE PASSED: {sys.platform}, Gusto {version}, "
            "Installation/Dienst/CLI/UI/Update/Uninstall",
        )
        return 0
    except Exception as error:
        print(f"SMOKE FAILED: {error}", file=sys.stderr)
        return 1
    finally:
        if install_complete and wrapper is not None and wrapper.exists():
            try:
                execute(
                    [os.fspath(wrapper), "uninstall", "--keep-data", "--json"],
                    environment=environment,
                    log_path=command_log,
                )
                wait_removed(app_dir)
            except Exception as cleanup_error:
                print(f"Cleanup fehlgeschlagen: {cleanup_error}",
                      file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
