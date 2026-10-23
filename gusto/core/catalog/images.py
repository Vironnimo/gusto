"""Recipe image metadata, files and cover transactions."""
from __future__ import annotations

import shutil
import uuid
from pathlib import Path
import gusto.core._images as image_files
import gusto.core.records as records
import gusto.core._timestamps as timestamps
import gusto.core._validation as validation
import gusto.core.catalog.recipes as recipe_store
import gusto.core.catalog.snapshots as snapshots
import gusto.core.storage as storage
import gusto.core.errors as errors


def recipe_image_path(slug: str, image: records.RecipeImage) -> Path:
    return storage.recipe_images_dir(slug) / image.filename


def archived_recipe_image_path(slug: str, image: records.RecipeImage) -> Path:
    return storage.archive_images_dir(slug) / image.filename


def get_archived_recipe_image(slug: str, image_id: str) -> records.RecipeImage | None:
    entry = snapshots.get_archived(slug)
    if entry is None:
        return None
    return next((image for image in entry.images if image.id == image_id), None)


def list_recipe_images(slug: str) -> tuple[list[records.RecipeImage], str | None]:
    recipe = recipe_store.get(slug)
    if recipe is None:
        raise errors.NotFoundError(f"Kein Rezept mit Slug '{slug}'.")
    cover = recipe.cover_image
    return recipe.images, cover.id if cover is not None else None


def get_recipe_image(slug: str, image_id: str) -> records.RecipeImage | None:
    recipe = recipe_store.get(slug)
    if recipe is None:
        return None
    return next((image for image in recipe.images if image.id == image_id), None)


@storage.locked_mutation("catalog")
def add_recipe_image(slug: str, source: str | Path, role: str = "gallery",
                     caption: str = "", cover: bool = False) -> records.RecipeImage:
    recipes = recipe_store.load_recipes()
    recipe = next((item for item in recipes if item.slug == slug), None)
    if recipe is None:
        raise errors.NotFoundError(f"Kein Rezept mit Slug '{slug}'.")

    source_path, suffix = image_files.validate_source(source)

    clean_role = role.strip() or "gallery"
    clean_caption = caption.strip()
    validation._no_newlines(clean_role, "Die Bildrolle")
    validation._no_newlines(clean_caption, "Die Bildbeschreibung")
    image = records.RecipeImage(
        id=uuid.uuid4().hex, filename="", role=clean_role,
        caption=clean_caption, created_at=timestamps._now_iso(),
    )
    make_cover = cover or recipe.cover_image is None
    image.filename = f"{image.id}{suffix}"
    destination = recipe_image_path(slug, image)
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        shutil.copy2(source_path, destination)
    except Exception:
        image_files._discard_imported_image(destination)
        raise

    recipe.images.append(image)
    if make_cover:
        recipe.cover_image_id = image.id
    index_checkpoint = storage.mutation_checkpoint()
    try:
        recipe_store._save_recipes_unlocked(recipes)
    except Exception:
        if storage.mutation_checkpoint() == index_checkpoint:
            image_files._discard_imported_image(destination)
        raise
    return image


@storage.locked_mutation("catalog")
def update_recipe_image(slug: str, image_id: str, role: str | None = None,
                        caption: str | None = None) -> records.RecipeImage:
    recipes = recipe_store.load_recipes()
    recipe = next((item for item in recipes if item.slug == slug), None)
    if recipe is None:
        raise errors.NotFoundError(f"Kein Rezept mit Slug '{slug}'.")
    image = next((item for item in recipe.images if item.id == image_id), None)
    if image is None:
        raise errors.NotFoundError(f"Kein Bild mit id '{image_id}' bei Rezept '{slug}'.")
    if role is not None:
        clean_role = role.strip() or "gallery"
        validation._no_newlines(clean_role, "Die Bildrolle")
        image.role = clean_role
    if caption is not None:
        clean_caption = caption.strip()
        validation._no_newlines(clean_caption, "Die Bildbeschreibung")
        image.caption = clean_caption
    recipe_store._save_recipes_unlocked(recipes)
    return image


@storage.locked_mutation("catalog")
def set_recipe_cover(slug: str, image_id: str) -> records.RecipeImage:
    recipes = recipe_store.load_recipes()
    recipe = next((item for item in recipes if item.slug == slug), None)
    if recipe is None:
        raise errors.NotFoundError(f"Kein Rezept mit Slug '{slug}'.")
    image = next((item for item in recipe.images if item.id == image_id), None)
    if image is None:
        raise errors.NotFoundError(f"Kein Bild mit id '{image_id}' bei Rezept '{slug}'.")
    recipe.cover_image_id = image.id
    recipe_store._save_recipes_unlocked(recipes)
    return image


@storage.locked_mutation("catalog")
def remove_recipe_image(slug: str, image_id: str) -> str | None:
    recipes = recipe_store.load_recipes()
    recipe = next((item for item in recipes if item.slug == slug), None)
    if recipe is None:
        raise errors.NotFoundError(f"Kein Rezept mit Slug '{slug}'.")
    image = next((item for item in recipe.images if item.id == image_id), None)
    if image is None:
        raise errors.NotFoundError(f"Kein Bild mit id '{image_id}' bei Rezept '{slug}'.")

    recipe.images = [item for item in recipe.images if item.id != image_id]
    remaining_ids = {item.id for item in recipe.images}
    if recipe.cover_image_id == image_id or recipe.cover_image_id not in remaining_ids:
        recipe.cover_image_id = recipe.images[0].id if recipe.images else None
    recipe_store._save_recipes_unlocked(recipes)
    recipe_image_path(slug, image).unlink(missing_ok=True)
    folder = storage.recipe_images_dir(slug)
    if folder.exists() and not any(folder.iterdir()):
        folder.rmdir()
    return recipe.cover_image_id
