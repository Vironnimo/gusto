"""Core logic of the recipe system.

ALL logic lives here. The CLI (gusto/cli.py) and the web UI (gusto/web.py)
are only thin shells around this module. No feature exists in only one surface.

Data model:
  recipes/<slug>.md   pure Markdown content of a recipe (NO frontmatter)
  data/recipes.json   metadata of ALL recipes (source for list/search/filter)
  images/<slug>/      image files owned and stored by Gusto
  data/favorites.json shared shopping needs and ranked preferred products
  images/_favorites/  preferred-product images owned and stored by Gusto
  data/log.json       cooking log: what was cooked when

The slug links both:  data/recipes.json[*].slug  <->  recipes/<slug>.md

User-facing strings (error messages, the German section headers in recipe
content) stay German on purpose; identifiers and comments are English.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sys
import uuid
from dataclasses import dataclass, field, asdict, fields
from datetime import date, datetime, timedelta, timezone
from pathlib import Path


# --- Paths (platform default, overridable via GUSTO_HOME) --------------------

def default_data_root(platform_name: str | None = None,
                      environ: dict[str, str] | None = None,
                      home: str | Path | None = None) -> Path:
    """Return the normal per-user data directory for the operating system."""
    platform_name = platform_name or sys.platform
    environ = os.environ if environ is None else environ
    user_home = Path.home() if home is None else Path(home)

    if platform_name.startswith("win"):
        base = Path(environ.get("LOCALAPPDATA") or user_home / "AppData" / "Local")
        return (base / "Gusto").expanduser().resolve()
    if platform_name == "darwin":
        return (user_home / "Library" / "Application Support" / "Gusto").resolve()
    base = Path(environ.get("XDG_DATA_HOME") or user_home / ".local" / "share")
    return (base / "gusto").expanduser().resolve()


def _legacy_data_root() -> Path:
    """Location used before Gusto adopted per-user platform data directories."""
    return Path(__file__).resolve().parent.parent


def contains_gusto_data(root: Path) -> bool:
    """Return whether a directory contains a recognizable Gusto store."""
    data = root / "data"
    if any((data / name).is_file() for name in (
            "recipes.json", "categories.json", "log.json",
            "shopping_list.json", "favorites.json")):
        return True
    recipes = root / "recipes"
    return recipes.is_dir() and next(recipes.glob("*.md"), None) is not None


def _resolve_data_root(environ: dict[str, str] | None = None,
                       default: Path | None = None,
                       legacy: Path | None = None) -> tuple[Path, str, Path]:
    environ = os.environ if environ is None else environ
    configured = environ.get("GUSTO_HOME")
    default = default_data_root(environ=environ) if default is None else default
    if configured:
        return Path(configured).expanduser().resolve(), "environment", default

    legacy = _legacy_data_root() if legacy is None else legacy
    if (legacy != default and contains_gusto_data(legacy)
            and not contains_gusto_data(default)):
        return legacy, "legacy", default
    return default, "platform_default", default


def project_root() -> Path:
    return _resolve_data_root()[0]


def storage_info() -> dict[str, str]:
    """Explain the active data root for CLI users and installation tooling."""
    path, source, default = _resolve_data_root()
    return {
        "path": os.fspath(path),
        "source": source,
        "platform_default": os.fspath(default),
    }


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


def shopping_path() -> Path:
    return data_dir() / "shopping_list.json"


def favorites_path() -> Path:
    return data_dir() / "favorites.json"


def images_dir() -> Path:
    return project_root() / "images"


def recipe_images_dir(slug: str) -> Path:
    return images_dir() / slug


def favorite_images_dir() -> Path:
    return images_dir() / "_favorites"


# --- Data model -------------------------------------------------------------

@dataclass
class RecipeImage:
    id: str
    filename: str
    role: str = "gallery"
    caption: str = ""
    created_at: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "RecipeImage":
        allowed = {f.name for f in fields(cls)}
        return cls(**{key: value for key, value in data.items() if key in allowed})


@dataclass
class Recipe:
    slug: str
    title: str
    tags: list[str] = field(default_factory=list)
    duration_min: int | None = None
    servings: int | None = None
    last_cooked: str | None = None  # ISO "YYYY-MM-DD" or None
    images: list[RecipeImage] = field(default_factory=list)
    cover_image_id: str | None = None

    @property
    def path(self) -> Path:
        return recipe_file(self.slug)

    def content(self) -> str:
        """Pure Markdown content from the .md file."""
        p = self.path
        return p.read_text(encoding="utf-8") if p.exists() else ""

    @property
    def cover_image(self) -> RecipeImage | None:
        if self.cover_image_id:
            match = next((image for image in self.images
                          if image.id == self.cover_image_id), None)
            if match is not None:
                return match
        return self.images[0] if self.images else None

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Recipe":
        allowed = {f.name for f in fields(cls)}
        values = {k: v for k, v in d.items() if k in allowed}
        values["images"] = [RecipeImage.from_dict(image)
                            for image in values.get("images", [])]
        return cls(**values)


@dataclass
class FavoriteProduct:
    """One concrete product in a household preference ranking."""
    id: str
    name: str
    brand: str = ""
    store: str = ""
    note: str = ""
    image_filename: str = ""
    created_at: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "FavoriteProduct":
        allowed = {f.name for f in fields(cls)}
        return cls(**{key: value for key, value in data.items() if key in allowed})


@dataclass
class ShoppingNeed:
    """A reusable shopping need with exact aliases and ranked products."""
    id: str
    name: str
    aliases: list[str] = field(default_factory=list)
    products: list[FavoriteProduct] = field(default_factory=list)
    created_at: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "ShoppingNeed":
        allowed = {f.name for f in fields(cls)}
        values = {key: value for key, value in data.items() if key in allowed}
        values["aliases"] = list(values.get("aliases", []))
        values["products"] = [FavoriteProduct.from_dict(product)
                              for product in values.get("products", [])]
        return cls(**values)


# --- Load / save ------------------------------------------------------------

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
    """Write atomically -- on abort no half-written file is left behind."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


