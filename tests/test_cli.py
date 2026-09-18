"""Hermetic smoke checks for the agent-facing JSON CLI."""
from __future__ import annotations

import base64
import atexit
import importlib
import io
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from contextlib import redirect_stderr, redirect_stdout
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from urllib import request


ROOT = Path(__file__).resolve().parent.parent
HOME = Path(tempfile.mkdtemp(prefix="gusto-cli-test-"))
ENV = {**os.environ, "GUSTO_HOME": os.fspath(HOME)}
sys.path.insert(0, os.fspath(ROOT))
checks = 0
SERVER = None


def free_port():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


PORT = free_port()
SERVER_URL = f"http://127.0.0.1:{PORT}"


def check(condition, message):
    global checks
    assert condition, message
    checks += 1


def run(*arguments, expect=0, input_text=None):
    result = subprocess.run(
        [
            sys.executable, "-m", "gusto", *arguments,
            "--server", SERVER_URL,
        ],
        cwd=ROOT, env=ENV, input=input_text,
        text=True, encoding="utf-8", capture_output=True,
    )
    check(result.returncode == expect,
          f"{' '.join(arguments)} returned {result.returncode}: {result.stderr}")
    return result


def as_json(*arguments):
    return json.loads(run(*arguments, "--json").stdout)


def as_json_error(*arguments):
    result = run(*arguments, "--json", expect=1)
    check(result.stderr == "", "JSON command errors must not emit plain stderr text")
    payload = json.loads(result.stdout)
    check(payload.get("ok") is False and isinstance(payload.get("error"), str),
          "JSON command errors must use the documented error object")
    return payload


def stop_server():
    global SERVER
    if SERVER is None or SERVER.poll() is not None:
        return
    SERVER.terminate()
    try:
        SERVER.wait(timeout=10)
    except subprocess.TimeoutExpired:
        SERVER.kill()
        SERVER.wait(timeout=10)


def start_server():
    global SERVER
    SERVER = subprocess.Popen(
        [
            sys.executable, "-m", "gusto", "serve",
            "--host", "127.0.0.1", "--port", str(PORT),
        ],
        cwd=ROOT, env=ENV,
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        text=True, encoding="utf-8",
    )
    atexit.register(stop_server)
    deadline = time.monotonic() + 15
    last_error = None
    while time.monotonic() < deadline:
        if SERVER.poll() is not None:
            diagnostics = SERVER.stderr.read() if SERVER.stderr else ""
            raise AssertionError(
                f"isolated Gusto server exited with {SERVER.returncode}: {diagnostics}"
            )
        try:
            with request.urlopen(
                f"{SERVER_URL}/api/v1/health", timeout=0.5,
            ) as response:
                payload = json.loads(response.read().decode("utf-8"))
            if isinstance(payload, dict):
                return
        except Exception as error:
            last_error = error
            time.sleep(0.05)
    stop_server()
    raise AssertionError(f"isolated Gusto server did not become healthy: {last_error}")


def store_snapshot():
    return {
        os.fspath(path.relative_to(HOME)): path.read_bytes()
        for path in HOME.rglob("*")
        if path.is_file()
    }


