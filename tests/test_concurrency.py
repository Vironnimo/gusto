"""Cross-process checks for agent-style parallel Gusto mutations.

Runnable WITHOUT pytest:  python tests/test_concurrency.py

Every worker points at the same throwaway GUSTO_HOME. A start barrier makes
the separate processes contend for the catalog, favorites, and shopping
sources of truth instead of accidentally running one after another.
"""
from __future__ import annotations

import base64
import multiprocessing
import os
import queue
import subprocess
import sys
import tempfile
import traceback
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
WORKERS = 6
ITERATIONS = 4
CLI_CALLS = 5
checks = 0


def check(condition, message):
    global checks
    assert condition, message
    checks += 1


def worker(home: str, image_path: str, number: int, ready, start, results) -> None:
    os.environ["GUSTO_HOME"] = home
    sys.path.insert(0, os.fspath(ROOT))
    from gusto import core

    ready.put(number)
    if not start.wait(30):
        results.put(f"worker {number}: start barrier timed out")
        return

    try:
        for iteration in range(ITERATIONS):
            suffix = f"{number}-{iteration}"
            core.shopping_add(f"Shopping {suffix}")
            core.shopping_merge([{
                "id": f"remote-{suffix}",
                "text": f"Remote {suffix}",
                "quantity": "",
                "checked": False,
                "source": None,
                "created_at": "2026-07-21T12:00:00.000Z",
                "updated_at": "2026-07-21T12:00:00.000Z",
                "deleted": False,
            }])
            core.add_recipe(
                f"Parallel recipe {suffix}", slug=f"parallel-{suffix}",
            )
            core.favorite_add_need(f"Parallel need {suffix}")
            core.log_cooked("meal", when=f"2026-07-{iteration + 1:02d}")
            core.add_recipe_image(
                "photos", image_path, caption=f"Parallel photo {suffix}",
            )
    except BaseException:
        results.put(f"worker {number}:\n{traceback.format_exc()}")
    else:
        results.put(None)


def main() -> None:
    home = Path(tempfile.mkdtemp(prefix="gusto-concurrency-test-"))
    os.environ["GUSTO_HOME"] = os.fspath(home)
    sys.path.insert(0, os.fspath(ROOT))
    from gusto import core

    check(str(core.project_root()).startswith(tempfile.gettempdir()),
          "GUSTO_HOME does not point into the temp directory -- abort.")

    core.add_recipe("Meal", slug="meal")
    core.add_recipe("Photos", slug="photos")
    image_path = home / "source.png"
    image_path.write_bytes(base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
    ))

    context = multiprocessing.get_context("spawn")
    ready = context.Queue()
    results = context.Queue()
    start = context.Event()
    processes = [
        context.Process(
            target=worker,
            args=(os.fspath(home), os.fspath(image_path), number,
                  ready, start, results),
        )
        for number in range(WORKERS)
    ]
    for process in processes:
        process.start()

    try:
        for _ in processes:
            ready.get(timeout=30)
    except queue.Empty as error:
        raise AssertionError("workers did not reach the start barrier") from error
    start.set()

    errors = []
    for _ in processes:
        result = results.get(timeout=120)
        if result is not None:
            errors.append(result)
    for process in processes:
        process.join(timeout=30)
        check(not process.is_alive(), f"worker {process.pid} did not finish")
        check(process.exitcode == 0,
              f"worker {process.pid} exited with {process.exitcode}")
    check(not errors, "parallel workers failed:\n" + "\n".join(errors))

    cli_processes = [
        subprocess.Popen(
            [sys.executable, "-m", "gusto", "shopping", "add",
             f"CLI shopping {number}", "--json"],
            cwd=ROOT, env={**os.environ, "GUSTO_HOME": os.fspath(home)},
            text=True, encoding="utf-8", stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        for number in range(CLI_CALLS)
    ]
    for process in cli_processes:
        stdout, stderr = process.communicate(timeout=30)
        check(process.returncode == 0,
              f"parallel CLI add failed: {stderr or stdout}")

    expected = WORKERS * ITERATIONS
    shopping = core.shopping_load()
    check(len(shopping) == expected * 2 + CLI_CALLS,
          "parallel Core/CLI shopping adds and PWA merges must retain every id")
    recipes = core.load_recipes()
    check(len(recipes) == expected + 2,
          "parallel recipe creation must retain every catalog entry")
    check(len(core.favorites_load()) == expected,
          "parallel favorite creation must retain every need")
    check(len(core.load_log()) == expected,
          "parallel cooking writes must retain every log entry")
    photos = next(recipe for recipe in recipes if recipe.slug == "photos")
    check(len(photos.images) == expected,
          "parallel image imports must retain every image metadata entry")
    check(all(core.recipe_image_path("photos", image).is_file()
              for image in photos.images),
          "parallel image imports must retain every owned file")
    check(photos.cover_image_id in {image.id for image in photos.images},
          "parallel image imports must leave a valid selected cover")

    print(f"OK - {checks} concurrency checks passed (GUSTO_HOME={home})")


if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
