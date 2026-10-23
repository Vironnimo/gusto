"""CLI local recovery, foreground serving and installed lifecycle commands."""
from __future__ import annotations

import argparse
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

from . import client
from ._cli_terminal import dump, remote


def _port(value: str) -> int:
    try:
        port = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("Port muss eine ganze Zahl sein.") from error
    if not 1 <= port <= 65535:
        raise argparse.ArgumentTypeError("Port muss zwischen 1 und 65535 liegen.")
    return port


def cmd_install_skill(args):
    from . import skill_install

    result = skill_install.install_skill(args.host, dry_run=args.dry_run)
    if args.json:
        dump(result)
        return
    if args.dry_run:
        action = "würde ersetzt" if result["overwritten"] else "würde installiert"
    else:
        action = "ersetzt" if result["overwritten"] else "installiert"
    print(f"Gusto-Skill für {result['host']} {action}.")
    print(f"Quelle: {result['source']}")
    print(f"Ziel:   {result['destination']}")


def cmd_home(args):
    from .core import storage

    info = storage.storage_info()
    server_url = client.resolve_server_url(getattr(args, "server", None))
    info["server_url"] = server_url
    try:
        service = client.health(server_url, retries=0)
    except client.ClientError as error:
        info["server_reachable"] = False
        info["service_status"] = "unreachable"
        info["service_error"] = str(error)
    else:
        info["server_reachable"] = True
        info["service_status"] = str(service.get("status", "running"))
        info["server_data_path"] = service.get("data_path")
        info["server_version"] = service.get("version")
        info["server_revision"] = service.get("revision")
    if args.json:
        dump(info)
        return
    labels = {
        "environment": "GUSTO_HOME",
        "settings": "Instanz-Settings",
        "legacy": "bestehender Checkout (Kompatibilitätsmodus)",
        "platform_default": "Standard-Benutzerdatenordner",
    }
    print(info["path"])
    print(f"  Quelle: {labels.get(info['source'], info['source'])}")
    if info["source"] == "settings":
        print(f"  Settings: {info['settings_path']}")
    if info["source"] == "legacy":
        print(f"  Neuer Plattformstandard: {info['platform_default']}")
    print(f"  Server: {server_url}")
    print(
        "  Dienst: "
        + ("erreichbar" if info["server_reachable"] else "nicht erreichbar")
    )
    if info["server_reachable"] and info.get("server_data_path"):
        print(f"  Server-Daten: {info['server_data_path']}")


def cmd_check(args):
    if args.offline:
        from .core import checks
        res = checks.check()
    else:
        res = remote(args, "check.run")
    if args.json:
        dump(res)
    else:
        print(
            f"Aktiv: {res['recipe_count']} Rezept(e) · "
            f"Archiv: {res['archive_count']} Rezept(e)"
        )
        labels = {
            "duplicate_recipe_slugs": "Doppelte aktive Slugs",
            "invalid_data_files": "Ungültige Daten-JSON-Dateien",
            "orphaned_files": ".md ohne Index-Eintrag",
            "missing_files": "Index-Eintrag ohne .md",
            "title_mismatches": "Titel stimmt nicht mit Markdown-H1 überein",
            "orphaned_image_folders": "Bildordner ohne aktives Rezept",
            "orphaned_image_files": "Aktive Bilder ohne Metadaten",
            "missing_image_files": "Fehlende aktive Bilddateien",
            "invalid_cover_images": "Ungültige aktive Top-Bilder",
            "invalid_archive_entries": "Ungültige Archiveinträge",
            "orphaned_archive_root_files": "Dateien außerhalb eines Archiveintrags",
            "stale_archive_transactions": "Unvollständige Archivvorgänge",
            "missing_archive_files": "Archiv-Metadaten ohne Markdown",
            "archived_title_mismatches": "Archiv-Titel stimmt nicht mit H1 überein",
            "missing_archive_image_files": "Fehlende Archivbilder",
            "orphaned_archive_image_files": "Archivbilder ohne Metadaten",
            "invalid_archive_cover_images": "Ungültige Top-Bilder im Archiv",
            "active_archive_conflicts": "Slug gleichzeitig aktiv und archiviert",
            "unresolved_shopping_sources": "Unbekannte Quellen auf der Einkaufsliste",
            "duplicate_favorite_aliases": "Mehrdeutige Lieblingsprodukt-Aliasse",
            "orphaned_favorite_image_files": "Produktbilder ohne Metadaten",
            "missing_favorite_image_files": "Fehlende Produktbilder",
            "uncategorized_tags": "Tags ohne Kategorie",
            "unresolved_log_references": "Historische Log-Slugs ohne Rezept",
        }
        for diagnostic in res["errors"]:
            print(
                f"  Fehler · {labels.get(diagnostic['code'], diagnostic['code'])}: "
                + ", ".join(str(item) for item in diagnostic["items"])
            )
        for diagnostic in res["warnings"]:
            print(
                f"  Warnung · {labels.get(diagnostic['code'], diagnostic['code'])}: "
                + ", ".join(str(item) for item in diagnostic["items"])
            )
        if res["ok"] and not res["warnings"]:
            print("  Alles konsistent.")
        elif res["ok"]:
            print("  Keine Integritätsfehler.")
    if not res["ok"]:
        raise SystemExit(1)


