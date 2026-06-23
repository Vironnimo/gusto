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
import uuid
from dataclasses import dataclass, field, asdict, fields
from datetime import date, datetime, timedelta, timezone
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


def categories_path() -> Path:
    return data_dir() / "categories.json"


def recipe_file(slug: str) -> Path:
    return recipes_dir() / f"{slug}.md"


def einkauf_path() -> Path:
    return data_dir() / "einkaufsliste.json"


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


# --- Kategorien (Facetten) --------------------------------------------------
# Tags sind in data/recipes.json bewusst eine flache Liste. Welcher Tag zu
# welcher Kategorie gehoert, steht zentral in data/categories.json:
#   { "<key>": {"label": str, "tags": [str, ...]}, ... }
# Die Reihenfolge im JSON ist die Anzeige-Reihenfolge. Ein Tag ohne Kategorie
# gilt als "unsortiert" und landet in der gemeinsamen Gruppe "Sonstige".

def load_categories() -> dict:
    """Kategorien-Definition aus data/categories.json (Reihenfolge erhalten).
    Fehlt die Datei, ist das Ergebnis {} – dann ist jeder Tag unsortiert."""
    p = categories_path()
    if not p.exists():
        return {}
    return json.loads(p.read_text(encoding="utf-8"))


def _tag_to_category(categories: dict | None = None) -> dict[str, str]:
    """Inverse Zuordnung tag(lowercase) -> kategorie-key."""
    categories = load_categories() if categories is None else categories
    return {t.lower(): key
            for key, cat in categories.items()
            for t in cat.get("tags", [])}


def tag_category(tag: str, categories: dict | None = None) -> str | None:
    """Kategorie-Key eines Tags, oder None wenn unsortiert."""
    return _tag_to_category(categories).get(tag.lower())


def tag_groups(only_used: bool = True) -> list[dict]:
    """Kategorien mit ihren Tags in Anzeige-Reihenfolge – fuer die Tag-Leiste
    und `recipe tags`. Gibt [{"key","label","tags":[...]}, ...] zurueck.

    only_used=True: nur Tags, die in Rezepten vorkommen; leere Kategorien
    entfallen; tatsaechlich verwendete Tags ohne Kategorie kommen als Gruppe
    "Sonstige" ans Ende. only_used=False: alle definierten Kategorien/Tags."""
    categories = load_categories()
    used_names = sorted({t for r in load_recipes() for t in r.tags},
                        key=str.lower) if only_used else None
    used_lower = {t.lower() for t in used_names} if used_names is not None else None

    gruppen: list[dict] = []
    erfasst: set[str] = set()
    for key, cat in categories.items():
        erfasst |= {t.lower() for t in cat.get("tags", [])}
        tags = [t for t in cat.get("tags", [])
                if used_lower is None or t.lower() in used_lower]
        if tags or used_lower is None:
            gruppen.append({"key": key, "label": cat.get("label", key), "tags": tags})

    if used_names is not None:
        rest = [t for t in used_names if t.lower() not in erfasst]
        if rest:
            gruppen.append({"key": "sonstige", "label": "Sonstige", "tags": rest})
    return gruppen


# --- Suche ------------------------------------------------------------------

def search(query: str = "", match: str = "any", tags: list[str] | None = None,
           max_time: int | None = None) -> list[Recipe]:
    """Filtert ueber Metadaten (tags, max_time) und durchsucht bei `query`
    zusaetzlich Titel, Tags UND den Markdown-Inhalt (also auch die Zutaten).

    Tag-Filter (Facetten): mehrere `tags` werden nach ihrer Kategorie gruppiert.
    Ein Rezept passt, wenn es in JEDER ausgewaehlten Kategorie MINDESTENS EINEN
    der gewaehlten Tags besitzt – also ODER innerhalb einer Kategorie und UND
    ueber Kategorien hinweg. Tags ohne Kategorie bilden gemeinsam die Gruppe
    "Sonstige" (untereinander ebenfalls ODER)."""
    terme = [t.lower() for t in query.split()]
    gruppen = _gruppiere_tags(tags or [])
    treffer = []
    for r in load_recipes():
        if gruppen and not _passt_tags(r, gruppen):
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


