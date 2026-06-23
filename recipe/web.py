"""Web UI (FastAPI, server-rendered).

Thin shell around recipe.core -- just like the CLI. Every action here has its
counterpart in the core and thus in the CLI; there is no web-only feature.
Visible UI text lives in the templates and stays German.
"""
from __future__ import annotations

from pathlib import Path
from urllib.parse import urlencode

import markdown as md
from fastapi import FastAPI, Form, Query, Request
from fastapi.responses import (HTMLResponse, RedirectResponse, PlainTextResponse,
                               JSONResponse, FileResponse)
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import core

BASE = Path(__file__).resolve().parent
app = FastAPI(title="Gusto")
app.mount("/static", StaticFiles(directory=str(BASE / "static")), name="static")
templates = Jinja2Templates(directory=str(BASE / "templates"))


# --- Helpers ----------------------------------------------------------------

def _split_title(text: str) -> str:
    """Drop the first H1 line (the title) -- we show it separately."""
    out, dropped = [], False
    for ln in text.splitlines():
        if not dropped and ln.strip().startswith("# "):
            dropped = True
            continue
        out.append(ln)
    return "\n".join(out).strip()


def _render(text: str) -> str:
    return md.markdown(_split_title(text), extensions=["extra", "sane_lists"])


def _int_or_none(v: str | None):
    v = (v or "").strip()
    return int(v) if v.lstrip("-").isdigit() else None


def _tags(s: str | None):
    return [t.strip() for t in (s or "").split(",") if t.strip()]


def _filter_href(q: str, tags: list[str]) -> str:
    """Link to the recipe list with the given search + tag selection."""
    params = ([("q", q)] if q else []) + [("tag", t) for t in tags]
    return "/?" + urlencode(params) if params else "/"


def _tag_bar(q: str, selected: list[str]) -> list[dict]:
    """Grouped tag chips for the filter bar. Each chip knows its toggle link,
    which adds its tag to the selection or removes it -- the rest of the
    selection and the search stay intact."""
    sel = {t.lower() for t in selected}
    bar = []
    for g in core.tag_groups(only_used=True):
        chips = []
        for name in g["tags"]:
            on = name.lower() in sel
            new = ([t for t in selected if t.lower() != name.lower()]
                   if on else selected + [name])
            chips.append({"name": name, "on": on, "href": _filter_href(q, new)})
        bar.append({"label": g["label"], "chips": chips})
    return bar


# --- Pages ------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
def index(request: Request, q: str = "", tag: list[str] = Query(default=[])):
    recipes = sorted(core.search(query=q, tags=tag), key=lambda r: r.title.lower())
    return templates.TemplateResponse(request, "list.html", {
        "nav": "recipes", "title": "Rezepte",
        "recipes": recipes, "q": q,
        "tag_bar": _tag_bar(q, tag),
        "selected": bool(tag),
        "reset_href": _filter_href(q, []),
    })


@app.get("/recipe/{slug}", response_class=HTMLResponse)
def recipe_detail(request: Request, slug: str):
    r = core.get(slug)
    if r is None:
        raise StarletteHTTPException(status_code=404)
    return templates.TemplateResponse(request, "recipe.html", {
        "nav": "recipes", "title": r.title,
        "r": r, "content_html": _render(r.content()),
    })


@app.get("/new", response_class=HTMLResponse)
def new_form(request: Request):
    form = {"title": "", "tags": "", "duration": "", "servings": "",
            "content": "## Zutaten\n\n- \n\n## Zubereitung\n\n1. "}
    return templates.TemplateResponse(request, "form.html", {
        "nav": "new", "title": "Neues Rezept",
        "r": None, "form": form, "form_action": "/new",
    })


@app.post("/new")
def create(request: Request, title: str = Form(...), tags: str = Form(""),
           duration: str = Form(""), servings: str = Form(""),
           content: str = Form("")):
    body = f"# {title.strip()}\n\n{content.strip()}\n"
    try:
        r = core.add_recipe(title.strip(), tags=_tags(tags),
                            duration_min=_int_or_none(duration),
                            servings=_int_or_none(servings), content=body)
    except ValueError as e:
        form = {"title": title, "tags": tags, "duration": duration,
                "servings": servings, "content": content.strip()}
        return templates.TemplateResponse(request, "form.html", {
            "nav": "new", "title": "Neues Rezept",
            "r": None, "form": form, "form_action": "/new", "error": str(e),
        }, status_code=400)
    return RedirectResponse(f"/recipe/{r.slug}", status_code=303)


@app.get("/recipe/{slug}/edit", response_class=HTMLResponse)
def edit_form(request: Request, slug: str):
    r = core.get(slug)
    if r is None:
        raise StarletteHTTPException(status_code=404)
    form = {"title": r.title, "tags": ", ".join(r.tags),
            "duration": r.duration_min or "", "servings": r.servings or "",
            "content": _split_title(r.content())}
    return templates.TemplateResponse(request, "form.html", {
        "nav": "recipes", "title": f"{r.title} bearbeiten",
        "r": r, "form": form, "form_action": f"/recipe/{slug}/edit",
    })


