"""Active recipe metadata, Markdown and cooking transactions."""
from __future__ import annotations

import hashlib
import json
import re
from datetime import date
import gusto.core.records as records
import gusto.core._validation as validation
import gusto.core.storage as storage
import gusto.core.errors as errors


def load_recipes() -> list[records.Recipe]:
    p = storage.index_path()
    if not p.exists():
        return []
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(
            f"Ungültiger Rezeptkatalog in '{p.name}': {error}"
        ) from error
    if not isinstance(raw, list):
        raise ValueError(
            f"Ungültiger Rezeptkatalog in '{p.name}': JSON-Liste erwartet."
        )
    recipes = []
    for entry in raw:
        if not isinstance(entry, dict):
            raise ValueError(
                f"Ungültiger Rezeptkatalog in '{p.name}': "
                "jeder Eintrag muss ein JSON-Objekt sein."
            )
        try:
            recipes.append(records.Recipe.from_dict(entry))
        except ValueError as error:
            raise ValueError(
                f"Ungültiger Rezeptkatalog in '{p.name}': {error}"
            ) from error
    return recipes


def _save_recipes_unlocked(recipes: list[records.Recipe]) -> None:
    storage.write_json(storage.index_path(), [r.to_dict() for r in recipes])


@storage.locked_mutation("catalog")
def save_recipes(recipes: list[records.Recipe]) -> None:
    """Replace the full catalog through the public locked save primitive."""
    _save_recipes_unlocked(recipes)


def get(slug: str) -> records.Recipe | None:
    return next((r for r in load_recipes() if r.slug == slug), None)


def edit_revision(recipe: records.Recipe, content: str | None) -> str:
    """Identify the editable state; None distinguishes a missing Markdown file."""
    value = [recipe.slug, recipe.title, recipe.tags, recipe.duration_min,
             recipe.servings, content]
    encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _edit_content(recipe: records.Recipe) -> str | None:
    return recipe.path.read_text(encoding="utf-8") if recipe.path.is_file() else None


@storage.locked_mutation("catalog")
def get_edit_snapshot(slug: str) -> dict:
    """Read recipe metadata, Markdown and its edit token under one lock."""
    recipe = get(slug)
    if recipe is None:
        raise errors.NotFoundError(f"Kein Rezept mit Slug '{slug}'.")
    content = _edit_content(recipe)
    return {"recipe": recipe, "content": content or "",
            "revision": edit_revision(recipe, content)}


MAX_RECIPE_SLUG_LENGTH = 150


_WINDOWS_DEVICE_NAMES = {
    "con", "prn", "aux", "nul",
    *(f"com{number}" for number in range(1, 10)),
    *(f"lpt{number}" for number in range(1, 10)),
}


def slugify(title: str) -> str:
    s = title.strip().lower()
    s = s.replace("ä", "ae").replace("ö", "oe").replace("ü", "ue").replace("ß", "ss")
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    s = s[:MAX_RECIPE_SLUG_LENGTH].rstrip("-") or "recipe"
    return f"recipe-{s}" if s in _WINDOWS_DEVICE_NAMES else s


def _recipe_slug(slug: str) -> str:
    """Keep recipe-owned paths portable across Windows and Linux."""
    if (not isinstance(slug, str) or not slug
            or len(slug) > MAX_RECIPE_SLUG_LENGTH
            or slug.startswith(".") or slug.endswith((".", " "))
            or slug.casefold() == "_favorites"
            or slug.split(".", 1)[0].casefold() in _WINDOWS_DEVICE_NAMES
            or any(ord(character) < 32 or character in '<>:"/\\|?*'
                   for character in slug)):
        raise ValueError(
            "Der Rezept-Slug muss ein nicht-leerer, portabler Dateiname "
            f"mit höchstens {MAX_RECIPE_SLUG_LENGTH} Zeichen sein; "
            "Pfadanteile, führende Punkte und reservierte Namen sind nicht erlaubt."
        )
    return slug


