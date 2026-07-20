"""Windowless Windows autostart launcher checks, runnable without pytest."""
from __future__ import annotations

import io
import os
import sys
import tempfile
from collections.abc import Sequence
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, os.fspath(ROOT))

from gusto import cli  # noqa: E402


checks = 0


def check(condition: object, message: str) -> None:
    global checks
    assert condition, message
    checks += 1


with tempfile.TemporaryDirectory(prefix="gusto-autostart-test-") as temporary:
    root = Path(temporary)
    log = root / "autostart.log"
    received: list[str] = []

    def returning_error(arguments: Sequence[str]) -> int:
        received.extend(arguments)
        print("server status on stdout")
        print("server diagnostics on stderr", file=sys.stderr)
        return 7

    visible_stdout = io.StringIO()
    visible_stderr = io.StringIO()
    with redirect_stdout(visible_stdout), redirect_stderr(visible_stderr):
        exit_code = cli.autostart_main(
            ["--host", "127.0.0.1", "--port", "8420"],
            run_cli=returning_error,
            log_path=log,
        )

    check(exit_code == 7, "the delegated CLI exit code must reach Task Scheduler")
    check(
        received == ["serve", "--host", "127.0.0.1", "--port", "8420"],
        "the GUI launcher must delegate to the normal serve command",
    )
    check(not visible_stdout.getvalue() and not visible_stderr.getvalue(),
          "autostart output must not leak to inherited console streams")
    log_text = log.read_text(encoding="utf-8")
    check("server status on stdout" in log_text,
          "stdout must remain available in the autostart log")
    check("server diagnostics on stderr" in log_text,
          "stderr must remain available in the autostart log")

    success_log = root / "success.log"
    check(
        cli.autostart_main([], run_cli=lambda arguments: 0,
                           log_path=success_log) == 0,
        "successful startup must return zero",
    )

    def system_exit(_arguments: Sequence[str]) -> int:
        raise SystemExit(9)

    check(
        cli.autostart_main([], run_cli=system_exit,
                           log_path=root / "system-exit.log") == 9,
        "a numeric SystemExit must be preserved",
    )

    def textual_exit(_arguments: Sequence[str]) -> int:
        raise SystemExit("expected startup failure")

    text_log = root / "text-exit.log"
    check(
        cli.autostart_main([], run_cli=textual_exit, log_path=text_log) == 1,
        "a textual SystemExit must become the conventional failure code",
    )
    check("expected startup failure" in text_log.read_text(encoding="utf-8"),
          "a textual startup failure must be logged")

    # pythonw.exe may initialize both streams as None.  The launcher must set
    # usable streams before the normal CLI or Uvicorn touches them.
    none_log = root / "none-streams.log"
    original_stdout, original_stderr = sys.stdout, sys.stderr
    try:
        sys.stdout = None
        sys.stderr = None

        def with_none_streams(_arguments: Sequence[str]) -> int:
            print("redirected from None")
            return 0

        none_exit = cli.autostart_main(
            [], run_cli=with_none_streams, log_path=none_log,
        )
    finally:
        sys.stdout, sys.stderr = original_stdout, original_stderr
    check(none_exit == 0, "None stdout/stderr must not break windowless startup")
    check("redirected from None" in none_log.read_text(encoding="utf-8"),
          "None stdout/stderr must be replaced by the log stream")


print(f"OK - {checks} Windows autostart launcher checks passed")
