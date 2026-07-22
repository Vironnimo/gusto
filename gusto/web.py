"""Web UI (FastAPI, server-rendered).

Thin shell around gusto.core -- just like the CLI. Every action here has its
counterpart in the core and thus in the CLI; there is no web-only feature.
Visible UI text lives in the templates and stays German.
"""
from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import shutil
import tempfile
from urllib.parse import urlencode

import markdown as md
from fastapi import FastAPI, File, Form, Query, Request, UploadFile
from fastapi.responses import (HTMLResponse, RedirectResponse, PlainTextResponse,
                               JSONResponse, FileResponse)
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from PIL import Image, ImageOps, UnidentifiedImageError
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import core

BASE = Path(__file__).resolve().parent
app = FastAPI(title="Gusto")
app.mount("/static", StaticFiles(directory=str(BASE / "static")), name="static")
templates = Jinja2Templates(directory=str(BASE / "templates"))

MAX_WEB_IMAGE_BYTES = 25 * 1024 * 1024
MAX_WEB_IMAGE_EDGE = 1920
RECIPE_IMAGE_ROLES = [
    ("result", "Fertiges Gericht"),
    ("ingredients", "Zutaten"),
    ("step", "Zubereitung"),
    ("gallery", "Weitere Ansicht"),
]


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
    if not v:
        return None
    try:
        return int(v)
    except ValueError as error:
        raise ValueError("Dauer und Portionen müssen ganze Zahlen sein.") from error


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


def _favorite_dict(need: core.ShoppingNeed) -> dict:
    data = need.to_dict()
    for product in data["products"]:
        filename = product.get("image_filename")
        product["image_url"] = (f"/media/favorite/{filename}" if filename else None)
    return data


def _safe_return_to(value: str, fallback: str) -> str:
    return value if value.startswith("/") and not value.startswith("//") else fallback


def _error_redirect(path: str, message: str) -> RedirectResponse:
    separator = "&" if "?" in path else "?"
    return RedirectResponse(
        path + separator + urlencode({"error": message}), status_code=303,
    )


def _chosen_photo(camera: UploadFile | None,
                  library: UploadFile | None) -> UploadFile | None:
    chosen = [upload for upload in (camera, library)
              if upload is not None and upload.filename]
    if len(chosen) > 1:
        raise ValueError("Bitte nur eine Aufnahme oder eine Bilddatei auswählen.")
    return chosen[0] if chosen else None


@contextmanager
def _prepared_photo(camera: UploadFile | None,
                    library: UploadFile | None):
    """Normalize a browser photo before core copies it into owned storage."""
    upload = _chosen_photo(camera, library)
    if upload is None:
        yield None
        return

    upload.file.seek(0, 2)
    size = upload.file.tell()
    upload.file.seek(0)
    if size > MAX_WEB_IMAGE_BYTES:
        raise ValueError("Das Foto ist größer als 25 MB.")

    suffix = Path(upload.filename).suffix or ".image"
    source_path: Path | None = None
    prepared_path: Path | None = None
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as handle:
        shutil.copyfileobj(upload.file, handle)
        source_path = Path(handle.name)
    try:
        with Image.open(source_path) as opened:
            opened.seek(0)
            photo = ImageOps.exif_transpose(opened)
            photo.thumbnail(
                (MAX_WEB_IMAGE_EDGE, MAX_WEB_IMAGE_EDGE),
                Image.Resampling.LANCZOS,
            )
            has_alpha = photo.mode in {"RGBA", "LA"} or (
                photo.mode == "P" and "transparency" in photo.info
            )
            clean = photo.convert("RGBA" if has_alpha else "RGB")
            with tempfile.NamedTemporaryFile(delete=False, suffix=".webp") as handle:
                prepared_path = Path(handle.name)
            clean.save(prepared_path, format="WEBP", quality=84, method=4)
        yield prepared_path
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as error:
        raise ValueError(
            "Das Foto konnte nicht gelesen werden. Bitte JPG, PNG, WebP oder GIF verwenden."
        ) from error
    finally:
        if source_path is not None:
            source_path.unlink(missing_ok=True)
        if prepared_path is not None:
            prepared_path.unlink(missing_ok=True)


