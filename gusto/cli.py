"""Command line assembly, dispatch and windowless autostart for Gusto.

Domain command registrations and their handlers live together in private CLI
units. Only explicit local recovery/lifecycle commands import their local owners.
"""
from __future__ import annotations

import argparse
import os
import sys
import traceback
from collections.abc import Callable, Sequence
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime
from pathlib import Path

from . import _cli_catalog as _catalog
from . import _cli_backup as _backup
from . import _cli_editor as _editor
from . import _cli_local as _local
from . import _cli_shopping as _shopping
from . import _cli_terminal as _terminal
from . import client


def _autostart_log_path() -> Path:
    """Return the log owned by the active Gusto data store.

    Resolving instance settings can itself fail.  Keep the GUI launcher
    windowless in that case and give it a platform-default place to record the
    later startup error.
    """
    from .core import storage

    try:
        root = storage.project_root()
    except (OSError, ValueError):
        root = storage.default_data_root()
    return root / "gusto-autostart.log"


def _system_exit_code(code: object) -> int:
    """Convert SystemExit's permissive payload to a process exit code."""
    if code is None:
        return 0
    if isinstance(code, int):
        return code
    print(code, file=sys.stderr)
    return 1


def autostart_main(
    argv: Sequence[str] | None = None,
    *,
    run_cli: Callable[[Sequence[str]], object] | None = None,
    log_path: str | os.PathLike[str] | None = None,
) -> int:
    """Run ``gusto serve`` through the windowless Windows GUI entry point.

    A ``gui-scripts`` launcher has no console, so stdout and stderr may be
    ``None`` under pythonw.exe.  Redirect both before entering the normal CLI
    path; this preserves its behavior and exit code while keeping diagnostics
    in a durable file instead of opening a login console.
    """
    arguments = list(sys.argv[1:] if argv is None else argv)
    destination = Path(log_path) if log_path is not None else _autostart_log_path()
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        output = destination.open("a", encoding="utf-8", buffering=1)
    except OSError:
        output = open(os.devnull, "w", encoding="utf-8")

    with output, redirect_stdout(output), redirect_stderr(output):
        print(
            f"\n[{datetime.now().isoformat(timespec='seconds')}] "
            f"Gusto-Autostart (PID {os.getpid()})"
        )
        try:
            result = (run_cli or main)(["serve", *arguments])
        except SystemExit as error:
            return _system_exit_code(error.code)
        except KeyboardInterrupt:
            return 130
        except Exception:
            traceback.print_exc()
            return 1
        return 0 if result is None else int(result)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="gusto",
        description="Markdown-Rezepte – komplett per CLI steuerbar. "
                    "Normale Befehle nutzen den laufenden Gusto-Dienst. "
                    "Jedes Kommando versteht --json.",
    )
    p.add_argument(
        "--server",
        help="Server-URL für Domain-Befehle. Ohne Angabe: GUSTO_URL, dann "
             "Instanz-Settings, dann http://127.0.0.1:8000. Lokale "
             "Dienstaktionen nutzen ihre Installation.",
    )
    sub = p.add_subparsers(dest="command", required=True)

    base = argparse.ArgumentParser(add_help=False)
    base.add_argument(
        "--server",
        default=argparse.SUPPRESS,
        help="Server-URL; ohne Angabe GUSTO_URL, Instanz-Settings oder "
             "http://127.0.0.1:8000. Lokale Dienstaktionen bleiben lokal.",
    )
    base.add_argument("--json", action="store_true",
                      help="Ergebnis als JSON auf stdout. Ohne Flag: lesbarer Text.")

    _catalog.register_discovery(sub, base)
    _local.register_discovery(sub, base)
    _catalog.register_recipes(sub, base)
    _local.register_check(sub, base)
    _catalog.register_management(sub, base)
    _shopping.register_commands(sub, base)
    _backup.register_commands(sub, base)
    _local.register_lifecycle(sub, base)

    _complete_help(p)
    return p


def _complete_help(parser: argparse.ArgumentParser) -> None:
    """Show command summaries both in the command index and its own help."""
    for action in parser._actions:
        if not isinstance(action, argparse._SubParsersAction):
            continue
        summaries = {choice.dest: choice.help for choice in action._choices_actions}
        for name, child in action.choices.items():
            if child.description is None:
                child.description = summaries[name]
            _complete_help(child)
        return
    parser.epilog = (
        "Exit-Status: 0 Erfolg, 1 Befehlsfehler, 2 ungültiger Aufruf. "
        "Bei --json stehen Befehlsfehler auf stdout; Aufruffehler auf stderr."
    )
    if parser.prog == "gusto serve":
        parser.epilog += " --json meldet nur den Start; Dienstprotokolle stehen auf stderr."


def main(argv=None) -> None:
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # robust umlauts/JSON on Windows
        except Exception:
            pass
    args = build_parser().parse_args(argv)
    try:
        args.func(args)
    except (client.ClientError, ValueError) as error:
        if args.json:
            result = {"ok": False, "error": str(error)}
            if getattr(error, "outcome_unknown", False):
                result["outcome_unknown"] = True
            recovery = error if isinstance(error, _editor.EditError) else error.__cause__
            if isinstance(recovery, _editor.EditError):
                result["slug"] = recovery.slug
                result["recovery_path"] = recovery.recovery_path
            _terminal.dump(result)
        else:
            print(str(error), file=sys.stderr)
        raise SystemExit(1) from None
    except SystemExit as error:
        if args.json and isinstance(error.code, str):
            _terminal.dump({"ok": False, "error": error.code})
            raise SystemExit(1) from None
        raise
