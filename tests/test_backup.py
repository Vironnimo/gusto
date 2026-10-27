"""Household backup and guarded restore contracts using an isolated store."""
from __future__ import annotations

import io
import hashlib
import json
import os
import stat
import sys
import tempfile
import zipfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, os.fspath(ROOT))
import gusto  # noqa: E402, F401
from gusto.core import backup, errors, favorites, shopping, shopping_sync, storage  # noqa: E402
from gusto.core.catalog import archive, images, recipes, snapshots  # noqa: E402

checks = 0


def check(condition, message):
    global checks
    assert condition, message
    checks += 1


def rejected(fn, message):
    try:
        fn()
    except ValueError:
        check(True, message)
    else:
        raise AssertionError(message)


def contents(value):
    with zipfile.ZipFile(io.BytesIO(value)) as zipped:
        return {name: zipped.read(name) for name in zipped.namelist()}


with tempfile.TemporaryDirectory(prefix="gusto-backup-test-") as temporary:
    root = Path(temporary)
    os.environ["GUSTO_HOME"] = os.fspath(root / "store")
    recipe = recipes.add_recipe("Suppe", content="# Suppe\n\nZutaten\n")
    recipe2 = recipes.add_recipe("Kuchen")
    picture = root / "sample.png"
    picture.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\0\0\0\rIHDR" + b"\0\0\0\1\0\0\0\1")
    images.add_recipe_image(recipe.slug, picture)
    images.add_recipe_image(recipe2.slug, picture)
    archive.archive_recipe(recipe2.slug)
    favorites.favorite_add_need("Milch")
    shopping.shopping_add("Tomaten")
    recipes.log_cooked(recipe.slug)
    saved = backup.create_backup()
    with zipfile.ZipFile(io.BytesIO(saved)) as zipped:
        names = set(zipped.namelist())
        check("manifest.json" in names and "recipes/suppe.md" in names
              and "archive/kuchen/metadata.json" in names
              and any(name.startswith("images/suppe/") for name in names)
              and any(name.startswith("archive/kuchen/images/") for name in names)
              and "data/changes.json" not in names
              and not any(".lock" in name for name in names),
              "backup must include household data and exclude journal and locks")

    recipes.add_recipe("Salat")
    preview = backup.preview_restore(saved)
    check(preview["counts"]["recipe_count"] == 1
          and preview["counts"]["archive_count"] == 1
          and preview["counts"]["favorite_need_count"] == 1
          and preview["counts"]["shopping_visible_count"] == 1
          and preview["counts"]["log_count"] == 1
          and preview["counts"]["image_count"] == 1
          and preview["counts"]["archive_image_count"] == 1
          and preview["effects"]["removed"] > 0,
          "preview must describe restored state and removed files")
    shopping.shopping_add("Brot")
    rejected(lambda: backup.restore_backup(saved, preview["token"]),
             "restore must reject a stale store preview")
    preview = backup.preview_restore(saved)
    rejected(lambda: backup.restore_backup(saved + b"x", preview["token"]),
             "restore must bind the exact archive bytes")
    revision = storage.change_state()["revision"]
    result = backup.restore_backup(saved, preview["token"])
    check(recipes.get("suppe") is not None and recipes.get("salat") is None
          and snapshots.get_archived("kuchen") is not None
          and [item.text for item in shopping.shopping_load()] == ["Tomaten"],
          "restore must replace every domain from the snapshot")
    check(storage.change_state()["revision"] > revision,
          "restore must preserve and advance the change journal")
    rejected(lambda: backup.restore_backup(saved, preview["token"]),
             "a successful restore must invalidate the consumed preview")
    safety = backup.read_safety_backup(result["safety_backup_id"])
    check(backup.preview_restore(safety)["counts"]["recipe_count"] == 2,
          "automatic safety backup must capture the previous store")
    rejected(lambda: backup.read_safety_backup("../escape"),
             "safety backup IDs must not address other paths")

    identical_preview = backup.preview_restore(saved)
    backup.restore_backup(saved, identical_preview["token"])
    rejected(lambda: backup.restore_backup(saved, identical_preview["token"]),
             "restoring identical content must still consume the preview generation")

    preview = backup.preview_restore(safety)
    before = backup.create_backup()
    before_revision = storage.change_state()["revision"]
    real_replace = os.replace
    replace_count = 0

    def fail_mid_restore(source, target):
        global replace_count
        if ".gusto-restore-" in str(source) or ".gusto-restore-" in str(target):
            replace_count += 1
            if replace_count == 4:
                raise OSError("injected restore failure")
        return real_replace(source, target)

    try:
        with patch.object(backup.os, "replace", side_effect=fail_mid_restore):
            backup.restore_backup(safety, preview["token"])
    except OSError:
        check(True, "restore must report the failed replacement")
    else:
        raise AssertionError("injected restore failure was ignored")
    check(contents(backup.create_backup()) == contents(before) and storage.change_state()["revision"] == before_revision,
          "a complete rollback must preserve the old files and journal revision")

    generation_before = shopping_sync.generation_path().read_bytes()
    reset = shopping_sync.reset_generation_unlocked

    def fail_after_reset():
        reset()
        raise OSError("injected generation failure")

    try:
        with patch.object(shopping_sync, "reset_generation_unlocked", side_effect=fail_after_reset):
            backup.restore_backup(safety, preview["token"])
    except OSError:
        check(True, "a generation failure must reject the restore")
    else:
        raise AssertionError("injected generation failure was ignored")
    check(shopping_sync.generation_path().read_bytes() == generation_before
          and contents(backup.create_backup()) == contents(before)
          and storage.change_state()["revision"] == before_revision,
          "rollback must restore the previous shopping generation and household data")

    with zipfile.ZipFile(io.BytesIO(saved)) as zipped:
        entries = {name: zipped.read(name) for name in zipped.namelist()}
    for path, content in (("../escape", b"x"), ("data/changes.json", b"{}")):
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w") as zipped:
            for name, data in entries.items():
                zipped.writestr(name, data)
            zipped.writestr(path, content)
        rejected(lambda: backup.preview_restore(output.getvalue()),
                 "restore must reject unexpected and escaping archive paths")
    altered = dict(entries)
    altered["recipes/suppe.md"] = b"tampered"
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as zipped:
        for name, data in altered.items():
            zipped.writestr(name, data)
    rejected(lambda: backup.preview_restore(output.getvalue()),
             "restore must reject an archive whose data fails its manifest")
    invalid_records = dict(entries)
    invalid_records["data/recipes.json"] = b"[]"
    manifest = json.loads(invalid_records["manifest.json"])
    for item in manifest["files"]:
        if item["path"] == "data/recipes.json":
            item["size"] = len(invalid_records[item["path"]])
            item["sha256"] = hashlib.sha256(invalid_records[item["path"]]).hexdigest()
    invalid_records["manifest.json"] = json.dumps(manifest).encode("utf-8")
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as zipped:
        for name, data in invalid_records.items():
            zipped.writestr(name, data)
    rejected(lambda: backup.preview_restore(output.getvalue()),
             "matching hashes must not bypass domain consistency validation")
    escaped = dict(entries)
    recipe_data = json.loads(escaped["data/recipes.json"])
    recipe_data[0]["images"][0]["filename"] = "../../outside.png"
    escaped["data/recipes.json"] = json.dumps(recipe_data).encode("utf-8")
    manifest = json.loads(escaped["manifest.json"])
    for item in manifest["files"]:
        if item["path"] == "data/recipes.json":
            item["size"] = len(escaped[item["path"]])
            item["sha256"] = hashlib.sha256(escaped[item["path"]]).hexdigest()
    escaped["manifest.json"] = json.dumps(manifest).encode("utf-8")
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as zipped:
        for name, data in escaped.items():
            zipped.writestr(name, data)
    rejected(lambda: backup.preview_restore(output.getvalue()),
             "metadata must not contain image paths outside their owner")
    for metadata_path in ("data/recipes.json", "archive/kuchen/metadata.json"):
        duplicate = dict(entries)
        metadata = json.loads(duplicate[metadata_path])
        record = metadata[0] if isinstance(metadata, list) else metadata["recipe"]
        record["images"].append(dict(record["images"][0]))
        duplicate[metadata_path] = json.dumps(metadata).encode("utf-8")
        manifest = json.loads(duplicate["manifest.json"])
        for item in manifest["files"]:
            if item["path"] == metadata_path:
                item["size"] = len(duplicate[metadata_path])
                item["sha256"] = hashlib.sha256(duplicate[metadata_path]).hexdigest()
        duplicate["manifest.json"] = json.dumps(manifest).encode("utf-8")
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w") as zipped:
            for name, data in duplicate.items():
                zipped.writestr(name, data)
        rejected(lambda: backup.preview_restore(output.getvalue()),
                 "duplicate image records must not enter active or archived recipes")
    rejected(lambda: backup.preview_restore(b"not a zip"),
             "restore must reject unreadable archives")
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as zipped:
        for name, data in entries.items():
            zipped.writestr(name, data)
        link = zipfile.ZipInfo("images/suppe/link.png")
        link.create_system = 3
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        zipped.writestr(link, "../../outside")
    rejected(lambda: backup.preview_restore(output.getvalue()),
             "restore must reject link entries before extracting")
    with patch.object(backup, "MAX_TOTAL_BYTES", 1):
        rejected(lambda: backup.preview_restore(saved),
                 "restore must enforce its uncompressed size limit")

    replace_count = 0

    def fail_restore_and_rollback(source, target):
        global replace_count
        if ".gusto-restore-" in str(source) or ".gusto-restore-" in str(target):
            replace_count += 1
            if replace_count in {4, 5}:
                raise OSError("injected restore and rollback failure")
        return real_replace(source, target)

    try:
        with patch.object(backup.os, "replace", side_effect=fail_restore_and_rollback):
            backup.restore_backup(safety, preview["token"])
    except errors.PostMutationError as error:
        recovery = list(storage.project_root().glob(".gusto-restore-*"))
        check(len(recovery) == 1 and str(recovery[0]) in str(error)
              and (recovery[0] / "before" / "images").is_dir(),
              "failed rollback must retain recovery files and report uncertain outcome")
    else:
        raise AssertionError("an incomplete rollback must report an uncertain outcome")

print(f"OK - {checks} backup checks passed")
