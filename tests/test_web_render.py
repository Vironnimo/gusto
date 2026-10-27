"""Hermetic checks for the web layer's Markdown rendering and photo pipeline.

Locks the XSS fix at the single Markdown render point: raw Markdown HTML
(<img onerror=...>) and dangerous URL schemes (javascript:/vbscript:/data:)
must not reach the templates that render content_html with |safe, while
normal German recipe markdown (## Zutaten, bullets, numbered steps) and safe
links survive. Also locks the _prepared_photo split: only preparation
failures become the "unreadable photo" notice, consumer-body failures stay
server-side errors.
"""
from __future__ import annotations

from io import BytesIO
from concurrent.futures import ThreadPoolExecutor
import json
import os
import re
import sys
import tempfile
import threading
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from PIL import Image
from fastapi.testclient import TestClient
from starlette.datastructures import UploadFile


sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import gusto  # noqa: E402, F401 - configure checkout temp before allocating data

HOME = Path(tempfile.mkdtemp(prefix="gusto-web-render-test-"))
os.environ["GUSTO_HOME"] = os.fspath(HOME)

from gusto import web, rendering # noqa: E402
import gusto.core.catalog.categories as core_categories  # noqa: E402
import gusto.core.catalog.images as core_images  # noqa: E402
import gusto.core.catalog.recipes as core_recipes  # noqa: E402
import gusto.core.catalog.snapshots as core_snapshots  # noqa: E402
import gusto.core.errors as core_errors  # noqa: E402
import gusto.core.favorites as core_favorites  # noqa: E402
import gusto.core.shopping as core_shopping  # noqa: E402
import gusto.core.storage as core_storage  # noqa: E402

checks = 0


def check(condition, message):
    global checks
    assert condition, message
    checks += 1


def upload_image(payload: bytes, filename: str) -> UploadFile:
    return UploadFile(file=BytesIO(payload), filename=filename)


def upload_png() -> UploadFile:
    buffer = BytesIO()
    Image.new("RGB", (4, 4), "red").save(buffer, format="PNG")
    buffer.seek(0)
    return UploadFile(file=buffer, filename="foto.png")


def test_xss_is_neutralized():
    poison = rendering.render_recipe(
        "# Titel\n\n"
        "<img src=x onerror=alert(1)>\n\n"
        "Text mit [klick](javascript:alert(1)), "
        "[fies](vbscript:msgbox) und [d](data:text/html,<b>x</b>).\n\n"
        "<script>alert(1)</script>\n"
    )
    lowered = poison.lower()
    check("onerror" not in lowered, "onerror handlers must be stripped")
    check("javascript:" not in lowered, "javascript: URLs must be rejected")
    check("vbscript:" not in lowered, "vbscript: URLs must be rejected")
    check("data:" not in lowered, "data: URLs must be rejected")
    check("<script" not in lowered, "script tags must be dropped")
    check('src="x"' in poison, "a plain img src must survive")
    check(">klick<" in poison and ">fies<" in poison,
          "the text of links with rejected URLs must survive")
    check("alert(1)" in poison,
          "dropped dangerous elements keep their text, escaped")


def test_normal_markdown_survives():
    rendered = rendering.render_recipe(
        "# Pasta Pomodoro\n\n"
        "## Zutaten\n\n"
        "- 500 g Tomaten\n- Basilikum\n\n"
        "## Zubereitung\n\n"
        "1. Backofen vorheizen\n"
        "2. Teig ausrollen\n\n"
        "Mehr auf [der Seite](https://example.com/rezept), "
        "[intern](/recipe/pasta), [Sprung](#zutaten) und "
        "[mit Titel](https://example.com/x \"Hinweis\").\n"
    )
    check("<h2>Zutaten</h2>" in rendered, "## headings must still render")
    check("<ul>" in rendered and "<li>500 g Tomaten</li>" in rendered,
          "bullets must still render")
    check("<ol>" in rendered and "<li>Backofen vorheizen</li>" in rendered,
          "numbered steps must still render")
    check('href="https://example.com/rezept"' in rendered,
          "https links must survive")
    check('href="/recipe/pasta"' in rendered, "relative links must survive")
    check('href="#zutaten"' in rendered, "anchors must survive")
    check('title="Hinweis"' in rendered, "link titles must survive")
    check("<h1>" not in rendered, "the H1 title line must stay dropped")

    structure = rendering.render_recipe(
        "> Notiz\n\n**fett** und *kursiv*\n\n"
        "| A | B |\n|---|---|\n| 1 | 2 |\n\n"
        "```python\nprint(1)\n```\n"
    )
    check("<blockquote>" in structure, "blockquotes must survive")
    check("<strong>fett</strong>" in structure and "<em>kursiv</em>" in structure,
          "emphasis must survive")
    check("<table>" in structure and "<th>A</th>" in structure
          and "<td>1</td>" in structure, "tables must render allowed cells")
    check("<pre>" in structure and "print(1)" in structure,
          "fenced code must survive")


