"""HTTP client discovery, attachment and timeout contracts."""
from __future__ import annotations

import base64
import io
import json
import os
from email.message import Message
from unittest.mock import patch
from urllib.error import HTTPError


from _cli_support import (
    HOME, check,
)

def check_client_contract():
    from gusto import client
    from _cli_support import invoke

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
    for invalid in ("http://[", "http://:123", "http://example.com:abc",
                    "http://example.com:99999", "http://example.com:0",
                    "http://bad host", "http://bad\nhost"):
        with patch.object(client, "_request_json") as send:
            try:
                client.command("shopping.add", {"text": "Milch"}, server_url=invalid)
            except client.ClientError as error:
                check(not error.outcome_unknown and not send.called,
                      "invalid server URLs must fail definitively before sending")
            else:
                raise AssertionError(f"invalid server URL accepted: {invalid}")
    invalid_settings = HOME / "invalid-utf8-settings.json"
    invalid_settings.write_bytes(b"\xff")
    try:
        client.resolve_server_url(environ={}, settings_path=invalid_settings)
    except client.ClientError as error:
        check(str(invalid_settings) in str(error) and not error.outcome_unknown,
              "invalid settings encoding must identify the file as a definite failure")
    else:
        raise AssertionError("invalid settings encoding was accepted")
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
    oversized = HOME / "oversized-attachment.bin"
    with oversized.open("wb") as output:
        output.seek(client.MAX_ATTACHMENT_BYTES)
        output.write(b"x")
    with patch.object(client.base64, "b64encode") as encode:
        try:
            client.encode_attachment("image", oversized)
        except client.ClientError as error:
            check("25 MiB" in str(error) and not error.outcome_unknown
                  and not encode.called,
                  "oversized attachments must fail before encoding or sending")
        else:
            raise AssertionError("an oversized attachment was accepted")
    with patch.object(client, "MAX_ATTACHMENT_BYTES", len(image.read_bytes())):
        check(client.encode_attachment("image", image) == attachment,
              "the exact attachment size limit must remain inclusive")
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
    heavy = HOME / "heavy-attachment.bin"
    heavy.write_bytes(bytes(1024 * 1024))
    with patch.object(
        client.request, "urlopen",
        return_value=io.BytesIO(b'{"ok":true,"result":{"slug":"test"}}'),
    ) as send:
        with patch.object(client.json, "dumps", wraps=json.dumps) as serialize:
            client.command(
                "recipe.show", {"slug": "test"},
                attachments=[client.encode_attachment("image", heavy)],
                server_url="http://gusto.local:9000",
            )
    sent = send.call_args.args[0]
    effective_timeout = send.call_args.kwargs["timeout"]
    check(
        client.DEFAULT_TIMEOUT_SECONDS < effective_timeout
        <= client.DEFAULT_TIMEOUT_SECONDS + 2.0,
        "large attachments must scale the command timeout instead of "
        "failing as unreachable",
    )
    check(serialize.call_count == 1
          and effective_timeout == client.DEFAULT_TIMEOUT_SECONDS
          + len(sent.data) / (1024 * 1024) * client.REQUEST_TIMEOUT_SECONDS_PER_MB
          and json.loads(sent.data)["attachments"][0]["filename"] == heavy.name,
          "timeout sizing and HTTP delivery must reuse one JSON serialization")
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

    for argv, method, expected in (
        (["backup", "create", "--file", "backup.zip"], "backup_download",
         {"path": "backup.zip", "bytes": 12}),
        (["backup", "preview", "--file", "backup.zip"], "backup_upload",
         {"token": "token"}),
        (["backup", "restore", "--file", "backup.zip", "--token", "token"],
         "backup_upload", {"safety_backup_id": "saved"}),
        (["backup", "safety-download", "saved", "--file", "backup.zip"],
         "backup_download", {"path": "backup.zip", "bytes": 12}),
    ):
        with patch.object(client, method, return_value=expected) as call:
            result = invoke([*argv, "--server", "http://example.test", "--json"])
        check(result.returncode == 0 and json.loads(result.stdout) == expected
              and call.call_args.kwargs["server_url"] == "http://example.test",
              "backup commands must forward to the selected service and return JSON")
    existing = HOME / "existing-backup.zip"
    existing.write_bytes(b"do not overwrite")
    try:
        client.backup_download(str(existing), server_url="http://example.test")
    except client.ClientError:
        check(existing.read_bytes() == b"do not overwrite",
              "backup download must not replace a local file")
    else:
        raise AssertionError("backup download overwrote an existing file")
    missing_destination = HOME / "missing-safety.zip"
    failure = HTTPError("http://example.test", 404, "missing", {},
                        io.BytesIO(b'{"ok":false,"error":"Sicherung nicht gefunden."}'))
    with patch.object(client.request, "urlopen", side_effect=failure):
        try:
            client.backup_download(str(missing_destination), server_url="http://example.test",
                                   safety_id="missing")
        except client.ClientError as error:
            check("Sicherung" in str(error) and not missing_destination.exists(),
                  "failed backup downloads must expose the server error and remove partial files")
        else:
            raise AssertionError("missing safety backup was accepted")
    truncated = io.BytesIO(b"partial")
    truncated.headers = Message()
    truncated.headers["Content-Type"] = "application/zip"
    truncated.headers["Content-Length"] = "40"
    with patch.object(client.request, "urlopen", return_value=truncated):
        try:
            client.backup_download(str(missing_destination), server_url="http://example.test")
        except client.ClientError:
            check(not missing_destination.exists(),
                  "a truncated backup download must not leave a purportedly complete file")
        else:
            raise AssertionError("a truncated backup was accepted")
    archive = HOME / "input-backup.zip"
    archive.write_bytes(b"archive fixture")
    for token, expected_unknown in ((None, False), ("review-token", True)):
        with patch.object(client.request, "urlopen", side_effect=OSError("lost reply")) as send:
            try:
                client.backup_upload(str(archive), server_url="http://example.test", token=token)
            except client.ClientError as error:
                check(error.outcome_unknown is expected_unknown and send.call_count == 1,
                      "backup transport failure must distinguish preview from restore without retry")
            else:
                raise AssertionError("a lost backup response was accepted")