def check_client_contract():
    from gusto import client

    settings = HOME / "client-settings.json"
    settings.write_text(
        json.dumps({
            "data_dir": os.fspath(HOME),
            "server_url": "http://settings.example:8123/",
        }),
        encoding="utf-8",
    )
    check(
        client.resolve_server_url(
            "http://explicit.example:9000/",
            environ={"GUSTO_URL": "http://environment.example:8001"},
            settings_path=settings,
        ) == "http://explicit.example:9000",
        "--server must have the highest discovery priority",
    )
    check(
        client.resolve_server_url(
            environ={"GUSTO_URL": "http://environment.example:8001"},
            settings_path=settings,
        ) == "http://environment.example:8001",
        "GUSTO_URL must override instance settings",
    )
    check(
        client.resolve_server_url(environ={}, settings_path=settings)
        == "http://settings.example:8123",
        "instance settings must provide the server URL",
    )
    try:
        client.resolve_server_url("", environ={})
    except client.ClientError as error:
        check("Ungültige Gusto-Server-URL aus --server" in str(error),
              "an empty --server must fail instead of falling through")
    else:
        raise AssertionError("an empty --server value was silently ignored")
    check(
        client.resolve_server_url(
            environ={"GUSTO_URL": ""}, settings_path=settings,
        ) == "http://settings.example:8123",
        "an empty GUSTO_URL must keep behaving like an unset variable",
    )
    image = HOME / "attachment.bin"
    image.write_bytes(b"\x00gusto\xff")
    attachment = client.encode_attachment("image", image)
    check(
        attachment == {
            "name": "image",
            "filename": "attachment.bin",
            "content_base64": base64.b64encode(b"\x00gusto\xff").decode("ascii"),
        },
        "attachments must use the versioned Base64 envelope",
    )
    attachment_folder = HOME / "attachment-folder"
    attachment_folder.mkdir()
    for description, rejected in (
        ("directories", attachment_folder),
        ("missing paths", HOME / "missing-attachment.bin"),
    ):
        try:
            client.encode_attachment("image", rejected)
        except client.ClientError as error:
            check("keine reguläre Datei" in str(error),
                  f"encode_attachment must reject {description} with a clear message")
        else:
            raise AssertionError(f"encode_attachment accepted {description}")
    captured = []

    def fake_request(method, url, *, payload=None, timeout=None):
        captured.append((method, url, payload, timeout))
        return {"ok": True, "result": {"slug": "test"}}

    with patch.object(client, "_request_json", side_effect=fake_request):
        result = client.command(
            "recipe.show", {"slug": "test"},
            attachments=[attachment], server_url="http://gusto.local:9000",
        )
    check(result == {"slug": "test"}
          and captured[0][0:2] == (
              "POST", "http://gusto.local:9000/api/v1/command",
          )
          and captured[0][2] == {
              "operation": "recipe.show",
              "arguments": {"slug": "test"},
              "attachments": [attachment],
          }, "client command must send and unwrap the fixed API envelope")
    with patch.object(
        client, "_request_json",
        return_value={"ok": False, "error": "remote failure"},
    ):
        try:
            client.command("catalog.list", server_url="http://gusto.local")
        except client.ClientError as error:
            check(str(error) == "remote failure",
                  "remote command errors must retain their API message")
        else:
            raise AssertionError("remote command failure was not raised")
    timeouts = []

    def timing_request(method, url, *, payload=None, timeout=None):
        timeouts.append(timeout)
        return {"ok": True, "result": {"slug": "test"}}

    heavy = HOME / "heavy-attachment.bin"
    heavy.write_bytes(bytes(1024 * 1024))
    with patch.object(client, "_request_json", side_effect=timing_request):
        client.command(
            "recipe.show", {"slug": "test"},
            attachments=[client.encode_attachment("image", heavy)],
            server_url="http://gusto.local:9000",
        )
    check(
        client.DEFAULT_TIMEOUT_SECONDS < timeouts[0]
        <= client.DEFAULT_TIMEOUT_SECONDS + 2.0,
        "large attachments must scale the command timeout instead of "
        "failing as unreachable",
    )
    with patch.object(
        client, "_request_json", return_value={"ok": True, "version": "1.2.3"},
    ):
        check(client.health("http://gusto.local")["version"] == "1.2.3",
              "health must accept the documented ok=true response")
    with patch.object(
        client, "_request_json", return_value={"ok": False, "error": "startet"},
    ):
        try:
            client.health("http://gusto.local")
        except client.ClientError as error:
            check(str(error) == "startet",
                  "health must retain an explicit ok=false error message")
        else:
            raise AssertionError("health accepted an ok=false response")
    with patch.object(
        client, "_request_json", return_value={"status": "ready"},
    ):
        try:
            client.health("http://gusto.local")
        except client.ClientError as error:
            check("Health" in str(error),
                  "health must reject a body without an ok field")
        else:
            raise AssertionError("health accepted a response without ok")


