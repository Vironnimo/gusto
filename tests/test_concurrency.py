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
import socket
import subprocess
import sys
import tempfile
import time
import traceback
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from pathlib import Path
from threading import Event
from unittest.mock import patch


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, os.fspath(ROOT))
import gusto  # noqa: E402, F401 - also configures spawned workers

WORKERS = 6
ITERATIONS = 4
CLI_CALLS = 5
checks = 0


def check(condition, message):
    global checks
    assert condition, message
    checks += 1


def free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def wait_for_server(url: str, process: subprocess.Popen, timeout: float = 30) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url + "/api/v1/health", timeout=1):
                return True
        except (OSError, urllib.error.URLError):
            if process.poll() is not None:
                return False
            time.sleep(0.1)
    return False


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

    check(core.project_root().is_relative_to(Path(tempfile.gettempdir()).resolve()),
          "GUSTO_HOME does not point into the temp directory -- abort.")

    core.data_dir().mkdir(parents=True)
    abandoned_lock = core.data_dir() / ".gusto-catalog.lock"
    abandoned_lock.touch()
    with patch.object(core, "_LOCK_TIMEOUT_SECONDS", 0):
        core.add_recipe("Meal", slug="meal")
    check(abandoned_lock.stat().st_size == 0,
          "an empty lock left by an interrupted creator must remain usable")
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

    port = free_port()
    server_url = f"http://127.0.0.1:{port}"
    server_environment = {
        **os.environ,
        "GUSTO_HOME": os.fspath(home),
    }
    server = subprocess.Popen(
        [
            sys.executable, "-m", "uvicorn", "gusto.web:app",
            "--host", "127.0.0.1", "--port", str(port),
            "--log-level", "warning",
        ],
        cwd=ROOT, env=server_environment,
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        text=True, encoding="utf-8",
    )
    try:
        check(wait_for_server(server_url, server),
              "isolated server did not become ready for parallel CLI calls")
        cli_environment = {
            **server_environment,
            "GUSTO_URL": server_url,
        }
        cli_processes = [
            subprocess.Popen(
                [sys.executable, "-m", "gusto", "shopping", "add",
                 f"CLI shopping {number}", "--json"],
                cwd=ROOT, env=cli_environment,
                text=True, encoding="utf-8", stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            for number in range(CLI_CALLS)
        ]
        for process in cli_processes:
            stdout, stderr = process.communicate(timeout=30)
            check(process.returncode == 0,
                  f"parallel CLI add failed: {stderr or stdout}")
    finally:
        server.terminate()
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()
            server.wait(timeout=10)

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

    restored_source = core.add_recipe("Sync during restore")
    core.archive_recipe(restored_source.slug)
    restore_parked = Event()
    finish_restore = Event()
    sync_started = Event()
    save_catalog = core._save_recipes_unlocked

    def pause_restore_before_index(recipes):
        restore_parked.set()
        if not finish_restore.wait(5):
            raise AssertionError("restore barrier timed out")
        save_catalog(recipes)

    def sync_restored_source():
        sync_started.set()
        return core.shopping_merge([core.ShoppingItem(
            id="during-restore", text="Mehl", source=restored_source.slug,
            created_at="2026-07-21T12:00:00.000Z",
            updated_at="2026-07-21T12:00:00.000Z",
        ).to_dict()])

    with ThreadPoolExecutor(max_workers=2) as executor:
        with patch.object(core, "_save_recipes_unlocked", pause_restore_before_index):
            restoring = executor.submit(core.restore_archived_recipe, restored_source.slug)
            try:
                check(restore_parked.wait(5), "restore must reach its transient catalog gap")
                syncing = executor.submit(sync_restored_source)
                check(sync_started.wait(5), "the parallel sync must start")
                try:
                    syncing.result(timeout=0.05)
                except FutureTimeout:
                    pass
            finally:
                finish_restore.set()
            restoring.result(timeout=5)
            synced = syncing.result(timeout=5)
    check(any(item.id == "during-restore" and item.source == restored_source.slug
              for item in synced),
          "sync must wait for restore and retain its still-valid recipe source")

    if os.name == "nt":
        # Real Windows open-reader handles deny atomic replacement until they
        # close. Readers such as SSE must not make an otherwise valid mutation
        # fail after its business data has already been committed.
        for filename, writer, initial, changed in (
            ("reader.json", core._write_json, {"old": True}, {"new": True}),
            ("reader.md", core._write_text, "# Alt\n", "# Neu\n"),
        ):
            target = home / filename
            writer(target, initial)
            started = Event()

            def replace_while_reading():
                started.set()
                return writer(target, changed)

            with ThreadPoolExecutor(max_workers=1) as executor:
                reader = target.open(encoding="utf-8")
                try:
                    future = executor.submit(replace_while_reading)
                    check(started.wait(5), "the replacement worker must start")
                    try:
                        future.result(timeout=0.05)
                    except FutureTimeout:
                        pass
                    check(bool(reader.read()),
                          "the open reader must retain its complete original snapshot")
                finally:
                    reader.close()
                check(future.result(timeout=5) is True,
                      "atomic replacement must recover once the Windows reader closes")
            check(not list(home.glob(f".{filename}.*.tmp")),
                  "a recovered replacement must leave no temporary file behind")

    print(f"OK - {checks} concurrency checks passed (GUSTO_HOME={home})")


if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
