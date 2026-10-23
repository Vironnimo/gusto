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
import asyncio
import json
import os
import sys
import tempfile
import threading
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from PIL import Image
from fastapi.testclient import TestClient
from starlette.datastructures import UploadFile
from starlette.requests import Request


sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import gusto  # noqa: E402, F401 - configure checkout temp before allocating data

HOME = Path(tempfile.mkdtemp(prefix="gusto-web-render-test-"))
os.environ["GUSTO_HOME"] = os.fspath(HOME)

from gusto import web  # noqa: E402

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
    poison = web._render(
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
    rendered = web._render(
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

    structure = web._render(
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
    sanitized = web._sanitize_html(
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
    dropped = web._sanitize_html(
        "<iframe>schaden</iframe><div><p>bleibt</p></div><p>offen"
    )
    check("schaden" in dropped and "<p>bleibt</p>" in dropped,
          "dropped tags keep their content and allowed children")
    check("<iframe" not in dropped and "<div" not in dropped,
          "non-allowlisted tags must be dropped")
    check(dropped.endswith("<p>offen</p>"),
          "unclosed allowed tags must be closed at the end")


def test_shopping_error_notice_is_fixed_text():
    notice = web.SHOPPING_NOTICES.get(web.SHOPPING_EMPTY_MARKER)
    check(bool(notice), "the empty-add marker must map to a fixed notice")
    check(web.SHOPPING_NOTICES.get("<script>alert(1)</script>") is None,
          "foreign/unknown error parameters must render nothing")


def test_return_destination_stays_local():
    for value in ("//example.org", "/\\example.org", "/\t/example.org",
                  "/\r/example.org", "https://example.org"):
        check(web._safe_return_to(value, "/shopping") == "/shopping",
              "browser URL normalization must not turn a return path into an external redirect")
    local = "/favorites/einkauf?text=Milch%20%26%20Brot"
    check(web._safe_return_to(local, "/shopping") == local,
          "local return paths and encoded query values must survive")


def test_shopping_sync_keeps_server_responsive():
    async def exercise():
        started = threading.Event()
        release = threading.Event()
        responsive = []

        def slow_merge(items):
            started.set()
            responsive.append(release.wait(timeout=2))
            return []

        async def receive():
            return {"type": "http.request", "body": b'{"items": []}',
                    "more_body": False}

        async def other_request():
            while not started.is_set():
                await asyncio.sleep(0.001)
            release.set()

        request = Request({"type": "http", "headers": []}, receive)
        with patch.object(web.core, "shopping_merge", side_effect=slow_merge):
            response, _ = await asyncio.gather(
                web.api_shopping_sync(request), other_request(),
            )
        check(json.loads(response.body) == {"items": []}
              and responsive == [True],
              "shopping sync waiting on a file lock must not block other requests")

    asyncio.run(exercise())


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
    before = web.core.shopping_load()
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
    check(web.core.shopping_load() == before, "rejected writes must not change the store")
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
    recipe = web.core.add_recipe("Ungewisser Speicherstand")
    with patch.object(web.core, "_record_change", side_effect=OSError("journal unavailable")):
        response = client.post(f"/recipe/{recipe.slug}/cooked")
    check(response.status_code == 500 and "data-outcome-unknown" in response.text,
          "a committed cooking entry must show uncertainty instead of 404")
    check(len(web.core.load_log()) == 1,
          "a post-save failure must keep exactly the one cooking entry it committed")
    check('href="/log"' in response.text and "<form" not in response.text
          and "data-gusto-live" not in response.text,
          "uncertainty must offer a read link without a resubmit form or live reload")

    with patch.object(web.core, "_record_change", side_effect=OSError("journal unavailable")):
        response = client.post("/new", data={"title": "Bereits gespeichert"})
    check(response.status_code == 500 and web.core.get("bereits-gespeichert") is not None,
          "a saved recipe must not return a validation form with HTTP 400")
    with patch.object(web.core, "_record_change", side_effect=OSError("journal unavailable")):
        response = client.post(f"/recipe/{recipe.slug}/delete")
    check(response.status_code == 500 and web.core.get_archived(recipe.slug) is not None,
          "moving the recipe must not turn a post-save failure into not-found")

    with patch.object(web.core, "shopping_toggle", side_effect=web.core.PostMutationError("saved")), \
            patch.dict(web.templates.env.globals,
                       {"gusto_change_state": lambda: (_ for _ in ()).throw(ValueError("bad journal"))}):
        response = client.post("/shopping/item/toggle")
    check(response.status_code == 500 and 'href="/shopping"' in response.text,
          "the recovery page must render without reading the failed journal")

    with patch.object(web.core, "shopping_merge", side_effect=web.core.PostMutationError("saved")):
        response = client.post("/api/shopping/sync", json={"items": []})
    check(response.status_code == 500 and response.json().get("outcome_unknown") is True,
          "a saved sync must report uncertainty, never invalid input")
    with patch.object(web.core, "shopping_merge", side_effect=ValueError("invalid recipe source")):
        response = client.post("/api/shopping/sync", json={"items": []})
    check(response.status_code == 400 and response.json().get("detail") == "invalid recipe source",
          "a rejected sync must include its validation detail for recovery")
    with patch.object(web.core, "update_recipe_image"), \
            patch.object(web.core, "set_recipe_cover", side_effect=ValueError("missing image")):
        response = client.post("/recipe/any/images/image/set", data={"cover": "1"})
    check(response.status_code == 500 and "data-outcome-unknown" in response.text,
          "a failed cover change after saved metadata must not imply total rejection")
    check(client.post("/recipe/missing/cooked").status_code == 404,
          "missing recipes before a write remain definite not-found errors")
    check(client.post("/new", data={"title": "Invalid", "duration": "not a number"}).status_code == 400,
          "validation before a write must still return the submitted form")


def test_tag_filters_keep_stale_selections_removable():
    # A bookmark can outlive the last recipe carrying its tag. Every active
    # tag must retain a removal link, including case-duplicate query values.
    groups = [{"key": "cuisine", "label": "Küche", "tags": ["italienisch"]}]
    selected = ["Italienisch", "nicht mehr da", "NICHT MEHR DA"]
    with patch.object(web.core, "tag_groups", return_value=groups):
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
    test_shopping_sync_keeps_server_responsive()
    test_browser_writes_require_same_origin()
    test_saved_mutations_never_look_rejected()
    test_tag_filters_keep_stale_selections_removable()
    test_prepared_photo_translates_only_preparation_errors()
    print(f"OK: {checks} checks passed")


if __name__ == "__main__":
    main()