def check_serve_json_contract():
    from gusto import cli

    calls = []
    fake_uvicorn = SimpleNamespace(
        run=lambda *args, **kwargs: calls.append((args, kwargs)),
    )
    output = io.StringIO()
    with patch.object(cli, "_load_web_runtime", return_value=fake_uvicorn):
        with redirect_stdout(output):
            cli.cmd_serve(SimpleNamespace(
                host="0.0.0.0", port=8765, reload=True, json=True,
            ))
    payload = json.loads(output.getvalue())
    check(payload["status"] == "starting"
          and payload["url"] == "http://127.0.0.1:8765"
          and payload["reload"] is True
          and calls[0][1]["port"] == 8765
          and calls[0][1]["reload"] is True,
          "serve --json must preserve reload and emit startup before serving")


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

    with patch.object(cli.importlib, "import_module", side_effect=fake_import):
        missing = cli._missing_web_dependencies()

    check(missing == ["markdown", "pillow"],
          "serve preflight must report every unavailable web dependency")
    check(imported == [
        "fastapi", "uvicorn", "jinja2", "markdown",
        "python_multipart", "PIL.Image",
    ], "serve preflight must validate every declared web dependency")

    stdout = io.StringIO()
    stderr = io.StringIO()
    with patch.object(
        cli, "_missing_web_dependencies", return_value=["markdown", "pillow"],
    ):
        try:
            with redirect_stdout(stdout), redirect_stderr(stderr):
                cli.main(["serve", "--json"])
        except SystemExit as error:
            exit_code = error.code
        else:
            exit_code = 0

    payload = json.loads(stdout.getvalue())
    check(exit_code == 1 and stderr.getvalue() == "",
          "missing web dependencies must be a clean JSON command failure")
    check(payload.get("ok") is False
          and "markdown, pillow" in payload.get("error", "")
          and 'python -m pip install -e ".[web]"' in payload["error"]
          and "python install.py (ohne --cli-only)" in payload["error"],
          "serve dependency errors must list packages and both install paths")
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
    output = io.StringIO()
    with patch.object(service, "service_action", return_value=status_result):
        with patch.object(
            cli.client, "command",
            side_effect=AssertionError("lifecycle command used the server API"),
        ):
            with redirect_stdout(output):
                cli.main(["status", "--json"])
    check(json.loads(output.getvalue()) == status_result,
          "status must be a local JSON-capable lifecycle command")

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


def check_edit_readback_contract():
    from gusto import cli

    def non_utf8_editor(path, *, quiet=False):
        Path(path).write_bytes(b"# Kaputt\n\n- Zutat \xe4\n")
        return {"editor": "fake", "path": os.fspath(Path(path)), "exit_code": 0}

    def show_only(_args, operation, _arguments=None, *, attachments=None):
        check(operation == "recipe.show",
              "a non-UTF-8 editor result must not be uploaded")
        return {"slug": "edit-readback", "content": "# Ok\n"}

    with patch.object(cli, "_open_editor", side_effect=non_utf8_editor):
        with patch.object(cli, "_remote", side_effect=show_only):
            try:
                cli._edit_remote(
                    SimpleNamespace(server=None, json=True), "edit-readback",
                )
            except cli.client.ClientError as error:
                check("UTF-8" in str(error) and "codec" not in str(error),
                      "edit must report a non-UTF-8 editor result in German")
            except UnicodeDecodeError:
                raise AssertionError(
                    "edit leaked a raw UnicodeDecodeError for non-UTF-8 content"
                ) from None
            else:
                raise AssertionError("non-UTF-8 editor content was accepted")