def test_attribute_allowlist():
    sanitized = rendering.sanitize_html(
        '<td colspan="2" rowspan="3" onclick="evil()" style="x">zelle</td>'
        '<a href="/ok" title="t" class="c" onclick="evil()">link</a>'
        '<img src="/p.webp" alt="a" width="10" height="5" onerror="evil()">'
    )
    check('colspan="2"' in sanitized and 'rowspan="3"' in sanitized,
          "table cell span attributes must survive")
    check('<a href="/ok" title="t">link</a>' in sanitized,
          "safe link attributes must survive")
    check('src="/p.webp"' in sanitized and 'alt="a"' in sanitized
          and 'width="10"' in sanitized and 'height="5"' in sanitized,
          "safe img attributes must survive")
    check("onclick" not in sanitized and "onerror" not in sanitized,
          "event handlers must be stripped everywhere")
    check("style=" not in sanitized and 'class="c"' not in sanitized,
          "non-allowlisted attributes must be stripped")


def test_dropped_tags_keep_children():
    dropped = rendering.sanitize_html(
        "<iframe>schaden</iframe><div><p>bleibt</p></div><p>offen"
    )
    check("schaden" in dropped and "<p>bleibt</p>" in dropped,
          "dropped tags keep their content and allowed children")
    check("<iframe" not in dropped and "<div" not in dropped,
          "non-allowlisted tags must be dropped")
    check(dropped.endswith("<p>offen</p>"),
          "unclosed allowed tags must be closed at the end")


def test_shopping_error_notice_is_fixed_text():
    client = TestClient(web.app, follow_redirects=False)
    before = client.get("/api/shopping").json()
    response = client.post("/shopping/add", data={"text": "   "})
    check(response.status_code == 303, "empty shopping additions must redirect to a notice")
    notice = client.get(response.headers["location"])
    check(notice.status_code == 200 and 'class="error"' in notice.text,
          "the empty-add redirect must render a visible notice")
    check(client.get("/api/shopping").json() == before,
          "an empty addition must not change the shopping state")
    unknown = client.get("/shopping", params={"error": "<script>alert(1)</script>"})
    check('class="error"' not in unknown.text and "<script>alert(1)</script>" not in unknown.text,
          "foreign or unknown notice parameters must render no notice or executable HTML")


def test_return_destination_stays_local():
    client = TestClient(web.app, follow_redirects=False)
    need = core_favorites.favorite_add_need("Redirect-Prüfung")
    for value in ("//example.org", "/\\example.org", "/\t/example.org",
                  "/\r/example.org", "https://example.org"):
        response = client.post("/favorites/assign", data={
            "need_id": need.id, "alias": "Redirect-Alias", "return_to": value,
        })
        check(response.status_code == 303 and response.headers["location"] == "/shopping",
              "browser URL normalization must not turn a return path into an external redirect")
    local = "/favorites/einkauf?text=Milch%20%26%20Brot"
    response = client.post("/favorites/assign", data={
        "need_id": need.id, "alias": "Redirect-Alias", "return_to": local,
    })
    check(response.status_code == 303 and response.headers["location"] == local,
          "local return paths and encoded query values must survive")