def _gruppiere_tags(tags: list[str]) -> dict[str, set[str]]:
    """Ausgewaehlte Tags nach Kategorie gruppieren: key -> {tag-lowercase, ...}.
    Tags ohne Kategorie landen gemeinsam unter "__sonstige__"."""
    mapping = _tag_to_category()
    gruppen: dict[str, set[str]] = {}
    for t in tags:
        tl = t.lower()
        gruppen.setdefault(mapping.get(tl, "__sonstige__"), set()).add(tl)
    return gruppen


def _passt_tags(r: Recipe, gruppen: dict[str, set[str]]) -> bool:
    """ODER innerhalb einer Gruppe, UND ueber Gruppen hinweg."""
    rezept_tags = {t.lower() for t in r.tags}
    return all(rezept_tags & gewaehlt for gewaehlt in gruppen.values())


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
    """Prueft, ob Index (recipes.json) und .md-Dateien zusammenpassen und ob
    alle verwendeten Tags einer Kategorie (categories.json) zugeordnet sind."""
    recipes = load_recipes()
    indexiert = {r.slug for r in recipes}
    vorhanden = {p.stem for p in recipes_dir().glob("*.md")} if recipes_dir().exists() else set()
    mapping = _tag_to_category()
    unsortiert = sorted({t for r in recipes for t in r.tags if t.lower() not in mapping})
    return {
        "anzahl_rezepte": len(indexiert),
        "verwaiste_dateien": sorted(vorhanden - indexiert),   # .md ohne Index-Eintrag
        "fehlende_dateien": sorted(indexiert - vorhanden),    # Index-Eintrag ohne .md
        "unsortierte_tags": unsortiert,                       # Tags in keiner Kategorie
    }


# ===========================================================================
# Einkaufsliste  (Feature: Einkaufsliste + PWA, siehe docs/plan-einkaufsliste-pwa.md)
# ===========================================================================
#
# KONTRAKT (Phase 0). Datenmodell, Signaturen und Regeln stehen hier fest; die
# Implementierung folgt in Phase 1 (Subagent 1A) bzw. fuer `einkauf_merge` in
# Phase 2.1 (Subagent 2A). Bis dahin werfen die Funktionen NotImplementedError.
#
# Speicherort:  data/einkaufsliste.json   (Form: {"items": [ <item-dict>, ... ]})
# Ein Item ist sync-faehig: `geaendert_am` (UTC-ISO) treibt beim Sync
# "letzter gewinnt", `geloescht` ist ein Tombstone, damit Loeschungen
# zwischen Geraeten propagieren. NICHTS wird hart geloescht.


@dataclass
class EinkaufItem:
    """Ein Eintrag auf der Einkaufsliste (sync-faehig)."""
    id: str
    text: str                       # z.B. "200 g Spaghetti" (v1: ganze Zutat-Zeile)
    menge: str = ""                 # optional, v1 meist leer (text enthaelt die Menge)
    checked: bool = False           # abgehakt?
    quelle: str | None = None       # slug des Rezepts, aus dem die Zutat stammt
    erstellt_am: str = ""           # UTC-ISO, z.B. "2026-06-23T18:00:00Z"
    geaendert_am: str = ""          # UTC-ISO – treibt "letzter gewinnt" beim Sync
    geloescht: bool = False         # Tombstone fuer Sync (nie hart loeschen)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "EinkaufItem":
        erlaubt = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in erlaubt})


def _jetzt_iso() -> str:
    """Aktueller UTC-Zeitstempel als ISO 8601 mit 'Z' (sekundengenau).

    Beispiel: '2026-06-23T18:00:00Z'. ISO-Strings dieser Form sind als Text
    direkt vergleichbar – genau das braucht der Sync ("letzter gewinnt").
    """
    return (datetime.now(timezone.utc).replace(microsecond=0)
            .isoformat().replace("+00:00", "Z"))


