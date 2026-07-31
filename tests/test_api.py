"""Hermetic contract checks for the versioned anonymous command API."""
from __future__ import annotations

import asyncio
import base64
import json
import os
import sys
import tempfile
from pathlib import Path


HOME = Path(tempfile.mkdtemp(prefix="gusto-api-test-"))
os.environ["GUSTO_HOME"] = os.fspath(HOME)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from gusto import __version__, api, core  # noqa: E402


checks = 0


def check(condition, message):
    global checks
    assert condition, message
    checks += 1


app = FastAPI()
app.include_router(api.router)
client = TestClient(app)


def command(operation, arguments=None, attachments=None, status=200):
    response = client.post("/api/v1/command", json={
        "operation": operation,
        "arguments": arguments or {},
        "attachments": attachments or [],
    })
    check(
        response.status_code == status,
        f"{operation} must return HTTP {status}, got {response.status_code}: "
        f"{response.text}",
    )
    payload = response.json()
    check(
        payload.get("ok") is (status == 200),
        f"{operation} must use the command response envelope",
    )
    return payload.get("result") if status == 200 else payload


def attachment(name, filename, content):
    return {
        "name": name,
        "filename": filename,
        "content_base64": base64.b64encode(content).decode("ascii"),
    }


def png_pixel():
    return base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk"
        "+A8AAQUBAScY42YAAAAASUVORK5CYII="
    )


def parse_sse(chunk):
    line = chunk.strip().splitlines()[0]
    check(line.startswith("data: "), "SSE changes must use a data field")
    return json.loads(line.removeprefix("data: "))


class ConnectedRequest:
    async def is_disconnected(self):
        return False


async def check_event_stream():
    stream = api.event_stream(
        ConnectedRequest(), poll_seconds=0.001, heartbeat_seconds=60,
    )
    initial = parse_sse(await anext(stream))
    item = core.shopping_add("SSE-Nachricht")
    changed = parse_sse(await asyncio.wait_for(anext(stream), timeout=1))
    await stream.aclose()
    check(
        changed["revision"] > initial["revision"],
        "SSE must deliver a later revision after a Core mutation",
    )
    check(
        changed["resources"] == ["shopping"],
        "SSE must identify the changed resource",
    )
    core.shopping_remove(item.id)

    replay_after = core.change_state()["revision"]
    first = core.shopping_add("Replay A")
    second = core.shopping_add("Replay B")
    replay = api.event_stream(
        ConnectedRequest(), after_revision=replay_after,
        poll_seconds=0.001, heartbeat_seconds=60,
    )
    replayed_first = parse_sse(await anext(replay))
    replayed_second = parse_sse(await anext(replay))
    check(
        [replayed_first["revision"], replayed_second["revision"]]
        == [replay_after + 1, replay_after + 2],
        "SSE after=N must replay retained events in revision order",
    )
    third = core.shopping_add("Replay C")
    live_after_replay = parse_sse(
        await asyncio.wait_for(anext(replay), timeout=1),
    )
    await replay.aclose()
    check(
        live_after_replay["revision"] == replay_after + 3,
        "SSE must continue after replay without duplicating an event",
    )
    for created in (first, second, third):
        core.shopping_remove(created.id)


def check_compacted_event_replay():
    before = core.change_state()["revision"]
    core._record_change(("catalog",))
    for _ in range(core._CHANGE_HISTORY_LIMIT):
        core._record_change(("shopping",))
    replay = core.change_events(before)
    check(
        replay[-1]["revision"] == before + core._CHANGE_HISTORY_LIMIT + 1,
        "compacted replay must reach the current revision",
    )
    check(
        any("catalog" in event["resources"] for event in replay),
        "compacted replay must retain resources from dropped events",
    )