# --- Create -----------------------------------------------------------------

def slugify(title: str) -> str:
    s = title.strip().lower()
    s = s.replace("ä", "ae").replace("ö", "oe").replace("ü", "ue").replace("ß", "ss")
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s or "recipe"


def add_recipe(title: str, tags=None, duration_min=None, servings=None,
               content: str | None = None, slug: str | None = None) -> Recipe:
    title = _recipe_title(title)
    _positive_recipe_number(duration_min, "Die Dauer")
    _positive_recipe_number(servings, "Die Portionszahl")
    recipes = load_recipes()
    slug = slug or slugify(title)
    if any(r.slug == slug for r in recipes):
        raise ValueError(f"Es gibt bereits ein Rezept mit dem Slug '{slug}'.")
    recipes_dir().mkdir(parents=True, exist_ok=True)
    recipe_file(slug).write_text(
        content if content is not None else _template(title), encoding="utf-8"
    )
    r = Recipe(slug=slug, title=title, tags=tags or [],
               duration_min=duration_min, servings=servings)
    recipes.append(r)
    save_recipes(recipes)
    return r


def _template(title: str) -> str:
    return f"# {title}\n\n## Zutaten\n\n- \n\n## Zubereitung\n\n1. \n"


def _recipe_title(value: str) -> str:
    """Return a normalized non-empty recipe title."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Das Rezept braucht einen Titel.")
    return value.strip()


def _positive_recipe_number(value: int | None, label: str) -> None:
    """Validate optional recipe counts shared by CLI and web."""
    if value is not None and (
            isinstance(value, bool) or not isinstance(value, int) or value < 1):
        raise ValueError(f"{label} muss eine positive ganze Zahl sein.")


# --- Categories (facets) ----------------------------------------------------
# Per recipe, tags are deliberately a flat list in data/recipes.json. Which tag
# belongs to which category lives centrally in data/categories.json:
#   { "<key>": {"label": str, "tags": [str, ...]}, ... }
# The order in the JSON is the display order. A tag without a category counts
# as "uncategorized" and ends up in the shared "Sonstige" group.

def load_categories() -> dict:
    """Category definition from data/categories.json (order preserved).
    If the file is missing the result is {} -- then every tag is uncategorized."""
    p = categories_path()
    if not p.exists():
        return {}
    return json.loads(p.read_text(encoding="utf-8"))


def _tag_to_category(categories: dict | None = None) -> dict[str, str]:
    """Inverse mapping tag(lowercase) -> category key."""
    categories = load_categories() if categories is None else categories
    return {t.lower(): key
            for key, cat in categories.items()
            for t in cat.get("tags", [])}


def tag_category(tag: str, categories: dict | None = None) -> str | None:
    """Category key of a tag, or None if uncategorized."""
    return _tag_to_category(categories).get(tag.lower())


def tag_groups(only_used: bool = True) -> list[dict]:
    """Categories with their tags in display order -- for the tag bar and
    `gusto tags`. Returns [{"key", "label", "tags": [...]}, ...].

    only_used=True: only tags that occur in recipes; empty categories are
    dropped; actually-used tags without a category come last as a "Sonstige"
    group. only_used=False: all defined categories/tags."""
    categories = load_categories()
    used_names = sorted({t for r in load_recipes() for t in r.tags},
                        key=str.lower) if only_used else None
    used_lower = {t.lower() for t in used_names} if used_names is not None else None

    groups: list[dict] = []
    covered: set[str] = set()
    for key, cat in categories.items():
        covered |= {t.lower() for t in cat.get("tags", [])}
        tags = [t for t in cat.get("tags", [])
                if used_lower is None or t.lower() in used_lower]
        if tags or used_lower is None:
            groups.append({"key": key, "label": cat.get("label", key), "tags": tags})

    if used_names is not None:
        leftover = [t for t in used_names if t.lower() not in covered]
        if leftover:
            groups.append({"key": "other", "label": "Sonstige", "tags": leftover})
    return groups


# --- Search -----------------------------------------------------------------

def search(query: str = "", match: str = "any", tags: list[str] | None = None,
           max_time: int | None = None) -> list[Recipe]:
    """Filter over metadata (tags, max_time) and, for `query`, additionally
    search the title, tags AND the Markdown content (so the ingredients too).

    Tag filter (facets): multiple `tags` are grouped by their category. A recipe
    matches if it has AT LEAST ONE of the selected tags in EVERY selected
    category -- i.e. OR within a category and AND across categories. Tags without
    a category form one shared group (OR among themselves)."""
    terms = [t.lower() for t in query.split()]
    groups = _group_tags(tags or [])
    hits = []
    for r in load_recipes():
        if groups and not _matches_tags(r, groups):
            continue
        if max_time is not None and (r.duration_min is None or r.duration_min > max_time):
            continue
        if terms:
            haystack = f"{r.title} {' '.join(r.tags)} {r.content()}".lower()
            ok = (all(t in haystack for t in terms) if match == "all"
                  else any(t in haystack for t in terms))
            if not ok:
                continue
        hits.append(r)
    return hits


def _group_tags(tags: list[str]) -> dict[str, set[str]]:
    """Group selected tags by category: key -> {tag-lowercase, ...}.
    Tags without a category go together under "__other__"."""
    mapping = _tag_to_category()
    groups: dict[str, set[str]] = {}
    for t in tags:
        tl = t.lower()
        groups.setdefault(mapping.get(tl, "__other__"), set()).add(tl)
    return groups


def _matches_tags(r: Recipe, groups: dict[str, set[str]]) -> bool:
    """OR within a group, AND across groups."""
    recipe_tags = {t.lower() for t in r.tags}
    return all(recipe_tags & selected for selected in groups.values())


# --- Cooking log ------------------------------------------------------------

def load_log(days: int | None = None) -> list[dict]:
    p = log_path()
    entries = json.loads(p.read_text(encoding="utf-8")) if p.exists() else []
    if days is not None:
        cutoff = (date.today() - timedelta(days=days)).isoformat()
        entries = [e for e in entries if e["date"] >= cutoff]
    return sorted(entries, key=lambda e: e["date"])


def log_cooked(slug: str, when: str | None = None) -> None:
    recipes = load_recipes()
    if not any(r.slug == slug for r in recipes):
        raise ValueError(f"Kein Rezept mit Slug '{slug}'.")
    when = when or date.today().isoformat()
    entries = load_log()
    entries.append({"date": when, "slug": slug})
    _write_json(log_path(), sorted(entries, key=lambda e: e["date"]))
    for r in recipes:
        if r.slug == slug and (r.last_cooked is None or when > r.last_cooked):
            r.last_cooked = when
    save_recipes(recipes)


# --- Suggestions ------------------------------------------------------------

def suggest(days: int = 7, limit: int | None = None) -> list[Recipe]:
    """Candidates for the next meal: everything NOT cooked in the last `days`
    days -- longest-not-cooked first.

    Deliberately simple and rule-based: the actual decision is made by a human
    or an agent that additionally uses `log`, `search` & `list`.
    """
    recent = {e["slug"] for e in load_log(days=days)}
    remaining = [r for r in load_recipes() if r.slug not in recent]
    remaining.sort(key=lambda r: r.last_cooked or "")  # None/"" = longest ago
    return remaining[:limit] if limit else remaining


# --- Update / delete --------------------------------------------------------

def update_recipe(slug: str, title: str | None = None, tags=None,
                  duration_min=None, servings=None,
                  content: str | None = None, *,
                  clear_duration: bool = False,
                  clear_servings: bool = False) -> Recipe:
    """Update a recipe. None means 'leave unchanged' (exception: tags=[] clears
    the tags). Explicit clear flags remove optional numeric metadata; `content`
    overwrites the .md file."""
    if clear_duration and duration_min is not None:
        raise ValueError("Die Dauer kann nicht gleichzeitig gesetzt und entfernt werden.")
    if clear_servings and servings is not None:
        raise ValueError("Die Portionszahl kann nicht gleichzeitig gesetzt und entfernt werden.")
    _positive_recipe_number(duration_min, "Die Dauer")
    _positive_recipe_number(servings, "Die Portionszahl")
    recipes = load_recipes()
    target = next((r for r in recipes if r.slug == slug), None)
    if target is None:
        raise ValueError(f"Kein Rezept mit Slug '{slug}'.")
    if title is not None:
        target.title = _recipe_title(title)
    if tags is not None:
        target.tags = tags
    if clear_duration:
        target.duration_min = None
    elif duration_min is not None:
        target.duration_min = duration_min
    if clear_servings:
        target.servings = None
    elif servings is not None:
        target.servings = servings
    if content is not None:
        recipe_file(slug).write_text(content, encoding="utf-8")
    save_recipes(recipes)
    return target


def delete_recipe(slug: str) -> None:
    """Remove the index entry and the .md file. Log entries are kept as
    history."""
    recipes = load_recipes()
    remaining = [r for r in recipes if r.slug != slug]
    if len(remaining) == len(recipes):
        raise ValueError(f"Kein Rezept mit Slug '{slug}'.")
    save_recipes(remaining)
    p = recipe_file(slug)
    if p.exists():
        p.unlink()
    image_folder = recipe_images_dir(slug)
    if image_folder.exists():
        shutil.rmtree(image_folder)


# --- Recipe images ----------------------------------------------------------

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}


def _valid_image_header(path: Path, suffix: str) -> bool:
    """Validate the format and positive dimensions without optional libraries."""
    with path.open("rb") as handle:
        header = handle.read(32)

        if suffix == ".png":
            return (header.startswith(b"\x89PNG\r\n\x1a\n")
                    and header[12:16] == b"IHDR"
                    and int.from_bytes(header[16:20], "big") > 0
                    and int.from_bytes(header[20:24], "big") > 0)

        if suffix == ".gif":
            return (header[:6] in {b"GIF87a", b"GIF89a"}
                    and int.from_bytes(header[6:8], "little") > 0
                    and int.from_bytes(header[8:10], "little") > 0)

        if suffix == ".webp":
            if not (header.startswith(b"RIFF") and header[8:12] == b"WEBP"):
                return False
            chunk = header[12:16]
            if chunk == b"VP8X" and len(header) >= 30:
                width = 1 + int.from_bytes(header[24:27], "little")
                height = 1 + int.from_bytes(header[27:30], "little")
                return width > 0 and height > 0
            if chunk == b"VP8L" and len(header) >= 25 and header[20] == 0x2F:
                bits = int.from_bytes(header[21:25], "little")
                return (bits & 0x3FFF) + 1 > 0 and ((bits >> 14) & 0x3FFF) + 1 > 0
            if chunk == b"VP8 " and len(header) >= 30 and header[23:26] == b"\x9d\x01\x2a":
                width = int.from_bytes(header[26:28], "little") & 0x3FFF
                height = int.from_bytes(header[28:30], "little") & 0x3FFF
                return width > 0 and height > 0
            return False

        if suffix in {".jpg", ".jpeg"}:
            if header[:2] != b"\xff\xd8":
                return False
            handle.seek(2)
            sof_markers = {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7,
                           0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}
            while True:
                byte = handle.read(1)
                if not byte:
                    return False
                if byte != b"\xff":
                    continue
                marker_byte = handle.read(1)
                while marker_byte == b"\xff":
                    marker_byte = handle.read(1)
                if not marker_byte:
                    return False
                marker = marker_byte[0]
                if marker in {0x01, 0xD8} or 0xD0 <= marker <= 0xD7:
                    continue
                if marker in {0xD9, 0xDA}:
                    return False
                length_bytes = handle.read(2)
                if len(length_bytes) != 2:
                    return False
                length = int.from_bytes(length_bytes, "big")
                if length < 2:
                    return False
                if marker in sof_markers:
                    dimensions = handle.read(5)
                    return (len(dimensions) == 5
                            and int.from_bytes(dimensions[1:3], "big") > 0
                            and int.from_bytes(dimensions[3:5], "big") > 0)
                handle.seek(length - 2, 1)

    return False


def recipe_image_path(slug: str, image: RecipeImage) -> Path:
    return recipe_images_dir(slug) / image.filename


def list_recipe_images(slug: str) -> tuple[list[RecipeImage], str | None]:
    recipe = get(slug)
    if recipe is None:
        raise ValueError(f"Kein Rezept mit Slug '{slug}'.")
    cover = recipe.cover_image
    return recipe.images, cover.id if cover is not None else None


def get_recipe_image(slug: str, image_id: str) -> RecipeImage | None:
    recipe = get(slug)
    if recipe is None:
        return None
    return next((image for image in recipe.images if image.id == image_id), None)


def add_recipe_image(slug: str, source: str | Path, role: str = "gallery",
                     caption: str = "", cover: bool = False) -> RecipeImage:
    recipes = load_recipes()
    recipe = next((item for item in recipes if item.slug == slug), None)
    if recipe is None:
        raise ValueError(f"Kein Rezept mit Slug '{slug}'.")

    source_path = Path(source).expanduser()
    if not source_path.is_file():
        raise ValueError(f"Bilddatei nicht gefunden: '{source_path}'.")
    suffix = source_path.suffix.lower()
    if suffix not in IMAGE_EXTENSIONS:
        allowed = ", ".join(sorted(IMAGE_EXTENSIONS))
        raise ValueError(f"Nicht unterstütztes Bildformat '{suffix}' ({allowed}).")
    if not _valid_image_header(source_path, suffix):
        raise ValueError(
            f"Datei ist kein gültiges {suffix.lstrip('.').upper()}-Bild: '{source_path}'."
        )

    image = RecipeImage(
        id=uuid.uuid4().hex,
        filename="", role=role.strip() or "gallery",
        caption=caption.strip(), created_at=_now_iso(),
    )
    image.filename = f"{image.id}{suffix}"
    destination = recipe_image_path(slug, image)
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source_path, destination)

    recipe.images.append(image)
    if cover or recipe.cover_image is None:
        recipe.cover_image_id = image.id
    try:
        save_recipes(recipes)
    except Exception:
        destination.unlink(missing_ok=True)
        raise
    return image


def update_recipe_image(slug: str, image_id: str, role: str | None = None,
                        caption: str | None = None) -> RecipeImage:
    recipes = load_recipes()
    recipe = next((item for item in recipes if item.slug == slug), None)
    if recipe is None:
        raise ValueError(f"Kein Rezept mit Slug '{slug}'.")
    image = next((item for item in recipe.images if item.id == image_id), None)
    if image is None:
        raise ValueError(f"Kein Bild mit id '{image_id}' bei Rezept '{slug}'.")
    if role is not None:
        image.role = role.strip() or "gallery"
    if caption is not None:
        image.caption = caption.strip()
    save_recipes(recipes)
    return image


def set_recipe_cover(slug: str, image_id: str) -> RecipeImage:
    recipes = load_recipes()
    recipe = next((item for item in recipes if item.slug == slug), None)
    if recipe is None:
        raise ValueError(f"Kein Rezept mit Slug '{slug}'.")
    image = next((item for item in recipe.images if item.id == image_id), None)
    if image is None:
        raise ValueError(f"Kein Bild mit id '{image_id}' bei Rezept '{slug}'.")
    recipe.cover_image_id = image.id
    save_recipes(recipes)
    return image


def remove_recipe_image(slug: str, image_id: str) -> str | None:
    recipes = load_recipes()
    recipe = next((item for item in recipes if item.slug == slug), None)
    if recipe is None:
        raise ValueError(f"Kein Rezept mit Slug '{slug}'.")
    image = next((item for item in recipe.images if item.id == image_id), None)
    if image is None:
        raise ValueError(f"Kein Bild mit id '{image_id}' bei Rezept '{slug}'.")

    recipe.images = [item for item in recipe.images if item.id != image_id]
    remaining_ids = {item.id for item in recipe.images}
    if recipe.cover_image_id == image_id or recipe.cover_image_id not in remaining_ids:
        recipe.cover_image_id = recipe.images[0].id if recipe.images else None
    save_recipes(recipes)
    recipe_image_path(slug, image).unlink(missing_ok=True)
    folder = recipe_images_dir(slug)
    if folder.exists() and not any(folder.iterdir()):
        folder.rmdir()
    return recipe.cover_image_id


# --- Consistency ------------------------------------------------------------

def check() -> dict:
    """Check whether the index (recipes.json) and the .md files match, and
    whether every used tag is assigned to a category (categories.json)."""
    recipes = load_recipes()
    indexed = {r.slug for r in recipes}
    present = {p.stem for p in recipes_dir().glob("*.md")} if recipes_dir().exists() else set()
    mapping = _tag_to_category()
    uncategorized = sorted({t for r in recipes for t in r.tags if t.lower() not in mapping})
    image_root = images_dir()
    image_folders = ({path.name for path in image_root.iterdir()
                      if path.is_dir() and path.name != "_favorites"}
                     if image_root.exists() else set())
    orphaned_image_folders = sorted(image_folders - indexed)
    missing_image_files: list[str] = []
    orphaned_image_files: list[str] = []
    invalid_cover_images: list[str] = []
    for recipe in recipes:
        referenced = {image.filename for image in recipe.images}
        folder = recipe_images_dir(recipe.slug)
        present_images = ({path.name for path in folder.iterdir() if path.is_file()}
                          if folder.exists() else set())
        missing_image_files.extend(
            f"{recipe.slug}/{filename}" for filename in sorted(referenced - present_images)
        )
        orphaned_image_files.extend(
            f"{recipe.slug}/{filename}" for filename in sorted(present_images - referenced)
        )
        if ((recipe.images and recipe.cover_image_id is None)
                or (recipe.cover_image_id is not None
                    and recipe.cover_image_id not in {image.id for image in recipe.images})):
            invalid_cover_images.append(recipe.slug)
    favorite_needs = favorites_load()
    referenced_favorite_images = {
        product.image_filename
        for need in favorite_needs
        for product in need.products
        if product.image_filename
    }
    favorite_root = favorite_images_dir()
    present_favorite_images = ({path.name for path in favorite_root.iterdir()
                                if path.is_file()}
                               if favorite_root.exists() else set())
    alias_owners: dict[str, list[str]] = {}
    for need in favorite_needs:
        for label in [need.name, *need.aliases]:
            alias_owners.setdefault(normalize_shopping_text(label), []).append(need.name)
    duplicate_favorite_aliases = sorted(
        label for label, owners in alias_owners.items()
        if label and len(set(owners)) > 1
    )

    return {
        "recipe_count": len(indexed),
        "orphaned_files": sorted(present - indexed),   # .md without index entry
        "missing_files": sorted(indexed - present),    # index entry without .md
        "uncategorized_tags": uncategorized,           # tags in no category
        "orphaned_image_folders": orphaned_image_folders,
        "orphaned_image_files": orphaned_image_files,
        "missing_image_files": missing_image_files,
        "invalid_cover_images": sorted(invalid_cover_images),
        "favorite_need_count": len(favorite_needs),
        "duplicate_favorite_aliases": duplicate_favorite_aliases,
        "orphaned_favorite_image_files": sorted(
            present_favorite_images - referenced_favorite_images
        ),
        "missing_favorite_image_files": sorted(
            referenced_favorite_images - present_favorite_images
        ),
    }


# ===========================================================================
# Shopping list  (feature: shopping list + PWA, see docs/plan-einkaufsliste-pwa.md)
# ===========================================================================
#
# Storage:  data/shopping_list.json   (shape: {"items": [ <item-dict>, ... ]})
# An item is sync-capable: `updated_at` (UTC ISO) drives "last writer wins" on
# sync, `deleted` is a tombstone so deletions propagate between devices.
# NOTHING is ever hard-deleted.


@dataclass
class ShoppingItem:
    """One entry on the shopping list (sync-capable)."""
    id: str
    text: str                       # e.g. "200 g Spaghetti" (v1: whole ingredient line)
    quantity: str = ""              # optional, v1 usually empty (text holds the amount)
    checked: bool = False           # ticked off?
    source: str | None = None       # slug of the recipe the ingredient came from
    created_at: str = ""            # UTC ISO, e.g. "2026-06-23T18:00:00Z"
    updated_at: str = ""            # UTC ISO -- drives "last writer wins" on sync
    deleted: bool = False           # tombstone for sync (never hard-delete)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "ShoppingItem":
        allowed = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in allowed})


def _parse_iso(value: str) -> datetime | None:
    """Parse an ISO timestamp used by the shopping sync.

    Both the former second-precision format and the current millisecond format
    are accepted so existing data remains compatible.
    """
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _version_key(value: str) -> tuple:
    """Comparable sync version; valid ISO values beat malformed legacy data."""
    parsed = _parse_iso(value)
    return (1, parsed) if parsed is not None else (0, value)


def _now_iso(after: str | None = None) -> str:
    """Current UTC timestamp with milliseconds and a literal ``Z``.

    When ``after`` belongs to the same item, the result is guaranteed to be
    newer. This prevents a rapid add-then-toggle sequence from producing two
    indistinguishable versions while remaining compatible with old timestamps.
    """
    now = datetime.now(timezone.utc)
    previous = _parse_iso(after or "")
    if previous is not None and now <= previous:
        now = previous + timedelta(milliseconds=1)
    return now.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def new_id() -> str:
    """New unique item id (uuid4 hex)."""
    return uuid.uuid4().hex


# --- Preferred products -----------------------------------------------------

def normalize_shopping_text(value: str) -> str:
    """Deterministic favorite matching: case and whitespace only.

    Quantities, punctuation and words deliberately stay untouched. A label is
    matched only after it has explicitly become a need name or alias.
    """
    lowered = re.sub(r"\s+", " ", (value or "").strip()).lower()
    return lowered.replace("ß", "ss")


def favorites_load() -> list[ShoppingNeed]:
    """Load the shared household preference catalog."""
    path = favorites_path()
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    return [ShoppingNeed.from_dict(raw) for raw in data.get("needs", [])]


def favorites_save(needs: list[ShoppingNeed]) -> None:
    _write_json(favorites_path(), {"needs": [need.to_dict() for need in needs]})


def favorite_get_need(identifier: str) -> ShoppingNeed | None:
    """Find a need by opaque id or exact normalized canonical name."""
    normalized = normalize_shopping_text(identifier)
    return next((need for need in favorites_load()
                 if need.id == identifier
                 or normalize_shopping_text(need.name) == normalized), None)


def favorite_match(text: str, needs: list[ShoppingNeed] | None = None) -> ShoppingNeed | None:
    """Return the need whose name or explicit alias matches ``text`` exactly."""
    normalized = normalize_shopping_text(text)
    if not normalized:
        return None
    for need in favorites_load() if needs is None else needs:
        labels = [need.name, *need.aliases]
        if normalized in {normalize_shopping_text(label) for label in labels}:
            return need
    return None


def _favorite_need(needs: list[ShoppingNeed], identifier: str) -> ShoppingNeed:
    normalized = normalize_shopping_text(identifier)
    need = next((item for item in needs
                 if item.id == identifier
                 or normalize_shopping_text(item.name) == normalized), None)
    if need is None:
        raise ValueError(f"Kein Einkaufsbedarf mit id oder Name '{identifier}'.")
    return need


def _favorite_product(need: ShoppingNeed, product_id: str) -> FavoriteProduct:
    product = next((item for item in need.products if item.id == product_id), None)
    if product is None:
        raise ValueError(
            f"Kein Lieblingsprodukt mit id '{product_id}' bei '{need.name}'."
        )
    return product


def _favorite_label_owner(label: str, needs: list[ShoppingNeed],
                          except_need_id: str | None = None) -> ShoppingNeed | None:
    normalized = normalize_shopping_text(label)
    if not normalized:
        return None
    for need in needs:
        if need.id == except_need_id:
            continue
        if normalized in {normalize_shopping_text(value)
                          for value in [need.name, *need.aliases]}:
            return need
    return None


def favorite_add_need(name: str, aliases: list[str] | None = None) -> ShoppingNeed:
    name = (name or "").strip()
    if not name:
        raise ValueError("Der Einkaufsbedarf braucht einen Namen.")
    needs = favorites_load()
    owner = _favorite_label_owner(name, needs)
    if owner is not None:
        raise ValueError(f"'{name}' gehört bereits zu '{owner.name}'.")

    clean_aliases: list[str] = []
    seen = {normalize_shopping_text(name)}
    for alias in aliases or []:
        cleaned = alias.strip()
        normalized = normalize_shopping_text(cleaned)
        if not normalized or normalized in seen:
            continue
        owner = _favorite_label_owner(cleaned, needs)
        if owner is not None:
            raise ValueError(f"'{cleaned}' gehört bereits zu '{owner.name}'.")
        seen.add(normalized)
        clean_aliases.append(cleaned)

    need = ShoppingNeed(
        id=new_id(), name=name, aliases=clean_aliases, products=[],
        created_at=_now_iso(),
    )
    needs.append(need)
    favorites_save(needs)
    return need


def favorite_update_need(identifier: str, name: str) -> ShoppingNeed:
    needs = favorites_load()
    need = _favorite_need(needs, identifier)
    name = (name or "").strip()
    if not name:
        raise ValueError("Der Einkaufsbedarf braucht einen Namen.")
    owner = _favorite_label_owner(name, needs, except_need_id=need.id)
    if owner is not None:
        raise ValueError(f"'{name}' gehört bereits zu '{owner.name}'.")

    old_name = need.name
    if normalize_shopping_text(old_name) != normalize_shopping_text(name):
        if normalize_shopping_text(old_name) not in {
                normalize_shopping_text(alias) for alias in need.aliases}:
            need.aliases.append(old_name)
    need.name = name
    need.aliases = [alias for alias in need.aliases
                    if normalize_shopping_text(alias) != normalize_shopping_text(name)]
    favorites_save(needs)
    return need


def favorite_add_alias(identifier: str, alias: str) -> ShoppingNeed:
    needs = favorites_load()
    need = _favorite_need(needs, identifier)
    alias = (alias or "").strip()
    if not alias:
        raise ValueError("Der Alias darf nicht leer sein.")
    normalized = normalize_shopping_text(alias)
    own_labels = {normalize_shopping_text(value)
                  for value in [need.name, *need.aliases]}
    if normalized in own_labels:
        return need
    owner = _favorite_label_owner(alias, needs, except_need_id=need.id)
    if owner is not None:
        raise ValueError(f"'{alias}' gehört bereits zu '{owner.name}'.")
    need.aliases.append(alias)
    favorites_save(needs)
    return need


def favorite_remove_alias(identifier: str, alias: str) -> ShoppingNeed:
    needs = favorites_load()
    need = _favorite_need(needs, identifier)
    normalized = normalize_shopping_text(alias)
    original_count = len(need.aliases)
    need.aliases = [value for value in need.aliases
                    if normalize_shopping_text(value) != normalized]
    if len(need.aliases) == original_count:
        raise ValueError(f"Kein Alias '{alias}' bei '{need.name}'.")
    favorites_save(needs)
    return need


def _copy_favorite_image(source: str | Path) -> tuple[str, Path]:
    source_path = Path(source).expanduser()
    if not source_path.is_file():
        raise ValueError(f"Bilddatei nicht gefunden: '{source_path}'.")
    suffix = source_path.suffix.lower()
    if suffix not in IMAGE_EXTENSIONS:
        allowed = ", ".join(sorted(IMAGE_EXTENSIONS))
        raise ValueError(f"Nicht unterstütztes Bildformat '{suffix}' ({allowed}).")
    if not _valid_image_header(source_path, suffix):
        raise ValueError(
            f"Datei ist kein gültiges {suffix.lstrip('.').upper()}-Bild: '{source_path}'."
        )
    filename = f"{new_id()}{suffix}"
    destination = favorite_images_dir() / filename
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source_path, destination)
    return filename, destination


def favorite_image_path(filename: str) -> Path:
    return favorite_images_dir() / Path(filename).name


def favorite_image_is_referenced(filename: str) -> bool:
    return any(product.image_filename == filename
               for need in favorites_load() for product in need.products)


def favorite_add_product(identifier: str, name: str, *, brand: str = "",
                         store: str = "", note: str = "",
                         image: str | Path | None = None) -> FavoriteProduct:
    needs = favorites_load()
    need = _favorite_need(needs, identifier)
    name = (name or "").strip()
    if not name:
        raise ValueError("Das Lieblingsprodukt braucht einen Namen.")
    brand = (brand or "").strip()
    if not brand:
        raise ValueError("Das Lieblingsprodukt braucht eine Marke.")
    product = FavoriteProduct(
        id=new_id(), name=name, brand=brand, store=store.strip(),
        note=note.strip(), created_at=_now_iso(),
    )
    copied: Path | None = None
    if image:
        product.image_filename, copied = _copy_favorite_image(image)
    need.products.append(product)
    try:
        favorites_save(needs)
    except Exception:
        if copied is not None:
            copied.unlink(missing_ok=True)
        raise
    return product


def favorite_update_product(identifier: str, product_id: str, *,
                            name: str | None = None, brand: str | None = None,
                            store: str | None = None, note: str | None = None,
                            image: str | Path | None = None,
                            remove_image: bool = False) -> FavoriteProduct:
    if image and remove_image:
        raise ValueError("Bild kann nicht gleichzeitig ersetzt und entfernt werden.")
    needs = favorites_load()
    need = _favorite_need(needs, identifier)
    product = _favorite_product(need, product_id)
    if name is not None:
        cleaned_name = name.strip()
        if not cleaned_name:
            raise ValueError("Das Lieblingsprodukt braucht einen Namen.")
        product.name = cleaned_name
    if brand is not None:
        cleaned_brand = brand.strip()
        if not cleaned_brand:
            raise ValueError("Das Lieblingsprodukt braucht eine Marke.")
        product.brand = cleaned_brand
    if store is not None:
        product.store = store.strip()
    if note is not None:
        product.note = note.strip()

    old_filename = product.image_filename
    copied: Path | None = None
    if image:
        product.image_filename, copied = _copy_favorite_image(image)
    elif remove_image:
        product.image_filename = ""
    try:
        favorites_save(needs)
    except Exception:
        if copied is not None:
            copied.unlink(missing_ok=True)
        raise
    if old_filename and old_filename != product.image_filename:
        favorite_image_path(old_filename).unlink(missing_ok=True)
    return product


def favorite_move_product(identifier: str, product_id: str,
                          position: int) -> ShoppingNeed:
    needs = favorites_load()
    need = _favorite_need(needs, identifier)
    product = _favorite_product(need, product_id)
    if position < 1 or position > len(need.products):
        raise ValueError(f"Position muss zwischen 1 und {len(need.products)} liegen.")
    need.products.remove(product)
    need.products.insert(position - 1, product)
    favorites_save(needs)
    return need


def favorite_remove_product(identifier: str, product_id: str) -> ShoppingNeed:
    needs = favorites_load()
    need = _favorite_need(needs, identifier)
    product = _favorite_product(need, product_id)
    need.products = [item for item in need.products if item.id != product.id]
    favorites_save(needs)
    if product.image_filename:
        favorite_image_path(product.image_filename).unlink(missing_ok=True)
    folder = favorite_images_dir()
    if folder.exists() and not any(folder.iterdir()):
        folder.rmdir()
    return need


def favorite_remove_need(identifier: str) -> ShoppingNeed:
    needs = favorites_load()
    need = _favorite_need(needs, identifier)
    favorites_save([item for item in needs if item.id != need.id])
    for product in need.products:
        if product.image_filename:
            favorite_image_path(product.image_filename).unlink(missing_ok=True)
    folder = favorite_images_dir()
    if folder.exists() and not any(folder.iterdir()):
        folder.rmdir()
    return need


# --- Ingredient parsing -----------------------------------------------------

def parse_ingredients(content: str) -> list[str]:
    """Extract ingredient lines from the recipe Markdown.

    Rule (v1): all bullet items ('-' or '*') in the section under the heading
    '## Zutaten', up to the next '##' heading. For each bullet the WHOLE line
    (without the bullet char and surrounding whitespace) as one entry. Empty
    bullets are skipped. If there is no ingredient section the result is [].
    """
    ingredients: list[str] = []
    in_section = False
    for line in content.splitlines():
        s = line.strip()
        if s == "## Zutaten":
            in_section = True
            continue
        if not in_section:
            continue
        if s.startswith("## "):       # next heading ends the section
            break
        if s.startswith("-") or s.startswith("*"):
            entry = s[1:].strip()
            if entry:                 # skip empty bullets
                ingredients.append(entry)
    return ingredients


# --- Load / save ------------------------------------------------------------

def shopping_load() -> list[ShoppingItem]:
    """Load all items from data/shopping_list.json -- INCLUDING tombstones
    (deleted=True). If the file does not exist the result is []."""
    p = shopping_path()
    if not p.exists():
        return []
    data = json.loads(p.read_text(encoding="utf-8"))
    return [ShoppingItem.from_dict(d) for d in data.get("items", [])]


def shopping_save(items: list[ShoppingItem]) -> None:
    """Write items atomically to data/shopping_list.json (uses _write_json).
    File shape: {"items": [ <item-dict>, ... ]}. Tombstones are kept."""
    _write_json(shopping_path(), {"items": [i.to_dict() for i in items]})


# --- Modify -----------------------------------------------------------------

def shopping_add(text: str, quantity: str = "", source: str | None = None) -> ShoppingItem:
    """Create, save and return a new item. Sets id (new_id()), created_at and
    updated_at (= _now_iso()), checked=False, deleted=False."""
    now = _now_iso()
    item = ShoppingItem(id=new_id(), text=text, quantity=quantity, checked=False,
                        source=source, created_at=now, updated_at=now,
                        deleted=False)
    items = shopping_load()
    items.append(item)
    shopping_save(items)
    return item


def shopping_add_recipe(slug: str) -> list[ShoppingItem]:
    """Put all ingredients of a recipe (parse_ingredients on its .md) onto the
    list, source=slug. Returns the NEWLY added items. ValueError if there is no
    recipe with this slug."""
    r = get(slug)
    if r is None:
        raise ValueError(f"Kein Rezept mit Slug '{slug}'.")
    items = shopping_load()
    new_items: list[ShoppingItem] = []
    for ingredient in parse_ingredients(r.content()):
        now = _now_iso()
        new_items.append(ShoppingItem(id=new_id(), text=ingredient, quantity="",
                                      checked=False, source=slug, created_at=now,
                                      updated_at=now, deleted=False))
    items.extend(new_items)
    shopping_save(items)
    return new_items


def shopping_list(include_done: bool = True,
                  include_deleted: bool = False) -> list[ShoppingItem]:
    """Visible items. Tombstones (deleted) hidden by default; include_done=False
    additionally hides done (checked) ones. Order: by created_at ascending
    (insertion order)."""
    items = [i for i in shopping_load() if include_deleted or not i.deleted]
    if not include_done:
        items = [i for i in items if not i.checked]
    items.sort(key=lambda i: i.created_at)
    return items


def shopping_toggle(item_id: str, checked: bool | None = None) -> ShoppingItem:
    """Set the done checkmark. checked=None toggles; otherwise the value is set.
    Updates updated_at and returns the item. ValueError if the id is unknown or
    the item is a tombstone."""
    items = shopping_load()
    target = next((i for i in items if i.id == item_id and not i.deleted), None)
    if target is None:
        raise ValueError(f"Kein Einkauf-Item mit id '{item_id}'.")
    target.checked = (not target.checked) if checked is None else checked
    target.updated_at = _now_iso(target.updated_at)
    shopping_save(items)
    return target


def shopping_remove(item_id: str) -> None:
    """Mark item as a tombstone: deleted=True + update updated_at (do NOT hard-
    delete from the file, so the deletion syncs). ValueError if the id is
    unknown."""
    items = shopping_load()
    target = next((i for i in items if i.id == item_id), None)
    if target is None:
        raise ValueError(f"Kein Einkauf-Item mit id '{item_id}'.")
    target.deleted = True
    target.updated_at = _now_iso(target.updated_at)
    shopping_save(items)


def shopping_clear_done() -> int:
    """Mark all done (checked, not yet tombstone) items as a tombstone
    (deleted=True + updated_at). Returns the number of items removed this way."""
    items = shopping_load()
    count = 0
    for i in items:
        if i.checked and not i.deleted:
            i.deleted = True
            i.updated_at = _now_iso(i.updated_at)
            count += 1
    if count:
        shopping_save(items)
    return count


# --- Sync -------------------------------------------------------------------

def shopping_merge(remote_items: list[dict]) -> list[ShoppingItem]:
    """Full-state sync: merge remote_items (raw item dicts from the client) into
    the local list, save the result and return it (incl. tombstones).

    Rule "last writer wins": per id the version with the later ISO timestamp
    wins. Old second-precision and new millisecond timestamps are both accepted.
    On a tie the local version stays. Ids that exist only remotely are taken
    over; tombstones (deleted=True) propagate like any other change.
    """
    # Local state (incl. tombstones) as the source of truth, indexed by id.
    merged: dict[str, ShoppingItem] = {i.id: i for i in shopping_load()}

    if not isinstance(remote_items, list):
        raise ValueError("Der Einkaufslisten-Stand muss eine Liste sein.")

    for raw in remote_items:
        if not isinstance(raw, dict):
            raise ValueError("Jeder Einkaufslisten-Eintrag muss ein Objekt sein.")
        try:
            remote = ShoppingItem.from_dict(raw)
        except TypeError as error:
            raise ValueError("Ein Einkaufslisten-Eintrag ist unvollständig.") from error
        if not isinstance(remote.id, str) or not remote.id:
            raise ValueError("Jeder Einkaufslisten-Eintrag braucht eine id.")
        if not isinstance(remote.text, str):
            raise ValueError("Der Text eines Einkaufslisten-Eintrags ist ungültig.")
        if not isinstance(remote.quantity, str):
            raise ValueError("Die Menge eines Einkaufslisten-Eintrags ist ungültig.")
        if not isinstance(remote.checked, bool) or not isinstance(remote.deleted, bool):
            raise ValueError("Der Status eines Einkaufslisten-Eintrags ist ungültig.")
        if remote.source is not None and not isinstance(remote.source, str):
            raise ValueError("Die Quelle eines Einkaufslisten-Eintrags ist ungültig.")
        if not isinstance(remote.created_at, str) or not isinstance(remote.updated_at, str):
            raise ValueError("Der Zeitstempel eines Einkaufslisten-Eintrags ist ungültig.")
        local = merged.get(remote.id)
        # id only remote -> take it over.
        # id on both sides -> later updated_at wins;
        # on a tie the local version stays.
        if local is None or _version_key(remote.updated_at) > _version_key(local.updated_at):
            merged[remote.id] = remote
    # ids that exist only locally stay unchanged.

    result = list(merged.values())
    shopping_save(result)
    return result
