"""Independent CLI-to-command protocol contracts, through the public entry point."""
from __future__ import annotations

import base64
import json
from unittest.mock import patch

from _cli_support import HOME, check, invoke
from test_agent_contract import EXPECTED_COMMANDS


def check_forwarding():
    from gusto import cli

    image = HOME / "mapping-image.bin"
    image.write_bytes(b"mapping\x00\xff")
    text = HOME / "mapping-content.md"
    text.write_bytes(b"# New title\r\n\r\n- Salt\r\n")
    upload = [{"name": "image", "filename": image.name,
               "content_base64": base64.b64encode(image.read_bytes()).decode("ascii")}]
    # Explicit expectations are deliberately independent of handlers and parser defaults.
    cases = [
        (["list", "--tag", " pasta , vegan ", "--tag", "quick", "--max-time", "30"],
         "catalog.list", {"tags": ["pasta", "vegan", "quick"], "max_time": 30}),
        (["search", "green soup", "--match", "all", "--tag", "one,two", "--tag", "three", "--max-time", "15"],
         "catalog.search", {"query": "green soup", "match": "all", "tags": ["one", "two", "three"], "max_time": 15}),
        (["tags", "--all"], "tags.list", {"all": True}),
        (["categories", "list"], "categories.list", None),
        (["categories", "add", "diet", "Diet", "--position", "2"], "categories.add", {"key": "diet", "label": "Diet", "position": 2}),
        (["categories", "set", "diet", "--label", "New", "--position", "1"], "categories.set", {"key": "diet", "label": "New", "position": 1}),
        (["categories", "remove", "diet"], "categories.remove", {"key": "diet"}),
        (["categories", "assign", "diet", "vegan", "quick"], "categories.assign", {"key": "diet", "tags": ["vegan", "quick"]}),
        (["categories", "unassign", "vegan", "quick"], "categories.unassign", {"tags": ["vegan", "quick"]}),
        (["categories", "tag-move", "diet", "vegan", "2"], "categories.tag.move", {"key": "diet", "tag": "vegan", "position": 2}),
        (["show", "soup"], "recipe.show", {"slug": "soup"}),
        (["new", "Green soup", "--tags", "green, quick", "--duration", "20", "--servings", "3"], "recipe.create",
         {"title": "Green soup", "tags": ["green", "quick"], "duration_min": 20, "servings": 3}),
        (["new", "Complete soup", "--file", str(text)], "recipe.create",
         {"title": "Complete soup", "tags": [], "duration_min": None, "servings": None,
          "content": "# New title\n\n- Salt\n"}),
        (["set", "soup", "--file", str(text), "--title", "Together", "--revision", "rev"], "recipe.update",
         {"slug": "soup", "title": "Together", "tags": None, "duration_min": None,
          "servings": None, "clear_duration": False, "clear_servings": False,
          "revision": "rev", "content": "# New title\n\n- Salt\n"}),
        (["content", "set", "soup", "--file", str(text), "--revision", "rev"], "recipe.content.set", {"slug": "soup", "content": "# New title\n\n- Salt\n", "revision": "rev"}),
        (["cooked", "soup", "--date", "2026-07-13"], "recipe.cooked", {"slug": "soup", "date": "2026-07-13"}),
        (["log", "--days", "7"], "log.list", {"days": 7}),
        (["suggest", "--days", "5", "--limit", "4"], "suggest.list", {"days": 5, "limit": 4}),
        (["check"], "check.run", None),
        (["set", "soup", "--title", "New", "--tags", "", "--clear-duration", "--clear-servings", "--revision", "rev"], "recipe.update",
         {"slug": "soup", "title": "New", "tags": [], "duration_min": None,
          "servings": None, "clear_duration": True, "clear_servings": True, "revision": "rev"}),
        (["delete", "soup"], "recipe.archive", {"slug": "soup"}),
        (["archive", "list"], "archive.list", None),
        (["archive", "show", "soup"], "archive.show", {"slug": "soup"}),
        (["archive", "restore", "soup"], "archive.restore", {"slug": "soup"}),
        (["archive", "purge", "soup", "--yes"], "archive.purge", {"slug": "soup", "yes": True}),
        (["image", "list", "soup"], "image.list", {"slug": "soup"}),
        (["image", "add", "soup", str(image), "--role", "step", "--caption", "Mix", "--cover"], "image.add",
         {"slug": "soup", "role": "step", "caption": "Mix", "cover": True}, upload),
        (["image", "set", "soup", "photo", "--role", "result", "--caption", "Done"], "image.set",
         {"slug": "soup", "id": "photo", "role": "result", "caption": "Done"}),
        (["image", "cover", "soup", "photo"], "image.cover", {"slug": "soup", "id": "photo"}),
        (["image", "remove", "soup", "photo"], "image.remove", {"slug": "soup", "id": "photo"}),
        (["favorites", "list"], "favorites.list", None),
        (["favorites", "show", "need", "--store", "Market"], "favorites.show", {"need": "need", "store": "Market"}),
        (["favorites", "match", "pizza dough", "--store", "Market"], "favorites.match", {"text": "pizza dough", "store": "Market"}),
        (["favorites", "add", "Dough", "--alias", "roll", "--alias", "ball"], "favorites.add", {"name": "Dough", "aliases": ["roll", "ball"]}),
        (["favorites", "set", "need", "--name", "New"], "favorites.set", {"need": "need", "name": "New"}),
        (["favorites", "remove", "need"], "favorites.remove", {"need": "need"}),
        (["favorites", "alias-add", "need", "roll"], "favorites.alias.add", {"need": "need", "alias": "roll"}),
        (["favorites", "alias-remove", "need", "roll"], "favorites.alias.remove", {"need": "need", "alias": "roll"}),
        (["favorites", "product-add", "need", "Product", "--brand", "Brand", "--store", "Market", "--note", "Note", "--image", str(image)], "favorites.product.add",
         {"need": "need", "name": "Product", "brand": "Brand", "store": "Market", "note": "Note"}, upload),
        (["favorites", "product-set", "need", "product", "--name", "New", "--brand", "Brand", "--store", "Market", "--note", "Note", "--image", str(image)], "favorites.product.set",
         {"need": "need", "id": "product", "name": "New", "brand": "Brand", "store": "Market", "note": "Note", "remove_image": False}, upload),
        (["favorites", "product-move", "need", "product", "2"], "favorites.product.move", {"need": "need", "id": "product", "position": 2}),
        (["favorites", "product-remove", "need", "product"], "favorites.product.remove", {"need": "need", "id": "product"}),
        (["shopping", "list", "--pending"], "shopping.list", {"pending": True}),
        (["shopping", "list", "--store", "REWE"], "shopping.list", {"pending": False, "store": "REWE"}),
        (["shopping", "add", "Salt", "--quantity", "2 g", "--source", "soup"], "shopping.add", {"text": "Salt", "quantity": "2 g", "source": "soup"}),
        (["shopping", "add-many", "Brot", "6 Eier"], "shopping.add_many", {"texts": ["Brot", "6 Eier"]}),
        (["shopping", "add-recipe", "soup"], "shopping.add_recipe", {"slug": "soup", "servings": None, "revision": None}),
        (["shopping", "preview-recipe", "soup", "--servings", "4"], "shopping.preview_recipe", {"slug": "soup", "servings": 4}),
        (["shopping", "check", "item"], "shopping.check", {"id": "item"}),
        (["shopping", "uncheck", "item"], "shopping.uncheck", {"id": "item"}),
        (["shopping", "remove", "item"], "shopping.remove", {"id": "item"}),
        (["shopping", "check", "first", "second"], "shopping.check_many", {"ids": ["first", "second"]}),
        (["shopping", "uncheck", "first", "second"], "shopping.uncheck_many", {"ids": ["first", "second"]}),
        (["shopping", "remove", "first", "second"], "shopping.remove_many", {"ids": ["first", "second"]}),
        (["shopping", "remove-done"], "shopping.remove_done", None),
        (["shopping", "clear"], "shopping.clear", None),
    ]
    covered = set()
    grouped = {"categories", "content", "archive", "image", "favorites", "shopping"}
    result = {"ok": True, "marker": "Zürich 東京"}
    for arguments, operation, expected, *attachments in cases:
        # Both global and leaf --server positions must reach the public client.
        for global_server in (False, True):
            argv = [*arguments, "--json"]
            argv = (["--server", "http://mapping.example:8123", *argv] if global_server
                    else [*argv, "--server", "http://mapping.example:8123"])
            with patch.object(cli.client, "command", return_value=result) as command:
                output = invoke(argv)
            check(output.returncode == 0 and not output.stderr
                  and json.loads(output.stdout) == result,
                  f"{' '.join(arguments)} must return the remote JSON unchanged")
            command.assert_called_once_with(operation, expected,
                                            attachments=attachments[0] if attachments else None,
                                            server_url="http://mapping.example:8123")
            check(True, f"{operation} operation, arguments and attachments must match")
        covered.add(" ".join(arguments[:2] if arguments[0] in grouped else arguments[:1]))
    local_or_editor = {"home", "serve", "status", "start", "stop", "restart",
                       "update", "uninstall", "install-skill", "edit",
                       "backup create", "backup preview", "backup restore", "backup safety-download"}
    check(covered == EXPECTED_COMMANDS - local_or_editor,
          "the independent forwarding matrix must cover every remote leaf")

