"""Editor recovery and genuine process/socket boundary contracts."""
from __future__ import annotations

import io
import json
import os
import shlex
import subprocess
import threading
import venv
from contextlib import redirect_stdout
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib import request


from _cli_support import (
    HOME, ENV, SERVER_URL, check, process_json, process_json_error,
)

def check_edit_readback_contract():
    from gusto import cli

    edited_paths = []

    def non_utf8_editor(path, *, quiet=False):
        edited_paths.append(Path(path))
        Path(path).write_bytes(b"# Kaputt\n\n- Zutat \xe4\n")
        return {"editor": "fake", "path": os.fspath(Path(path)), "exit_code": 0}

    from _cli_support import invoke

    with patch.object(cli, "_open_editor", side_effect=non_utf8_editor):
        with patch.object(cli.client, "command", return_value={
            "slug": "edit-readback", "content": "# Ok\n",
        }) as command:
            result = invoke(["edit", "edit-readback", "--json"])
    payload = json.loads(result.stdout)
    check(result.returncode == 1 and not result.stderr
          and payload["ok"] is False and "UTF-8" in payload["error"],
          "invalid editor encoding must fail through the JSON entry point")
    check(command.call_count == 1
          and command.call_args.args == ("recipe.show", {"slug": "edit-readback"}),
          "invalid editor bytes must not be uploaded")
    check(payload["slug"] == "edit-readback"
          and payload["recovery_path"] == os.fspath(edited_paths[-1])
          and edited_paths[-1].read_bytes() == b"# Kaputt\n\n- Zutat \xe4\n",
          "invalid editor encoding must retain the exact local recovery bytes and path")
    for path in edited_paths:
        path.unlink()


def check_edit_cleanup_failure():
    from gusto import cli
    from _cli_support import invoke

    paths = []
    content = "# Saved\n\n- Käse\n"

    def editor(path, *, quiet=False):
        paths.append(Path(path))
        Path(path).write_text(content, encoding="utf-8")
        return {"editor": "fixture", "path": str(path), "exit_code": 0}

    real_unlink = Path.unlink

    def refuse_copy_unlink(path, *args, **kwargs):
        if path in paths:
            raise PermissionError("locked by editor")
        return real_unlink(path, *args, **kwargs)

    for plain in (False, True):
        with patch.object(cli.client, "command", side_effect=[
            {"slug": "cleanup", "content": "# Saved\n"}, {"slug": "cleanup"},
        ]) as command, patch.object(cli, "_open_editor", side_effect=editor), \
                patch.object(Path, "unlink", refuse_copy_unlink):
            result = invoke(["edit", "cleanup", *([] if plain else ["--json"])])
        check(result.returncode == 0 and not result.stderr,
              "a local cleanup failure after upload must remain success")
        check(command.call_count == 2 and command.call_args.args == (
            "recipe.content.set", {"slug": "cleanup", "content": content}),
            "cleanup failure must not replay an accepted upload")
        if plain:
            check(str(paths[-1]) in result.stdout and "cleanup" in result.stdout,
                  "plain edit success must identify the retained copy and saved slug")
        else:
            payload = json.loads(result.stdout)
            check(payload["slug"] == "cleanup"
                  and payload["retained_path"] == str(paths[-1])
                  and not payload.get("outcome_unknown"),
                  "JSON edit success must expose retained_path without uncertainty")
        check(paths[-1].read_text(encoding="utf-8") == content,
              "failed cleanup must retain the exact saved recovery text")
        paths[-1].unlink()


