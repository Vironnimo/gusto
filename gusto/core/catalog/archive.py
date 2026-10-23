"""Archive, restore and purge transactions across catalog and shopping."""
from __future__ import annotations

import shutil
import tempfile
import uuid
from pathlib import Path
import gusto.core.records as records
import gusto.core._timestamps as timestamps
import gusto.core.catalog.recipes as recipe_store
import gusto.core.catalog.snapshots as snapshots
import gusto.core.shopping as shopping
import gusto.core.storage as storage
import gusto.core.errors as errors


@storage.locked_mutation("catalog")
def archive_recipe(slug: str) -> records.ArchivedRecipe:
    """Move recipe-owned Markdown, metadata and images into the archive."""
    recipes = recipe_store.load_recipes()
    recipe = next((item for item in recipes if item.slug == slug), None)
    if recipe is None:
        raise errors.NotFoundError(f"Kein Rezept mit Slug '{slug}'.")
    source_markdown = storage.recipe_file(slug)
    if not source_markdown.is_file():
        raise ValueError(
            f"Rezept '{slug}' kann nicht archiviert werden: "
            "Die Markdown-Datei fehlt."
        )
    target = storage.archive_recipe_dir(slug)
    if target.exists():
        raise ValueError(f"Rezept '{slug}' liegt bereits im Archiv.")

    root = storage.archive_dir()
    root.mkdir(parents=True, exist_ok=True)
    checkpoint = storage.mutation_checkpoint()
    temporary = Path(tempfile.mkdtemp(prefix=f".{slug}.archive.", dir=root))
    storage.mark_mutation()
    archived_at = timestamps._now_iso()
    source_images = storage.recipe_images_dir(slug)
    final_created = False
    markdown_moved = False
    images_moved = False
    index_checkpoint = None
    try:
        source_markdown.replace(temporary / "recipe.md")
        markdown_moved = True
        if source_images.exists():
            source_images.replace(temporary / "images")
            images_moved = True
        storage.write_json(temporary / "metadata.json", {
            "version": 1,
            "archived_at": archived_at,
            "recipe": recipe.to_dict(),
        })
        temporary.replace(target)
        final_created = True
        index_checkpoint = storage.mutation_checkpoint()
        recipe_store._save_recipes_unlocked([
            item for item in recipes if item.slug != slug
        ])
    except Exception:
        if index_checkpoint is None or storage.mutation_checkpoint() == index_checkpoint:
            rollback = target if final_created else temporary
            if markdown_moved:
                source_markdown.parent.mkdir(parents=True, exist_ok=True)
                (rollback / "recipe.md").replace(source_markdown)
            if images_moved:
                source_images.parent.mkdir(parents=True, exist_ok=True)
                (rollback / "images").replace(source_images)
            if rollback.exists():
                shutil.rmtree(rollback)
            storage.rollback_completed(checkpoint)
        raise
    return records.ArchivedRecipe(recipe=recipe, archived_at=archived_at)


@storage.locked_mutation("catalog")
def restore_archived_recipe(slug: str) -> records.Recipe:
    """Restore one archived snapshot to the active catalog."""
    entry = snapshots.get_archived(slug)
    if entry is None:
        raise errors.NotFoundError(f"Kein archiviertes Rezept mit Slug '{slug}'.")
    if recipe_store.get(slug) is not None:
        raise ValueError(f"Rezept '{slug}' ist bereits aktiv.")
    target_markdown = storage.recipe_file(slug)
    target_images = storage.recipe_images_dir(slug)
    if target_markdown.exists() or target_images.exists():
        raise ValueError(
            f"Rezept '{slug}' kann nicht wiederhergestellt werden: "
            "Aktive Dateien mit diesem Slug sind bereits vorhanden."
        )

    source = storage.archive_recipe_dir(slug)
    root = storage.archive_dir()
    temporary = root / f".{slug}.restore.{uuid.uuid4().hex}"
    checkpoint = storage.mutation_checkpoint()
    source.replace(temporary)
    storage.mark_mutation()
    markdown_moved = False
    images_moved = False
    index_checkpoint = None
    try:
        target_markdown.parent.mkdir(parents=True, exist_ok=True)
        (temporary / "recipe.md").replace(target_markdown)
        markdown_moved = True
        if (temporary / "images").exists():
            target_images.parent.mkdir(parents=True, exist_ok=True)
            (temporary / "images").replace(target_images)
            images_moved = True
        recipes = recipe_store.load_recipes()
        recipes.append(entry.recipe)
        index_checkpoint = storage.mutation_checkpoint()
        recipe_store._save_recipes_unlocked(recipes)
    except Exception:
        if index_checkpoint is None or storage.mutation_checkpoint() == index_checkpoint:
            if markdown_moved:
                target_markdown.replace(temporary / "recipe.md")
            if images_moved:
                target_images.replace(temporary / "images")
            temporary.replace(source)
            storage.rollback_completed(checkpoint)
        raise
    shutil.rmtree(temporary)
    return entry.recipe


@storage.locked_mutation("catalog", "shopping", event_resources=("catalog",))
def purge_archived_recipe(slug: str) -> records.ArchivedRecipe:
    """Permanently remove one archived recipe snapshot.

    The snapshot is first moved into a hidden transaction folder under the
    archive root and only then removed recursively. An interrupted purge
    therefore never leaves a half-deleted visible archive entry behind; the
    leftover folder is reported by ``check`` as ``stale_archive_transactions``.
    """
    entry = snapshots.get_archived(slug)
    if entry is None:
        raise errors.NotFoundError(f"Kein archiviertes Rezept mit Slug '{slug}'.")
    visible_sources = [
        item for item in shopping.shopping_load()
        if not item.deleted and item.source == slug
    ]
    if visible_sources:
        count = len(visible_sources)
        verb = "verweist" if count == 1 else "verweisen"
        raise ValueError(
            f"{count} sichtbare Einkaufsposten {verb} noch auf '{slug}'. "
            "Entferne diese Einträge vor dem endgültigen Löschen."
        )
    temporary = storage.archive_dir() / f".{slug}.purge.{uuid.uuid4().hex}"
    storage.archive_recipe_dir(slug).replace(temporary)
    storage.mark_mutation()
    shutil.rmtree(temporary)
    return entry