@storage.locked_mutation("catalog")
def add_recipe(title: str, tags=None, duration_min=None, servings=None,
               content: str | None = None, slug: str | None = None) -> records.Recipe:
    title = _recipe_title(title)
    tags = _recipe_tags(tags or [])
    validation._positive_recipe_number(duration_min, "Die Dauer")
    validation._positive_recipe_number(servings, "Die Portionszahl")
    slug = _recipe_slug(slugify(title) if slug is None else slug)
    recipes = load_recipes()
    if any(r.slug == slug for r in recipes):
        raise ValueError(f"Es gibt bereits ein Rezept mit dem Slug '{slug}'.")
    if storage.archive_recipe_dir(slug).exists():
        raise ValueError(
            f"Der Slug '{slug}' liegt im Archiv. Stelle das Rezept wieder her "
            "oder lösche den Archiveintrag endgültig."
        )
    markdown_path = storage.recipe_file(slug)
    if markdown_path.exists() or storage.recipe_images_dir(slug).exists():
        raise ValueError(
            f"Dateien für Rezept '{slug}' sind bereits vorhanden. "
            "Prüfe den Datenbestand mit 'gusto check', bevor du ein neues "
            "Rezept mit diesem Slug anlegst."
        )
    storage.recipes_dir().mkdir(parents=True, exist_ok=True)
    recipe_content = (
        _content_with_title(content, title) if content is not None
        else _template(title)
    )
    r = records.Recipe(slug=slug, title=title, tags=tags,
               duration_min=duration_min, servings=servings)
    recipes.append(r)
    checkpoint = storage.mutation_checkpoint()
    index_checkpoint = None
    try:
        storage.write_text(markdown_path, recipe_content)
        index_checkpoint = storage.mutation_checkpoint()
        _save_recipes_unlocked(recipes)
    except Exception:
        # Without the index entry the .md would stay behind as an orphaned
        # file. Roll it back only if the index write did not commit; otherwise
        # the new recipe is already durable and its files must stay together.
        if index_checkpoint is None or storage.mutation_checkpoint() == index_checkpoint:
            markdown_path.unlink(missing_ok=True)
            storage.rollback_completed(checkpoint)
        raise
    return r


def _template(title: str) -> str:
    return f"# {title}\n\n## Zutaten\n\n- \n\n## Zubereitung\n\n1. \n"


MAX_RECIPE_TITLE_LENGTH = 150


def _recipe_title(value: str) -> str:
    """Return a normalized non-empty, single-line recipe title."""
    if (not isinstance(value, str) or not value.strip()
            or "\n" in value or "\r" in value):
        raise ValueError(
            "Das Rezept braucht einen nicht-leeren, einzeiligen Titel."
        )
    title = value.strip()
    if len(title) > MAX_RECIPE_TITLE_LENGTH:
        raise ValueError(
            f"Der Titel darf höchstens {MAX_RECIPE_TITLE_LENGTH} Zeichen lang sein."
        )
    return title


def _recipe_tags(values) -> list[str]:
    """Return one-line recipe tags, case-insensitively deduplicated."""
    if (not isinstance(values, list)
            or not all(isinstance(value, str) and value.strip()
                       and "\n" not in value and "\r" not in value
                       for value in values)):
        raise ValueError(
            "Rezept-Tags müssen eine Liste nicht-leerer, einzeiliger Texte sein."
        )
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        tag = value.strip()
        if tag.lower() not in seen:
            seen.add(tag.lower())
            result.append(tag)
    return result


def _content_with_title(content: str, title: str) -> str:
    """Make the first Markdown line the canonical recipe title."""
    normalized = (content or "").replace("\r\n", "\n").replace("\r", "\n")
    first, separator, remainder = normalized.partition("\n")
    if re.fullmatch(r"#(?:\s.*)?", first):
        return f"# {title}" + (separator + remainder if separator else "\n")
    if not normalized:
        return f"# {title}\n"
    return f"# {title}\n\n{normalized.lstrip(chr(10))}"


def _markdown_title(content: str) -> str | None:
    first = content.replace("\r\n", "\n").replace("\r", "\n").partition("\n")[0]
    match = re.fullmatch(r"#\s+(.+?)\s*", first)
    return match.group(1) if match else None


def load_log(days: int | None = None) -> list[dict]:
    validation._positive_recipe_number(days, "Die Anzahl der Tage")
    p = storage.log_path()
    entries: list[dict] = []
    if p.exists():
        try:
            entries = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ValueError(
                f"Ungültiges Kochprotokoll in '{p.name}': {error}"
            ) from error
        if (not isinstance(entries, list)
                or not all(isinstance(entry, dict) for entry in entries)):
            raise ValueError(
                f"Ungültiges Kochprotokoll in '{p.name}': "
                "JSON-Liste von Objekten erwartet."
            )
        # 'date' treibt Filter und Sortierung; ein gebrochener Eintrag würde
        # sonst mit raw KeyError den Loader (und check()) crashen, statt als
        # Datenfehler gemeldet zu werden.
        for entry in entries:
            if (not isinstance(entry.get("date"), str)
                    or not isinstance(entry.get("slug"), str)):
                raise ValueError(
                    f"Ungültiges Kochprotokoll in '{p.name}': "
                    "jeder Eintrag braucht ein Textfeld 'date' und 'slug'."
                )
    if days is not None:
        # A window longer than the calendar reaches all available history;
        # even arbitrarily large positive CLI integers must not overflow.
        cutoff = date.fromordinal(max(1, date.today().toordinal() - days)).isoformat()
        entries = [e for e in entries if e["date"] >= cutoff]
    return sorted(entries, key=lambda e: e["date"])


