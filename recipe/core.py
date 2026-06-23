"""Kern-Logik des Rezept-Systems.

Hier lebt ALLE Logik. Die CLI (recipe/cli.py) und – später – die Web-UI sind
nur duenne Huellen um dieses Modul. Es gibt bewusst kein Feature, das nur in
einer Oberflaeche existiert.

Datenmodell:
  recipes/<slug>.md   reiner Markdown-Inhalt eines Rezepts (KEIN Frontmatter)
  data/recipes.json   Metadaten ALLER Rezepte (Quelle fuer Liste/Suche/Filter)
  data/log.json       Koch-Logbuch: was wurde wann gekocht

Der slug verbindet beides:  data/recipes.json[*].slug  <->  recipes/<slug>.md
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field, asdict, fields
from datetime import date, timedelta
from pathlib import Path


# --- Pfade (per RECIPE_HOME ueberschreibbar, z.B. auf dem Pi) ----------------

def project_root() -> Path:
    env = os.environ.get("RECIPE_HOME")
    if env:
        return Path(env).expanduser()
    return Path(__file__).resolve().parent.parent


def recipes_dir() -> Path:
    return project_root() / "recipes"


def data_dir() -> Path:
    return project_root() / "data"


def index_path() -> Path:
    return data_dir() / "recipes.json"


def log_path() -> Path:
    return data_dir() / "log.json"


def recipe_file(slug: str) -> Path:
    return recipes_dir() / f"{slug}.md"


# --- Datenmodell ------------------------------------------------------------

@dataclass
class Recipe:
    slug: str
    titel: str
    tags: list[str] = field(default_factory=list)
    dauer_minuten: int | None = None
    portionen: int | None = None
    zuletzt_gekocht: str | None = None  # ISO "YYYY-MM-DD" oder None

    @property
    def pfad(self) -> Path:
        return recipe_file(self.slug)

    def inhalt(self) -> str:
        """Reiner Markdown-Inhalt aus der .md-Datei."""
        p = self.pfad
        return p.read_text(encoding="utf-8") if p.exists() else ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Recipe":
        erlaubt = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in erlaubt})


# --- Laden / Speichern ------------------------------------------------------

def load_recipes() -> list[Recipe]:
    p = index_path()
    if not p.exists():
        return []
    return [Recipe.from_dict(d) for d in json.loads(p.read_text(encoding="utf-8"))]


def save_recipes(recipes: list[Recipe]) -> None:
    _write_json(index_path(), [r.to_dict() for r in recipes])


def get(slug: str) -> Recipe | None:
    return next((r for r in load_recipes() if r.slug == slug), None)


def _write_json(path: Path, data) -> None:
    """Atomar schreiben – bei Abbruch bleibt keine halbe Datei zurueck."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


# --- Anlegen ----------------------------------------------------------------

def slugify(titel: str) -> str:
    s = titel.strip().lower()
    s = s.replace("ä", "ae").replace("ö", "oe").replace("ü", "ue").replace("ß", "ss")
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s or "rezept"


def add_recipe(titel: str, tags=None, dauer_minuten=None, portionen=None,
               inhalt: str | None = None, slug: str | None = None) -> Recipe:
    recipes = load_recipes()
    slug = slug or slugify(titel)
    if any(r.slug == slug for r in recipes):
        raise ValueError(f"Es gibt bereits ein Rezept mit dem Slug '{slug}'.")
    recipes_dir().mkdir(parents=True, exist_ok=True)
    recipe_file(slug).write_text(
        inhalt if inhalt is not None else _vorlage(titel), encoding="utf-8"
    )
    r = Recipe(slug=slug, titel=titel, tags=tags or [],
               dauer_minuten=dauer_minuten, portionen=portionen)
    recipes.append(r)
    save_recipes(recipes)
    return r


def _vorlage(titel: str) -> str:
    return f"# {titel}\n\n## Zutaten\n\n- \n\n## Zubereitung\n\n1. \n"


# --- Suche ------------------------------------------------------------------