def cmd_serve(args):
    from .runtime import load_web_runtime

    uvicorn = load_web_runtime()
    local_host = {"0.0.0.0": "127.0.0.1", "::": "::1"}.get(args.host, args.host)
    if ":" in local_host and not local_host.startswith("["):
        local_host = f"[{local_host}]"
    url = f"http://{local_host}:{args.port}"
    if args.json:
        dump({
            "status": "starting",
            "host": args.host,
            "port": args.port,
            "url": url,
            "reload": args.reload,
        })
        sys.stdout.flush()
    else:
        print(f"Gusto startet auf {url}  (Strg+C zum Beenden)")
    uvicorn.run("gusto.web:app", host=args.host, port=args.port, reload=args.reload)


def cmd_service(args):
    from . import service

    try:
        result = service.service_action(
            args.command,
            app_root=args.app_root,
            dry_run=args.dry_run,
        )
    except (OSError, ValueError, service.ServiceError) as error:
        raise ValueError(f"Service-Aktion fehlgeschlagen: {error}") from error

    if args.json:
        dump(result)
        return
    if args.dry_run:
        print(f"Geplant: Gusto {args.command}")
        if result.get("command"):
            print("  " + subprocess.list2cmdline(result["command"]))
        return
    labels = {
        "status": "Status",
        "start": "Gusto wurde gestartet.",
        "stop": "Gusto wurde gestoppt.",
        "restart": "Gusto wurde neu gestartet.",
    }
    if args.command == "status":
        state = "läuft" if result["running"] else "ist gestoppt"
        print(f"Gusto {state} (Version {result['version']}).")
    else:
        print(labels[args.command])
    if result.get("url"):
        print(f"  {result['url']}")


def cmd_update(args):
    from . import update

    try:
        result = update.run_update(
            app_root=args.app_root,
            manifest_url=args.manifest_url,
            check=args.check,
            dry_run=args.dry_run,
        )
    except update.UpdateError as error:
        if args.json and error.result:
            dump(error.result)
            raise SystemExit(1) from None
        raise ValueError(f"Update fehlgeschlagen: {error}") from error
    except (OSError, ValueError) as error:
        raise ValueError(f"Update fehlgeschlagen: {error}") from error

    if args.json:
        dump(result)
        return
    status = result["status"]
    if status == "current":
        print(f"Gusto ist aktuell (Version {result['current_version']}).")
    elif status == "update_available":
        print(
            f"Update verfügbar: {result['current_version']} → "
            f"{result['latest_version']}"
        )
    elif status == "dry_run":
        print(
            f"Geplantes Update: {result['current_version']} → "
            f"{result['latest_version']}"
        )
    elif status == "updated":
        print(
            f"Gusto wurde von {result['previous_version']} auf "
            f"{result['current_version']} aktualisiert."
        )