def test_favorite_creation_error_keeps_shopping_context():
    client = TestClient(web.app, follow_redirects=False)
    core_favorites.favorite_add_need("Bereits vorhanden")
    alias = "2 & mehr Brötchen"
    response = client.post("/favorites/add", data={
        "name": "Bereits vorhanden", "alias": alias, "return_to": "/shopping",
    })
    location = response.headers["location"]
    check(response.status_code == 303 and urlsplit(location).path == "/favorites/match"
          and parse_qs(urlsplit(location).query).get("text") == [alias],
          "failed shopping-need creation must keep the original item context")
    page = client.get(location)
    check(page.status_code == 200 and 'class="error"' in page.text
          and "2 &amp; mehr Brötchen" in page.text,
          "the original item and error must be visible for correction")


def test_recipe_form_preserves_markdown_indentation():
    client = TestClient(web.app, follow_redirects=False)
    body = "    Code am Anfang\n\nText mit Zeilenumbruch  \nFortsetzung\n"
    created = client.post("/new", data={"title": "Einrückung", "content": body})
    check(created.status_code == 303, "a recipe with indented Markdown must save")
    path = created.headers["location"]
    check("<pre><code>Code am Anfang" in client.get(path).text,
          "the first indented line must remain a Markdown code block")
    form = client.get(path + "/edit")
    revision = re.search(r'name="revision" value="([^"]+)"', form.text).group(1)
    check("    Code am Anfang" in form.text and "Zeilenumbruch  \n" in form.text,
          "editing must keep indentation and Markdown hard-break spaces")
    rejected = client.post(path + "/edit", data={
        "title": "Einrückung", "duration": "invalid", "content": body, "revision": revision,
    })
    check(rejected.status_code == 400 and "    Code am Anfang" in rejected.text
          and "Zeilenumbruch  \n" in rejected.text,
          "a validation error must retain the submitted Markdown")
    saved = client.post(path + "/edit", data={"title": "Einrückung", "content": body,
                                              "revision": revision})
    check(saved.status_code == 303 and "<pre><code>Code am Anfang" in client.get(path).text,
          "saving edits must keep the leading Markdown indentation")


def test_recipe_edit_conflict_keeps_both_versions():
    client = TestClient(web.app, follow_redirects=False)
    recipe = core_recipes.add_recipe("Web Konflikt", content="## Zutaten\n\n- Salz")
    path = f"/recipe/{recipe.slug}/edit"
    page = client.get(path)
    token = re.search(r'name="revision" value="([^"]+)"', page.text).group(1)
    core_recipes.update_recipe(recipe.slug, content="## Zutaten\n\n- Pfeffer")
    response = client.post(path, data={"title": recipe.title, "revision": token,
                                       "content": "## Zutaten\n\n- Mein Entwurf"})
    check(response.status_code == 409 and "Mein Entwurf" in response.text
          and "Pfeffer" in response.text and 'name="revision"' not in response.text,
          "a stale form must preserve both versions without offering a silent overwrite")
    check("Pfeffer" in core_recipes.get(recipe.slug).content(),
          "a stale save must not change the current recipe")


def test_recipe_shopping_preview_is_bound_to_recipe():
    client = TestClient(web.app, follow_redirects=False)
    recipe = core_recipes.add_recipe("Web Portionen", servings=2,
                                    content="## Zutaten\n\n- 2 Eier\n- Salz")
    path = f"/recipe/{recipe.slug}/shopping"
    page = client.get(path, params={"servings": 4})
    token = re.search(r'name="revision" value="([^"]+)"', page.text).group(1)
    check(page.status_code == 200 and "4 Eier" in page.text and "Salz" in page.text
          and 'class="preview-warning"' in page.text,
          "the preview must show scaled ingredients and unscaled warnings")
    before = core_shopping.shopping_list()
    changed = client.post(path, data={"servings": "6", "preview_servings": "4", "revision": token})
    check(changed.status_code == 303 and "changed=1" in changed.headers["location"]
          and core_shopping.shopping_list() == before,
          "changing portions without recalculating must require another review")
    core_recipes.update_recipe(recipe.slug, content="## Zutaten\n\n- 3 Eier")
    before = core_shopping.shopping_list()
    response = client.post(path, data={"servings": "4", "preview_servings": "4", "revision": token})
    check(response.status_code == 303 and core_shopping.shopping_list() == before,
          "a stale preview must not import ingredients")
    page = client.get(path, params={"servings": 4})
    token = re.search(r'name="revision" value="([^"]+)"', page.text).group(1)
    response = client.post(path, data={"servings": "4", "preview_servings": "4", "revision": token})
    check(response.status_code == 303 and any(item.text == "6 Eier" for item in core_shopping.shopping_list()),
          "confirmation must import exactly the current scaled preview")