def main():
    core.data_dir().mkdir(parents=True)
    core.categories_path().write_text(json.dumps({
        "diet": {"label": "Ernährung", "tags": ["vegan"]},
        "dish": {"label": "Art", "tags": ["suppe"]},
    }), encoding="utf-8")

    health = client.get("/api/v1/health")
    check(health.status_code == 200, "health must be reachable anonymously")
    health_data = health.json()
    check(
        health_data == {
            "ok": True,
            "status": "ready",
            "version": __version__,
            "data_path": os.fspath(HOME.resolve()),
            "revision": 0,
            "resources": [],
        },
        "health must expose version, active store and initial revision",
    )

    # Envelope, unknown/lifecycle operations, and strict argument validation.
    bad_json = client.post(
        "/api/v1/command",
        content=b"{",
        headers={"Content-Type": "application/json"},
    )
    check(
        bad_json.status_code == 400 and bad_json.json()["ok"] is False,
        "malformed JSON must return the error envelope",
    )
    command("serve", status=400)
    command("update", status=400)
    response = client.post("/api/v1/command", json={
        "operation": "catalog.list",
        "arguments": {},
        "unexpected": True,
    })
    check(response.status_code == 400, "unknown envelope fields must fail")
    invalid_after = client.get("/api/v1/events?after=-1")
    check(
        invalid_after.status_code == 400
        and invalid_after.json()["ok"] is False,
        "SSE after must reject negative revisions before streaming",
    )

    # Catalog, text editing, cooking log, diagnostics, and suggestions.
    soup = command("recipe.create", {
        "title": "Kartoffelsuppe",
        "tags": ["vegan", "suppe"],
        "duration_min": 30,
        "servings": 4,
    })
    salad = command("recipe.create", {
        "title": "Salat",
        "tags": ["vegan"],
    })
    check(
        [item["slug"] for item in command("catalog.list")]
        == ["kartoffelsuppe", "salat"],
        "catalog.list must sort recipes by title",
    )
    check(
        command("catalog.search", {"query": "Kartoffel", "match": "all",
                                   "tags": [], "max_time": 40})[0]["slug"]
        == soup["slug"],
        "catalog.search must delegate all filters to Core",
    )
    check(
        command("tags.list", {"all": True})[0]["label"] == "Ernährung",
        "tags.list must return Core facet groups",
    )
    category = command("categories.add", {
        "key": "season", "label": "Jahreszeit", "position": 1,
    })
    check(category == {
        "key": "season", "label": "Jahreszeit", "tags": [],
    }, "categories.add must return the created category")
    category = command("categories.set", {
        "key": "season", "label": "Saison", "position": 2,
    })
    check(category["label"] == "Saison"
          and command("categories.list")[1]["key"] == "season",
          "categories.set must update label and display position")
    category = command("categories.assign", {
        "key": "season", "tags": ["saisonal", "regional"],
    })
    check(category["tags"] == ["saisonal", "regional"],
          "categories.assign must expose the resulting ordered tags")
    category = command("categories.tag.move", {
        "key": "season", "tag": "regional", "position": 1,
    })
    check(category["tags"] == ["regional", "saisonal"],
          "categories.tag.move must preserve explicit tag order")
    check(command("categories.unassign", {"tags": ["regional"]})
          == {"unassigned": ["regional"]},
          "categories.unassign must report tags moved to Sonstige")
    removed_category = command("categories.remove", {"key": "season"})
    check(removed_category["removed"] is True,
          "categories.remove must report the removed category")
    command("categories.set", {"key": "missing", "label": "Fehlt"}, status=404)
    command("categories.add", {"key": "bad", "label": "X", "extra": True},
            status=400)
    shown = command("recipe.show", {"slug": soup["slug"]})
    check(
        shown["content"].startswith("# Kartoffelsuppe"),
        "recipe.show must include Markdown content",
    )
    content = "# Falscher Transporttitel\n\n## Zutaten\n\n- Kartoffeln\n- Brühe\n"
    content_result = command("recipe.content.set", {
        "slug": soup["slug"],
        "content": content,
    })
    check(
        content_result["content"].startswith("# Kartoffelsuppe\n"),
        "recipe.content.set must preserve the canonical metadata/H1 invariant",
    )
    updated = command("recipe.update", {
        "slug": soup["slug"],
        "title": "Kartoffelsuppe fein",
        "tags": ["vegan", "saisonal"],
        "duration_min": 25,
        "servings": None,
        "clear_duration": False,
        "clear_servings": True,
    })
    check(
        updated["title"] == "Kartoffelsuppe fein"
        and updated["duration_min"] == 25
        and updated["servings"] is None
        and updated["warnings"][0]["code"] == "uncategorized_tags",
        "recipe.update must preserve recipe shape and structured tag warnings",
    )
    cooked = command("recipe.cooked", {"slug": soup["slug"], "date": None})
    check(cooked["ok"] is True, "recipe.cooked must return the CLI result shape")
    check(
        command("log.list", {"days": 7})[-1]["slug"] == soup["slug"],
        "log.list must expose cooking history",
    )
    check(
        command("suggest.list", {"days": 7, "limit": 1})[0]["slug"]
        == salad["slug"],
        "suggest.list must return serialized recipes",
    )
    diagnostics = command("check.run")
    check(
        diagnostics["ok"] is True,
        "check.run must return complete diagnostics as a successful command",
    )

    # Attachments: strict shape/context, successful import, and cleanup.
    image_attachment = attachment("image", "pixel.png", png_pixel())
    temp_before = set(Path(tempfile.gettempdir()).glob("gusto-api-*"))
    image = command(
        "image.add",
        {"slug": soup["slug"], "role": "result", "caption": "Fertig",
         "cover": True},
        [image_attachment],
    )
    check(image["is_cover"] is True, "image.add must import its attachment")
    check(
        set(Path(tempfile.gettempdir()).glob("gusto-api-*")) == temp_before,
        "successful attachment materialization must be cleaned",
    )
    images = command("image.list", {"slug": soup["slug"]})
    check(images["cover_image_id"] == image["id"], "image.list must mark cover")
    changed_image = command("image.set", {
        "slug": soup["slug"], "id": image["id"],
        "role": "step", "caption": None,
    })
    check(changed_image["role"] == "step", "image.set must update metadata")
    cover = command(
        "image.cover", {"slug": soup["slug"], "id": image["id"]},
    )
    check(cover["cover_image_id"] == image["id"], "image.cover must select image")

    command(
        "image.add",
        {"slug": "missing"},
        [image_attachment],
        status=404,
    )
    check(
        set(Path(tempfile.gettempdir()).glob("gusto-api-*")) == temp_before,
        "failed commands must clean materialized attachments",
    )
    command(
        "image.add",
        {"slug": soup["slug"]},
        [{"name": "image", "filename": "pixel.png",
          "content_base64": "not base64"}],
        status=400,
    )
    command(
        "catalog.list", attachments=[image_attachment], status=400,
    )
    command(
        "image.add",
        {"slug": soup["slug"]},
        [attachment("image", "../pixel.png", png_pixel())],
        status=400,
    )
    previous_limit = api.MAX_ATTACHMENT_BYTES
    api.MAX_ATTACHMENT_BYTES = 2
    try:
        command(
            "image.add",
            {"slug": soup["slug"]},
            [attachment("image", "large.png", b"123")],
            status=413,
        )
    finally:
        api.MAX_ATTACHMENT_BYTES = previous_limit
    removed_image = command("image.remove", {
        "slug": soup["slug"], "id": image["id"],
    })
    check(removed_image["removed"] is True, "image.remove must remove owned media")

    # Favorite needs, aliases, ranked products, and optional product images.
    need = command("favorites.add", {
        "name": "Hafermilch", "aliases": ["Haferdrink"],
    })
    check(command("favorites.list")[0]["id"] == need["id"],
          "favorites.list must serialize needs")
    check(command("favorites.show", {"need": need["id"]})["name"] == "Hafermilch",
          "favorites.show must resolve an id")
    check(command("favorites.match", {"text": " Haferdrink "})["id"] == need["id"],
          "favorites.match must use Core exact matching")
    command("favorites.alias.add", {"need": need["id"], "alias": "Hafer Drink"})
    aliases = command(
        "favorites.alias.remove",
        {"need": need["id"], "alias": "Hafer Drink"},
    )
    check("Hafer Drink" not in aliases["aliases"],
          "favorite aliases must be mutable through the API")
    first_product = command(
        "favorites.product.add",
        {"need": need["id"], "name": "Barista", "brand": "Oat",
         "store": "", "note": ""},
        [image_attachment],
    )
    second_product = command("favorites.product.add", {
        "need": need["id"], "name": "Natur", "brand": "Oat",
    })
    set_product = command("favorites.product.set", {
        "need": need["id"], "id": first_product["id"],
        "name": "Barista Plus", "brand": None, "store": None, "note": None,
        "remove_image": True,
    })
    check(set_product["name"] == "Barista Plus"
          and set_product["image_filename"] == "",
          "favorites.product.set must support partial edits and image removal")
    moved = command("favorites.product.move", {
        "need": need["id"], "id": second_product["id"], "position": 1,
    })
    check(moved["products"][0]["id"] == second_product["id"],
          "favorites.product.move must preserve manual ranking")
    after_product_remove = command("favorites.product.remove", {
        "need": need["id"], "id": second_product["id"],
    })
    check(len(after_product_remove["products"]) == 1,
          "favorites.product.remove must return the owning need")
    renamed_need = command("favorites.set", {
        "need": need["id"], "name": "Haferdrink",
    })
    check(renamed_need["name"] == "Haferdrink",
          "favorites.set must update the need")

    # Shopping commands, including idempotent actions and ingredient import.
    free_item = command("shopping.add", {
        "text": "Äpfel", "quantity": "2", "source": None,
    })
    many = command("shopping.add_many", {"texts": ["Brot", "Salz"]})
    check(len(many) == 2, "shopping.add_many must preserve the requested group")
    imported = command("shopping.add_recipe", {"slug": soup["slug"]})
    check(len(imported) == 2, "shopping.add_recipe must import each ingredient")
    check(len(command("shopping.list", {"pending": True})) == 5,
          "shopping.list must expose pending visible items")
    checked = command("shopping.check", {"id": free_item["id"]})
    check(checked["checked"] is True, "shopping.check must set checked=true")
    same_revision = core.change_state()["revision"]
    command("shopping.check", {"id": free_item["id"]})
    check(core.change_state()["revision"] == same_revision,
          "idempotent shopping.check must not emit a new revision")
    unchecked = command("shopping.uncheck", {"id": free_item["id"]})
    check(unchecked["checked"] is False, "shopping.uncheck must reopen an item")
    removed = command("shopping.remove", {"id": many[0]["id"]})
    check(removed == {"id": many[0]["id"], "deleted": True},
          "shopping.remove must preserve its CLI result shape")
    command("shopping.check", {"id": many[1]["id"]})
    check(command("shopping.remove_done")["removed"] == 1,
          "shopping.remove_done must tombstone checked visible items")
    check(command("shopping.clear")["removed"] == 3,
          "shopping.clear must tombstone every remaining visible item")

    merge_revision = core.change_state()["revision"]
    core.shopping_merge([item.to_dict() for item in core.shopping_load()])
    check(core.change_state()["revision"] == merge_revision,
          "idempotent PWA full-state merge must not emit an event")

    # Reversible archive lifecycle exposed as domain commands, not app lifecycle.
    archived = command("recipe.archive", {"slug": salad["slug"]})
    check(archived["archived"] is True, "recipe.archive must be reversible")
    check(command("archive.list")[0]["slug"] == salad["slug"],
          "archive.list must serialize snapshots")
    check(command("archive.show", {"slug": salad["slug"]})["content"],
          "archive.show must include Markdown content")
    restored = command("archive.restore", {"slug": salad["slug"]})
    check(restored["recipe"]["slug"] == salad["slug"],
          "archive.restore must return the active recipe")
    command("recipe.archive", {"slug": salad["slug"]})
    purged = command("archive.purge", {"slug": salad["slug"], "yes": True})
    check(purged["purged"] is True, "archive.purge must require explicit yes")

    # Every operation rejects an invalid argument envelope consistently.
    for operation in api._OPERATIONS:
        result = command(operation, {"__invalid__": True}, status=400)
        check(isinstance(result["error"], str) and result["error"],
              f"{operation} must explain invalid input")

    # Representative entity lookups distinguish not-found from invalid input.
    for operation, arguments, attachments in [
        ("recipe.show", {"slug": "missing"}, None),
        ("recipe.content.set", {"slug": "missing", "content": "# Missing\n"}, None),
        ("recipe.update", {"slug": "missing", "title": "Missing"}, None),
        ("recipe.cooked", {"slug": "missing"}, None),
        ("recipe.archive", {"slug": "missing"}, None),
        ("archive.show", {"slug": "missing"}, None),
        ("archive.restore", {"slug": "missing"}, None),
        ("archive.purge", {"slug": "missing", "yes": True}, None),
        ("image.list", {"slug": "missing"}, None),
        ("image.add", {"slug": "missing"}, [image_attachment]),
        ("favorites.show", {"need": "missing"}, None),
        ("favorites.set", {"need": "missing", "name": "X"}, None),
        ("favorites.product.add",
         {"need": "missing", "name": "X", "brand": "Y"}, None),
        ("shopping.add",
         {"text": "X", "source": "missing"}, None),
        ("shopping.add_recipe", {"slug": "missing"}, None),
        ("shopping.check", {"id": "missing"}, None),
        ("shopping.uncheck", {"id": "missing"}, None),
        ("shopping.remove", {"id": "missing"}, None),
    ]:
        command(operation, arguments, attachments, status=404)

    removed_need = command("favorites.remove", {"need": need["id"]})
    check(removed_need["removed"] is True,
          "favorites.remove must return the removed id")

    asyncio.run(check_event_stream())
    check_compacted_event_replay()

    final_health = client.get("/api/v1/health").json()
    check(final_health["revision"] > 0,
          "health must expose the latest persisted revision")
    check(isinstance(final_health["resources"], list),
          "health must expose the latest affected resources")

    print(f"OK - {checks} API checks passed (GUSTO_HOME={HOME})")


if __name__ == "__main__":
    main()