def _uninstall_choice(
    args,
    targets,
    *,
    interactive: bool,
    input_func: Callable[[str], str] = input,
) -> bool | None:
    """Return whether to delete data; ``None`` means an interactive cancel."""
    from . import uninstall

    if args.keep_data:
        return False
    if args.delete_data:
        if args.yes or args.dry_run:
            return True
        if not interactive:
            raise uninstall.UninstallError(
                "--delete-data braucht in nicht-interaktiven Aufrufen zusätzlich --yes."
            )
        answer = input_func(
            f"Alle Rezepte und Daten unter '{targets.data}' löschen? "
            "Tippe exakt DATEN LÖSCHEN: "
        )
        if answer != "DATEN LÖSCHEN":
            return None
        return True

    if not interactive:
        raise uninstall.UninstallError(
            "Wähle --keep-data oder --delete-data; mit --delete-data ist --yes nötig."
        )

    print(f"Installation: {targets.application}")
    print(f"Daten:        {targets.data}")
    print()
    print("Was soll entfernt werden?")
    print("  1  Nur die Gusto-App (Rezepte und Daten behalten)")
    print("  2  Gusto-App und alle Rezepte/Daten unwiderruflich löschen")
    print("  3  Abbrechen")
    while True:
        choice = input_func("Auswahl [1-3]: ").strip()
        if choice == "1":
            return False
        if choice == "3":
            return None
        if choice == "2":
            if args.yes or args.dry_run:
                return True
            answer = input_func("Tippe zur Bestätigung exakt DATEN LÖSCHEN: ")
            return True if answer == "DATEN LÖSCHEN" else None
        print("Bitte 1, 2 oder 3 eingeben.")


def cmd_uninstall(args):
    from . import uninstall

    try:
        targets = uninstall.discover_targets()
        interactive = not args.json and sys.stdin.isatty()
        delete_data = _uninstall_choice(
            args, targets, interactive=interactive,
        )
        if delete_data is None:
            if args.json:
                dump({"status": "cancelled"})
            else:
                print("Deinstallation abgebrochen.")
            return
        result = uninstall.perform_uninstall(
            targets,
            delete_data=delete_data,
            dry_run=args.dry_run,
            interactive=interactive,
        )
    except uninstall.UninstallError as error:
        if error.result:
            if args.json:
                dump(error.result)
            else:
                print(str(error), file=sys.stderr)
                print(f"  App:   {error.result['application_path']}", file=sys.stderr)
                print(f"  Daten: {error.result['data_path']}", file=sys.stderr)
            raise SystemExit(1) from None
        sys.exit(f"Deinstallation fehlgeschlagen: {error}")
    except (OSError, ValueError) as error:
        sys.exit(f"Deinstallation fehlgeschlagen: {error}")

    if args.json:
        dump(result)
        return
    if args.dry_run:
        print("Geplante Deinstallation (es wurde nichts verändert):")
    else:
        print("Gusto wird nach dem Ende dieses Befehls entfernt.")
    print(f"  App:   {result['application_path']}")
    if result["delete_data"]:
        print(f"  Daten: löschen ({result['data_path']})")
    else:
        print(f"  Daten: behalten ({result['data_path']})")
    if result.get("log_path"):
        print(f"  Log:   {result['log_path']}")


def register_discovery(sub, base) -> None:
    sp = sub.add_parser(
        "install-skill",
        help="Gebündelten Gusto-Agent-Skill lokal installieren.",
    )
    sp.add_argument("--json", action="store_true",
                    help="Ergebnis als JSON auf stdout. Ohne Flag: lesbarer Text.")
    sp.add_argument(
        "host", metavar="HOST",
        help="Agent-Host; derzeit wird vbot unterstützt.",
    )
    sp.add_argument(
        "--dry-run", action="store_true",
        help="Quelle und Ziel prüfen. Ohne Flag: die Zielkopie vollständig ersetzen.",
    )
    sp.set_defaults(func=cmd_install_skill)

    sp = sub.add_parser("home", parents=[base],
                        help="Lokale Datenauswahl und gewählten Server mit Erreichbarkeit anzeigen.")
    sp.set_defaults(func=cmd_home)


