"""Read-only consistency diagnostics across every persisted domain."""
from __future__ import annotations

from collections import Counter
from pathlib import Path
import gusto.core.records as records
import gusto.core.catalog.categories as facets
import gusto.core.catalog.recipes as recipe_store
import gusto.core.catalog.snapshots as snapshots
import gusto.core.favorites as preferences
import gusto.core.shopping as shopping
import gusto.core.shopping_sync as shopping_sync
import gusto.core.storage as storage


def check() -> dict:
    """Check hard data-integrity errors and non-blocking organization warnings."""
    # Corrupted data files must be reported as integrity errors instead of
    # crashing the whole diagnostic with a raw loader exception.
    invalid_data_files: list[str] = []

    try:
        storage.change_state()
    except ValueError as error:
        invalid_data_files.append(f"{storage.changes_path()}: {error}")

    try:
        shopping_sync.shopping_generation()
    except ValueError as error:
        invalid_data_files.append(f"{shopping_sync.generation_path()}: {error}")

    def directory_entries(path: Path) -> list[Path]:
        try:
            if not path.exists():
                return []
            return list(path.iterdir())
        except FileNotFoundError:
            return []
        except OSError as error:
            invalid_data_files.append(
                f"Datenverzeichnis '{path}' kann nicht gelesen werden: {error}"
            )
            return []

    def markdown_content(path: Path) -> str | None:
        try:
            return path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as error:
            invalid_data_files.append(
                f"Rezeptdatei '{path}' kann nicht gelesen werden: {error}"
            )
            return None

    try:
        recipes = recipe_store.load_recipes()
    except ValueError as error:
        recipes = []
        invalid_data_files.append(str(error))
    indexed = {r.slug for r in recipes}
    present = {path.stem for path in directory_entries(storage.recipes_dir())
               if path.is_file() and path.match("*.md")}
    slug_counts = Counter(recipe.slug for recipe in recipes)
    duplicate_recipe_slugs = sorted(
        slug for slug, count in slug_counts.items() if count > 1
    )
    try:
        uncategorized = facets.uncategorized_tags([
            tag for recipe in recipes for tag in recipe.tags
        ])
    except ValueError as error:
        uncategorized = []
        invalid_data_files.append(str(error))
    title_mismatches: list[str] = []
    for recipe in recipes:
        path = storage.recipe_file(recipe.slug)
        if recipe.slug in present:
            content = markdown_content(path)
            if content is not None and recipe_store._markdown_title(content) != recipe.title:
                title_mismatches.append(recipe.slug)
    image_root = storage.images_dir()
    image_folders = {path.name for path in directory_entries(image_root)
                     if path.is_dir() and path.name != "_favorites"}
    orphaned_image_folders = sorted(image_folders - indexed)
    missing_image_files: list[str] = []
    orphaned_image_files: list[str] = []
    invalid_cover_images: list[str] = []
    for recipe in recipes:
        referenced = {image.filename for image in recipe.images}
        folder = storage.recipe_images_dir(recipe.slug)
        present_images = {path.name for path in directory_entries(folder)
                          if path.is_file()}
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
    try:
        favorite_needs = preferences.favorites_load()
    except ValueError as error:
        favorite_needs = []
        invalid_data_files.append(str(error))
    referenced_favorite_images = {
        product.image_filename
        for need in favorite_needs
        for product in need.products
        if product.image_filename
    }
    favorite_root = storage.favorite_images_dir()
    present_favorite_images = {path.name for path in directory_entries(favorite_root)
                               if path.is_file()}
    alias_owners: dict[str, list[str]] = {}
    for need in favorite_needs:
        for label in [need.name, *need.aliases]:
            alias_owners.setdefault(preferences.normalize_shopping_text(label), []).append(need.name)
    duplicate_favorite_aliases = sorted(
        label for label, owners in alias_owners.items()
        if label and len(set(owners)) > 1
    )

    archive_root = storage.archive_dir()
    archive_paths = directory_entries(archive_root)
    archive_folders = [path for path in archive_paths if path.is_dir()]
    orphaned_archive_root_files = sorted(
        path.name for path in archive_paths if path.is_file()
    )
    stale_archive_transactions = sorted(
        path.name for path in archive_folders if path.name.startswith(".")
    )
    archived_entries: list[records.ArchivedRecipe] = []
    invalid_archive_entries: list[str] = []
    missing_archive_files: list[str] = []
    archived_title_mismatches: list[str] = []
    missing_archive_image_files: list[str] = []
    orphaned_archive_image_files: list[str] = []
    invalid_archive_cover_images: list[str] = []
    for folder in archive_folders:
        if folder.name.startswith("."):
            continue
        try:
            entry = snapshots._load_archived_entry(folder)
        except ValueError:
            invalid_archive_entries.append(folder.name)
            continue
        archived_entries.append(entry)
        markdown = storage.archive_recipe_file(entry.slug)
        if not markdown.is_file():
            missing_archive_files.append(entry.slug)
        else:
            content = markdown_content(markdown)
            if content is not None and recipe_store._markdown_title(content) != entry.title:
                archived_title_mismatches.append(entry.slug)
        referenced = {image.filename for image in entry.images}
        folder_images = storage.archive_images_dir(entry.slug)
        present_images = {path.name for path in directory_entries(folder_images)
                          if path.is_file()}
        missing_archive_image_files.extend(
            f"{entry.slug}/{filename}"
            for filename in sorted(referenced - present_images)
        )
        orphaned_archive_image_files.extend(
            f"{entry.slug}/{filename}"
            for filename in sorted(present_images - referenced)
        )
        image_ids = {image.id for image in entry.images}
        if ((entry.images and entry.recipe.cover_image_id is None)
                or (entry.recipe.cover_image_id is not None
                    and entry.recipe.cover_image_id not in image_ids)):
            invalid_archive_cover_images.append(entry.slug)

    archived_slugs = {entry.slug for entry in archived_entries}
    known_slugs = indexed | archived_slugs
    active_archive_conflicts = sorted(indexed & archived_slugs)
    try:
        shopping_items = shopping.shopping_load()
    except ValueError as error:
        shopping_items = []
        invalid_data_files.append(str(error))
    unresolved_shopping_sources = sorted({
        item.source for item in shopping_items
        if not item.deleted and item.source and item.source not in known_slugs
    })
    try:
        log_entries = recipe_store.load_log()
    except ValueError as error:
        log_entries = []
        invalid_data_files.append(str(error))
    unresolved_log_references = sorted({
        entry.get("slug") for entry in log_entries
        if entry.get("slug") and entry.get("slug") not in known_slugs
    })

    result = {
        "recipe_count": len(indexed),
        "archive_count": len(archived_entries),
        "duplicate_recipe_slugs": duplicate_recipe_slugs,
        "orphaned_files": sorted(present - indexed),   # .md without index entry
        "missing_files": sorted(indexed - present),    # index entry without .md
        "title_mismatches": sorted(title_mismatches),
        "uncategorized_tags": uncategorized,           # tags in no category
        "orphaned_image_folders": orphaned_image_folders,
        "orphaned_image_files": orphaned_image_files,
        "missing_image_files": missing_image_files,
        "invalid_cover_images": sorted(invalid_cover_images),
        "invalid_archive_entries": sorted(invalid_archive_entries),
        "orphaned_archive_root_files": orphaned_archive_root_files,
        "stale_archive_transactions": stale_archive_transactions,
        "missing_archive_files": sorted(missing_archive_files),
        "archived_title_mismatches": sorted(archived_title_mismatches),
        "missing_archive_image_files": missing_archive_image_files,
        "orphaned_archive_image_files": orphaned_archive_image_files,
        "invalid_archive_cover_images": sorted(invalid_archive_cover_images),
        "active_archive_conflicts": active_archive_conflicts,
        "unresolved_shopping_sources": unresolved_shopping_sources,
        "unresolved_log_references": unresolved_log_references,
        "favorite_need_count": len(favorite_needs),
        "duplicate_favorite_aliases": duplicate_favorite_aliases,
        "orphaned_favorite_image_files": sorted(
            present_favorite_images - referenced_favorite_images
        ),
        "missing_favorite_image_files": sorted(
            referenced_favorite_images - present_favorite_images
        ),
        "invalid_data_files": invalid_data_files,
    }
    hard_error_fields = [
        "duplicate_recipe_slugs",
        "orphaned_files",
        "missing_files",
        "title_mismatches",
        "orphaned_image_folders",
        "orphaned_image_files",
        "missing_image_files",
        "invalid_cover_images",
        "invalid_archive_entries",
        "orphaned_archive_root_files",
        "stale_archive_transactions",
        "missing_archive_files",
        "archived_title_mismatches",
        "missing_archive_image_files",
        "orphaned_archive_image_files",
        "invalid_archive_cover_images",
        "active_archive_conflicts",
        "unresolved_shopping_sources",
        "duplicate_favorite_aliases",
        "orphaned_favorite_image_files",
        "missing_favorite_image_files",
        "invalid_data_files",
    ]
    warning_fields = ["uncategorized_tags", "unresolved_log_references"]
    result["errors"] = [
        {"code": field, "items": result[field]}
        for field in hard_error_fields if result[field]
    ]
    result["warnings"] = [
        {"code": field, "items": result[field]}
        for field in warning_fields if result[field]
    ]
    result["ok"] = not result["errors"]
    return result