def check_editor_arguments_contract():
    """Run a real quoted editor command, including its arguments and failure."""
    from gusto import cli

    editor_root = HOME / "editor with spaces"
    venv.EnvBuilder(with_pip=False).create(editor_root)
    executable = editor_root / (
        "Scripts/python.exe" if os.name == "nt" else "bin/python"
    )
    script = editor_root / "edit recipe.py"
    script.write_text(
        "import sys\n"
        "from pathlib import Path\n"
        "Path(sys.argv[-1]).write_text(\n"
        "    '# Ignored editor title\\n\\n' + sys.argv[1] + '\\n', encoding='utf-8')\n"
        "print('editor output must not corrupt JSON')\n"
        "raise SystemExit(int(sys.argv[2]))\n",
        encoding="utf-8",
    )
    body = 'Grüne "Kräuter" & ein Pfad C:\\Zutaten\\frisch'
    previous_editor = ENV.get("EDITOR")
    previous_path = ENV.get("PATH", "")
    previous_pathext = ENV.get("PATHEXT")
    unchanged = editor_root / "unchanged recipe.md"
    unchanged.write_text("# A recipe without executable content\n", encoding="utf-8")
    from _cli_support import invoke
    with patch.dict(os.environ, {"EDITOR": os.fspath(executable)}), \
            patch.object(cli.client, "command", side_effect=[
                {"slug": "bare-editor", "content": unchanged.read_text(encoding="utf-8")},
                {"slug": "bare-editor"},
            ]) as command:
        bare = invoke(["edit", "bare-editor", "--json"])
    check(bare.returncode == 0 and json.loads(bare.stdout)["exit_code"] == 0
          and command.call_count == 2,
          "an unquoted executable path with spaces must work through edit")

    def editor_command(exit_code, content=body):
        arguments = [os.fspath(executable), os.fspath(script), content, str(exit_code)]
        return (subprocess.list2cmdline(arguments) if os.name == "nt"
                else shlex.join(arguments))

    try:
        ENV["EDITOR"] = editor_command(0)
        created = process_json("new", "Editor Argumente", "--edit")
        slug = created["slug"]
        check(created["editor"]["exit_code"] == 0
              and process_json("show", slug)["content"] == f"# Editor Argumente\n\n{body}\n",
              "EDITOR must preserve quoted executable/script paths and argument values")
        check(not Path(created["editor"]["path"]).exists(),
              "a successful editor command must clean its temporary recipe")

        failed_body = body + " (nicht gespeichert)"
        ENV["EDITOR"] = editor_command(7, failed_body)
        failed = process_json_error("edit", slug)
        recovery = Path(failed["recovery_path"])
        check(failed["slug"] == slug and "Status 7" in failed["error"]
              and recovery.read_text(encoding="utf-8") == f"# Ignored editor title\n\n{failed_body}\n",
              "an editor with arguments that fails must preserve its exact recovery copy")
        check(process_json("show", slug)["content"] == f"# Editor Argumente\n\n{body}\n",
              "a failing editor must not upload its temporary recipe")
        recovery.unlink()

        if os.name == "nt":
            launcher_root = editor_root / "PATH launchers"
            launcher_root.mkdir()
            launcher_script = launcher_root / "editor launcher.py"
            launcher_script.write_text(
                "import sys\n"
                "from pathlib import Path\n"
                "assert sys.argv[1:-1] == ['--wait', 'argument with spaces']\n"
                "Path(sys.argv[-1]).write_text(\n"
                "    '# Ignored title\\n\\n- PATH launcher\\n', encoding='utf-8')\n",
                encoding="utf-8",
            )
            launcher = launcher_root / "gusto-editor.cmd"
            launcher.write_text(
                "@echo off\n" + subprocess.list2cmdline(
                    [os.fspath(executable), os.fspath(launcher_script)]
                ) + " %*\n",
                encoding="utf-8",
            )
            ENV["PATH"] = os.fspath(launcher_root) + os.pathsep + previous_path
            ENV["PATHEXT"] = ".EXE;.CMD"
            ENV["EDITOR"] = 'gusto-editor --wait "argument with spaces"'
            edited = process_json("edit", slug)
            check(edited["exit_code"] == 0
                  and process_json("show", slug)["content"]
                  == "# Editor Argumente\n\n- PATH launcher\n",
                  "Windows EDITOR must resolve extensionless .cmd PATH launchers")

        process_json("delete", slug)
        process_json("archive", "purge", slug, "--yes")
    finally:
        ENV["PATH"] = previous_path
        if previous_pathext is None:
            ENV.pop("PATHEXT", None)
        else:
            ENV["PATHEXT"] = previous_pathext
        if previous_editor is None:
            ENV.pop("EDITOR", None)
        else:
            ENV["EDITOR"] = previous_editor


