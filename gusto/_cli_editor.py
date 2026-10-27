"""CLI recipe-text input, editor execution and recoverable round-trips."""
from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from . import client
from ._cli_terminal import dump, remote


class EditError(client.ClientError):
    """An unfinished editor round-trip with a recoverable local copy."""

    def __init__(self, error: Exception, *, slug: str, path: Path):
        super().__init__(
            f"{error} Lokale Rezeptkopie bleibt erhalten: '{path}'. "
            f"Lies 'show {slug} --json' auf demselben Server und führe Änderungen "
            f"zusammen; nutze dann 'content set {slug}' mit --file, diesem "
            "Dateipfad und --revision aus show.",
            outcome_unknown=getattr(error, "outcome_unknown", False),
        )
        self.slug = slug
        self.recovery_path = os.fspath(path)


def _read_content_file(path: Path) -> str:
    """Accept UTF-8 and BOM-marked Unicode files produced by Windows editors."""
    data = path.read_bytes()
    encoding = "utf-8-sig"
    if data.startswith((b"\xff\xfe\x00\x00", b"\x00\x00\xfe\xff")):
        raise ValueError(
            f"Rezeptdatei '{path}' ist UTF-32. Speichere sie als UTF-8 "
            "oder UTF-16 mit BOM. Es wurde nichts hochgeladen."
        )
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        encoding = "utf-16"
    try:
        return data.decode(encoding).replace("\r\n", "\n").replace("\r", "\n")
    except UnicodeDecodeError as error:
        expected = "UTF-16 mit BOM" if encoding == "utf-16" else "UTF-8"
        raise ValueError(
            f"Rezeptdatei '{path}' konnte nicht als {expected} gelesen werden. "
            "Speichere sie als UTF-8 oder UTF-16 mit BOM. "
            "Es wurde nichts hochgeladen."
        ) from error


def _open_editor(path, *, quiet: bool = False) -> dict:
    editor = os.environ.get("EDITOR") or ("notepad" if os.name == "nt" else "nano")
    options = {}
    if quiet:
        options = {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
    try:
        if Path(editor).is_file():
            # Keep accepting a bare executable path containing spaces.
            command = [editor, str(path)]
        elif os.name == "nt":
            # CreateProcess parses Windows quotes/backslashes itself. A POSIX
            # shell split would corrupt paths such as C:\\Tools\\editor.exe.
            # Resolve PATHEXT first: CreateProcess alone misses code.cmd when
            # EDITOR is "code --wait". Preserve the remaining native arguments.
            match = re.fullmatch(
                r'\s*(?:"([^"]+)"|([^\s"]+))(\s.*|$)', editor,
                flags=re.DOTALL,
            )
            if match is None:
                raise ValueError("EDITOR enthält keinen gültigen Programmnamen.")
            program = match[1] or match[2]
            resolved = shutil.which(program) or program
            command = (
                subprocess.list2cmdline([resolved]) + match[3]
                + " " + subprocess.list2cmdline([str(path)])
            )
        else:
            command = [*shlex.split(editor), str(path)]
            if len(command) == 1:
                raise ValueError("EDITOR enthält keinen Programmnamen.")
        exit_code = subprocess.call(command, **options)
    except (OSError, ValueError) as error:
        raise ValueError(f"Editor '{editor}' konnte nicht gestartet werden: {error}") from error
    if exit_code:
        raise ValueError(f"Editor '{editor}' wurde mit Status {exit_code} beendet.")
    return {
        "editor": editor,
        "path": os.fspath(Path(path).resolve()),
        "exit_code": exit_code,
    }


def _edit_remote(args, slug: str) -> dict:
    recipe = remote(args, "recipe.show", {"slug": slug})
    try:
        handle = tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", suffix=".md", prefix=f"gusto-{slug}-",
            delete=False,
        )
    except OSError as error:
        raise client.ClientError(
            f"Lokale Rezeptkopie konnte nicht angelegt werden: {error} "
            "Es wurde nichts hochgeladen."
        ) from error
    temporary = Path(handle.name)
    try:
        with handle:
            handle.write(recipe["content"])
        editor_result = _open_editor(temporary, quiet=args.json)
        try:
            content = _read_content_file(temporary)
        except OSError as error:
            raise client.ClientError(
                f"Bearbeiteter Rezepttext konnte nicht gelesen werden: {error}"
            ) from error
        remote(
            args, "recipe.content.set", {"slug": slug, "content": content,
                                         "revision": recipe["revision"]},
        )
    except (OSError, ValueError) as error:
        if temporary.is_file():
            raise EditError(error, slug=slug, path=temporary) from error
        raise
    else:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            # The upload succeeded; local cleanup must not invite a replay.
            editor_result["retained_path"] = os.fspath(temporary)
    editor_result["slug"] = slug
    return editor_result


def cmd_edit(args):
    result = _edit_remote(args, args.slug)
    if args.json:
        dump(result)
    else:
        print(f"Inhalt gespeichert: {result['slug']}")
        if result.get("retained_path"):
            print(f"Lokale Kopie konnte nicht entfernt werden: {result['retained_path']}")


def cmd_content_set(args):
    if args.stdin:
        try:
            content = sys.stdin.read()
        except UnicodeDecodeError as error:
            raise client.ClientError(
                "Rezepttext auf stdin konnte nicht gelesen werden "
                "(erwartet wird UTF-8)."
            ) from error
    else:
        try:
            content = _read_content_file(Path(args.file).expanduser())
        except OSError as error:
            raise client.ClientError(
                f"Rezeptdatei '{args.file}' konnte nicht gelesen werden: {error}"
            ) from error
    recipe = remote(
        args, "recipe.content.set", {"slug": args.slug, "content": content,
                                     "revision": args.revision},
    )
    if args.json:
        dump(recipe)
    else:
        print(f"Inhalt aktualisiert: {args.slug}")


def register_commands(sub, base) -> None:
    sp = sub.add_parser("edit", parents=[base], help="Rezept im interaktiven Editor bearbeiten, danach hochladen; ohne Editor: content set.")
    sp.add_argument("slug", help="Rezept-Slug aus list oder search. Editor über EDITOR, sonst notepad/nano.")
    sp.set_defaults(func=cmd_edit)

    sp = sub.add_parser(
        "content", help="Rezeptinhalt agentenfähig über den Server setzen.",
    )
    csub = sp.add_subparsers(dest="content_command", required=True)
    cp = csub.add_parser(
        "set", parents=[base],
        help="Gesamten Rezeptinhalt ersetzen; die erste Zeile wird als gespeicherter Titel gesetzt.",
    )
    cp.add_argument("slug", help="Rezept-Slug aus list oder search. Titel ändern: set --title.")
    cp.add_argument("--revision", required=True, help="Revision aus show --json; bei Konflikt Inhalt erneut lesen und Änderungen zusammenführen.")
    source = cp.add_mutually_exclusive_group(required=True)
    source.add_argument("--file", help="Lokale Markdown-Datei: UTF-8, auch mit BOM, oder UTF-16 mit BOM. Datei bleibt erhalten.")
    source.add_argument(
        "--stdin", action="store_true",
        help="Vollständigen UTF-8-Markdown-Inhalt von stdin lesen. Genau eines von --file/--stdin angeben.",
    )
    cp.set_defaults(func=cmd_content_set)
