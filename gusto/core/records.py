"""Validated records shared by the domain owners."""
from __future__ import annotations

from dataclasses import dataclass, field, asdict, fields
from pathlib import Path
import uuid
import gusto.core._validation as validation
import gusto.core.storage as storage


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
        if not isinstance(data, dict):
            raise ValueError(
                "Ungültiges Bild in einem Rezept-Eintrag: JSON-Objekt erwartet."
            )
        allowed = {f.name for f in fields(cls)}
        filtered = {key: value for key, value in data.items() if key in allowed}
        # api.py und check() lesen id/filename als Strings; ein gebrochener
        # Typ im Speicher muss als Datenfehler auflaufen, nicht als Crash.
        if (not isinstance(filtered.get("id"), str)
                or not isinstance(filtered.get("filename"), str)):
            raise ValueError(
                "Ungültiges Bild in einem Rezept-Eintrag: "
                "'id' und 'filename' müssen Texte sein."
            )
        if any(not isinstance(filtered.get(key, ""), str)
               for key in ("role", "caption", "created_at")):
            raise ValueError("Ungültige Textfelder in einem Rezeptbild.")
        return cls(**filtered)


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
        return storage.recipe_file(self.slug)

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
        if not isinstance(d, dict):
            raise ValueError("Ein Rezept-Eintrag muss ein JSON-Objekt sein.")
        allowed = {f.name for f in fields(cls)}
        values = {k: v for k, v in d.items() if k in allowed}
        # api.py sortiert nach title und check() liest images; ein nicht
        # ladbarer Eintrag muss als Datenfehler sichtbar werden, statt beim
        # Lesen mit raw KeyError/AttributeError den ganzen Aufruf zu crashen.
        if (not isinstance(values.get("title"), str)
                or not values["title"].strip()):
            raise ValueError("'title' muss ein nicht-leerer Text sein.")
        if not isinstance(values.get("slug"), str) or not values["slug"]:
            raise ValueError("'slug' muss ein nicht-leerer Text sein.")
        tags = values.get("tags", [])
        if not isinstance(tags, list) or not all(isinstance(tag, str) for tag in tags):
            raise ValueError("'tags' muss eine Liste von Texten sein.")
        for key in ("duration_min", "servings"):
            validation._positive_recipe_number(values.get(key), f"'{key}'")
        for key in ("last_cooked", "cover_image_id"):
            if values.get(key) is not None and not isinstance(values[key], str):
                raise ValueError(f"'{key}' muss ein Text oder null sein.")
        images = values.get("images", [])
        if not isinstance(images, list):
            raise ValueError("'images' muss eine Liste sein.")
        values["images"] = [RecipeImage.from_dict(image) for image in images]
        return cls(**values)


@dataclass
class ArchivedRecipe:
    """A complete reversible recipe snapshot outside the active catalog."""
    recipe: Recipe
    archived_at: str

    @property
    def slug(self) -> str:
        return self.recipe.slug

    @property
    def title(self) -> str:
        return self.recipe.title

    @property
    def images(self) -> list[RecipeImage]:
        return self.recipe.images

    @property
    def cover_image(self) -> RecipeImage | None:
        return self.recipe.cover_image

    def content(self) -> str:
        path = storage.archive_recipe_file(self.slug)
        return path.read_text(encoding="utf-8") if path.exists() else ""

    def to_dict(self, *, include_content: bool = False) -> dict:
        result = self.recipe.to_dict()
        result["archived_at"] = self.archived_at
        if include_content:
            result["content"] = self.content()
        return result


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
        if not isinstance(data, dict):
            raise ValueError("Ein Lieblingsprodukt muss ein JSON-Objekt sein.")
        allowed = {f.name for f in fields(cls)}
        values = {key: value for key, value in data.items() if key in allowed}
        if any(not isinstance(values.get(key), str) or not values[key]
               for key in ("id", "name")):
            raise ValueError("Ein Lieblingsprodukt braucht 'id' und 'name' als Text.")
        if any(not isinstance(values.get(key, ""), str)
               for key in ("brand", "store", "note", "image_filename", "created_at")):
            raise ValueError("Die Textfelder eines Lieblingsprodukts sind ungültig.")
        return cls(**values)


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
        if not isinstance(data, dict):
            raise ValueError("Ein Einkaufsbedarf muss ein JSON-Objekt sein.")
        allowed = {f.name for f in fields(cls)}
        values = {key: value for key, value in data.items() if key in allowed}
        if any(not isinstance(values.get(key), str) or not values[key]
               for key in ("id", "name")):
            raise ValueError("Ein Einkaufsbedarf braucht 'id' und 'name' als Text.")
        aliases = values.get("aliases", [])
        if (not isinstance(aliases, list)
                or not all(isinstance(alias, str) for alias in aliases)):
            raise ValueError("Die Aliasse eines Einkaufsbedarfs müssen eine Textliste sein.")
        if not isinstance(values.get("products", []), list):
            raise ValueError("Die Produkte eines Einkaufsbedarfs müssen eine Liste sein.")
        if not isinstance(values.get("created_at", ""), str):
            raise ValueError("Der Zeitstempel eines Einkaufsbedarfs muss ein Text sein.")
        values["aliases"] = list(aliases)
        values["products"] = [FavoriteProduct.from_dict(product)
                              for product in values.get("products", [])]
        return cls(**values)


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
        if not isinstance(d, dict):
            raise ValueError("Ein Einkaufslisten-Eintrag muss ein JSON-Objekt sein.")
        allowed = {f.name for f in fields(cls)}
        values = {k: v for k, v in d.items() if k in allowed}
        if any(not isinstance(values.get(key), str) or not values[key]
               for key in ("id", "text")):
            raise ValueError("Ein Einkaufslisten-Eintrag braucht 'id' und 'text' als Text.")
        if not isinstance(values.get("quantity", ""), str):
            raise ValueError("Die Menge eines Einkaufslisten-Eintrags ist ungültig.")
        if any(not isinstance(values.get(key, False), bool)
               for key in ("checked", "deleted")):
            raise ValueError("Der Status eines Einkaufslisten-Eintrags ist ungültig.")
        if values.get("source") is not None and not isinstance(values["source"], str):
            raise ValueError("Die Quelle eines Einkaufslisten-Eintrags ist ungültig.")
        if any(not isinstance(values.get(key, ""), str)
               for key in ("created_at", "updated_at")):
            raise ValueError("Der Zeitstempel eines Einkaufslisten-Eintrags ist ungültig.")
        return cls(**values)


def new_id() -> str:
    """Allocate an opaque identifier for a new persisted record."""
    return uuid.uuid4().hex