def test_backup_forms_require_confirmation_and_matching_upload():
    client = TestClient(web.app, follow_redirects=False)
    preview = {"token": "preview-token", "counts": {"recipe_count": 1, "archive_count": 0,
               "favorite_need_count": 0, "shopping_visible_count": 0, "log_count": 0,
               "image_count": 0, "archive_image_count": 0, "favorite_image_count": 0,
               "file_count": 1, "total_bytes": 4},
               "effects": {"added": 1, "replaced": 0, "removed": 0}, "warnings": []}
    upload = {"archive": ("sicherung.zip", b"ZIPDATA", "application/zip")}
    with patch.object(web.core_backup, "preview_restore", return_value=preview) as inspect, \
            patch.object(web.core_backup, "restore_backup", return_value={"safety_backup_id": "a" * 32}) as restore:
        page = client.post("/backup/preview", files=upload)
        check(page.status_code == 200 and 'value="preview-token"' in page.text
              and inspect.call_args.args == (b"ZIPDATA",), "preview must show the reviewed archive token")
        rejected = client.post("/backup/restore", files=upload, data={"token": "preview-token"})
        check(rejected.status_code == 400 and not restore.called,
              "restore without explicit confirmation must never call Core")
        response = client.post("/backup/restore", files=upload,
                               data={"token": "preview-token", "confirm": "1"})
        check(response.status_code == 303 and restore.call_args.args == (b"ZIPDATA", "preview-token")
              and "restored=" in response.headers["location"],
              "confirmed restore must pass the chosen archive and preview token to Core")
    with patch.object(web, "MAX_BACKUP_BYTES", 4), \
            patch.object(web.core_backup, "preview_restore") as inspect, \
            patch.object(web.core_backup, "restore_backup") as restore:
        oversized = client.post("/backup/preview", files=upload)
        rejected = client.post("/backup/restore", files=upload,
                               data={"token": "preview-token", "confirm": "1"})
        check(oversized.status_code == 400 and rejected.status_code == 400
              and not inspect.called and not restore.called,
              "oversized uploads must be rejected before preview or restore reaches Core")


def test_backup_restore_rejects_stale_preview_without_writes():
    client = TestClient(web.app, follow_redirects=False)
    download = client.get("/backup/download")
    check(download.status_code == 200 and download.headers["content-type"].startswith("application/zip"),
          "backup download must return the ZIP")
    upload = {"archive": ("sicherung.zip", download.content, "application/zip")}
    page = client.post("/backup/preview", files=upload)
    token = re.search(r'name="token" value="([^"]+)"', page.text).group(1)
    core_shopping.shopping_add("Nach der Vorschau")
    before = client.get("/api/shopping").json()
    response = client.post("/backup/restore", files=upload,
                           data={"token": token, "confirm": "1"})
    check(response.status_code == 400 and client.get("/api/shopping").json() == before,
          "stale restore preview must leave the current store unchanged")


def test_shopping_sync_keeps_server_responsive():
    started = threading.Event()
    release = threading.Event()

    def slow_merge(items, generation):
        started.set()
        if not release.wait(timeout=3):
            raise AssertionError("another request could not release the simulated lock")
        return {"items": [], "generation": "initial", "reset": False}

    # Both requests share the TestClient's application event loop. A blocking
    # merge on that loop would prevent health from releasing the barrier.
    with TestClient(web.app) as client, ThreadPoolExecutor(max_workers=1) as worker:
        with patch.object(core_shopping, "shopping_exchange", side_effect=slow_merge):
            pending = worker.submit(client.post, "/api/shopping/sync", json={"items": []})
            try:
                check(started.wait(timeout=2), "sync must reach the simulated lock")
                health = client.get("/api/v1/health")
            finally:
                release.set()
            response = pending.result(timeout=3)
    check(health.status_code == 200 and response.json()["items"] == [],
          "shopping sync waiting on a file lock must not block other requests")


