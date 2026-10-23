"""Render recipe Markdown as the shared allowlisted HTML presentation contract."""
from __future__ import annotations

import html
from html.parser import HTMLParser
import re

import markdown as md


def without_title(text: str) -> str:
    """Drop the first H1 line (the title) -- we show it separately."""
    out, dropped = [], False
    for ln in text.splitlines():
        if not dropped and ln.strip().startswith("# "):
            dropped = True
            continue
        out.append(ln)
    return "\n".join(out).strip()


_ALLOWED_TAGS = frozenset({
    "p", "br", "hr", "h1", "h2", "h3", "h4", "h5", "h6",
    "ul", "ol", "li", "blockquote", "em", "strong", "code", "pre",
    "a", "img", "table", "thead", "tbody", "tr", "th", "td",
    "del", "sub", "sup", "span", "figure", "figcaption",
})
_VOID_TAGS = frozenset({"br", "hr", "img"})

# Small per-tag attribute allowlist; everything else -- including all on*
# event handlers, style and class -- is stripped.
_ALLOWED_ATTRS = {
    "a": {"href", "title"},
    "img": {"src", "alt", "title", "width", "height"},
    "th": {"colspan", "rowspan"},
    "td": {"colspan", "rowspan"},
}
_URL_ATTRS = frozenset({"href", "src"})

# URL schemes that can smuggle script execution are rejected outright; data:
# URLs are rejected entirely (a data:image would be acceptable, but one
# simple rule is safer to keep correct).
_DANGEROUS_URL_PREFIXES = ("javascript:", "vbscript:", "data:")

# Browsers ignore whitespace and control characters inside a URL scheme, so
# they must be stripped before the scheme check ("jav\tascript:" is
# javascript:).
_URL_SCHEME_NOISE = re.compile(r"[\s\x00-\x1f\x7f]+")


def _safe_url(value: str) -> bool:
    compact = _URL_SCHEME_NOISE.sub("", value).lower()
    return not compact.startswith(_DANGEROUS_URL_PREFIXES)


class _HtmlAllowlistParser(HTMLParser):
    """Rewrites rendered Markdown into an allowlisted HTML subset.

    Project security rule: no raw user input into HTML. Markdown admits raw
    tags (<img src=x onerror=...>) and javascript: links, and recipe.html and
    archive_recipe.html render this result with |safe -- so the single render
    point below sanitizes. The repo ships no sanitizer dependency and the
    dependency rule forbids adding one ad hoc, hence this stdlib filter.
    Allowed tags keep only their allowlisted attributes; other tags are
    dropped as tags while their children (and script/style text) stay as
    escaped plain text, so nothing disappears silently.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self.open_tags: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag not in _ALLOWED_TAGS:
            return  # drop the tag, keep rendering its children
        self.out.append(self._build_start(tag, attrs))
        if tag not in _VOID_TAGS:
            self.open_tags.append(tag)

    def handle_startendtag(self, tag, attrs):
        if tag not in _ALLOWED_TAGS:
            return
        self.out.append(self._build_start(tag, attrs))
        if tag not in _VOID_TAGS:
            self.out.append(f"</{tag}>")

    def handle_endtag(self, tag):
        if tag not in _ALLOWED_TAGS or tag in _VOID_TAGS:
            return
        if tag not in self.open_tags:
            return  # stray end tag: drop instead of emitting broken HTML
        # Close conservatively up to the matching open tag. Markdown output is
        # well-nested; only raw user HTML can mis-nest.
        while self.open_tags:
            open_tag = self.open_tags.pop()
            self.out.append(f"</{open_tag}>")
            if open_tag == tag:
                break

    def handle_data(self, data):
        self.out.append(html.escape(data, quote=False))

    def _build_start(self, tag, attrs) -> str:
        allowed = _ALLOWED_ATTRS.get(tag, frozenset())
        parts = [tag]
        for name, value in attrs:
            if name not in allowed:
                continue
            if name in _URL_ATTRS and (value is None or not _safe_url(value)):
                continue
            if value is None:
                parts.append(name)
            else:
                parts.append(f'{name}="{html.escape(value, quote=True)}"')
        return "<" + " ".join(parts) + ">"

    def close_open_tags(self) -> None:
        while self.open_tags:
            self.out.append(f"</{self.open_tags.pop()}>")


def sanitize_html(rendered: str) -> str:
    parser = _HtmlAllowlistParser()
    parser.feed(rendered)
    parser.close()
    parser.close_open_tags()
    return "".join(parser.out)


def render_recipe(text: str) -> str:
    # Sanitize at this single render point so every template rendering the
    # result with |safe (recipe.html and archive_recipe.html) is covered.
    return sanitize_html(
        md.markdown(without_title(text), extensions=["extra", "sane_lists"])
    )