def search(query: str = "", match: str = "any", tag: str | None = None,
           max_time: int | None = None) -> list[Recipe]:
    """Filtert ueber Metadaten (tag, max_time) und durchsucht bei `query`
    zusaetzlich Titel, Tags UND den Markdown-Inhalt (also auch die Zutaten)."""
    terme = [t.lower() for t in query.split()]
    treffer = []
    for r in load_recipes():
        if tag and tag.lower() not in [t.lower() for t in r.tags]:
            continue
        if max_time is not None and (r.dauer_minuten is None or r.dauer_minuten > max_time):
            continue
        if terme:
            heuhaufen = f"{r.titel} {' '.join(r.tags)} {r.inhalt()}".lower()
            ok = (all(t in heuhaufen for t in terme) if match == "all"
                  else any(t in heuhaufen for t in terme))
            if not ok:
                continue
        treffer.append(r)
    return treffer


# --- Logbuch ----------------------------------------------------------------

def load_log(days: int | None = None) -> list[dict]:
    p = log_path()
    eintraege = json.loads(p.read_text(encoding="utf-8")) if p.exists() else []
    if days is not None:
        grenze = (date.today() - timedelta(days=days)).isoformat()
        eintraege = [e for e in eintraege if e["datum"] >= grenze]
    return sorted(eintraege, key=lambda e: e["datum"])


def log_cooked(slug: str, datum: str | None = None) -> None:
    recipes = load_recipes()
    if not any(r.slug == slug for r in recipes):
        raise ValueError(f"Kein Rezept mit Slug '{slug}'.")
    datum = datum or date.today().isoformat()
    eintraege = load_log()
    eintraege.append({"datum": datum, "slug": slug})
    _write_json(log_path(), sorted(eintraege, key=lambda e: e["datum"]))
    for r in recipes:
        if r.slug == slug and (r.zuletzt_gekocht is None or datum > r.zuletzt_gekocht):
            r.zuletzt_gekocht = datum
    save_recipes(recipes)


# --- Vorschlaege ------------------------------------------------------------

def suggest(days: int = 7, limit: int | None = None) -> list[Recipe]:
    """Kandidaten fuers naechste Essen: alles, was in den letzten `days` Tagen
    NICHT gekocht wurde – am laengsten nicht Gekochtes zuerst.

    Bewusst simpel und regelbasiert: die eigentliche Entscheidung trifft der
    Mensch oder ein Agent, der zusaetzlich `log`, `search` & `list` nutzt.
    """
    kuerzlich = {e["slug"] for e in load_log(days=days)}
    rest = [r for r in load_recipes() if r.slug not in kuerzlich]
    rest.sort(key=lambda r: r.zuletzt_gekocht or "")  # None/"" = am laengsten her
    return rest[:limit] if limit else rest


# --- Aendern / Loeschen -----------------------------------------------------

def update_recipe(slug: str, titel: str | None = None, tags=None,
                  dauer_minuten=None, portionen=None,
                  inhalt: str | None = None) -> Recipe:
    """Aktualisiert ein Rezept. None bedeutet 'unveraendert lassen'
    (Ausnahme: tags=[] leert die Tags). `inhalt` ueberschreibt die .md."""
    recipes = load_recipes()
    ziel = next((r for r in recipes if r.slug == slug), None)
    if ziel is None:
        raise ValueError(f"Kein Rezept mit Slug '{slug}'.")
    if titel is not None:
        ziel.titel = titel
    if tags is not None:
        ziel.tags = tags
    if dauer_minuten is not None:
        ziel.dauer_minuten = dauer_minuten
    if portionen is not None:
        ziel.portionen = portionen
    if inhalt is not None:
        recipe_file(slug).write_text(inhalt, encoding="utf-8")
    save_recipes(recipes)
    return ziel


def delete_recipe(slug: str) -> None:
    """Entfernt Index-Eintrag und .md-Datei. Logbuch-Eintraege bleiben
    als Historie erhalten."""
    recipes = load_recipes()
    rest = [r for r in recipes if r.slug != slug]
    if len(rest) == len(recipes):
        raise ValueError(f"Kein Rezept mit Slug '{slug}'.")
    save_recipes(rest)
    p = recipe_file(slug)
    if p.exists():
        p.unlink()


# --- Konsistenz -------------------------------------------------------------

def check() -> dict:
    """Prueft, ob Index (recipes.json) und .md-Dateien zusammenpassen."""
    indexiert = {r.slug for r in load_recipes()}
    vorhanden = {p.stem for p in recipes_dir().glob("*.md")} if recipes_dir().exists() else set()
    return {
        "anzahl_rezepte": len(indexiert),
        "verwaiste_dateien": sorted(vorhanden - indexiert),   # .md ohne Index-Eintrag
        "fehlende_dateien": sorted(indexiert - vorhanden),    # Index-Eintrag ohne .md
    }