def test_shopping_sync_endpoint_contract():
    client = TestClient(web.app)
    core_shopping.shopping_save([])
    first = core_shopping.shopping_add("Bestand", quantity="1 Packung")
    core_shopping.shopping_remove(first.id)
    tombstone = core_shopping.shopping_load()[0].to_dict()
    item = {"id": "endpoint-milk", "text": "Milch", "quantity": "2 l",
            "checked": False, "source": None, "created_at": "2026-06-23T12:00:00Z",
            "updated_at": "2026-06-23T12:00:00.001Z", "deleted": False}
    response = client.post("/api/shopping/sync", json={"items": [item]})
    expected = {first.id: tombstone, item["id"]: item}
    check(response.status_code == 200 and {i["id"]: i for i in response.json()["items"]} == expected
          and expected[first.id]["deleted"] is True,
          "sync must serialize accepted items and the complete server state including tombstones")
    for payload in ({}, {"items": None}, {"items": []}):
        response = client.post("/api/shopping/sync", json=payload)
        check(response.status_code == 200
              and {i["id"]: i for i in response.json()["items"]} == expected,
              "missing, null or empty items must return the full server state")
    before = client.get("/api/shopping").json()
    version = core_storage.change_state()["revision"]
    good = {**item, "id": "must-not-be-partially-saved"}
    for payload in ([], {"items": {}}, {"items": [good, {}]},
                    {"items": [good, {**item, "checked": "yes"}]},
                    {"items": [good, {**item, "source": "missing-recipe", "updated_at": "2099-01-01T00:00:00Z"}]}):
        response = client.post("/api/shopping/sync", json=payload)
        check(response.status_code == 400 and bool(response.json().get("error"))
              and client.get("/api/shopping").json() == before
              and core_storage.change_state()["revision"] == version,
              "invalid shapes or entries must reject the entire sync without writes or events")
    response = client.post("/api/shopping/sync", content="not-json",
                           headers={"Content-Type": "application/json"})
    check(response.status_code == 400 and client.get("/api/shopping").json() == before,
          "malformed JSON must be a definite rejection without changes")
    # Favorites JSON is the PWA's read-only offline catalog, including the
    # derived image URL that is absent from the persisted Core record.
    need = core_favorites.favorite_add_need("Endpoint-Produkt")
    core_favorites.favorite_add_product(need.id, "Ohne Foto", brand="Marke")
    photo = HOME / "endpoint-photo.png"
    Image.new("RGB", (4, 4), "blue").save(photo)
    product = core_favorites.favorite_add_product(need.id, "Mit Foto", brand="Marke", image=photo)
    response = client.get("/api/favorites")
    catalog = next(n for n in response.json()["needs"] if n["id"] == need.id)
    check(response.status_code == 200 and catalog["products"][0]["image_url"] is None
          and catalog["products"][1]["image_url"] == "/media/favorite/" + product.image_filename,
          "the preference endpoint must preserve ranking and serialize optional image URLs")
    core_shopping.shopping_save([])


def test_browser_writes_require_same_origin():
    client = TestClient(web.app, follow_redirects=False)
    rejected = [
        {"Origin": "https://foreign.example"},
        {"Origin": "http://testserver:81"},
        {"Origin": "null"},
        {"Origin": "http://testserver:invalid"},
        {"Sec-Fetch-Site": "cross-site"},
        {"Sec-Fetch-Site": "same-site"},
        {"Referer": "https://foreign.example/form"},
    ]
    payload = json.dumps({"operation": "shopping.add", "arguments": {"text": "Fremd"}})
    before = core_shopping.shopping_load()
    for headers in rejected:
        # text/plain avoids a browser CORS preflight, so the guard must run
        # before the mounted command API parses even a perfectly valid body.
        response = client.post("/api/v1/command", content=payload,
                               headers={**headers, "Content-Type": "text/plain"})
        check(response.status_code == 403 and response.json().get("ok") is False,
              f"foreign browser command must be definitely rejected: {headers}")
        response = client.post("/shopping/add", data={"text": "Fremd"}, headers=headers)
        check(response.status_code == 403,
              f"foreign browser forms must be rejected: {headers}")
    check(core_shopping.shopping_load() == before, "rejected writes must not change the store")
    for headers in ({}, {"Origin": "http://TESTSERVER:80", "Sec-Fetch-Site": "same-origin"},
                    {"Referer": "http://testserver/shopping"}, {"Sec-Fetch-Site": "none"}):
        response = client.post("/shopping/add", data={"text": "Erlaubt"}, headers=headers)
        check(response.status_code == 303,
              "same-origin browser forms and headerless CLI requests must keep working")
    response = client.post("/api/v1/command", content=payload)
    check(response.status_code == 200 and response.json().get("ok") is True,
          "the command API must remain usable by headerless CLI clients")
    response = client.get("/api/v1/health", headers=rejected[0])
    check(response.status_code == 200, "foreign read requests must remain read-only and usable")