# --- Pages ------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
def index(request: Request, q: str = "", tag: list[str] = Query(default=[])):
    recipes = sorted(core.search(query=q, tags=tag), key=lambda r: r.title.lower())
    selected_count = len({item.lower() for item in tag})
    return templates.TemplateResponse(request, "list.html", {
        "nav": "recipes", "title": "Rezepte",
        "recipes": recipes, "q": q,
        "tag_bar": _tag_bar(q, tag),
        "selected": bool(tag),
        "selected_count": selected_count,
        "reset_href": _filter_href(q, []),
    })


@app.get("/recipe/{slug}", response_class=HTMLResponse)
def recipe_detail(request: Request, slug: str, error: str = ""):
    r = core.get(slug)
    if r is None:
        raise StarletteHTTPException(status_code=404)
    return templates.TemplateResponse(request, "recipe.html", {
        "nav": "recipes", "title": r.title,
        "r": r, "content_html": _render(r.content()), "error": error,
    })


@app.get("/media/recipe/{slug}/{image_id}", include_in_schema=False)
def recipe_image(slug: str, image_id: str):
    image = core.get_recipe_image(slug, image_id)
    if image is None:
        raise StarletteHTTPException(status_code=404)
    path = core.recipe_image_path(slug, image)
    if not path.is_file():
        raise StarletteHTTPException(status_code=404)
    return FileResponse(str(path))


@app.get("/media/favorite/{filename}", include_in_schema=False)
def favorite_image(filename: str):
    if not core.favorite_image_is_referenced(filename):
        raise StarletteHTTPException(status_code=404)
    path = core.favorite_image_path(filename)
    if not path.is_file():
        raise StarletteHTTPException(status_code=404)
    return FileResponse(str(path))


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
def update(request: Request, slug: str, title: str = Form(...), tags: str = Form(""),
           duration: str = Form(""), servings: str = Form(""),
           content: str = Form("")):
    recipe = core.get(slug)
    if recipe is None:
        raise StarletteHTTPException(status_code=404)
    body = f"# {title.strip()}\n\n{content.strip()}\n"
    try:
        core.update_recipe(slug, title=title.strip(), tags=_tags(tags),
                           duration_min=_int_or_none(duration),
                           servings=_int_or_none(servings), content=body,
                           clear_duration=not duration.strip(),
                           clear_servings=not servings.strip())
    except ValueError as error:
        form = {"title": title, "tags": tags, "duration": duration,
                "servings": servings, "content": content.strip()}
        return templates.TemplateResponse(request, "form.html", {
            "nav": "recipes", "title": f"{recipe.title} bearbeiten",
            "r": recipe, "form": form,
            "form_action": f"/recipe/{slug}/edit", "error": str(error),
        }, status_code=400)
    return RedirectResponse(f"/recipe/{slug}", status_code=303)


@app.get("/recipe/{slug}/images", response_class=HTMLResponse)
def recipe_images_page(request: Request, slug: str, error: str = ""):
    recipe = core.get(slug)
    if recipe is None:
        raise StarletteHTTPException(status_code=404)
    return templates.TemplateResponse(request, "recipe_images.html", {
        "nav": "recipes", "title": f"Bilder · {recipe.title}",
        "r": recipe, "role_options": RECIPE_IMAGE_ROLES, "error": error,
    })


@app.post("/recipe/{slug}/images/add")
def recipe_image_add_route(
        slug: str, role: str = Form("gallery"), caption: str = Form(""),
        cover: str = Form(""),
        image_camera: UploadFile | None = File(None),
        image_file: UploadFile | None = File(None)):
    try:
        with _prepared_photo(image_camera, image_file) as image_path:
            if image_path is None:
                raise ValueError("Bitte ein Foto aufnehmen oder ein Bild auswählen.")
            core.add_recipe_image(
                slug, image_path, role=role, caption=caption, cover=cover == "1",
            )
    except ValueError as error:
        return _error_redirect(f"/recipe/{slug}/images", str(error))
    return RedirectResponse(f"/recipe/{slug}/images", status_code=303)


@app.post("/recipe/{slug}/images/{image_id}/set")
def recipe_image_set_route(
        slug: str, image_id: str, role: str = Form("gallery"),
        caption: str = Form(""), cover: str = Form("")):
    try:
        core.update_recipe_image(slug, image_id, role=role, caption=caption)
        if cover == "1":
            core.set_recipe_cover(slug, image_id)
    except ValueError as error:
        return _error_redirect(f"/recipe/{slug}/images", str(error))
    return RedirectResponse(f"/recipe/{slug}/images", status_code=303)