@storage.locked_mutation("catalog", "log")
def log_cooked(slug: str, when: str | None = None) -> str:
    """Record a valid ISO date and return exactly the committed date."""
    recipes = load_recipes()
    if not any(r.slug == slug for r in recipes):
        raise errors.NotFoundError(f"Kein Rezept mit Slug '{slug}'.")
    if when is None:
        when = date.today().isoformat()
    if not isinstance(when, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", when):
        raise ValueError("Das Kochdatum muss im Format YYYY-MM-DD angegeben werden.")
    try:
        cooked_on = date.fromisoformat(when)
    except ValueError as error:
        raise ValueError("Das Kochdatum ist kein gültiges Kalenderdatum.") from error
    if cooked_on > date.today():
        raise ValueError("Das Kochdatum darf nicht in der Zukunft liegen.")
    entries = load_log()
    log_existed = storage.log_path().is_file()
    entries.append({"date": when, "slug": slug})
    for r in recipes:
        if r.slug == slug and (r.last_cooked is None or when > r.last_cooked):
            r.last_cooked = when
    checkpoint = storage.mutation_checkpoint()
    index_checkpoint = None
    try:
        storage.write_json(storage.log_path(), sorted(entries, key=lambda e: e["date"]))
        index_checkpoint = storage.mutation_checkpoint()
        _save_recipes_unlocked(recipes)
    except Exception:
        # The log entry must never persist without its last_cooked update.
        if index_checkpoint is None or storage.mutation_checkpoint() == index_checkpoint:
            if log_existed:
                storage.write_json(storage.log_path(), entries[:-1], track_mutation=False)
            else:
                storage.log_path().unlink(missing_ok=True)
            storage.rollback_completed(checkpoint)
        raise
    return when


def suggest(days: int = 7, limit: int | None = None) -> list[records.Recipe]:
    """Candidates for the next meal: everything NOT cooked in the last `days`
    days -- longest-not-cooked first.

    Deliberately simple and rule-based: the actual decision is made by a human
    or an agent that additionally uses `log`, `search` & `list`.
    """
    if limit is not None and (
            isinstance(limit, bool) or not isinstance(limit, int) or limit < 0):
        raise ValueError("Das Vorschlagslimit muss eine nichtnegative ganze Zahl sein.")
    recent = {e["slug"] for e in load_log(days=days)}
    remaining = [r for r in load_recipes() if r.slug not in recent]
    remaining.sort(key=lambda r: r.last_cooked or "")  # None/"" = longest ago
    return remaining[:limit] if limit is not None else remaining


@storage.locked_mutation("catalog")
def update_recipe(slug: str, title: str | None = None, tags=None,
                  duration_min=None, servings=None,
                  content: str | None = None, *,
                  clear_duration: bool = False,
                  clear_servings: bool = False,
                  expected_revision: str | None = None) -> records.Recipe:
    """Update a recipe. None means 'leave unchanged' (exception: tags=[] clears
    the tags). Explicit clear flags remove optional numeric metadata; `content`
    overwrites the .md file."""
    if clear_duration and duration_min is not None:
        raise ValueError("Die Dauer kann nicht gleichzeitig gesetzt und entfernt werden.")
    if clear_servings and servings is not None:
        raise ValueError("Die Portionszahl kann nicht gleichzeitig gesetzt und entfernt werden.")
    validation._positive_recipe_number(duration_min, "Die Dauer")
    validation._positive_recipe_number(servings, "Die Portionszahl")
    recipes = load_recipes()
    target = next((r for r in recipes if r.slug == slug), None)
    if target is None:
        raise errors.NotFoundError(f"Kein Rezept mit Slug '{slug}'.")
    if expected_revision is not None:
        current_revision = edit_revision(target, _edit_content(target))
        if expected_revision != current_revision:
            raise errors.RevisionConflictError(current_revision)
    if title is not None:
        target.title = _recipe_title(title)
    if tags is not None:
        target.tags = _recipe_tags(tags)
    if clear_duration:
        target.duration_min = None
    elif duration_min is not None:
        target.duration_min = duration_min
    if clear_servings:
        target.servings = None
    elif servings is not None:
        target.servings = servings
    markdown_path = storage.recipe_file(slug)
    old_content: str | None = None
    next_content: str | None = None
    if content is not None or title is not None:
        if content is None:
            if not markdown_path.is_file():
                raise ValueError(
                    f"Markdown-Datei für Rezept '{slug}' fehlt."
                )
            old_content = markdown_path.read_text(encoding="utf-8")
            next_content = _content_with_title(old_content, target.title)
        else:
            old_content = (
                markdown_path.read_text(encoding="utf-8")
                if markdown_path.is_file() else None
            )
            next_content = _content_with_title(content, target.title)
    checkpoint = storage.mutation_checkpoint()
    index_checkpoint = None
    try:
        if next_content is not None:
            storage.write_text(markdown_path, next_content)
        index_checkpoint = storage.mutation_checkpoint()
        _save_recipes_unlocked(recipes)
    except Exception:
        if index_checkpoint is None or storage.mutation_checkpoint() == index_checkpoint:
            if next_content is not None:
                if old_content is None:
                    markdown_path.unlink(missing_ok=True)
                else:
                    storage.write_text(markdown_path, old_content)
            storage.rollback_completed(checkpoint)
        raise
    return target