def new_id() -> str:
    """Neue eindeutige Item-id (uuid4-hex)."""
    return uuid.uuid4().hex


# --- Zutaten-Parsing --------------------------------------------------------

def parse_zutaten(inhalt: str) -> list[str]:
    """Zutaten-Zeilen aus dem Rezept-Markdown extrahieren.

    Regel (v1): Alle Bullet-Items ('-' oder '*') im Abschnitt unter der
    Ueberschrift '## Zutaten', bis zur naechsten '##'-Ueberschrift. Pro Bullet
    die GANZE Zeile (ohne Bullet-Zeichen und Rand-Whitespace) als ein Eintrag.
    Leere Bullets werden uebersprungen. Gibt es keinen Zutaten-Abschnitt, ist
    das Ergebnis [].
    """
    zutaten: list[str] = []
    im_abschnitt = False
    for zeile in inhalt.splitlines():
        s = zeile.strip()
        if s == "## Zutaten":
            im_abschnitt = True
            continue
        if not im_abschnitt:
            continue
        if s.startswith("## "):       # naechste Ueberschrift beendet den Abschnitt
            break
        if s.startswith("-") or s.startswith("*"):
            eintrag = s[1:].strip()
            if eintrag:               # leere Bullets ueberspringen
                zutaten.append(eintrag)
    return zutaten


# --- Laden / Speichern ------------------------------------------------------

def einkauf_load() -> list[EinkaufItem]:
    """Alle Items aus data/einkaufsliste.json laden – INKLUSIVE Tombstones
    (geloescht=True). Existiert die Datei nicht, ist das Ergebnis []."""
    p = einkauf_path()
    if not p.exists():
        return []
    daten = json.loads(p.read_text(encoding="utf-8"))
    return [EinkaufItem.from_dict(d) for d in daten.get("items", [])]


def einkauf_save(items: list[EinkaufItem]) -> None:
    """Items atomar nach data/einkaufsliste.json schreiben (nutze _write_json).
    Dateiform: {"items": [ <item-dict>, ... ]}. Tombstones bleiben erhalten."""
    _write_json(einkauf_path(), {"items": [i.to_dict() for i in items]})


# --- Veraendern -------------------------------------------------------------

def einkauf_add(text: str, menge: str = "", quelle: str | None = None) -> EinkaufItem:
    """Ein neues Item anlegen, speichern und zurueckgeben. Setzt id (new_id()),
    erstellt_am und geaendert_am (= _jetzt_iso()), checked=False,
    geloescht=False."""
    jetzt = _jetzt_iso()
    item = EinkaufItem(id=new_id(), text=text, menge=menge, checked=False,
                       quelle=quelle, erstellt_am=jetzt, geaendert_am=jetzt,
                       geloescht=False)
    items = einkauf_load()
    items.append(item)
    einkauf_save(items)
    return item


def einkauf_add_rezept(slug: str) -> list[EinkaufItem]:
    """Alle Zutaten eines Rezepts (parse_zutaten auf dessen .md) als Items auf
    die Liste setzen, quelle=slug. Gibt die NEU hinzugefuegten Items zurueck.
    ValueError, wenn es kein Rezept mit diesem slug gibt."""
    r = get(slug)
    if r is None:
        raise ValueError(f"Kein Rezept mit Slug '{slug}'.")
    items = einkauf_load()
    neu: list[EinkaufItem] = []
    for zutat in parse_zutaten(r.inhalt()):
        jetzt = _jetzt_iso()
        neu.append(EinkaufItem(id=new_id(), text=zutat, menge="", checked=False,
                               quelle=slug, erstellt_am=jetzt, geaendert_am=jetzt,
                               geloescht=False))
    items.extend(neu)
    einkauf_save(items)
    return neu


