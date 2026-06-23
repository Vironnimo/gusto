"""Web-Oberflaeche (FastAPI, server-gerendert).

Duenne Huelle um recipe.core – genau wie die CLI. Jede Aktion hier hat ihre
Entsprechung im Core und damit in der CLI; es gibt kein Web-only-Feature.
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


# --- Helfer -----------------------------------------------------------------

def _split_title(text: str) -> str:
    """Entfernt die erste H1-Zeile (den Titel) – den zeigen wir separat."""
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
    """Link auf die Rezeptliste mit gegebener Suche + Tag-Auswahl."""
    params = ([("q", q)] if q else []) + [("tag", t) for t in tags]
    return "/?" + urlencode(params) if params else "/"


def _tag_leiste(q: str, ausgewaehlt: list[str]) -> list[dict]:
    """Gruppierte Tag-Chips fuer die Filterleiste. Jeder Chip kennt seinen
    Toggle-Link, der seinen Tag zur Auswahl hinzufuegt oder daraus entfernt –
    die uebrige Auswahl und die Suche bleiben erhalten."""
    sel = {t.lower() for t in ausgewaehlt}
    leiste = []
    for g in core.tag_groups(only_used=True):
        chips = []
        for name in g["tags"]:
            on = name.lower() in sel
            neu = ([t for t in ausgewaehlt if t.lower() != name.lower()]
                   if on else ausgewaehlt + [name])
            chips.append({"name": name, "on": on, "href": _filter_href(q, neu)})
        leiste.append({"label": g["label"], "chips": chips})
    return leiste


# --- Seiten -----------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
def index(request: Request, q: str = "", tag: list[str] = Query(default=[])):
    recipes = sorted(core.search(query=q, tags=tag), key=lambda r: r.titel.lower())
    return templates.TemplateResponse(request, "list.html", {
        "nav": "rezepte", "titel": "Rezepte",
        "recipes": recipes, "q": q,
        "tag_leiste": _tag_leiste(q, tag),
        "ausgewaehlt": bool(tag),
        "reset_href": _filter_href(q, []),
    })


@app.get("/rezept/{slug}", response_class=HTMLResponse)
def rezept(request: Request, slug: str):
    r = core.get(slug)
    if r is None:
        raise StarletteHTTPException(status_code=404)
    return templates.TemplateResponse(request, "recipe.html", {
        "nav": "rezepte", "titel": r.titel,
        "r": r, "inhalt_html": _render(r.inhalt()),
    })


@app.get("/neu", response_class=HTMLResponse)
def neu_form(request: Request):
    form = {"titel": "", "tags": "", "dauer": "", "portionen": "",
            "inhalt": "## Zutaten\n\n- \n\n## Zubereitung\n\n1. "}
    return templates.TemplateResponse(request, "form.html", {
        "nav": "neu", "titel": "Neues Rezept",
        "r": None, "form": form, "form_action": "/neu",
    })


@app.post("/neu")
def neu_speichern(request: Request, titel: str = Form(...), tags: str = Form(""),
                  dauer: str = Form(""), portionen: str = Form(""),
                  inhalt: str = Form("")):
    content = f"# {titel.strip()}\n\n{inhalt.strip()}\n"
    try:
        r = core.add_recipe(titel.strip(), tags=_tags(tags),
                            dauer_minuten=_int_or_none(dauer),
                            portionen=_int_or_none(portionen), inhalt=content)
    except ValueError as e:
        form = {"titel": titel, "tags": tags, "dauer": dauer,
                "portionen": portionen, "inhalt": inhalt.strip()}
        return templates.TemplateResponse(request, "form.html", {
            "nav": "neu", "titel": "Neues Rezept",
            "r": None, "form": form, "form_action": "/neu", "fehler": str(e),
        }, status_code=400)
    return RedirectResponse(f"/rezept/{r.slug}", status_code=303)


@app.get("/rezept/{slug}/bearbeiten", response_class=HTMLResponse)
def bearbeiten_form(request: Request, slug: str):
    r = core.get(slug)
    if r is None:
        raise StarletteHTTPException(status_code=404)
    form = {"titel": r.titel, "tags": ", ".join(r.tags),
            "dauer": r.dauer_minuten or "", "portionen": r.portionen or "",
            "inhalt": _split_title(r.inhalt())}
    return templates.TemplateResponse(request, "form.html", {
        "nav": "rezepte", "titel": f"{r.titel} bearbeiten",
        "r": r, "form": form, "form_action": f"/rezept/{slug}/bearbeiten",
    })


@app.post("/rezept/{slug}/bearbeiten")
def bearbeiten_speichern(slug: str, titel: str = Form(...), tags: str = Form(""),
                         dauer: str = Form(""), portionen: str = Form(""),
                         inhalt: str = Form("")):
    content = f"# {titel.strip()}\n\n{inhalt.strip()}\n"
    try:
        core.update_recipe(slug, titel=titel.strip(), tags=_tags(tags),
                           dauer_minuten=_int_or_none(dauer),
                           portionen=_int_or_none(portionen), inhalt=content)
    except ValueError:
        raise StarletteHTTPException(status_code=404)
    return RedirectResponse(f"/rezept/{slug}", status_code=303)


@app.post("/rezept/{slug}/gekocht")
def gekocht(slug: str):
    try:
        core.log_cooked(slug)
    except ValueError:
        raise StarletteHTTPException(status_code=404)
    return RedirectResponse(f"/rezept/{slug}", status_code=303)


@app.post("/rezept/{slug}/loeschen")
def loeschen(slug: str):
    try:
        core.delete_recipe(slug)
    except ValueError:
        raise StarletteHTTPException(status_code=404)
    return RedirectResponse("/", status_code=303)


@app.get("/vorschlaege", response_class=HTMLResponse)
def vorschlaege(request: Request, days: int = 7):
    return templates.TemplateResponse(request, "suggest.html", {
        "nav": "vorschlaege", "titel": "Was koche ich?",
        "kandidaten": core.suggest(days=days), "days": days,
    })


@app.get("/logbuch", response_class=HTMLResponse)
def logbuch(request: Request):
    eintraege = list(reversed(core.load_log()))
    titel_map = {r.slug: r.titel for r in core.load_recipes()}
    return templates.TemplateResponse(request, "log.html", {
        "nav": "logbuch", "titel": "Logbuch",
        "eintraege": eintraege, "titel_map": titel_map,
    })


# --- Einkaufsliste ----------------------------------------------------------

@app.get("/einkauf", response_class=HTMLResponse)
def einkauf(request: Request):
    items = core.einkauf_list()
    offen = [i for i in items if not i.checked]
    erledigt = [i for i in items if i.checked]
    return templates.TemplateResponse(request, "einkauf.html", {
        "nav": "einkauf", "titel": "Einkaufsliste",
        "offen": offen, "erledigt": erledigt,
    })


@app.post("/einkauf/add")
def einkauf_add(text: str = Form(...), menge: str = Form("")):
    if text.strip():
        core.einkauf_add(text.strip(), menge=menge.strip())
    return RedirectResponse("/einkauf", status_code=303)


@app.post("/einkauf/{item_id}/toggle")
def einkauf_toggle(item_id: str):
    try:
        core.einkauf_toggle(item_id)
    except ValueError:
        raise StarletteHTTPException(status_code=404)
    return RedirectResponse("/einkauf", status_code=303)


@app.post("/einkauf/{item_id}/remove")
def einkauf_remove(item_id: str):
    try:
        core.einkauf_remove(item_id)
    except ValueError:
        raise StarletteHTTPException(status_code=404)
    return RedirectResponse("/einkauf", status_code=303)


@app.post("/einkauf/clear")
def einkauf_clear():
    core.einkauf_clear_done()
    return RedirectResponse("/einkauf", status_code=303)


@app.post("/rezept/{slug}/einkauf")
def rezept_einkauf(slug: str):
    try:
        core.einkauf_add_rezept(slug)
    except ValueError:
        raise StarletteHTTPException(status_code=404)
    return RedirectResponse("/einkauf", status_code=303)


# --- Einkauf-Sync-API (Phase 2; Kontrakt: docs/sync-kontrakt.md) -------------
# Voll-State-Sync als JSON fuer die PWA. Implementierung: Subagent 2A.

@app.get("/api/einkauf")
def api_einkauf_get():
    """Alle Items inkl. Tombstones als {"items": [<item-dict>, ...]}.

    Read-only-Ausgangsstand fuer die PWA und fuer Agents/Skripte.
    Implementierung (2A): JSONResponse({"items": [i.to_dict() for i in
    core.einkauf_load()]}).
    """
    return JSONResponse({"items": [i.to_dict() for i in core.einkauf_load()]})


@app.post("/api/einkauf/sync")
async def api_einkauf_sync(request: Request):
    """Voll-State-Sync. Body: {"items": [<item-dict>, ...]} (lokaler Stand des
    Clients). Antwort: {"items": [...]} = serverseitig gemergter Gesamtstand
    (core.einkauf_merge, "letzter gewinnt" + Tombstones).

    Implementierung (2A): body = await request.json();
    merged = core.einkauf_merge(body.get("items", []));
    return JSONResponse({"items": [i.to_dict() for i in merged]}).
    """
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON"}, status_code=400)
    merged = core.einkauf_merge(body.get("items", []))
    return JSONResponse({"items": [i.to_dict() for i in merged]})


# --- PWA: Service-Worker am Root ausliefern ---------------------------------
# Die Datei liegt unter static/, der SW MUSS aber vom Root aus registriert
# werden, sonst ist sein Scope nur /static/ und er kann /einkauf & Co. nicht
# offline bedienen.

@app.get("/sw.js", include_in_schema=False)
def service_worker():
    return FileResponse(str(BASE / "static" / "sw.js"),
                        media_type="application/javascript")


@app.exception_handler(StarletteHTTPException)
async def http_exception(request: Request, exc: StarletteHTTPException):
    if exc.status_code == 404:
        return templates.TemplateResponse(request, "404.html", {
            "nav": "", "titel": "Nicht gefunden",
        }, status_code=404)
    return PlainTextResponse(str(exc.detail or exc.status_code), status_code=exc.status_code)