def register_check(sub, base) -> None:
    sp = sub.add_parser(
        "check", parents=[base],
        help="Integrität aller Gusto-Daten prüfen; Fehler liefern Status 1.",
    )
    sp.add_argument(
        "--offline", action="store_true",
        help="Recovery-Check direkt auf der lokalen Dateninstanz. Ohne Flag: gewählten Server prüfen.",
    )
    sp.set_defaults(func=cmd_check)


def register_lifecycle(sub, base) -> None:
    sp = sub.add_parser("serve", parents=[base], help="Entwicklungs-/Recovery-Dienst im Vordergrund starten; läuft bis Strg+C. Installierter Dienst: start.")
    sp.add_argument("--host", default="0.0.0.0", help="Bind-Adresse. Ohne Angabe: 0.0.0.0 (alle Netzwerkschnittstellen).")
    sp.add_argument("--port", type=_port, default=8000,
                    help="TCP-Port zwischen 1 und 65535. Ohne Angabe: 8000.")
    sp.add_argument("--reload", action="store_true", help="Bei Quelländerungen neu laden. Ohne Flag: kein Auto-Reload.")
    sp.set_defaults(func=cmd_serve)

    for command, help_text in (
        ("status", "Status des Gusto-User-Dienstes anzeigen."),
        ("start", "Gusto-User-Dienst starten."),
        ("stop", "Gusto-User-Dienst stoppen."),
        ("restart", "Gusto-User-Dienst neu starten."),
    ):
        sp = sub.add_parser(command, parents=[base], help=help_text)
        sp.add_argument(
            "--app-root", type=Path,
            help="Verwaltete App-Wurzel. Ohne Angabe: die lokale Installation verwenden.",
        )
        sp.add_argument(
            "--dry-run", action="store_true",
            help="Service-Aufruf anzeigen. Ohne Flag: Aktion ausführen (status liest nur).",
        )
        sp.set_defaults(func=cmd_service, command=command)

    sp = sub.add_parser(
        "update", parents=[base],
        help="Gusto auf das neueste verifizierte Release aktualisieren.",
    )
    sp.add_argument(
        "--check", action="store_true",
        help="Nur prüfen, ob ein Update verfügbar ist. Ohne Flag: Update installieren.",
    )
    sp.add_argument(
        "--dry-run", action="store_true",
        help="Update prüfen und planen. Ohne Flag und ohne --check: Update installieren.",
    )
    sp.add_argument(
        "--app-root", type=Path,
        help="Verwaltete App-Wurzel. Ohne Angabe: die lokale Installation verwenden.",
    )
    sp.add_argument(
        "--manifest-url",
        help=argparse.SUPPRESS,
    )
    sp.set_defaults(func=cmd_update)

    sp = sub.add_parser(
        "uninstall", parents=[base],
        help="Lokale Installation entfernen; nicht-interaktiv --keep-data oder --delete-data --yes angeben.",
    )
    removal = sp.add_mutually_exclusive_group()
    removal.add_argument(
        "--keep-data", action="store_true",
        help="Nur die App entfernen; Rezepte und Daten behalten.",
    )
    removal.add_argument(
        "--delete-data", action="store_true",
        help="App sowie den aktiven Rezept-/Datenordner entfernen.",
    )
    sp.add_argument(
        "--yes", action="store_true",
        help="Bestätigung für --delete-data in nicht-interaktiven Aufrufen.",
    )
    sp.add_argument(
        "--dry-run", action="store_true",
        help="Ziele anzeigen, ohne Autostart, App oder Daten zu verändern.",
    )
    sp.set_defaults(func=cmd_uninstall)
