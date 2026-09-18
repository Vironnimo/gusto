"""Standard-library HTTP client for the Gusto command API."""
from __future__ import annotations

import base64
import json
import os
import socket
import sys
import time
from pathlib import Path
from typing import Any, Mapping, Sequence
from urllib import error, parse, request


DEFAULT_SERVER_URL = "http://127.0.0.1:8000"
DEFAULT_TIMEOUT_SECONDS = 15.0
REQUEST_TIMEOUT_SECONDS_PER_MB = 1.0
SETTINGS_FILENAME = "gusto.settings.json"


class ClientError(ValueError):
    """An expected transport or remote-command failure."""


def default_settings_path(
    package_root: str | os.PathLike[str] | None = None,
    prefix: str | os.PathLike[str] | None = None,
) -> Path:
    """Locate the settings file owned by this source or installed runtime."""
    root = (
        Path(__file__).resolve().parent.parent
        if package_root is None
        else Path(package_root)
    )
    if (root / "pyproject.toml").is_file():
        return root / SETTINGS_FILENAME
    return Path(sys.prefix if prefix is None else prefix) / SETTINGS_FILENAME


def _normalize_server_url(value: str, *, source: str) -> str:
    value = value.strip().rstrip("/")
    parsed = parse.urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ClientError(
            f"Ungültige Gusto-Server-URL aus {source}: '{value}'. "
            "Erwartet wird http://… oder https://…"
        )
    if parsed.query or parsed.fragment:
        raise ClientError(
            f"Ungültige Gusto-Server-URL aus {source}: "
            "Query und Fragment sind nicht erlaubt."
        )
    return value


def resolve_server_url(
    explicit: str | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    settings_path: str | os.PathLike[str] | None = None,
) -> str:
    """Resolve ``--server`` → ``GUSTO_URL`` → settings → loopback default."""
    environment = os.environ if environ is None else environ
    if explicit is not None:
        if not isinstance(explicit, str) or not explicit.strip():
            raise ClientError(
                "Ungültige Gusto-Server-URL aus --server: Erwartet wird "
                "http://… oder https://…"
            )
        return _normalize_server_url(explicit, source="--server")

    configured = environment.get("GUSTO_URL")
    if configured:
        return _normalize_server_url(configured, source="GUSTO_URL")

    path = default_settings_path() if settings_path is None else Path(settings_path)
    if path.is_file():
        try:
            settings = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ClientError(
                f"Ungültige Gusto-Settings in '{path}': {exc}"
            ) from exc
        if not isinstance(settings, dict):
            raise ClientError(
                f"Ungültige Gusto-Settings in '{path}': JSON-Objekt erwartet."
            )
        configured = settings.get("server_url")
        if configured is not None:
            if not isinstance(configured, str) or not configured.strip():
                raise ClientError(
                    f"Ungültige Gusto-Settings in '{path}': "
                    "'server_url' muss ein nicht-leerer Text sein."
                )
            return _normalize_server_url(configured, source=str(path))

    return DEFAULT_SERVER_URL


def encode_attachment(
    name: str,
    path: str | os.PathLike[str],
) -> dict[str, str]:
    """Read a local file and encode it for the command envelope."""
    source = Path(path).expanduser()
    if not source.is_file():
        raise ClientError(f"Datei '{source}' ist keine reguläre Datei.")
    try:
        content = source.read_bytes()
    except OSError as exc:
        raise ClientError(f"Datei '{source}' konnte nicht gelesen werden: {exc}") from exc
    return {
        "name": name,
        "filename": source.name,
        "content_base64": base64.b64encode(content).decode("ascii"),
    }


def _decode_response(response, *, url: str) -> Any:
    try:
        raw = response.read()
    except OSError as exc:
        raise ClientError(f"Antwort von Gusto konnte nicht gelesen werden: {exc}") from exc
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ClientError(
            f"Gusto-Server unter {url} lieferte keine gültige JSON-Antwort."
        ) from exc
    if not isinstance(payload, dict):
        raise ClientError(
            f"Gusto-Server unter {url} lieferte ein ungültiges Antwortformat."
        )
    return payload


def _request_json(
    method: str,
    url: str,
    *,
    payload: Mapping[str, Any] | None = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    data = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json; charset=utf-8"
    outgoing = request.Request(url, data=data, headers=headers, method=method)
    try:
        with request.urlopen(outgoing, timeout=timeout) as response:
            decoded = _decode_response(response, url=url)
    except error.HTTPError as exc:
        try:
            decoded = _decode_response(exc, url=url)
        except ClientError:
            raise ClientError(
                f"Gusto-Server unter {url} antwortete mit HTTP {exc.code}."
            ) from None
        message = decoded.get("error") if isinstance(decoded, dict) else None
        if not isinstance(message, str) or not message:
            message = f"Gusto-Server antwortete mit HTTP {exc.code}."
        raise ClientError(message) from None
    except (error.URLError, TimeoutError, socket.timeout, OSError) as exc:
        reason = getattr(exc, "reason", exc)
        raise ClientError(
            f"Gusto-Server unter {url} ist nicht erreichbar: {reason}"
        ) from exc
    return decoded


def command(
    operation: str,
    arguments: Mapping[str, Any] | None = None,
    *,
    attachments: Sequence[Mapping[str, str]] | None = None,
    server_url: str | None = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> Any:
    """Execute one command and unwrap the API's ``result`` value.

    The timeout scales with the serialized payload (plus one second per
    megabyte), so large Base64 attachments do not masquerade as an
    unreachable server.
    """
    base_url = resolve_server_url(server_url)
    endpoint = f"{base_url}/api/v1/command"
    envelope = {
        "operation": operation,
        "arguments": dict(arguments or {}),
        "attachments": [dict(item) for item in (attachments or [])],
    }
    payload_bytes = len(json.dumps(envelope, ensure_ascii=False).encode("utf-8"))
    effective_timeout = (
        timeout + payload_bytes / (1024 * 1024) * REQUEST_TIMEOUT_SECONDS_PER_MB
    )
    response = _request_json(
        "POST", endpoint, payload=envelope, timeout=effective_timeout,
    )
    if response.get("ok") is not True:
        message = response.get("error")
        if not isinstance(message, str) or not message:
            message = "Gusto-Server meldete einen unbekannten Fehler."
        raise ClientError(message)
    if "result" not in response:
        raise ClientError("Gusto-Serverantwort enthält kein 'result'-Feld.")
    return response["result"]


def health(
    server_url: str | None = None,
    *,
    timeout: float = 2.0,
    retries: int = 1,
) -> dict[str, Any]:
    """Read health state; bounded retries are allowed because this is read-only."""
    base_url = resolve_server_url(server_url)
    endpoint = f"{base_url}/api/v1/health"
    last_error: ClientError | None = None
    for attempt in range(max(0, retries) + 1):
        try:
            response = _request_json("GET", endpoint, timeout=timeout)
            if response.get("ok") is not True:
                message = response.get("error")
                if not isinstance(message, str) or not message:
                    message = (
                        "Gusto-Server meldet keinen gültigen Health-Status."
                    )
                raise ClientError(message)
            return response
        except ClientError as exc:
            last_error = exc
            if attempt < retries:
                time.sleep(0.1)
    assert last_error is not None
    raise last_error