def einkauf_list(include_done: bool = True,
                 include_deleted: bool = False) -> list[EinkaufItem]:
    """Sichtbare Items. Tombstones (geloescht) standardmaessig ausgeblendet;
    include_done=False blendet zusaetzlich erledigte (checked) aus.
    Reihenfolge: nach erstellt_am aufsteigend (Einfuegereihenfolge)."""
    items = [i for i in einkauf_load() if include_deleted or not i.geloescht]
    if not include_done:
        items = [i for i in items if not i.checked]
    items.sort(key=lambda i: i.erstellt_am)
    return items


def einkauf_toggle(item_id: str, checked: bool | None = None) -> EinkaufItem:
    """Erledigt-Haekchen setzen. checked=None schaltet um; sonst wird der Wert
    gesetzt. Aktualisiert geaendert_am und gibt das Item zurueck.
    ValueError, wenn die id unbekannt ist oder das Item ein Tombstone ist."""
    items = einkauf_load()
    ziel = next((i for i in items if i.id == item_id and not i.geloescht), None)
    if ziel is None:
        raise ValueError(f"Kein Einkauf-Item mit id '{item_id}'.")
    ziel.checked = (not ziel.checked) if checked is None else checked
    ziel.geaendert_am = _jetzt_iso()
    einkauf_save(items)
    return ziel


def einkauf_remove(item_id: str) -> None:
    """Item als Tombstone markieren: geloescht=True + geaendert_am aktualisieren
    (NICHT hart aus der Datei loeschen, damit die Loeschung synchronisiert).
    ValueError, wenn die id unbekannt ist."""
    items = einkauf_load()
    ziel = next((i for i in items if i.id == item_id), None)
    if ziel is None:
        raise ValueError(f"Kein Einkauf-Item mit id '{item_id}'.")
    ziel.geloescht = True
    ziel.geaendert_am = _jetzt_iso()
    einkauf_save(items)


def einkauf_clear_done() -> int:
    """Alle erledigten (checked, noch nicht Tombstone) Items als Tombstone
    markieren (geloescht=True + geaendert_am). Gibt die Anzahl der so
    entfernten Items zurueck."""
    items = einkauf_load()
    anzahl = 0
    for i in items:
        if i.checked and not i.geloescht:
            i.geloescht = True
            i.geaendert_am = _jetzt_iso()
            anzahl += 1
    if anzahl:
        einkauf_save(items)
    return anzahl


# --- Sync (Phase 2) ---------------------------------------------------------

def einkauf_merge(remote_items: list[dict]) -> list[EinkaufItem]:
    """Voll-State-Sync: remote_items (rohe Item-Dicts vom Client) in die lokale
    Liste mergen, das Ergebnis speichern und zurueckgeben (inkl. Tombstones).

    Regel "letzter gewinnt": pro id gewinnt die Version mit dem groesseren
    geaendert_am (die ISO-Strings sind als Text vergleichbar). Bei Gleichstand
    bleibt die lokale Version. Ids, die nur remote existieren, werden
    uebernommen; Tombstones (geloescht=True) propagieren wie jede andere
    Aenderung.

    Hinweis: Erst in Phase 2.1 (Subagent 2A) zu implementieren.
    """
    # Lokaler Stand (inkl. Tombstones) als Quelle der Wahrheit, indiziert per id.
    gemergt: dict[str, EinkaufItem] = {i.id: i for i in einkauf_load()}

    for roh in remote_items:
        remote = EinkaufItem.from_dict(roh)
        lokal = gemergt.get(remote.id)
        # id nur remote -> uebernehmen.
        # id beidseitig -> groesseres geaendert_am gewinnt (String-Vergleich);
        # bei Gleichstand bleibt die lokale Version.
        if lokal is None or remote.geaendert_am > lokal.geaendert_am:
            gemergt[remote.id] = remote
    # ids, die nur lokal existieren, bleiben unveraendert erhalten.

    ergebnis = list(gemergt.values())
    einkauf_save(ergebnis)
    return ergebnis
