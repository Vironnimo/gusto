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
import os
import sys
import tempfile
from pathlib import Path

from PIL import Image
from starlette.datastructures import UploadFile


HOME = Path(tempfile.mkdtemp(prefix="gusto-web-render-test-"))
os.environ["GUSTO_HOME"] = os.fspath(HOME)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

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


def main():
    test_xss_is_neutralized()
    test_normal_markdown_survives()
    test_attribute_allowlist()
    test_dropped_tags_keep_children()
    test_shopping_error_notice_is_fixed_text()
    test_prepared_photo_translates_only_preparation_errors()
    print(f"OK: {checks} checks passed")


if __name__ == "__main__":
    main()
