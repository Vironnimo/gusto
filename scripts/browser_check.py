"""End-to-end check of the web UI with Playwright.

Starts its own uvicorn server against a THROWAWAY copy of the data
(GUSTO_HOME -> a unique .tmp/browser-* directory), drives real Chromium, takes
screenshots and checks the most important flows. The real sample data stays
untouched.

Usage:  .venv/Scripts/python.exe scripts/browser_check.py
"""
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

from PIL import Image
from playwright.sync_api import sync_playwright

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import gusto  # noqa: E402, F401 - configure temp for this process and its children

TESTDATA = Path(tempfile.mkdtemp(prefix="browser-"))
SHOTS = TESTDATA / "shots"
print(f"Browser test data and screenshots: {TESTDATA}", flush=True)
with socket.socket() as port_socket:
    port_socket.bind(("127.0.0.1", 0))
    PORT = str(port_socket.getsockname()[1])
BASE = f"http://127.0.0.1:{PORT}"
PRODUCT_PHOTO = next((ROOT / "images" / "spaghetti-carbonara").glob("*.png"))

SHOTS.mkdir(exist_ok=True)

# --- fresh throwaway data ---
shutil.copytree(ROOT / "recipes", TESTDATA / "recipes")
shutil.copytree(ROOT / "data", TESTDATA / "data")
if (ROOT / "images").exists():
    shutil.copytree(ROOT / "images", TESTDATA / "images")
os.environ["GUSTO_HOME"] = str(TESTDATA)
import gusto.core.catalog.recipes as core_recipes  # noqa: E402
import gusto.core.storage as core_storage  # noqa: E402

CAMERA_PHOTO = TESTDATA / "camera-original.jpg"
camera_image = Image.new("RGB", (2400, 1200), "#c96b42")
camera_exif = Image.Exif()
camera_exif[0x010E] = "private camera metadata"
camera_image.save(CAMERA_PHOTO, quality=92, exif=camera_exif)

env = {**os.environ, "GUSTO_HOME": str(TESTDATA)}
srv = subprocess.Popen(
    [sys.executable, "-m", "uvicorn", "gusto.web:app",
     "--host", "127.0.0.1", "--port", PORT, "--log-level", "warning"],
    cwd=str(ROOT), env=env,
)

fails = []
def check(cond, msg):
    print(("  ok   " if cond else " FAIL ") + msg)
    if not cond:
        fails.append(msg)

def wait_up(timeout=40):
    for _ in range(timeout * 5):
        try:
            if srv.poll() is not None:
                return False
            with urllib.request.urlopen(BASE + "/api/v1/health", timeout=1) as response:
                health = json.loads(response.read())
            if Path(health.get("data_path", "")).resolve() != TESTDATA.resolve():
                raise RuntimeError("Refusing to test a different Gusto data store")
            return True
        except RuntimeError:
            raise
        except Exception:
            if srv.poll() is not None:
                return False
            time.sleep(0.2)
    return False


def observe_live(page):
    page.add_init_script("""(() => {
        window.gustoEvents = {open: false, revisions: []};
        const NativeEvents = window.EventSource;
        window.EventSource = class extends NativeEvents {
            constructor(...args) {
                super(...args);
                this.addEventListener('open', () => { window.gustoEvents.open = true; });
                this.addEventListener('message', event => {
                    window.gustoEvents.revisions.push(JSON.parse(event.data).revision);
                });
            }
        };
    })();""")


def double_cooked(page, selector):
    before = len(core_recipes.load_log())
    submitted = []

    def observe(request):
        if request.method == "POST" and request.url.endswith("/cooked"):
            submitted.append(request)

    page.on("request", observe)
    with page.expect_navigation(wait_until="load"):
        page.locator(selector).evaluate("button => { button.click(); button.click(); }")
    page.remove_listener("request", observe)
    # Navigation completion consumes the successful POST and redirected GET;
    # also count submissions so even an aborted duplicate cannot escape.
    return len(submitted) == 1 and len(core_recipes.load_log()) == before + 1

