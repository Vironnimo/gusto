#!/usr/bin/env python3
"""Minimal systemctl --user adapter for GitHub's non-login Linux runner.

The hosted runner has systemd as PID 1 but no reliable per-user login manager.
Release smoke tests still need to execute the real generated Gusto unit. This
adapter implements only the user-service verbs used by Gusto and launches the
unit's real ExecStart command as the current, unprivileged runner user.
"""
from __future__ import annotations

import os
import shlex
import signal
import subprocess
import sys
import time
from pathlib import Path


def environment_path(name: str) -> Path:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"{name} fehlt.")
    return Path(value)


def read_pid() -> int | None:
    path = environment_path("GUSTO_CI_PID_FILE")
    try:
        return int(path.read_text(encoding="ascii").strip())
    except (FileNotFoundError, ValueError):
        return None


def running(pid: int | None = None) -> bool:
    pid = read_pid() if pid is None else pid
    if pid is None:
        return False
    try:
        os.kill(pid, 0)
    except (OSError, ProcessLookupError):
        return False
    return True


def unit_value(name: str) -> str:
    prefix = name + "="
    text = environment_path("GUSTO_CI_UNIT_PATH").read_text(encoding="utf-8")
    for line in text.splitlines():
        if line.startswith(prefix):
            return line[len(prefix):]
    raise RuntimeError(f"{name} fehlt in der Gusto-Unit.")


def start() -> None:
    if running():
        return
    command = shlex.split(unit_value("ExecStart"), posix=True)
    working_directory = shlex.split(
        unit_value("WorkingDirectory"), posix=True,
    )[0]
    log_path = environment_path("GUSTO_CI_SERVICE_LOG")
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log = log_path.open("ab", buffering=0)
    process = subprocess.Popen(
        command,
        cwd=working_directory,
        env=os.environ.copy(),
        stdin=subprocess.DEVNULL,
        stdout=log,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    log.close()
    environment_path("GUSTO_CI_PID_FILE").write_text(
        f"{process.pid}\n", encoding="ascii",
    )


def stop() -> None:
    pid = read_pid()
    if pid is None:
        return
    if running(pid):
        try:
            os.killpg(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        deadline = time.monotonic() + 15
        while running(pid) and time.monotonic() < deadline:
            time.sleep(0.1)
        if running(pid):
            try:
                os.killpg(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
    environment_path("GUSTO_CI_PID_FILE").unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    arguments = list(argv if argv is not None else sys.argv[1:])
    if arguments[:1] == ["--user"]:
        arguments.pop(0)
    if not arguments:
        print("systemctl-Verb fehlt.", file=sys.stderr)
        return 2
    verb = arguments[0]
    if verb == "daemon-reload":
        return 0
    if verb == "enable" and "--now" in arguments:
        start()
        return 0
    if verb == "disable" and "--now" in arguments:
        stop()
        return 0
    if verb == "start":
        start()
        return 0
    if verb == "stop":
        stop()
        return 0
    if verb == "restart":
        stop()
        start()
        return 0
    if verb == "is-active":
        if running():
            print("active")
            return 0
        print("inactive")
        return 3
    print(f"Nicht unterstützter CI-systemctl-Aufruf: {arguments!r}",
          file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