def check_uncertain_delivery_contract():
    """Lose a response after the real service commits, then recover via the CLI."""
    from gusto import cli

    received = []
    drop_operations = {"shopping.add", "recipe.content.set"}

    class LostResponseProxy(BaseHTTPRequestHandler):
        def do_GET(self):
            with request.urlopen(f"{SERVER_URL}{self.path}") as upstream:
                payload = upstream.read()
            self.send_response(200)
            self.end_headers()
            self.wfile.write(payload)

        def do_POST(self):
            body = self.rfile.read(int(self.headers["Content-Length"]))
            operation = json.loads(body)["operation"]
            received.append(operation)
            outgoing = request.Request(
                f"{SERVER_URL}{self.path}", data=body,
                headers={"Content-Type": "application/json"}, method="POST",
            )
            with request.urlopen(outgoing) as upstream:
                payload = upstream.read()
            if operation in drop_operations:
                self.close_connection = True
                return
            self.send_response(200)
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *_args):
            pass

    with HTTPServer(("127.0.0.1", 0), LostResponseProxy) as proxy:
        worker = threading.Thread(target=proxy.serve_forever, daemon=True)
        worker.start()
        proxy_url = f"http://127.0.0.1:{proxy.server_port}"
        recovered_paths = []
        try:
            home = process_json("home", server_url=proxy_url)
            check(home["server_reachable"] and home["server_url"] == proxy_url
                  and Path(home["server_data_path"]).resolve() == HOME.resolve(),
                  "fault fixture must identify the same disposable live store")
            failed = process_json_error(
                "shopping", "add", "Verbindungsabbruch-Test", server_url=proxy_url,
            )
            check(failed.get("outcome_unknown") is True
                  and "demselben Server" in failed["error"],
                  "lost POST responses need explicit uncertainty and recovery guidance")
            applied = [item for item in process_json("shopping", "list")
                       if item["text"] == "Verbindungsabbruch-Test"]
            check(len(applied) == 1 and received.count("shopping.add") == 1,
                  "a committed command with a lost response must never be replayed")
            process_json("shopping", "remove", applied[0]["id"])

            recipe = process_json("new", "Editor Wiederaufnahme")
            content = "# Editor Wiederaufnahme\n\n## Zutaten\n\n- Bohnen\n"

            def edit_copy(path, *, quiet=False):
                recovered_paths.append(Path(path))
                Path(path).write_text(content, encoding="utf-8")
                return {"editor": "fixture", "path": str(path), "exit_code": 0}

            output = io.StringIO()
            with patch.object(cli, "_open_editor", side_effect=edit_copy):
                with redirect_stdout(output):
                    try:
                        cli.main([
                            "--server", proxy_url, "edit", recipe["slug"], "--json",
                        ])
                    except SystemExit as error:
                        check(error.code == 1, "failed edit must exit 1")
                    else:
                        raise AssertionError("lost edit response was accepted")
            failure = json.loads(output.getvalue())
            check(failure.get("outcome_unknown") is True
                  and failure.get("slug") == recipe["slug"]
                  and failure.get("recovery_path") == str(recovered_paths[-1])
                  and recovered_paths[-1].read_text(encoding="utf-8") == content,
                  "edit JSON must expose its intact local recovery copy and target")
            saved = process_json("show", recipe["slug"])
            check(saved["content"] == content
                  and received.count("recipe.content.set") == 1,
                  "readback must reveal the committed edit without replaying the upload")
            process_json("delete", recipe["slug"])
            process_json("archive", "purge", recipe["slug"], "--yes")
        finally:
            for path in recovered_paths:
                path.unlink(missing_ok=True)
            proxy.shutdown()
            worker.join(timeout=5)