def test_saved_mutations_never_look_rejected():
    client = TestClient(web.app, follow_redirects=False)
    recipe = core_recipes.add_recipe("Ungewisser Speicherstand")
    with patch.object(core_storage, "_record_change", side_effect=OSError("journal unavailable")):
        response = client.post(f"/recipe/{recipe.slug}/cooked")
    check(response.status_code == 500 and "data-outcome-unknown" in response.text,
          "a committed cooking entry must show uncertainty instead of 404")
    check(len(core_recipes.load_log()) == 1,
          "a post-save failure must keep exactly the one cooking entry it committed")
    check('href="/log"' in response.text and "<form" not in response.text
          and "data-gusto-live" not in response.text,
          "uncertainty must offer a read link without a resubmit form or live reload")

    with patch.object(core_storage, "_record_change", side_effect=OSError("journal unavailable")):
        response = client.post("/new", data={"title": "Bereits gespeichert"})
    check(response.status_code == 500 and core_recipes.get("bereits-gespeichert") is not None,
          "a saved recipe must not return a validation form with HTTP 400")
    with patch.object(core_storage, "_record_change", side_effect=OSError("journal unavailable")):
        response = client.post(f"/recipe/{recipe.slug}/delete")
    check(response.status_code == 500 and core_snapshots.get_archived(recipe.slug) is not None,
          "moving the recipe must not turn a post-save failure into not-found")

    with patch.object(core_shopping, "shopping_toggle", side_effect=core_errors.PostMutationError("saved")), \
            patch.dict(web.templates.env.globals,
                       {"gusto_change_state": lambda: (_ for _ in ()).throw(ValueError("bad journal"))}):
        response = client.post("/shopping/item/toggle")
    check(response.status_code == 500 and 'href="/shopping"' in response.text,
          "the recovery page must render without reading the failed journal")

    with patch.object(core_shopping, "shopping_exchange", side_effect=core_errors.PostMutationError("saved")):
        response = client.post("/api/shopping/sync", json={"items": []})
    check(response.status_code == 500 and response.json().get("outcome_unknown") is True,
          "a saved sync must report uncertainty, never invalid input")
    with patch.object(core_shopping, "shopping_exchange", side_effect=ValueError("invalid recipe source")):
        response = client.post("/api/shopping/sync", json={"items": []})
    check(response.status_code == 400 and response.json().get("detail") == "invalid recipe source",
          "a rejected sync must include its validation detail for recovery")
    with patch.object(core_images, "update_recipe_image"), \
            patch.object(core_images, "set_recipe_cover", side_effect=ValueError("missing image")):
        response = client.post("/recipe/any/images/image/set", data={"cover": "1"})
    check(response.status_code == 500 and "data-outcome-unknown" in response.text,
          "a failed cover change after saved metadata must not imply total rejection")
    for data_path, url in (
        (core_storage.index_path(), "/recipe/missing/cooked"),
        (core_storage.favorites_path(), "/favorites/missing/delete"),
        (core_storage.shopping_path(), "/shopping/missing/toggle"),
        (core_storage.shopping_path(), "/shopping/missing/remove"),
    ):
        check(client.post(url).status_code == 404,
              f"an absent entity before a write remains not-found: {url}")
        previous = data_path.read_bytes() if data_path.exists() else None
        revision = core_storage.change_state()
        invalid_data = b"{broken"
        try:
            data_path.parent.mkdir(parents=True, exist_ok=True)
            data_path.write_bytes(invalid_data)
            response = client.post(url)
            check(response.status_code == 400 and bool(response.text)
                  and data_path.read_bytes() == invalid_data
                  and core_storage.change_state() == revision,
                  f"invalid persisted state must be a rejection without writes: {url}")
        finally:
            if previous is None:
                data_path.unlink()
            else:
                data_path.write_bytes(previous)
    check(client.post("/new", data={"title": "Invalid", "duration": "not a number"}).status_code == 400,
          "validation before a write must still return the submitted form")