try:
    if not wait_up():
        print("!! server not reachable (uvicorn start failed)")
        raise SystemExit(2)

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        page.on("dialog", lambda d: d.accept())  # archive/purge confirmations

        # Home page
        page.goto(BASE, wait_until="load")
        check("Gusto" in page.content(), "homepage shows the brand")
        check(page.locator(".card").count() == 3, "3 recipe cards visible")
        check(page.locator(".card-image").count() == 3,
              "all sample recipes show their stored cover image")
        check(page.locator(".card").first.bounding_box()["y"] < 550,
              "desktop: compact facets leave the recipes in the first screen")
        page.screenshot(path=str(SHOTS / "01_home.png"), full_page=True)

        # Search (also searches ingredients in the text) -- server route (no-JS)
        page.goto(BASE + "/?q=kokos", wait_until="load")
        check(page.locator(".card").count() == 1, "search 'kokos' -> 1 hit (Dal)")
        page.screenshot(path=str(SHOTS / "02_search.png"), full_page=True)

        # Live search: typing updates without submit (and searches ingredients)
        page.goto(BASE, wait_until="load")
        page.fill("input[name=q]", "kokos")
        page.wait_for_function("() => document.querySelectorAll('#results .card').length === 1")
        check(page.locator("#results .card").count() == 1, "live search 'kokos' -> 1 hit (no submit)")
        check(page.locator("#result-count").inner_text() == "1 Rezept gefunden",
              "live search updates the accessible result count")
        check("Linsen-Dal" in page.content(), "live search finds ingredient in text (Dal)")
        page.fill("input[name=q]", "")
        page.wait_for_function("() => document.querySelectorAll('#results .card').length === 3")
        check(page.locator("#results .card").count() == 3, "live search cleared -> 3 hits again")

        # Live-search fallback: if the fetch fails, Enter must still navigate
        # to the server-rendered list instead of silently doing nothing.
        # The service worker would answer the fetch itself and bypass the
        # route, so this check runs in a context without service workers.
        fb_ctx = browser.new_context(service_workers="block")
        fb = fb_ctx.new_page()
        fb.route("**/*", lambda route: route.abort()
                 if route.request.resource_type in ("fetch", "xhr")
                 else route.continue_())
        fb.goto(BASE, wait_until="load")
        fb.fill("input[name=q]", "kokos")
        fb.press("input[name=q]", "Enter")
        try:
            fb.wait_for_url("**/*q=kokos*", timeout=3000)
            fallback_navigated = True
        except Exception:
            fallback_navigated = False
        check(fallback_navigated,
              "search fallback: Enter navigates when the live fetch fails")
        if fallback_navigated:
            fb.wait_for_load_state("load")
            check(fb.locator("#results .card").count() == 1,
                  "search fallback: server-rendered list appears after failed live fetch")
        fb_ctx.close()

        # A filter click within the search debounce must preserve the newly
        # typed query. Freeze browser timers to exercise that short window
        # deterministically instead of depending on the machine's speed.
        race_ctx = browser.new_context(service_workers="block")
        race = race_ctx.new_page()
        race.clock.install(time=1_700_000_000_000)
        race.goto(BASE, wait_until="load")
        race.clock.pause_at(1_700_000_060_000)
        race.fill("input[name=q]", "kokos")
        race.locator(".tagchip", has_text="vegan").first.click()
        race.wait_for_load_state("load")
        check("q=kokos" in race.url and "tag=vegan" in race.url
              and race.input_value("input[name=q]") == "kokos",
              "filter click preserves a query whose debounce has not completed")

        # An older response must not replace results/URL after the user has
        # already changed the query, even before the next request starts.
        race.goto(BASE, wait_until="load")
        held_search = {}

        def hold_old_search(route):
            if (route.request.resource_type in ("fetch", "xhr")
                    and "q=kokos" in route.request.url):
                held_search["route"] = route
                held_search["response"] = route.fetch()
            else:
                route.continue_()

        race.route("**/*", hold_old_search)
        race.fill("input[name=q]", "kokos")
        with race.expect_request(
                lambda request: request.resource_type in ("fetch", "xhr")
                and "q=kokos" in request.url, timeout=3000):
            race.clock.run_for(150)
        held_deadline = time.monotonic() + 3
        while "response" not in held_search and time.monotonic() < held_deadline:
            race.wait_for_timeout(20)
        check("response" in held_search, "delayed-search fixture intercepted the old response")
        if "response" in held_search:
            race.fill("input[name=q]", "pasta")
            with race.expect_response(lambda response: "q=kokos" in response.url) as old_reply:
                held_search["route"].fulfill(response=held_search["response"])
            old_reply.value.body()
            race.clock.run_for(0)
            check("q=kokos" not in race.url
                  and race.locator("#results .card").count() == 3,
                  "a stale search response cannot replace results or URL for newer text")
            race.clock.resume()
            race.wait_for_function(
                "() => new URLSearchParams(location.search).get('q') === 'pasta'")
            check(race.locator("#results .card").count() == 1
                  and "Carbonara" in race.locator("#results").inner_text(),
                  "the newest search still completes after a stale response is ignored")
        race_ctx.close()

        # --- Tag filter: facets / multi-select ---
        page.goto(BASE, wait_until="load")
        check(page.locator(".taggroup").count() >= 3, "tag bar grouped by category")
        check(page.locator(".taggroup-label", has_text="Küche").count() == 1, "category 'Küche' visible")

        # Single tag (as before)
        page.goto(BASE + "/?tag=vegan", wait_until="load")
        check(page.locator(".card").count() == 1, "tag filter 'vegan' -> 1 hit")

        # OR within a category (cuisine: italienisch OR indisch)
        page.goto(BASE + "/?tag=italienisch&tag=indisch", wait_until="load")
        check(page.locator(".card").count() == 2, "italienisch+indisch (OR) -> 2 hits")

        # AND across categories (cuisine=italienisch AND dish_type=pasta)
        page.goto(BASE + "/?tag=italienisch&tag=pasta", wait_until="load")
        check(page.locator(".card").count() == 1, "italienisch+pasta (AND) -> 1 hit (Carbonara)")

        # AND across categories with no intersection (vegetarisch AND pasta)
        page.goto(BASE + "/?tag=vegetarisch&tag=pasta", wait_until="load")
        check(page.locator(".card").count() == 0, "vegetarisch+pasta (AND) -> 0 hits")

        # Combine by clicking (toggle links, selection is kept)
        page.goto(BASE, wait_until="load")
        page.locator(".tagchip", has_text="indisch").first.click()
        page.wait_for_load_state("load")
        check(page.locator(".tagchip.is-on", has_text="indisch").count() == 1, "click: 'indisch' active")
        check(page.locator(".card").count() == 1, "click 'indisch' -> 1 hit")
        page.locator(".tagchip", has_text="italienisch").first.click()
        page.wait_for_load_state("load")
        check(page.locator(".tagchip.is-on").count() == 2, "click: two tags active at once")
        check(page.locator(".card").count() == 2, "indisch+italienisch -> 2 hits")
        page.screenshot(path=str(SHOTS / "02b_tagfilter.png"), full_page=True)
        page.locator(".tagchip.is-on", has_text="indisch").first.click()
        page.wait_for_load_state("load")
        check(page.locator(".card").count() == 1, "'indisch' deselected again -> 1 hit")

        # A bookmarked tag can become unused or be removed from the catalog.
        # Its active filter must remain reachable and individually removable.
        page.goto(BASE + "/?q=kokos&tag=veralteter-tag", wait_until="load")
        obsolete_filter = page.get_by_role(
            "link", name="veralteter-tag — Filter entfernen", exact=True)
        check(obsolete_filter.count() == 1 and obsolete_filter.is_visible(),
              "an obsolete active tag remains visible with an accessible removal action")
        obsolete_filter.click()
        page.wait_for_load_state("load")
        check("tag=" not in page.url and page.input_value("input[name=q]") == "kokos"
              and page.locator(".card").count() == 1,
              "removing an obsolete tag restores the matching recipes and preserves search")

        # Empty state with active search/tag filters: recipes exist, they just
        # do not match -- the CTA must not claim a first recipe, and the
        # active filter must offer its reset.
        page.goto(BASE + "/?q=zzz-gibts-nicht", wait_until="load")
        content = page.content()
        check("Neues Rezept anlegen" in content
              and "Erstes Rezept anlegen" not in content,
              "filtered empty state invites to create a new recipe, not the first one")
        check(page.locator(".empty a", has_text="Filter zurücksetzen").count() == 1,
              "filtered empty state offers to reset the filter")
        page.locator(".empty a", has_text="Filter zurücksetzen").click()
        page.wait_for_load_state("load")
        check(page.locator(".card").count() == 3
              and page.input_value('input[name="q"]') == "",
              "reset clears the unsuccessful text query and restores every recipe")

        nojs_search = browser.new_context(java_script_enabled=False)
        search_page = nojs_search.new_page()
        search_page.goto(BASE + "/?tag=vegan", wait_until="load")
        search_page.fill('input[name="q"]', "pasta")
        search_page.locator(".search button").click()
        search_page.wait_for_load_state("load")
        check("tag=vegan" in search_page.url and search_page.locator(".card").count() == 0,
              "no-JS search preserves active facets when submitting the query")
        nojs_search.close()

        # Recipe page
        page.goto(BASE, wait_until="load")
        page.locator(".card-title", has_text="Carbonara").click()
        page.wait_for_load_state("load")
        check("Zutaten" in page.content() and "Zubereitung" in page.content(),
              "recipe shows ingredients + steps")
        check(page.locator(".recipe-body ol li").count() >= 4, "steps rendered as a list")
        check(page.locator(".recipe-hero img").count() == 1,
              "recipe shows the selected cover image")
        check(page.locator(".recipe-gallery-item").count() == 1,
              "recipe shows an additional gallery image")
        check(page.locator(".recipe-gallery-item figcaption", has_text="Zubereitung").count() == 1,
              "gallery image shows its caption")
        image_response = page.request.get(
            BASE + page.locator(".recipe-gallery-item img").get_attribute("src")
        )
        check(image_response.ok and image_response.headers.get("content-type", "").startswith("image/"),
              "stored gallery image is served as an image")
        page.screenshot(path=str(SHOTS / "03_recipe.png"), full_page=True)

        missing_image = page.request.get(BASE + "/media/recipe/spaghetti-carbonara/gibtsnicht")
        check(missing_image.status == 404, "unknown recipe image -> 404")

        # "Cooked today" -> log
        page.locator("button.btn", has_text="Heute gekocht").click()
        page.wait_for_load_state("load")
        page.goto(BASE + "/log", wait_until="load")
        check(page.locator(".timeline li").count() >= 3, "log has a new entry")
        page.screenshot(path=str(SHOTS / "04_log.png"), full_page=True)

        # Double submit "Heute gekocht": a fast double click must produce
        # exactly one log entry (client-side submit guard; the core does not
        # deduplicate on purpose). Two synchronous clicks mimic the race.
        page.goto(BASE + "/recipe/rotes-linsen-dal", wait_until="load")
        check(double_cooked(page, 'form[action$="/cooked"] button'),
              "double click 'Heute gekocht' on the recipe logs exactly one entry")

        page.goto(BASE + "/suggestions", wait_until="load")
        check(double_cooked(page, 'form.card-quick button'),
              "double click 'Heute gekocht' on suggestions logs exactly one entry")

        # Suggestions
        page.goto(BASE + "/suggestions", wait_until="load")
        check("Was koche ich" in page.content(), "suggestions page loads")
        check("am längsten nicht Gekochte zuerst" in page.content(),
              "suggestion copy stays consistent with du-form wording")
        page.screenshot(path=str(SHOTS / "05_suggest.png"), full_page=True)

        # Create a new recipe
        page.goto(BASE + "/new", wait_until="load")
        page.fill("input[name=title]", "Test Pfannkuchen")
        page.fill("input[name=tags]", "test, suess")
        page.fill("input[name=duration]", "20")
        page.fill("input[name=servings]", "2")
        page.fill("textarea[name=content]",
                  "## Zutaten\n\n- 2 Eier\n- 250 ml Milch\n- 150 g Mehl\n\n"
                  "## Zubereitung\n\n1. Alles verruehren.\n2. In der Pfanne backen.")
        page.locator("button.btn", has_text="Speichern").click()
        page.wait_for_load_state("load")
        check("Test Pfannkuchen" in page.content(), "new recipe created")
        check(page.url.endswith("/recipe/test-pfannkuchen"), "slug generated correctly")
        page.screenshot(path=str(SHOTS / "06_new.png"), full_page=True)

        # Take or select recipe photos directly in the web UI.
        page.locator(".recipe-photo-action").click()
        page.wait_for_load_state("load")
        check("/recipe/test-pfannkuchen/images" in page.url,
              "recipe photo action opens image management")
        check(page.locator("input[name=image_camera]").first.get_attribute("capture") == "environment",
              "recipe camera action asks for the outward-facing camera")
        check(page.locator("input[name=image_file]").count() == 1,
              "recipe image management keeps a separate library choice")
        page.set_input_files("input[name=image_camera]", CAMERA_PHOTO)
        check(page.locator(".photo-preview:not([hidden])").count() == 1,
              "recipe photo selection shows a preview before upload")
        page.select_option("form[action$='/images/add'] select[name=role]", "result")
        page.fill("form[action$='/images/add'] input[name=caption]", "Direkt fotografiert")
        page.locator("form[action$='/images/add'] button", has_text="Bild hinzufügen").click()
        page.wait_for_load_state("load")
        check(page.locator(".image-admin-card").count() == 1,
              "camera photo is stored on the recipe")
        recipe_data = json.loads((TESTDATA / "data" / "recipes.json").read_text(encoding="utf-8"))
        uploaded = next(item for item in recipe_data if item["slug"] == "test-pfannkuchen")["images"][0]
        uploaded_path = TESTDATA / "images" / "test-pfannkuchen" / uploaded["filename"]
        with Image.open(uploaded_path) as normalized:
            check(normalized.format == "WEBP" and normalized.size == (1920, 960),
                  "web photo is normalized to WebP and a 1920 px maximum edge")
            check(not normalized.getexif(), "web photo metadata is removed")

        page.set_input_files("input[name=image_file]", PRODUCT_PHOTO)
        page.select_option("form[action$='/images/add'] select[name=role]", "step")
        page.fill("form[action$='/images/add'] input[name=caption]", "Beim Wenden")
        page.locator("form[action$='/images/add'] button", has_text="Bild hinzufügen").click()
        page.wait_for_load_state("load")
        check(page.locator(".image-admin-card").count() == 2,
              "existing image can be selected separately from the camera")
        second_image = page.locator(".image-admin-card").nth(1)
        second_image.locator("input[name=cover]").check()
        second_image.locator("button", has_text="Angaben speichern").click()
        page.wait_for_load_state("load")
        check(page.locator(".image-admin-card").nth(1).locator(".cover-badge").count() == 1,
              "any recipe image can become the top image in the web UI")
        page.screenshot(path=str(SHOTS / "06b_recipe_images.png"), full_page=True)

        # Edit (slug stays stable)
        page.goto(BASE + "/recipe/test-pfannkuchen/edit", wait_until="load")
        page.fill("input[name=title]", "Test Pfannkuchen Deluxe")
        page.fill("input[name=duration]", "")
        page.fill("input[name=servings]", "")
        page.locator("button.btn", has_text="Speichern").click()
        page.wait_for_load_state("load")
        check("Deluxe" in page.content(), "recipe edited")
        recipe_data = json.loads((TESTDATA / "data" / "recipes.json").read_text(encoding="utf-8"))
        edited = next(item for item in recipe_data if item["slug"] == "test-pfannkuchen")
        check(edited["duration_min"] is None and edited["servings"] is None,
              "recipe edit can clear optional duration and servings")

        page.goto(BASE + "/recipe/test-pfannkuchen/edit", wait_until="load")
        page.fill("textarea[name=content]", "## Zutaten\n\n- Mein Entwurf")
        core_recipes.update_recipe("test-pfannkuchen", content="## Zutaten\n\n- Aktueller Stand")
        page.locator("button.btn", has_text="Speichern").click()
        check(page.locator(".conflict-version").count() == 2
              and "Mein Entwurf" in page.content() and "Aktueller Stand" in page.content(),
              "stale edit preserves the draft next to the current version")
        page.screenshot(path=str(SHOTS / "06c_edit_conflict.png"), full_page=True)
        page.goto(BASE + "/recipe/test-pfannkuchen", wait_until="load")

        # Archive preserves the complete recipe and restore returns it intact.
        page.locator(".recipe-more summary").click()
        page.locator("button.btn-text", has_text="Archivieren").click()
        page.wait_for_load_state("load")
        check(page.url.endswith("/archive/test-pfannkuchen")
              and page.locator(".archive-badge", has_text="archiviert").count() >= 1,
              "archiving opens the read-only archived recipe")
        check(page.locator(".recipe-hero img").count() == 1
              and page.locator(".recipe-gallery-item").count() == 1,
              "archiving retains the recipe cover and gallery")
        page.screenshot(path=str(SHOTS / "06c_recipe_archive.png"), full_page=True)
        page.locator("button.btn", has_text="Wiederherstellen").click()
        page.wait_for_load_state("load")
        check(page.url.endswith("/recipe/test-pfannkuchen")
              and page.locator(".recipe-gallery-item").count() == 1,
              "restore returns the complete recipe to the active cookbook")

        # Archive once more and exercise the deliberately separate purge.
        page.locator(".recipe-more summary").click()
        page.locator("button.btn-text", has_text="Archivieren").click()
        page.wait_for_load_state("load")
        page.locator(".archive-purge summary").click()
        page.locator(".archive-purge button", has_text="Unwiderruflich").click()
        page.wait_for_load_state("load")
        check(page.url.endswith("/archive")
              and "Test Pfannkuchen Deluxe" not in page.content(),
              "explicit purge permanently removes the archived test recipe")

        # Confirmation dialogs must appear even when the title contains an
        # apostrophe (user text is kept out of JS strings, so it cannot break
        # the confirmation; without the fix the form submits silently).
        cp = browser.new_page(viewport={"width": 1280, "height": 900})
        cp.goto(BASE + "/new", wait_until="load")
        cp.fill("input[name=title]", "Oma's Kuchen")
        cp.fill("input[name=tags]", "test")
        cp.fill("textarea[name=content]",
                "## Zutaten\n\n- 1 Ei\n- 1 Prise Salz\n\n## Zubereitung\n\n1. Alles verruhren.")
        cp.locator("button.btn", has_text="Speichern").click()
        cp.wait_for_load_state("load")
        cp.locator(".recipe-more summary").click()
        confirm_messages = []
        confirm_accept = {"value": False}
        def handle_confirm(dialog):
            confirm_messages.append(dialog.message)
            if confirm_accept["value"]:
                dialog.accept()
            else:
                dialog.dismiss()
        cp.on("dialog", handle_confirm)
        cp.locator("button.btn-text", has_text="Archivieren").click()
        cp.wait_for_function("() => !document.querySelector('form[data-submitted]')")
        check(len(confirm_messages) == 1 and "Oma's Kuchen" in confirm_messages[0],
              "confirm with apostrophe in title: dialog appears")
        check("/archive/" not in cp.url,
              "confirm dismiss: recipe stays active")
        if "/archive/" not in cp.url:
            confirm_accept["value"] = True
            cp.locator("button.btn-text", has_text="Archivieren").click()
            cp.wait_for_load_state("load")
            check("/archive/" in cp.url,
                  "confirm accept: recipe is archived")
        cp.close()

        # 404
        resp = page.goto(BASE + "/recipe/gibtsnicht")
        check(resp.status == 404, "unknown recipe -> 404 page")

        # The root-scoped service worker must never come from the HTTP cache,
        # or SW/offline-shell updates would be delayed.
        sw_response = page.request.get(BASE + "/sw.js")
        check(sw_response.ok
              and sw_response.headers.get("cache-control") == "no-cache",
              "sw.js is served with Cache-Control: no-cache")

        # --- Shopping list (JS client takes over: renders into #shop-client) ---
        page.goto(BASE, wait_until="load")
        check(page.locator(".nav a", has_text="Einkauf").count() >= 1, "nav link to the shopping list")

        # Put a recipe's reviewed ingredients onto the list.
        page.goto(BASE + "/recipe/spaghetti-carbonara", wait_until="load")
        page.locator("a[href$='/shopping']", has_text="Zutaten einkaufen").click()
        check(page.locator(".preview-ingredients li").count() == 6,
              "recipe import shows the ingredients before adding them")
        page.locator("form[action$='/shopping'] button", has_text="zur Einkaufsliste hinzufügen").click()
        page.wait_for_load_state("load")
        check(page.url.endswith("/shopping"), "button leads to /shopping")
        page.wait_for_function("() => document.querySelectorAll('#shop-client .shop-item').length === 6")
        check(page.locator("#shop-client .shop-item").count() == 6, "6 Carbonara ingredients (client)")
        check(page.locator("#shop-client .shop-source", has_text="Spaghetti Carbonara").count() == 6,
              "recipe sources use the readable recipe title")
        page.screenshot(path=str(SHOTS / "09_shopping.png"), full_page=True)

        # A repeated recipe import must explain the no-op and keep the list stable.
        page.goto(BASE + "/recipe/spaghetti-carbonara", wait_until="load")
        page.locator("a[href$='/shopping']", has_text="Zutaten einkaufen").click()
        page.locator("form[action$='/shopping'] button", has_text="zur Einkaufsliste hinzufügen").click()
        page.wait_for_load_state("load")
        check(page.url.startswith(BASE + "/recipe/spaghetti-carbonara?error="),
              "duplicate recipe import returns to the recipe with context")
        check(page.locator(".error", has_text="bereits auf der Einkaufsliste").count() == 1,
              "duplicate recipe import is visibly explained")
        page.screenshot(path=str(SHOTS / "09a_shopping_duplicate.png"), full_page=True)
        page.goto(BASE + "/shopping", wait_until="load")
        page.wait_for_function("() => document.querySelectorAll('#shop-client .shop-item').length === 6")
        check(page.locator("#shop-client .shop-item").count() == 6,
              "duplicate recipe import does not add shopping items")

        # Archiving keeps shopping and log references useful.
        page.goto(BASE + "/recipe/spaghetti-carbonara", wait_until="load")
        page.locator(".recipe-more summary").click()
        page.locator("button.btn-text", has_text="Archivieren").click()
        page.wait_for_load_state("load")
        check(page.url.endswith("/archive/spaghetti-carbonara"),
              "a recipe with external references can still be archived")
        page.goto(BASE + "/shopping", wait_until="load")
        page.wait_for_function("() => document.querySelectorAll('#shop-client .shop-item').length === 6")
        archived_sources = page.locator("#shop-client .shop-source[href='/archive/spaghetti-carbonara']")
        check(archived_sources.count() == 6
              and archived_sources.first.text_content().endswith("(archiviert)"),
              "shopping sources resolve to the archived recipe")
        page.goto(BASE + "/log", wait_until="load")
        check(page.locator(
            ".timeline a[href='/archive/spaghetti-carbonara'] .archive-badge"
        ).count() >= 1, "log entries resolve to the archived recipe")
        page.goto(BASE + "/shopping", wait_until="load")

        # Create a reusable shopping need and two ranked preferred products.
        page.locator(".shop-favorites-link").click()
        page.wait_for_load_state("load")
        check(page.url.endswith("/favorites"), "preferred-product library opens from shopping")
        create_need = page.locator(".favorite-create-panel")
        page.fill(".favorite-create-panel input[name=name]", "Spaghetti")
        page.fill(".favorite-create-panel input[name=alias]", "200 g Spaghetti")
        create_need.locator("button", has_text="Anlegen").click()
        page.wait_for_load_state("load")
        check(page.locator(".favorite-alias", has_text="200 g Spaghetti").count() == 1,
              "exact shopping text is stored as an alias")

        add_product = page.locator(".favorite-product-create")
        page.fill(".favorite-product-create input[name=name]", "De Cecco Spaghetti n. 12")
        page.fill(".favorite-product-create input[name=brand]", "De Cecco")
        page.fill(".favorite-product-create input[name=store]", "REWE")
        page.fill(".favorite-product-create textarea[name=note]", "Bleibt schön bissfest")
        check(page.locator(".favorite-product-create input[name=image_camera]").get_attribute("capture") == "environment",
              "preferred product offers the outward-facing camera")
        check(page.locator(".favorite-product-create input[name=image_file]").count() == 1,
              "preferred product keeps a separate image-library choice")
        page.set_input_files(".favorite-product-create input[name=image_camera]", PRODUCT_PHOTO)
        check(page.locator(".favorite-product-create .photo-preview:not([hidden])").count() == 1,
              "preferred-product photo selection shows a preview")
        add_product.locator("button", has_text="Produkt hinzufügen").click()
        page.wait_for_load_state("load")
        favorite_data = json.loads((TESTDATA / "data" / "favorites.json").read_text(encoding="utf-8"))
        first_product_image = favorite_data["needs"][0]["products"][0]["image_filename"]
        check(first_product_image.endswith(".webp"),
              "preferred-product camera photo is stored in normalized form")
        page.locator(".favorite-product-create summary").click()
        page.fill(".favorite-product-create input[name=name]", "Barilla Spaghetti n. 5")
        page.fill(".favorite-product-create input[name=brand]", "Barilla")
        page.fill(".favorite-product-create input[name=store]", "EDEKA")
        page.set_input_files(".favorite-product-create input[name=image_file]", PRODUCT_PHOTO)
        page.locator(".favorite-product-create button", has_text="Produkt hinzufügen").click()
        page.wait_for_load_state("load")
        check(page.locator(".favorite-admin-product").count() == 2,
              "two preferred product cards are managed centrally")
        page.locator(".favorite-admin-product").nth(1).locator("summary").click()
        page.locator(".favorite-admin-product").nth(1).locator("button", has_text="Weiter nach oben").click()
        page.wait_for_load_state("load")
        check("Barilla" in page.locator(".favorite-admin-product").first.text_content(),
              "manual move changes the household ranking")
        first_product = page.locator(".favorite-admin-product").first
        first_product.locator("summary").click()
        first_product.locator("input[name=image_camera]").set_input_files(CAMERA_PHOTO)
        first_product.locator("button", has_text="Änderungen speichern").click()
        page.wait_for_load_state("load")
        check(page.locator(".favorite-admin-product").first.locator("summary img").count() == 1,
              "preferred-product photo can be replaced from the camera action")
        page.screenshot(path=str(SHOTS / "09b_favorites_admin.png"), full_page=True)

        page.goto(BASE + "/shopping", wait_until="load")
        spaghetti_item = page.locator("#shop-client .shop-item", has_text="200 g Spaghetti")
        page.wait_for_function(
            "() => [...document.querySelectorAll('#shop-client .shop-favorite-hint')].some(el => el.textContent.includes('2 Favoriten'))"
        )
        check("2 Favoriten" in spaghetti_item.locator(".shop-favorite-hint").text_content(),
              "known alias advertises its preferred products")
        spaghetti_item.locator(".shop-favorite-trigger").click()
        check(page.locator("#favorite-sheet:not([hidden])").count() == 1,
              "tapping a shopping text opens the product sheet")
        check(page.locator("#favorite-sheet .favorite-product-card").count() == 2,
              "product sheet shows the complete ranked list")
        check("Barilla" in page.locator("#favorite-sheet .favorite-product-card").first.text_content(),
              "product sheet preserves the manual ranking")
        page.keyboard.press("Shift+Tab")
        check(page.locator(".favorite-manage-link").evaluate("el => el === document.activeElement"),
              "product dialog: Shift+Tab wraps within the open sheet")
        page.keyboard.press("Tab")
        check(page.locator(".favorite-sheet-close").evaluate("el => el === document.activeElement"),
              "product dialog: Tab returns to the close button")
        page.screenshot(path=str(SHOTS / "09c_favorite_sheet.png"), full_page=True, animations="disabled")
        page.locator("#favorite-sheet .favorite-sheet-close").click()
        check(spaghetti_item.locator(".shop-favorite-trigger").evaluate(
            "el => el === document.activeElement"),
            "product dialog: closing returns focus to its shopping item")

        # A foreign favorite change must leave a half-written name intact.
        draft_page = browser.new_page()
        observe_live(draft_page)
        draft_page.goto(BASE + "/favorites", wait_until="load")
        draft_page.wait_for_function("() => window.gustoEvents.open")
        draft_page.locator(".favorite-create-panel summary").click()
        draft_page.fill('input[name="name"]', "Noch nicht gespeichert")
        draft_page.evaluate("window.draftSurvived = true")
        foreign = draft_page.request.post(BASE + "/api/v1/command", data={
            "operation": "favorites.add", "arguments": {"name": "Audit Bedarf"},
        })
        check(foreign.ok, "foreign favorite mutation succeeds")
        foreign_revision = core_storage.change_state()["revision"]
        draft_page.wait_for_function(
            "revision => window.gustoEvents.revisions.includes(revision)", arg=foreign_revision)
        check(draft_page.evaluate("window.draftSurvived === true")
              and draft_page.input_value('input[name="name"]') == "Noch nicht gespeichert",
              "favorite live updates preserve an unfinished form")
        draft_page.close()

        # Add manually (client form, optimistic + background sync)
        page.locator("#shop-client .shop-add-panel summary").click()
        page.fill("#shop-client input[name=text]", "Spaghettini")
        page.fill("#shop-client input[name=quantity]", "500 g")
        page.locator("#shop-client form.shop-add button").click()
        page.wait_for_function("() => document.querySelectorAll('#shop-client .shop-item').length === 7")
        check("Spaghettini" in page.content(), "manual item visible (client)")
        page.locator("#shop-client .shop-item", has_text="Spaghettini").locator(
            ".shop-favorite-trigger").click()
        check(page.locator("#favorite-sheet .favorite-setup-card").count() == 2,
              "unknown shopping text offers assignment or a new need")
        page.locator("#favorite-sheet form[action='/favorites/assign'] button").click()
        page.wait_for_load_state("load")
        page.wait_for_function(
            "() => [...document.querySelectorAll('#shop-client .shop-item')].some(item => item.querySelector('.shop-text').textContent === 'Spaghettini' && item.querySelector('.shop-favorite-hint').textContent.includes('2 Favoriten'))"
        )
        spaghettini_item = page.locator("#shop-client .shop-item", has_text="Spaghettini")
        check("2 Favoriten" in spaghettini_item.locator(".shop-favorite-hint").text_content(),
              "one-time assignment teaches the exact alias")

        # Tick one item off -> moves to "done"
        page.locator("#shop-client .shop-group:not(.shop-group-done) .shop-box").first.click()
        page.wait_for_function("() => document.querySelectorAll('#shop-client .shop-group-done .shop-item').length === 1")
        check(page.locator("#shop-client .shop-group-done .shop-item").count() == 1, "one item is done (client)")

        # Remove done (tombstone)
        page.locator("#shop-client .shop-remove-done button").click()
        page.wait_for_function("() => document.querySelectorAll('#shop-client .shop-group-done').length === 0")
        check(page.locator("#shop-client .shop-item").count() == 6, "6 open items again (client)")
        page.screenshot(path=str(SHOTS / "10_shopping_after.png"), full_page=True)

        # No-JS fallback: server-rendered list works without JavaScript
        # (state-independent: before/after instead of a fixed count)
        nojs = browser.new_context(java_script_enabled=False)
        njp = nojs.new_page()
        njp.goto(BASE + "/recipe/rotes-linsen-dal/images", wait_until="domcontentloaded")
        before_images = njp.locator(".image-admin-card").count()
        check(njp.locator("input[name=image_camera]").count() == 1,
              "no-JS: recipe camera upload remains available")
        njp.set_input_files("input[name=image_camera]", PRODUCT_PHOTO)
        njp.locator("form[action$='/images/add'] button", has_text="Bild hinzufügen").click()
        njp.wait_for_load_state("domcontentloaded")
        check(njp.locator(".image-admin-card").count() == before_images + 1,
              "no-JS: camera-selected recipe photo is stored")

        njp.goto(BASE + "/shopping", wait_until="domcontentloaded")
        check(njp.locator("#shop-server form.shop-add").count() == 1, "no-JS: server form present")
        before = njp.locator("#shop-server .shop-item").count()
        njp.locator("#shop-server .shop-add-panel summary").click()
        njp.fill("#shop-server input[name=text]", "Senf")
        njp.locator("#shop-server form.shop-add button").click()
        njp.wait_for_load_state("domcontentloaded")
        check(njp.locator("#shop-server .shop-item").count() == before + 1, "no-JS: add via server form")
        check("Senf" in njp.content(), "no-JS: new item visible")
        njp.locator("#shop-server .shop-add-panel summary").click()
        njp.fill("#shop-server input[name=text]", "200 g Spaghetti")
        njp.locator("#shop-server form.shop-add button").click()
        njp.wait_for_load_state("domcontentloaded")
        njp.locator("#shop-server .shop-item", has_text="200 g Spaghetti").locator(
            ".shop-favorite-trigger").click()
        njp.wait_for_load_state("domcontentloaded")
        check(njp.locator(".favorite-product-card").count() == 2,
              "no-JS: preferred products open on a normal page")
        nojs.close()

        # Mobile views
        m = browser.new_page(viewport={"width": 390, "height": 844}, device_scale_factor=2)
        m.goto(BASE, wait_until="load")
        first_card = m.locator(".card").first.bounding_box()
        check(m.locator(".nav").evaluate("el => getComputedStyle(el).position") == "fixed",
              "mobile: primary navigation stays at the bottom")
        check(m.evaluate(
            "() => !!document.elementFromPoint(innerWidth / 2, innerHeight - 20).closest('.nav')"),
            "mobile: primary navigation stays above scrolling content")
        check(m.locator(".filter-panel").get_attribute("open") is None,
              "mobile: recipe filters start collapsed")
        check(first_card is not None and first_card["y"] < 520,
              "mobile: the first recipe is visible without a long filter wall")
        m.set_viewport_size({"width": 1280, "height": 900})
        m.locator(".taggroup").first.wait_for(state="visible")
        check(m.locator(".taggroup").first.is_visible(),
              "responsive: widening a mobile page exposes its desktop filters")
        m.set_viewport_size({"width": 390, "height": 844})
        m.wait_for_function("() => !document.querySelector('.filter-panel').open")
        check(m.locator(".filter-panel").get_attribute("open") is None,
              "responsive: narrowing restores compact unselected filters")
        m.screenshot(path=str(SHOTS / "07_mobile_home.png"))
        m.goto(BASE + "/recipe/rotes-linsen-dal", wait_until="load")
        check(m.locator(".recipe-jump").is_visible(),
              "mobile: recipe content has a direct jump action")
        check(m.locator(".recipe-more").get_attribute("open") is None,
              "mobile: administration stays in the closed More menu")
        m.screenshot(path=str(SHOTS / "08_mobile_recipe.png"))
        m.locator(".recipe-photo-action").click()
        m.wait_for_load_state("load")
        check(m.locator(".photo-choice", has_text="Foto aufnehmen").is_visible()
              and m.locator(".photo-choice", has_text="Bild auswählen").is_visible(),
              "mobile: camera and library are separate, visible actions")
        first_photo_choice = m.locator(".photo-choice").first.bounding_box()
        check(first_photo_choice is not None and first_photo_choice["y"] < 520,
              "mobile: photo actions appear before the existing image library")
        m.screenshot(path=str(SHOTS / "08b_mobile_recipe_images.png"), full_page=True)
        m.goto(BASE + "/shopping", wait_until="load")
        first_shop_item = m.locator("#shop-client .shop-item").first.bounding_box()
        check(m.locator("#shop-client .shop-add-panel").get_attribute("open") is None,
              "mobile: add-item form starts collapsed")
        check(first_shop_item is not None and first_shop_item["y"] < 520,
              "mobile: shopping items lead the screen")
        m.screenshot(path=str(SHOTS / "11_mobile_shopping.png"))
        m.locator("#shop-client .shop-item", has_text="200 g Spaghetti").locator(
            ".shop-favorite-trigger").click()
        panel_box = m.locator("#favorite-sheet .favorite-sheet-panel").bounding_box()
        check(panel_box is not None and panel_box["y"] > 100,
              "mobile: preferred products open as a sheet from the bottom")
        check(m.evaluate(
            "() => !!document.elementFromPoint(innerWidth / 2, innerHeight - 20).closest('#favorite-sheet')"),
            "mobile: open product sheet stays above the fixed navigation")
        m.screenshot(path=str(SHOTS / "12_mobile_favorite_sheet.png"), animations="disabled")
        m.locator("#favorite-sheet .favorite-sheet-close").click()

        # Compact phones must be able to use the expanded forms without
        # horizontally scrolling to reach inputs or their submit buttons.
        m.set_viewport_size({"width": 320, "height": 720})
        m.goto(BASE + "/favorites", wait_until="load")
        m.locator(".favorite-create-panel summary").click()
        check(m.evaluate("() => document.documentElement.scrollWidth <= innerWidth"),
              "320px: expanded favorite creation stays within the viewport")
        m.screenshot(path=str(SHOTS / "12b_narrow_favorites.png"), full_page=True)
        m.goto(BASE + "/shopping", wait_until="load")
        m.locator("#shop-client .shop-add-panel summary").click()
        check(m.evaluate("() => document.documentElement.scrollWidth <= innerWidth"),
              "320px: expanded shopping form stays within the viewport")
        m.screenshot(path=str(SHOTS / "12c_narrow_shopping.png"), full_page=True)

        # No-JS destructive actions use distinct routes and semantics.
        nojs_clear = browser.new_context(java_script_enabled=False)
        clear_page = nojs_clear.new_page()
        clear_page.goto(BASE + "/shopping", wait_until="domcontentloaded")
        before_clear = clear_page.locator("#shop-server .shop-item").count()
        check(before_clear > 1, "no-JS: shopping list has entries for removal checks")
        clear_page.locator("#shop-server .shop-item .shop-check button").first.click()
        clear_page.wait_for_load_state("domcontentloaded")
        check(clear_page.locator("#shop-server .shop-group-done .shop-item").count() == 1,
              "no-JS: one item can be marked done before removing completed items")
        clear_page.locator(
            "#shop-server form[action='/shopping/remove-done'] button").click()
        clear_page.wait_for_load_state("domcontentloaded")
        check(clear_page.locator("#shop-server .shop-item").count() == before_clear - 1,
              "no-JS: remove-done removes only the checked item")
        clear_page.locator("#shop-server .shop-clear-all summary").click()
        clear_page.screenshot(
            path=str(SHOTS / "13_shopping_clear_confirmation.png"), full_page=True)
        clear_page.locator(
            "#shop-server form[action='/shopping/clear'] button").click()
        clear_page.wait_for_load_state("domcontentloaded")
        check(clear_page.locator("#shop-server .shop-item").count() == 0,
              "no-JS: clear removes every remaining visible item")
        check("Die Einkaufsliste ist leer" in clear_page.content(),
              "no-JS: complete clear renders the empty-list state")
        nojs_clear.close()

        # A normal CLI mutation goes through this running server and is pushed
        # to an already open catalog page without a manual reload.
        live_page = browser.new_page(viewport={"width": 1280, "height": 900})
        observe_live(live_page)
        live_page.goto(BASE, wait_until="load")
        live_page.wait_for_function("() => window.gustoEvents.open")
        cli_env = {**env, "GUSTO_URL": BASE}
        live_create = subprocess.run(
            [sys.executable, "-m", "gusto", "new", "Live SSE Rezept", "--json"],
            cwd=str(ROOT), env=cli_env, text=True, encoding="utf-8",
            capture_output=True,
        )
        check(live_create.returncode == 0,
              "live: CLI mutation succeeds through the server")
        if live_create.returncode == 0:
            live_recipe = json.loads(live_create.stdout)
            live_slug = live_recipe["slug"]
            try:
                live_page.wait_for_selector(
                    f'a[href="/recipe/{live_slug}"]', timeout=5000)
                live_visible = True
            except Exception:
                live_visible = False
            check(live_visible,
                  "live: CLI-created recipe appears without manual reload")

            live_category = subprocess.run(
                [sys.executable, "-m", "gusto", "categories", "add",
                 "live", "Live Facette", "--json"],
                cwd=str(ROOT), env=cli_env, text=True, encoding="utf-8",
                capture_output=True,
            )
            live_assignment = subprocess.run(
                [sys.executable, "-m", "gusto", "categories", "assign",
                 "live", "vegetarisch", "--json"],
                cwd=str(ROOT), env=cli_env, text=True, encoding="utf-8",
                capture_output=True,
            ) if live_category.returncode == 0 else None
            check(live_category.returncode == 0
                  and live_assignment is not None
                  and live_assignment.returncode == 0,
                  "live: CLI category maintenance succeeds through the server")
            if live_assignment is not None and live_assignment.returncode == 0:
                try:
                    live_page.wait_for_selector(
                        ".taggroup-label:text-is('Live Facette')", timeout=5000)
                    live_category_visible = True
                except Exception:
                    live_category_visible = False
                check(live_category_visible,
                      "live: CLI category assignment refreshes open browser facets")
                live_page.screenshot(
                    path=str(SHOTS / "14_live_category.png"), full_page=True)
            subprocess.run(
                [sys.executable, "-m", "gusto", "delete", live_slug, "--json"],
                cwd=str(ROOT), env=cli_env, text=True, encoding="utf-8",
                capture_output=True,
            )
        live_page.close()

        # Backup preview and restore stay usable without JavaScript.
        backup_context = browser.new_context(java_script_enabled=False,
                                             viewport={"width": 320, "height": 720})
        backup_page = backup_context.new_page()
        backup_page.goto(BASE + "/backup", wait_until="domcontentloaded")
        with backup_page.expect_download() as backup_download:
            backup_page.get_by_role("link", name="Sicherung herunterladen").click()
        backup_path = TESTDATA / "downloaded-backup.zip"
        backup_download.value.save_as(backup_path)
        backup_page.locator("form[action='/backup/preview'] input[type=file]").set_input_files(backup_path)
        backup_page.locator("form[action='/backup/preview'] button").click()
        check(backup_page.locator(".backup-preview").count() == 1,
              "no-JS: backup preview presents replacement effects")
        check(backup_page.evaluate("() => document.documentElement.scrollWidth <= innerWidth"),
              "320px: backup confirmation fits the viewport")
        backup_page.screenshot(path=str(SHOTS / "15_backup_preview.png"), full_page=True)
        backup_page.locator("form[action='/backup/restore'] input[type=file]").set_input_files(backup_path)
        backup_page.locator("form[action='/backup/restore'] input[type=checkbox]").check()
        backup_page.locator("form[action='/backup/restore'] button").click()
        check(backup_page.locator("a[href^='/backup/safety/']").count() == 1,
              "no-JS: confirmed restore exposes the pre-restore safety backup")
        backup_context.close()

        browser.close()
finally:
    srv.terminate()
    try:
        srv.wait(timeout=5)
    except Exception:
        srv.kill()

print()
if fails:
    print(f"{len(fails)} CHECK(S) FAILED:")
    for f in fails:
        print("   - " + f)
    sys.exit(1)
print("ALL CHECKS PASSED.")