def main():
    check_client_contract()
    importlib.import_module("gusto.cli")
    check("gusto.core" not in sys.modules,
          "importing the normal CLI client must not import local persistence")
    check_lifecycle_contract()
    check_edit_readback_contract()
    start_server()
    home = as_json("home")
    check(Path(home["path"]).resolve() == HOME.resolve()
          and home["source"] == "environment",
          "home --json must explain the active GUSTO_HOME data directory")
    check(home["server_url"] == SERVER_URL and home["server_reachable"] is True,
          "home --json must report server discovery and reachability separately")
    check(Path(home["server_data_path"]).resolve() == HOME.resolve()
          and isinstance(home["server_revision"], int)
          and home["server_version"],
          "home --json must identify the reachable server-owned store/version")
    check_serve_json_contract()
    check_serve_dependency_preflight()
    invalid_port = run("serve", "--port", "70000", expect=2)
    check("zwischen 1 und 65535" in invalid_port.stderr,
          "serve must reject ports outside the TCP range")

    created = as_json(
        "new", "Test Suppe", "--tags", "vegan,schnell",
        "--duration", "25", "--servings", "4",
    )
    slug = created["slug"]
    check(slug == "test-suppe", "new --json must return the generated slug")
    check(created["warnings"] == [{
        "code": "uncategorized_tags",
        "message": "Tags ohne Kategorie (Facet „Sonstige“): schnell, vegan",
        "tags": ["schnell", "vegan"],
    }], "new --json must identify tags without a named facet")
    check(not (HOME / "data" / "categories.json").exists(),
          "creating a recipe must not create an ineffective empty category file")
    human_warning = run("set", slug, "--tags", "vegan,schnell")
    check("Warnung: Tags ohne Kategorie" in human_warning.stdout,
          "human set output must warn about tags without a named facet")

    diet = as_json("categories", "add", "diet", "Ernährung")
    trait = as_json("categories", "add", "trait", "Merkmal")
    check(diet["key"] == "diet" and trait["key"] == "trait",
          "categories add must create server-owned facets")
    as_json("categories", "assign", "diet", "vegan")
    assigned = as_json(
        "categories", "assign", "trait", "schnell", "einfach", "SCHNELL",
    )
    check(assigned["tags"] == ["schnell", "einfach"],
          "categories assign must resolve warnings without duplicate tags")
    moved_tag = as_json("categories", "tag-move", "trait", "einfach", "1")
    check(moved_tag["tags"] == ["einfach", "schnell"],
          "categories tag-move must expose deterministic display order")
    moved_category = as_json(
        "categories", "set", "trait", "--label", "Eigenschaft",
        "--position", "1",
    )
    check(moved_category["label"] == "Eigenschaft"
          and as_json("categories", "list")[0]["key"] == "trait",
          "categories set/list must expose label and facet order")
    unassigned = as_json("categories", "unassign", "schnell")
    check(unassigned == {"unassigned": ["schnell"]},
          "categories unassign must explicitly move a tag to Sonstige")
    as_json("categories", "assign", "trait", "schnell")
    check(as_json("check")["uncategorized_tags"] == [],
          "an agent must be able to resolve every uncategorized-tag warning")
    removed_category = as_json("categories", "remove", "diet")
    check(removed_category["now_uncategorized"] == ["vegan"],
          "categories remove must report used tags moved to Sonstige")
    as_json("categories", "add", "diet", "Ernährung")
    as_json("categories", "assign", "diet", "vegan")
    check("Keine Tag-Kategorie" in as_json_error(
        "categories", "assign", "missing", "saisonal",
    )["error"], "category mutations must return structured unknown-key errors")
    list_help = run("list", "--help")
    check("ohne Dauerangabe werden ausgeschlossen"
          in " ".join(list_help.stdout.split()),
          "list help must explain how unknown durations are filtered")

    listed = as_json("list")
    check(len(listed) == 1 and listed[0]["title"] == "Test Suppe",
          "list --json must return recipes")

    shown = as_json("show", slug)
    check(shown["content"].startswith("# Test Suppe"),
          "show --json must include Markdown content")

    changed = as_json(
        "set", slug, "--title", "Neue Suppe", "--tags", "vegan,schnell",
        "--duration", "30",
    )
    check(changed["title"] == "Neue Suppe" and changed["duration_min"] == 30,
          "set --json must return updated metadata")
    check(as_json("show", slug)["content"].startswith("# Neue Suppe\n"),
          "set --title must keep the Markdown H1 synchronized")
    check("warnings" not in changed,
          "set --tags must stop warning after an agent assigns every facet")
    cleared = as_json("set", slug, "--clear-duration", "--clear-servings")
    check(cleared["duration_min"] is None and cleared["servings"] is None,
          "set must be able to remove optional duration and servings")
    check(as_json("list", "--max-time", "60") == [],
          "list --max-time must exclude a recipe with unknown duration")
    check("positive ganze Zahl" in as_json_error(
        "list", "--max-time", "0",
    )["error"], "list must reject a zero time cap")
    check("positive ganze Zahl" in as_json_error(
        "search", "Suppe", "--max-time", "-1",
    )["error"], "search must reject a negative time cap")
    no_change = as_json_error("set", slug)
    check("mindestens eine Änderung" in no_change["error"],
          "set without mutation flags must fail explicitly")
    invalid = run("new", "Unmögliche Suppe", "--duration", "0", expect=1)
    check("positive ganze Zahl" in invalid.stderr,
          "invalid recipe numbers must fail through the CLI")

    first_photo = HOME / "cover.png"
    second_photo = HOME / "step.png"
    pixel = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
    )
    first_photo.write_bytes(pixel)
    second_photo.write_bytes(pixel)
    cover = as_json(
        "image", "add", slug, os.fspath(first_photo), "--role", "result",
        "--caption", "Fertige Suppe",
    )
    check(cover["is_cover"] is True and cover["role"] == "result",
          "the first image added through the CLI must become the cover")
    step = as_json(
        "image", "add", slug, os.fspath(second_photo), "--role", "step",
        "--caption", "Beim Kochen",
    )
    listed_images = as_json("image", "list", slug)
    check(len(listed_images["images"]) == 2
          and listed_images["cover_image_id"] == cover["id"],
          "image list --json must expose all images and the cover")
    updated_image = as_json(
        "image", "set", slug, step["id"], "--role", "ingredients",
        "--caption", "Vorbereitung",
    )
    check(updated_image["role"] == "ingredients"
          and updated_image["caption"] == "Vorbereitung",
          "image set --json must update free role and caption")
    selected = as_json("image", "cover", slug, step["id"])
    check(selected["cover_image_id"] == step["id"],
          "image cover --json must select any stored image")
    removed_image = as_json("image", "remove", slug, step["id"])
    check(removed_image["removed"] is True
          and removed_image["cover_image_id"] == cover["id"],
          "removing the cover must return the fallback cover")

    cooked = as_json("cooked", slug, "--date", "2026-07-13")
    check(cooked == {"slug": slug, "date": "2026-07-13", "ok": True},
          "cooked --json must confirm the log entry")
    check(as_json("log") == [{"date": "2026-07-13", "slug": slug}],
          "log --json must return the written entry")
    bad_date = as_json_error("cooked", slug, "--date", "2026-13-45")
    check("Kalenderdatum" in bad_date["error"],
          "cooked must reject impossible calendar dates as JSON")
    future_date = (date.today() + timedelta(days=1)).isoformat()
    future = as_json_error("cooked", slug, "--date", future_date)
    check("Zukunft" in future["error"],
          "cooked must reject future dates as JSON")
    check(as_json("suggest", "--limit", "0") == [],
          "suggest --limit 0 must return an empty array")
    zero_suggest = run("suggest", "--limit", "0")
    check("--limit 0" in zero_suggest.stdout,
          "human suggest output must explain an explicitly empty limit")
    limit_error = as_json_error("suggest", "--limit", "-1")
    check("nichtnegative" in limit_error["error"],
          "suggest must reject negative limits")
    check("positive ganze Zahl" in as_json_error("log", "--days", "0")["error"],
          "log must reject a zero day range")
    check("positive ganze Zahl" in as_json_error(
        "suggest", "--days", "-1",
    )["error"], "suggest must reject a negative day range")

    recipe_source = HOME / "updated-recipe.md"
    recipe_source.write_text(
        "# Neue Suppe\n\n## Zutaten\n\n- Wasser\n- Salz\n", encoding="utf-8",
    )
    content_result = as_json(
        "content", "set", slug, "--file", os.fspath(recipe_source),
    )
    check(content_result["slug"] == slug,
          "content set --file must transfer Markdown through the API")
    stdin_content = "# Neue Suppe\n\n## Zutaten\n\n- Wasser\n- Salz\n"
    stdin_result = json.loads(run(
        "content", "set", slug, "--stdin", "--json",
        input_text=stdin_content,
    ).stdout)
    check(stdin_result["slug"] == slug,
          "content set --stdin must support headless agents")
    conflicting_content = run(
        "content", "set", slug, "--stdin", "--file", os.fspath(recipe_source),
        expect=2,
    )
    check("not allowed with argument" in conflicting_content.stderr,
          "content set must reject simultaneous stdin and file input")
    invalid_utf8 = HOME / "invalid-utf8.md"
    invalid_utf8.write_bytes(b"# Kaputte Suppe\n\n- Zutat \xe4\n")
    broken_encoding = as_json_error(
        "content", "set", slug, "--file", os.fspath(invalid_utf8),
    )
    check(os.fspath(invalid_utf8) in broken_encoding["error"]
          and "UTF-8" in broken_encoding["error"]
          and "codec" not in broken_encoding["error"],
          "content set must reject non-UTF-8 files with a German message "
          "including the path")
    sourced = as_json(
        "shopping", "add", "Eine Prise Salz", "--source", slug,
    )
    check(sourced["source"] == slug,
          "shopping add --source must retain a known recipe slug")
    partial_import = as_json_error("shopping", "add-recipe", slug)
    check("1 Einkaufsposten" in partial_import["error"],
          "a sourced single item must participate in the recipe import guard")
    unknown_source = as_json_error(
        "shopping", "add", "Pfeffer", "--source", "does-not-exist",
    )
    check("Kein Rezept" in unknown_source["error"],
          "shopping add --source must reject an unknown recipe slug")
    as_json("shopping", "remove", sourced["id"])
    add_help = run("shopping", "add", "--help")
    normalized_add_help = " ".join(add_help.stdout.split())
    check("--source SLUG" in normalized_add_help
          and "Herkunftsrezept" in normalized_add_help,
          "shopping add help must document sourced single items")
    clear_help = run("shopping", "clear", "--help")
    normalized_clear_help = " ".join(clear_help.stdout.split())
    check("gesamte Einkaufsliste" in normalized_clear_help
          and "offene und erledigte" in normalized_clear_help
          and "nicht per CLI wiederherstellbar" in normalized_clear_help,
          "shopping clear help must describe complete, irreversible removal")
    remove_done_help = run("shopping", "remove-done", "--help")
    check("abgehakten Eintraege"
          in " ".join(remove_done_help.stdout.split()),
          "shopping remove-done help must describe checked-only removal")
    add_recipe_help = run("shopping", "add-recipe", "--help")
    check("solange keine sichtbaren Posten"
          in " ".join(add_recipe_help.stdout.split()),
          "shopping add-recipe help must describe its actual repeat guard")
    imported = as_json("shopping", "add-recipe", slug)
    check([entry["text"] for entry in imported] == ["Wasser", "Salz"],
          "shopping add-recipe must return imported ingredients")
    duplicate_import = as_json_error("shopping", "add-recipe", slug)
    check("2 Einkaufsposten" in duplicate_import["error"]
          and "bereits auf der Einkaufsliste" in duplicate_import["error"],
          "a repeated recipe import must report how many items block it")
    empty_recipe = as_json("new", "Leeres Rezept")
    empty_import = as_json_error("shopping", "add-recipe", empty_recipe["slug"])
    check("keine importierbaren Zutaten" in empty_import["error"],
          "an empty recipe import must explain the parsing result")

    item = as_json("shopping", "add", "Milch", "--quantity", "1 L")
    check(item["text"] == "Milch" and item["quantity"] == "1 L",
          "shopping add --json must return the new item")
    batch = as_json(
        "shopping", "add-many", "Brot", "6 Eier", "200 g Spaghetti",
    )
    check([entry["text"] for entry in batch]
          == ["Brot", "6 Eier", "200 g Spaghetti"],
          "shopping add-many --json must return the complete created group")
    check(all(entry["quantity"] == "" for entry in batch),
          "shopping add-many must keep each free-text entry self-contained")
    checked = as_json("shopping", "check", item["id"])
    check(checked["checked"] is True, "shopping check --json must set checked")
    checked_again = as_json("shopping", "check", item["id"])
    check(checked_again["updated_at"] == checked["updated_at"],
          "repeated shopping check must preserve the sync timestamp")
    pending = as_json("shopping", "list", "--pending")
    check([entry["text"] for entry in pending]
          == ["Wasser", "Salz", "Brot", "6 Eier", "200 g Spaghetti"],
          "shopping list --pending --json must hide only checked items")
    removed_done = as_json("shopping", "remove-done")
    check(removed_done == {"removed": 1},
          "shopping remove-done must remove only the checked item")
    check([entry["text"] for entry in as_json("shopping", "list")]
          == ["Wasser", "Salz", "Brot", "6 Eier", "200 g Spaghetti"],
          "shopping remove-done must retain every open item")
    cleared_shopping = as_json("shopping", "clear")
    check(cleared_shopping == {"removed": 5},
          "shopping clear must remove every remaining visible item")
    check(as_json("shopping", "list") == [],
          "shopping clear must leave the visible list empty")
    check(as_json("shopping", "clear") == {"removed": 0},
          "shopping clear on an empty list must be a successful no-op")

    need = as_json(
        "favorites", "add", "Pizzateig", "--alias", "1 Rolle Pizzateig",
    )
    check(need["name"] == "Pizzateig" and need["aliases"] == ["1 Rolle Pizzateig"],
          "favorites add --json must create a need with exact aliases")
    matched = as_json("favorites", "match", "1 Rolle Pizzateig")
    check(matched["id"] == need["id"],
          "favorites match --json must expose the deterministic assignment")
    favorite = as_json(
        "favorites", "product-add", need["id"], "Frischer Pizzateig",
        "--brand", "Tante Fanny", "--store", "REWE", "--image", os.fspath(first_photo),
    )
    singular = run("favorites", "list")
    check("(1 Produkt)" in singular.stdout,
          "favorites list must use the German singular")
    fallback = as_json(
        "favorites", "product-add", need["id"], "Pizza-Kit",
        "--brand", "Knack & Back",
    )
    plural = run("favorites", "list")
    check("(2 Produkte)" in plural.stdout,
          "favorites list must use the German plural")
    moved = as_json(
        "favorites", "product-move", need["id"], fallback["id"], "1",
    )
    check([product["id"] for product in moved["products"]]
          == [fallback["id"], favorite["id"]],
          "favorites product-move --json must update the preference order")
    updated_favorite = as_json(
        "favorites", "product-set", need["id"], favorite["id"],
        "--note", "Unser Favorit",
    )
    check(updated_favorite["note"] == "Unser Favorit",
          "favorites product-set --json must update product details")
    shown_need = as_json("favorites", "show", need["id"])
    check(len(shown_need["products"]) == 2,
          "favorites show --json must include ranked products")
    renamed_need = as_json(
        "favorites", "set", need["id"], "--name", "Frischer Pizzateig",
    )
    check("Pizzateig" in renamed_need["aliases"],
          "renaming a shopping need must preserve the old name as an alias")
    favorites_set_help = run("favorites", "set", "--help")
    check("alten Namen als Alias"
          in " ".join(favorites_set_help.stdout.split()),
          "favorites set help must disclose alias preservation")

    without_alias = as_json("favorites", "add", "Zucker")
    check(without_alias["name"] == "Zucker" and without_alias["aliases"] == [],
          "favorites add without --alias must create a need with no aliases")
    as_json("favorites", "remove", without_alias["id"])

    consistency = as_json("check")
    check(consistency["recipe_count"] == 2
          and consistency["favorite_need_count"] == 1
          and consistency["ok"] is True
          and not consistency["missing_files"],
          "check --json must expose consistency state")
    drifting_source = HOME / "drifting-recipe.md"
    drifting_source.write_text(
        "# Abweichender Titel\n", encoding="utf-8",
    )
    (HOME / "recipes" / f"{slug}.md").write_text(
        drifting_source.read_text(encoding="utf-8"), encoding="utf-8",
    )
    broken_check = run("check", "--json", expect=1)
    check(not broken_check.stderr
          and json.loads(broken_check.stdout)["title_mismatches"] == [slug]
          and json.loads(broken_check.stdout)["ok"] is False,
          "check must return its full JSON diagnostics with status 1 on hard errors")
    as_json("set", slug, "--title", "Neue Suppe")
    check(as_json("check")["ok"] is True,
          "setting the canonical title again must repair a drifting H1")

    missing = as_json_error("show", "does-not-exist")
    check("Kein Rezept" in missing["error"],
          "domain errors under --json must be machine-readable")
    usage_error = run("show", "--json", expect=2)
    check(not usage_error.stdout and "usage:" in usage_error.stderr,
          "argparse usage errors must stay distinguishable on stderr with exit 2")

    ENV["EDITOR"] = sys.executable
    edited = as_json("edit", slug)
    check(edited["slug"] == slug and edited["exit_code"] == 0,
          "edit --json must return one parseable result after the editor exits")

    editor_failure = as_json_error("new", "Editorfail Test", "--edit")
    check("editorfail-test" in editor_failure["error"]
          and "bereits angelegt" in editor_failure["error"],
          "new --edit must name the already-created slug when the editor fails")
    check(as_json("show", "editorfail-test")["title"] == "Editorfail Test",
          "a failed new --edit must leave the created recipe in place")

    deleted = as_json("delete", slug)
    check(deleted["slug"] == slug and deleted["archived"] is True
          and deleted["archived_at"],
          "delete --json must confirm reversible archiving")
    archived_list = as_json("archive", "list")
    check([entry["slug"] for entry in archived_list] == [slug],
          "archive list --json must expose archived recipes")
    archived_show = as_json("archive", "show", slug)
    check(archived_show["content"].startswith("# Neue Suppe")
          and len(archived_show["images"]) == 1,
          "archive show --json must include Markdown, metadata and images")
    check("(archiviert)" in run("log").stdout,
          "human log output must resolve archived recipe titles")
    restored = as_json("archive", "restore", slug)
    check(restored["restored"] is True
          and as_json("show", slug)["title"] == "Neue Suppe",
          "archive restore must return the recipe to active commands")
    as_json("delete", slug)
    purge_without_yes = as_json_error("archive", "purge", slug)
    check("--yes" in purge_without_yes["error"],
          "archive purge must require explicit confirmation")
    purged = as_json("archive", "purge", slug, "--yes")
    check(purged == {"slug": slug, "purged": True}
          and as_json("archive", "list") == [],
          "archive purge --yes must permanently remove the snapshot")

    snapshot = store_snapshot()
    stop_server()
    unavailable = as_json_error("new", "Darf nicht angelegt werden")
    check("nicht erreichbar" in unavailable["error"],
          "a domain command must fail cleanly while the server is stopped")
    check(store_snapshot() == snapshot,
          "a failed remote mutation must not change the local store")
    offline = as_json("check", "--offline")
    check("recipe_count" in offline,
          "the explicitly marked offline check must not need the server")
    stopped_home = as_json("home")
    check(stopped_home["server_reachable"] is False,
          "home must stay local and report a stopped service")

    refused_uninstall = as_json_error("uninstall", "--keep-data")
    check("Projekt-Checkout" in refused_uninstall["error"],
          "uninstall must refuse to delete a development checkout")

    print(f"OK - {checks} CLI checks passed (GUSTO_HOME={HOME})")


if __name__ == "__main__":
    main()
