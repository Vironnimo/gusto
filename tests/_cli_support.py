"""Private isolated fixture and public CLI invocation helpers for the CLI owner gate."""
from __future__ import annotations

import atexit
import io
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch
from urllib import request


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, os.fspath(ROOT))
import gusto  # noqa: E402, F401 - configure temp before HOME and the child environment

HOME = Path(tempfile.mkdtemp(prefix="gusto-cli-test-"))
ENV = {**os.environ, "GUSTO_HOME": os.fspath(HOME)}
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


def process_run(*arguments, expect=0, input_text=None, server_url=SERVER_URL):
    result = subprocess.run(
        [
            sys.executable, "-m", "gusto", *arguments,
            "--server", server_url,
        ],
        cwd=ROOT, env=ENV, input=input_text,
        text=True, encoding="utf-8", capture_output=True,
    )
    check(result.returncode == expect,
          f"{' '.join(arguments)} returned {result.returncode}: {result.stderr}")
    return result


def process_json(*arguments, server_url=SERVER_URL):
    return json.loads(process_run(*arguments, "--json", server_url=server_url).stdout)


def process_json_error(*arguments, server_url=SERVER_URL):
    result = process_run(*arguments, "--json", expect=1, server_url=server_url)
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
            if (isinstance(payload, dict)
                    and Path(payload["data_path"]).resolve() == HOME.resolve()):
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


# Ordinary parser/presentation cases share the public ASGI command endpoint.
# The separate forwarding matrix supplies independent expected operation/args.
API_CLIENT = None


def initialize_command_fixture():
    global API_CLIENT
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from gusto import api
    app = FastAPI()
    app.include_router(api.router)
    API_CLIENT = TestClient(app)


def invoke(arguments, *, input_text=None):
    """Capture the public entry point, preserving stdout/stderr/exit semantics."""
    from gusto import cli
    output, errors = io.StringIO(), io.StringIO()
    with redirect_stdout(output), redirect_stderr(errors):
        with patch.object(sys, "stdin", io.StringIO(input_text or "")):
            try:
                cli.main(list(arguments))
            except SystemExit as error:
                code = error.code
            else:
                code = 0
    return subprocess.CompletedProcess(arguments, code, output.getvalue(), errors.getvalue())


def run(*arguments, expect=0, input_text=None, server_url=SERVER_URL):
    from gusto import client

    def command(operation, arguments=None, *, attachments=None, server_url=None):
        check(server_url == SERVER_URL, "the CLI must forward the chosen server")
        response = API_CLIENT.post("/api/v1/command", json={
            "operation": operation, "arguments": arguments or {},
            "attachments": attachments or [],
        })
        body = response.json()
        if not body["ok"]:
            raise client.ClientError(body["error"],
                                     outcome_unknown=body.get("outcome_unknown", False))
        return body["result"]

    with patch.dict(os.environ, ENV), patch.object(client, "command", side_effect=command):
        result = invoke([*arguments, "--server", server_url], input_text=input_text)
    check(result.returncode == expect,
          f"{' '.join(arguments)} returned {result.returncode}: {result.stderr}")
    return result


def as_json(*arguments, server_url=SERVER_URL):
    result = run(*arguments, "--json", server_url=server_url)
    check(not result.stderr, "successful JSON output must not emit stderr")
    return json.loads(result.stdout)


def as_json_error(*arguments, server_url=SERVER_URL):
    result = run(*arguments, "--json", expect=1, server_url=server_url)
    check(not result.stderr, "JSON command errors must not emit plain stderr text")
    payload = json.loads(result.stdout)
    check(payload.get("ok") is False and bool(payload.get("error")),
          "JSON command errors must use the documented error object")
    return payload