@app.post("/recipe/{slug}/images/{image_id}/remove")
def recipe_image_remove_route(slug: str, image_id: str):
    try:
        core.remove_recipe_image(slug, image_id)
    except ValueError as error:
        return _error_redirect(f"/recipe/{slug}/images", str(error))
    return RedirectResponse(f"/recipe/{slug}/images", status_code=303)


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


# --- Preferred products -----------------------------------------------------

@app.get("/favorites", response_class=HTMLResponse)
def favorites_page(request: Request, error: str = ""):
    needs = sorted(core.favorites_load(), key=lambda item: item.name.casefold())
    return templates.TemplateResponse(request, "favorites.html", {
        "nav": "shopping", "title": "Lieblingsprodukte",
        "needs": needs, "error": error,
    })


@app.get("/favorites/match", response_class=HTMLResponse)
def favorite_match_page(request: Request, text: str = "", error: str = ""):
    needs = sorted(core.favorites_load(), key=lambda item: item.name.casefold())
    need = core.favorite_match(text, needs)
    return templates.TemplateResponse(request, "favorite_match.html", {
        "nav": "shopping", "title": need.name if need else "Lieblingsprodukt",
        "text": text, "need": need, "needs": needs, "error": error,
    })


@app.post("/favorites/add")
def favorite_add_route(name: str = Form(...), alias: str = Form(""),
                       return_to: str = Form("")):
    try:
        need = core.favorite_add_need(name.strip(), aliases=[alias] if alias else [])
    except ValueError as error:
        return _error_redirect("/favorites", str(error))
    destination = _safe_return_to(return_to, f"/favorites/{need.id}")
    return RedirectResponse(destination, status_code=303)


@app.post("/favorites/assign")
def favorite_assign_route(need_id: str = Form(...), alias: str = Form(...),
                          return_to: str = Form("/shopping")):
    try:
        core.favorite_add_alias(need_id, alias)
    except ValueError as error:
        match_path = "/favorites/match?" + urlencode({"text": alias})
        return _error_redirect(match_path, str(error))
    return RedirectResponse(_safe_return_to(return_to, "/shopping"), status_code=303)


@app.get("/favorites/{need_id}", response_class=HTMLResponse)
def favorite_detail_page(request: Request, need_id: str, error: str = ""):
    need = core.favorite_get_need(need_id)
    if need is None:
        raise StarletteHTTPException(status_code=404)
    return templates.TemplateResponse(request, "favorite_detail.html", {
        "nav": "shopping", "title": need.name,
        "need": need, "error": error,
    })


@app.post("/favorites/{need_id}/set")
def favorite_set_route(need_id: str, name: str = Form(...)):
    try:
        core.favorite_update_need(need_id, name)
    except ValueError as error:
        return _error_redirect(f"/favorites/{need_id}", str(error))
    return RedirectResponse(f"/favorites/{need_id}", status_code=303)


@app.post("/favorites/{need_id}/delete")
def favorite_delete_route(need_id: str):
    try:
        core.favorite_remove_need(need_id)
    except ValueError:
        raise StarletteHTTPException(status_code=404)
    return RedirectResponse("/favorites", status_code=303)


@app.post("/favorites/{need_id}/alias/add")
def favorite_alias_add_route(need_id: str, alias: str = Form(...)):
    try:
        core.favorite_add_alias(need_id, alias)
    except ValueError as error:
        return _error_redirect(f"/favorites/{need_id}", str(error))
    return RedirectResponse(f"/favorites/{need_id}", status_code=303)


@app.post("/favorites/{need_id}/alias/remove")
def favorite_alias_remove_route(need_id: str, alias: str = Form(...)):
    try:
        core.favorite_remove_alias(need_id, alias)
    except ValueError as error:
        return _error_redirect(f"/favorites/{need_id}", str(error))
    return RedirectResponse(f"/favorites/{need_id}", status_code=303)


