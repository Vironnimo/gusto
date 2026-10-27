"""Backup and restore through the selected service."""
from __future__ import annotations

from . import client
from ._cli_terminal import dump, text


def cmd_create(args):
    result = client.backup_download(args.file, server_url=args.server)
    if args.json:
        dump(result)
    else:
        print(f"Backup gespeichert: {result['path']} ({result['bytes']} Bytes)")


def cmd_preview(args):
    result = client.backup_upload(args.file, server_url=args.server)
    if args.json:
        dump(result)
    else:
        print("Wiederherstellungsvorschau:")
        labels = {
            "recipe_count": "Rezepte", "archive_count": "Archivrezepte",
            "favorite_need_count": "Einkaufsbedarfe", "shopping_item_count": "Einkaufsposten gesamt",
            "shopping_visible_count": "Sichtbare Einkaufsposten", "log_count": "Kocheinträge",
            "file_count": "Dateien", "total_bytes": "Bytes", "image_count": "Rezeptbilder",
            "favorite_image_count": "Produktbilder", "archive_image_count": "Archivbilder",
            "added": "Dateien hinzugefügt", "removed": "Dateien entfernt", "replaced": "Dateien ersetzt",
        }
        for key, value in result["counts"].items():
            print(f"  {labels.get(key, text(key))}: {value}")
        for key, value in result["effects"].items():
            print(f"  {labels.get(key, text(key))}: {value}")
        for warning in result["warnings"]:
            print(f"  Hinweis: {text(warning)}")
        print(f"  Token: {result['token']}")


def cmd_restore(args):
    result = client.backup_upload(args.file, server_url=args.server, token=args.token)
    if args.json:
        dump(result)
    else:
        print(f"Backup wiederhergestellt; Sicherheitsbackup: {result['safety_backup_id']}")


def cmd_safety(args):
    result = client.backup_download(args.file, server_url=args.server, safety_id=args.id)
    if args.json:
        dump(result)
    else:
        print(f"Sicherheitsbackup gespeichert: {result['path']} ({result['bytes']} Bytes)")


def register_commands(sub, base):
    group = sub.add_parser("backup", help="Backup erstellen oder nach Vorschau wiederherstellen.")
    actions = group.add_subparsers(dest="backup_command", required=True)
    create = actions.add_parser("create", parents=[base], help="Backup vom Server in eine neue lokale Datei schreiben.")
    create.add_argument("--file", required=True, help="Neue lokale ZIP-Datei; bestehende Dateien werden nicht ersetzt.")
    create.set_defaults(func=cmd_create)
    preview = actions.add_parser("preview", parents=[base], help="Backup und Wiederherstellung prüfen, ohne Daten zu ändern.")
    preview.add_argument("--file", required=True, help="Lokale ZIP-Backup-Datei.")
    preview.set_defaults(func=cmd_preview)
    restore = actions.add_parser("restore", parents=[base], help="Geprüftes Backup wiederherstellen; erstellt zuvor ein Sicherheitsbackup.")
    restore.add_argument("--file", required=True, help="Dieselbe lokale ZIP-Backup-Datei wie bei preview.")
    restore.add_argument("--token", required=True, help="Token aus der aktuellen Vorschau für dieses Backup und den Serverzustand.")
    restore.set_defaults(func=cmd_restore)
    safety = actions.add_parser("safety-download", parents=[base], help="Sicherheitsbackup nach Wiederherstellung herunterladen.")
    safety.add_argument("id", help="safety_backup_id aus backup restore.")
    safety.add_argument("--file", required=True, help="Neue lokale ZIP-Datei; bestehende Dateien werden nicht ersetzt.")
    safety.set_defaults(func=cmd_safety)