@app.post("/recipe/{slug}/edit")
def update(slug: str, title: str = Form(...), tags: str = Form(""),
           duration: str = Form(""), servings: str = Form(""),
           content: str = Form("")):
    body = f"# {title.strip()}\n\n{content.strip()}\n"
    try:
        core.update_recipe(slug, title=title.strip(), tags=_tags(tags),
                           duration_min=_int_or_none(duration),
                           servings=_int_or_none(servings), content=body)
    except ValueError:
        raise StarletteHTTPException(status_code=404)
    return RedirectResponse(f"/recipe/{slug}", status_code=303)


@app.post("/recipe/{slug}/cooked")
def mark_cooked(slug: str):
    try:
        core.log_cooked(slug)
    except ValueError:
        raise StarletteHTTPException(status_code=404)
    return RedirectResponse(f"/recipe/{slug}", status_code=303)


@app.post("/recipe/{slug}/delete")
def delete_recipe_route(slug: str):
    try:
        core.delete_recipe(slug)
    except ValueError:
        raise StarletteHTTPException(status_code=404)
    return RedirectResponse("/", status_code=303)


@app.get("/suggestions", response_class=HTMLResponse)
def suggestions(request: Request, days: int = 7):
    return templates.TemplateResponse(request, "suggest.html", {
        "nav": "suggestions", "title": "Was koche ich?",
        "candidates": core.suggest(days=days), "days": days,
    })


@app.get("/log", response_class=HTMLResponse)
def log_page(request: Request):
    entries = list(reversed(core.load_log()))
    title_map = {r.slug: r.title for r in core.load_recipes()}
    return templates.TemplateResponse(request, "log.html", {
        "nav": "log", "title": "Logbuch",
        "entries": entries, "title_map": title_map,
    })


# --- Shopping list ----------------------------------------------------------

@app.get("/shopping", response_class=HTMLResponse)
def shopping_page(request: Request):
    items = core.shopping_list()
    open_items = [i for i in items if not i.checked]
    done_items = [i for i in items if i.checked]
    return templates.TemplateResponse(request, "shopping.html", {
        "nav": "shopping", "title": "Einkaufsliste",
        "open_items": open_items, "done_items": done_items,
    })


@app.post("/shopping/add")
def shopping_add_route(text: str = Form(...), quantity: str = Form("")):
    if text.strip():
        core.shopping_add(text.strip(), quantity=quantity.strip())
    return RedirectResponse("/shopping", status_code=303)


@app.post("/shopping/{item_id}/toggle")
def shopping_toggle_route(item_id: str):
    try:
        core.shopping_toggle(item_id)
    except ValueError:
        raise StarletteHTTPException(status_code=404)
    return RedirectResponse("/shopping", status_code=303)


@app.post("/shopping/{item_id}/remove")
def shopping_remove_route(item_id: str):
    try:
        core.shopping_remove(item_id)
    except ValueError:
        raise StarletteHTTPException(status_code=404)
    return RedirectResponse("/shopping", status_code=303)


@app.post("/shopping/clear")
def shopping_clear_route():
    core.shopping_clear_done()
    return RedirectResponse("/shopping", status_code=303)


@app.post("/recipe/{slug}/shopping")
def recipe_to_shopping(slug: str):
    try:
        core.shopping_add_recipe(slug)
    except ValueError:
        raise StarletteHTTPException(status_code=404)
    return RedirectResponse("/shopping", status_code=303)


# --- Shopping sync API (contract: docs/sync-kontrakt.md) --------------------
# Full-state sync as JSON for the PWA.

@app.get("/api/shopping")
def api_shopping_get():
    """All items incl. tombstones as {"items": [<item-dict>, ...]}.

    Read-only starting point for the PWA and for agents/scripts."""
    return JSONResponse({"items": [i.to_dict() for i in core.shopping_load()]})


@app.post("/api/shopping/sync")
async def api_shopping_sync(request: Request):
    """Full-state sync. Body: {"items": [<item-dict>, ...]} (the client's local
    state). Response: {"items": [...]} = server-side merged total state
    (core.shopping_merge, "last writer wins" + tombstones)."""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON"}, status_code=400)
    merged = core.shopping_merge(body.get("items", []))
    return JSONResponse({"items": [i.to_dict() for i in merged]})


# --- PWA: serve the service worker from the root ----------------------------
# The file lives under static/, but the SW MUST be registered from the root,
# otherwise its scope is only /static/ and it cannot serve /shopping & co.
# offline.

@app.get("/sw.js", include_in_schema=False)
def service_worker():
    return FileResponse(str(BASE / "static" / "sw.js"),
                        media_type="application/javascript")


@app.exception_handler(StarletteHTTPException)
async def http_exception(request: Request, exc: StarletteHTTPException):
    if exc.status_code == 404:
        return templates.TemplateResponse(request, "404.html", {
            "nav": "", "title": "Nicht gefunden",
        }, status_code=404)
    return PlainTextResponse(str(exc.detail or exc.status_code), status_code=exc.status_code)