@app.post("/favorites/{need_id}/product/add")
def favorite_product_add_route(
        need_id: str, name: str = Form(...), brand: str = Form(""),
        store: str = Form(""), note: str = Form(""),
        image_camera: UploadFile | None = File(None),
        image_file: UploadFile | None = File(None)):
    try:
        with _prepared_photo(image_camera, image_file) as image_path:
            core.favorite_add_product(
                need_id, name, brand=brand, store=store, note=note,
                image=image_path,
            )
    except ValueError as error:
        return _error_redirect(f"/favorites/{need_id}", str(error))
    return RedirectResponse(f"/favorites/{need_id}", status_code=303)


@app.post("/favorites/{need_id}/product/{product_id}/set")
def favorite_product_set_route(
        need_id: str, product_id: str, name: str = Form(...),
        brand: str = Form(""), store: str = Form(""), note: str = Form(""),
        remove_image: str = Form(""),
        image_camera: UploadFile | None = File(None),
        image_file: UploadFile | None = File(None)):
    try:
        with _prepared_photo(image_camera, image_file) as image_path:
            core.favorite_update_product(
                need_id, product_id, name=name, brand=brand, store=store,
                note=note, image=image_path, remove_image=remove_image == "1",
            )
    except ValueError as error:
        return _error_redirect(f"/favorites/{need_id}", str(error))
    return RedirectResponse(f"/favorites/{need_id}", status_code=303)


@app.post("/favorites/{need_id}/product/{product_id}/move")
def favorite_product_move_route(need_id: str, product_id: str,
                                position: int = Form(...)):
    try:
        core.favorite_move_product(need_id, product_id, position)
    except ValueError as error:
        return _error_redirect(f"/favorites/{need_id}", str(error))
    return RedirectResponse(f"/favorites/{need_id}", status_code=303)


@app.post("/favorites/{need_id}/product/{product_id}/remove")
def favorite_product_remove_route(need_id: str, product_id: str):
    try:
        core.favorite_remove_product(need_id, product_id)
    except ValueError as error:
        return _error_redirect(f"/favorites/{need_id}", str(error))
    return RedirectResponse(f"/favorites/{need_id}", status_code=303)


# --- Shopping list ----------------------------------------------------------

@app.get("/shopping", response_class=HTMLResponse)
def shopping_page(request: Request):
    items = core.shopping_list()
    needs = core.favorites_load()
    open_items = [i for i in items if not i.checked]
    done_items = [i for i in items if i.checked]
    source_titles = {recipe.slug: recipe.title for recipe in core.load_recipes()}
    return templates.TemplateResponse(request, "shopping.html", {
        "nav": "shopping", "title": "Einkaufsliste",
        "open_items": open_items, "done_items": done_items,
        "source_titles": source_titles,
        "favorite_matches": {item.id: core.favorite_match(item.text, needs)
                             for item in items},
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
    except ValueError as error:
        if core.get(slug) is None:
            raise StarletteHTTPException(status_code=404)
        return _error_redirect(f"/recipe/{slug}", str(error))
    return RedirectResponse("/shopping", status_code=303)


# --- Shopping sync API (contract: docs/sync-kontrakt.md) --------------------
# Full-state sync as JSON for the PWA.

@app.get("/api/shopping")
def api_shopping_get():
    """All items incl. tombstones as {"items": [<item-dict>, ...]}.

    Read-only starting point for the PWA and for agents/scripts."""
    return JSONResponse({"items": [i.to_dict() for i in core.shopping_load()]})


@app.get("/api/favorites")
def api_favorites_get():
    """Shared preference catalog for the offline-readable shopping client."""
    return JSONResponse({
        "needs": [_favorite_dict(need) for need in core.favorites_load()],
    })


@app.post("/api/shopping/sync")
async def api_shopping_sync(request: Request):
    """Full-state sync. Body: {"items": [<item-dict>, ...]} (the client's local
    state). Response: {"items": [...]} = server-side merged total state
    (core.shopping_merge, "last writer wins" + tombstones)."""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON"}, status_code=400)
    if not isinstance(body, dict) or not isinstance(body.get("items"), list):
        return JSONResponse({"error": "invalid shopping state"}, status_code=400)
    try:
        merged = core.shopping_merge(body["items"])
    except ValueError:
        return JSONResponse({"error": "invalid shopping state"}, status_code=400)
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
