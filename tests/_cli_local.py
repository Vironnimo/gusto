"""Local CLI lifecycle dispatch, serve output and dependency preflight."""
from __future__ import annotations

import io
import json
from contextlib import redirect_stdout
from types import SimpleNamespace
from unittest.mock import patch


from _cli_support import (
    check,
)

def check_serve_json_contract():
    from gusto import cli

    calls = []
    fake_uvicorn = SimpleNamespace(
        run=lambda *args, **kwargs: calls.append((args, kwargs)),
    )
    for host, local_host in (
        ("0.0.0.0", "127.0.0.1"),
        ("::", "[::1]"),
        ("2001:db8::1", "[2001:db8::1]"),
    ):
        output = io.StringIO()
        with patch.object(cli, "_load_web_runtime", return_value=fake_uvicorn):
            with redirect_stdout(output):
                cli.main(["serve", "--host", host, "--port", "8765", "--reload", "--json"])
        payload = json.loads(output.getvalue())
        check(payload["status"] == "starting"
              and payload["url"] == f"http://{local_host}:8765"
              and payload["reload"] is True
              and calls[-1][1]["host"] == host
              and calls[-1][1]["port"] == 8765
              and calls[-1][1]["reload"] is True,
              "serve --json must preserve its bind/reload and print a local IPv4/IPv6 URL")


def check_serve_dependency_preflight():
    from gusto import cli

    imported = []

    def fake_import(module_name):
        imported.append(module_name)
        if module_name in {"markdown", "PIL.Image"}:
            raise ModuleNotFoundError(
                f"No module named '{module_name}'", name=module_name,
            )
        return SimpleNamespace()

    from _cli_support import invoke

    # Trigger real preflight imports through the public command; no helper/order pin.
    with patch.object(cli.importlib, "import_module", side_effect=fake_import):
        result = invoke(["serve", "--json"])
    payload = json.loads(result.stdout)
    check(result.returncode == 1 and not result.stderr,
          "missing web dependencies must be a clean JSON command failure")
    check(set(imported) >= {"fastapi", "uvicorn", "jinja2", "markdown",
                            "python_multipart", "PIL.Image"},
          "serve preflight must check the complete dependency set")
    check(payload.get("ok") is False and bool(payload.get("error"))
          and all(package in payload["error"] for package in ("markdown", "pillow"))
          and 'python -m pip install -e ".[web]"' in payload["error"]
          and "--repair" in payload["error"] and "--cli-only" not in payload["error"],
          "serve failure must identify every missing package and both recovery commands")
    check(payload.get("status") is None,
          "serve must not report startup before dependency validation succeeds")


def check_lifecycle_contract():
    from gusto import cli, service, skill_install, update

    status_result = {
        "ok": True,
        "action": "status",
        "status": "running",
        "running": True,
        "version": "1.2.3",
        "url": "http://127.0.0.1:8000",
        "command": None,
    }
    from pathlib import Path
    from _cli_support import HOME, invoke

    for action in ("status", "start", "stop", "restart"):
        app_root = HOME / "custom application"
        with patch.object(service, "service_action", return_value=status_result) as service_action, \
                patch.object(cli.client, "command",
                             side_effect=AssertionError("local action used the server API")):
            output = invoke(["--server", "http://ignored.invalid", action,
                             "--app-root", str(app_root), "--dry-run", "--json"])
        check(output.returncode == 0 and not output.stderr
              and json.loads(output.stdout) == status_result,
              f"{action} must preserve local lifecycle JSON and ignore the remote server")
        check(service_action.call_args.args == (action,)
              and service_action.call_args.kwargs == {"app_root": Path(app_root), "dry_run": True},
              f"{action} must forward its explicit local installation and dry-run flag")

    skill_result = {
        "status": "dry_run",
        "host": "vbot",
        "gusto_version": "1.2.3",
        "source": "C:/runtime/share/gusto/skill/gusto",
        "destination": "C:/Users/test/.vbot/skills/gusto",
        "overwritten": True,
        "files": ["SKILL.md"],
    }
    output = io.StringIO()
    with patch.object(
        skill_install, "install_skill", return_value=skill_result,
    ) as install_skill:
        with patch.object(
            cli.client, "command",
            side_effect=AssertionError("install-skill used the server API"),
        ):
            with redirect_stdout(output):
                cli.main(["install-skill", "vbot", "--dry-run", "--json"])
    check(json.loads(output.getvalue()) == skill_result
          and install_skill.call_args.args == ("vbot",)
          and install_skill.call_args.kwargs == {"dry_run": True},
          "install-skill must stay local, explicit and machine-readable")

    output = io.StringIO()
    unsupported_exit = None
    with redirect_stdout(output):
        try:
            cli.main(["install-skill", "codex", "--json"])
        except SystemExit as error:
            unsupported_exit = error.code
    unsupported = json.loads(output.getvalue())
    check(unsupported_exit == 1
          and unsupported["ok"] is False
          and "Unterstützt: vbot" in unsupported["error"],
          "unsupported skill hosts must use the normal JSON failure contract")

    update_result = {
        "ok": True,
        "status": "update_available",
        "current_version": "1.2.3",
        "latest_version": "1.3.0",
        "update_available": True,
        "manifest_url": "https://example.invalid/release.json",
    }
    output = io.StringIO()
    with patch.object(update, "run_update", return_value=update_result) as run_update:
        with redirect_stdout(output):
            cli.main([
                "update", "--check", "--json",
                "--manifest-url", "https://example.invalid/release.json",
            ])
    check(json.loads(output.getvalue()) == update_result
          and run_update.call_args.kwargs["check"] is True,
          "update --check must stay local and machine-readable")

    failure_result = {
        "ok": False,
        "status": "update_failed",
        "error": "activation failed",
        "rollback": True,
        "current_version": "1.2.3",
        "attempted_version": "1.3.0",
    }
    output = io.StringIO()
    failure = update.UpdateError("activation failed", result=failure_result)
    with patch.object(update, "run_update", side_effect=failure):
        try:
            with redirect_stdout(output):
                cli.main(["update", "--json"])
        except SystemExit as error:
            exit_code = error.code
        else:
            exit_code = 0
    check(exit_code == 1 and json.loads(output.getvalue()) == failure_result,
          "update rollback failures must preserve their structured result")


