"""Core logic of the recipe system.

ALL logic lives here. The CLI (recipe/cli.py) and the web UI (recipe/web.py)
are only thin shells around this module. No feature exists in only one surface.

Data model:
  recipes/<slug>.md   pure Markdown content of a recipe (NO frontmatter)
  data/recipes.json   metadata of ALL recipes (source for list/search/filter)
  data/log.json       cooking log: what was cooked when

The slug links both:  data/recipes.json[*].slug  <->  recipes/<slug>.md

User-facing strings (error messages, the German section headers in recipe
content) stay German on purpose; identifiers and comments are English.
"""
from __future__ import annotations

import json
import os
import re
import uuid
from dataclasses import dataclass, field, asdict, fields
from datetime import date, datetime, timedelta, timezone
from pathlib import Path


# --- Paths (overridable via RECIPE_HOME, e.g. on the Pi) ---------------------

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


def shopping_path() -> Path:
    return data_dir() / "shopping_list.json"


# --- Data model -------------------------------------------------------------

@dataclass
class Recipe:
    slug: str
    title: str
    tags: list[str] = field(default_factory=list)
    duration_min: int | None = None
    servings: int | None = None
    last_cooked: str | None = None  # ISO "YYYY-MM-DD" or None

    @property
    def path(self) -> Path:
        return recipe_file(self.slug)

    def content(self) -> str:
        """Pure Markdown content from the .md file."""
        p = self.path
        return p.read_text(encoding="utf-8") if p.exists() else ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Recipe":
        allowed = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in allowed})


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
    `recipe tags`. Returns [{"key", "label", "tags": [...]}, ...].

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
                  content: str | None = None) -> Recipe:
    """Update a recipe. None means 'leave unchanged' (exception: tags=[] clears
    the tags). `content` overwrites the .md file."""
    recipes = load_recipes()
    target = next((r for r in recipes if r.slug == slug), None)
    if target is None:
        raise ValueError(f"Kein Rezept mit Slug '{slug}'.")
    if title is not None:
        target.title = title
    if tags is not None:
        target.tags = tags
    if duration_min is not None:
        target.duration_min = duration_min
    if servings is not None:
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


# --- Consistency ------------------------------------------------------------

def check() -> dict:
    """Check whether the index (recipes.json) and the .md files match, and
    whether every used tag is assigned to a category (categories.json)."""
    recipes = load_recipes()
    indexed = {r.slug for r in recipes}
    present = {p.stem for p in recipes_dir().glob("*.md")} if recipes_dir().exists() else set()
    mapping = _tag_to_category()
    uncategorized = sorted({t for r in recipes for t in r.tags if t.lower() not in mapping})
    return {
        "recipe_count": len(indexed),
        "orphaned_files": sorted(present - indexed),   # .md without index entry
        "missing_files": sorted(indexed - present),    # index entry without .md
        "uncategorized_tags": uncategorized,           # tags in no category
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

    for raw in remote_items:
        remote = ShoppingItem.from_dict(raw)
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