def test_tag_filters_keep_stale_selections_removable():
    # A bookmark can outlive the last recipe carrying its tag. Every active
    # tag must retain a removal link, including case-duplicate query values.
    groups = [{"key": "cuisine", "label": "Küche", "tags": ["italienisch"]}]
    selected = ["Italienisch", "nicht mehr da", "NICHT MEHR DA"]
    with patch.object(core_categories, "tag_groups", return_value=groups):
        bar = web._tag_bar("Salz & Pfeffer", selected)
    check(len(bar) == 2, "stale selected tags must have a visible filter group")
    active = [chip for group in bar for chip in group["chips"] if chip["on"]]
    check(len(active) == 2, "case-equivalent active tags must have one control")
    for chip in active:
        params = parse_qs(urlsplit(chip["href"]).query)
        check(params["q"] == ["Salz & Pfeffer"], "removing a tag must preserve the query")
        check(chip["name"].lower() not in [tag.lower() for tag in params.get("tag", [])],
              "removal link must clear every spelling of that tag")
        check(bool(params.get("tag")), "removing one tag must retain the other selection")
    check(selected == ["Italienisch", "nicht mehr da", "NICHT MEHR DA"],
          "building filter controls must not modify the selected tags")


def test_prepared_photo_translates_only_preparation_errors():
    # Consumer-body failures (e.g. a full/locked target during the later copy
    # in core) must propagate unchanged, not as the "unreadable photo" notice.
    caught = None
    try:
        with web._prepared_photo(upload_png(), None) as prepared:
            check(prepared is not None and prepared.suffix == ".webp",
                  "a valid photo must be prepared as WebP")
            raise OSError("simulated copy failure in the consumer body")
    except (ValueError, OSError) as error:
        caught = error
    check(isinstance(caught, OSError) and "simulated" in str(caught),
          f"consumer-body errors must propagate unchanged, got {caught!r}")

    caught = None
    try:
        with web._prepared_photo(
            upload_image(b"definitely not an image", "x.png"), None,
        ):
            pass
    except (ValueError, OSError) as error:
        caught = error
    check(isinstance(caught, ValueError) and "gelesen" in str(caught),
          f"unreadable photos must yield the German notice, got {caught!r}")

    # A failed upload copy must also clean the temporary source it opened.
    temporary_file = tempfile.NamedTemporaryFile
    with tempfile.TemporaryDirectory() as scratch:
        with patch.object(web.tempfile, "NamedTemporaryFile",
                          side_effect=lambda **kwargs: temporary_file(dir=scratch, **kwargs)), \
                patch.object(web.shutil, "copyfileobj", side_effect=OSError("disk full")):
            try:
                with web._prepared_photo(upload_png(), None):
                    raise AssertionError("a failed copy must never reach Core")
            except OSError:
                pass
        check(not list(Path(scratch).iterdir()),
              "a failed upload copy must not leave its partial temporary file behind")


def main():
    test_xss_is_neutralized()
    test_normal_markdown_survives()
    test_attribute_allowlist()
    test_dropped_tags_keep_children()
    test_shopping_error_notice_is_fixed_text()
    test_return_destination_stays_local()
    test_favorite_creation_error_keeps_shopping_context()
    test_recipe_form_preserves_markdown_indentation()
    test_recipe_edit_conflict_keeps_both_versions()
    test_recipe_shopping_preview_is_bound_to_recipe()
    test_backup_forms_require_confirmation_and_matching_upload()
    test_shopping_sync_keeps_server_responsive()
    test_shopping_sync_endpoint_contract()
    test_browser_writes_require_same_origin()
    test_saved_mutations_never_look_rejected()
    test_tag_filters_keep_stale_selections_removable()
    test_prepared_photo_translates_only_preparation_errors()
    test_backup_restore_rejects_stale_preview_without_writes()
    print(f"OK: {checks} checks passed")


if __name__ == "__main__":
    main()
