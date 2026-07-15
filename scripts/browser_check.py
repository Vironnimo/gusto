"""End-to-end check of the web UI with Playwright.

Starts its own uvicorn server against a THROWAWAY copy of the data
(GUSTO_HOME -> .testdata), clicks through the app in real Chromium, takes
screenshots and checks the most important flows. The real sample data stays
untouched.

Usage:  .venv/Scripts/python.exe scripts/browser_check.py
"""
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT = Path(__file__).resolve().parent.parent
SHOTS = Path(__file__).resolve().parent / "shots"
TESTDATA = ROOT / ".testdata"
PORT = "8011"
BASE = f"http://127.0.0.1:{PORT}"
PRODUCT_PHOTO = next((ROOT / "images" / "spaghetti-carbonara").glob("*.png"))

SHOTS.mkdir(exist_ok=True)

# --- fresh throwaway data ---
if TESTDATA.exists():
    shutil.rmtree(TESTDATA)
TESTDATA.mkdir()
shutil.copytree(ROOT / "recipes", TESTDATA / "recipes")
shutil.copytree(ROOT / "data", TESTDATA / "data")
if (ROOT / "images").exists():
    shutil.copytree(ROOT / "images", TESTDATA / "images")

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
            urllib.request.urlopen(BASE, timeout=1)
            return True
        except urllib.error.HTTPError:
            return True  # server responds (even with an error page) -> it is up
        except Exception:
            if srv.poll() is not None:
                return False
            time.sleep(0.2)
    return False

try:
    if not wait_up():
        print("!! server not reachable (uvicorn start failed)")
        raise SystemExit(2)

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        page.on("dialog", lambda d: d.accept())  # confirm() on delete

        # Home page
        page.goto(BASE, wait_until="networkidle")
        check("Gusto" in page.content(), "homepage shows the brand")
        check(page.locator(".card").count() == 3, "3 recipe cards visible")
        check(page.locator(".card-image").count() == 3,
              "all sample recipes show their stored cover image")
        page.screenshot(path=str(SHOTS / "01_home.png"), full_page=True)

        # Search (also searches ingredients in the text) -- server route (no-JS)
        page.goto(BASE + "/?q=kokos", wait_until="networkidle")
        check(page.locator(".card").count() == 1, "search 'kokos' -> 1 hit (Dal)")
        page.screenshot(path=str(SHOTS / "02_search.png"), full_page=True)

        # Live search: typing updates without submit (and searches ingredients)
        page.goto(BASE, wait_until="networkidle")
        page.fill("input[name=q]", "kokos")
        page.wait_for_function("() => document.querySelectorAll('#results .card').length === 1")
        check(page.locator("#results .card").count() == 1, "live search 'kokos' -> 1 hit (no submit)")
        check("Linsen-Dal" in page.content(), "live search finds ingredient in text (Dal)")
        page.fill("input[name=q]", "")
        page.wait_for_function("() => document.querySelectorAll('#results .card').length === 3")
        check(page.locator("#results .card").count() == 3, "live search cleared -> 3 hits again")

        # --- Tag filter: facets / multi-select ---
        page.goto(BASE, wait_until="networkidle")
        check(page.locator(".taggroup").count() >= 3, "tag bar grouped by category")
        check(page.locator(".taggroup-label", has_text="Küche").count() == 1, "category 'Küche' visible")

        # Single tag (as before)
        page.goto(BASE + "/?tag=vegan", wait_until="networkidle")
        check(page.locator(".card").count() == 1, "tag filter 'vegan' -> 1 hit")

        # OR within a category (cuisine: italienisch OR indisch)
        page.goto(BASE + "/?tag=italienisch&tag=indisch", wait_until="networkidle")
        check(page.locator(".card").count() == 2, "italienisch+indisch (OR) -> 2 hits")

        # AND across categories (cuisine=italienisch AND dish_type=pasta)
        page.goto(BASE + "/?tag=italienisch&tag=pasta", wait_until="networkidle")
        check(page.locator(".card").count() == 1, "italienisch+pasta (AND) -> 1 hit (Carbonara)")

        # AND across categories with no intersection (vegetarisch AND pasta)
        page.goto(BASE + "/?tag=vegetarisch&tag=pasta", wait_until="networkidle")
        check(page.locator(".card").count() == 0, "vegetarisch+pasta (AND) -> 0 hits")

        # Combine by clicking (toggle links, selection is kept)
        page.goto(BASE, wait_until="networkidle")
        page.locator(".tagchip", has_text="indisch").first.click()
        page.wait_for_load_state("networkidle")
        check(page.locator(".tagchip.is-on", has_text="indisch").count() == 1, "click: 'indisch' active")
        check(page.locator(".card").count() == 1, "click 'indisch' -> 1 hit")
        page.locator(".tagchip", has_text="italienisch").first.click()
        page.wait_for_load_state("networkidle")
        check(page.locator(".tagchip.is-on").count() == 2, "click: two tags active at once")
        check(page.locator(".card").count() == 2, "indisch+italienisch -> 2 hits")
        page.screenshot(path=str(SHOTS / "02b_tagfilter.png"), full_page=True)
        page.locator(".tagchip.is-on", has_text="indisch").first.click()
        page.wait_for_load_state("networkidle")
        check(page.locator(".card").count() == 1, "'indisch' deselected again -> 1 hit")

        # Recipe page
        page.goto(BASE, wait_until="networkidle")
        page.locator(".card-title", has_text="Carbonara").click()
        page.wait_for_load_state("networkidle")
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
        page.wait_for_load_state("networkidle")
        page.goto(BASE + "/log", wait_until="networkidle")
        check(page.locator(".timeline li").count() >= 3, "log has a new entry")
        page.screenshot(path=str(SHOTS / "04_log.png"), full_page=True)

        # Suggestions
        page.goto(BASE + "/suggestions", wait_until="networkidle")
        check("Was koche ich" in page.content(), "suggestions page loads")
        page.screenshot(path=str(SHOTS / "05_suggest.png"), full_page=True)

        # Create a new recipe
        page.goto(BASE + "/new", wait_until="networkidle")
        page.fill("input[name=title]", "Test Pfannkuchen")
        page.fill("input[name=tags]", "test, suess")
        page.fill("input[name=duration]", "20")
        page.fill("input[name=servings]", "2")
        page.fill("textarea[name=content]",
                  "## Zutaten\n\n- 2 Eier\n- 250 ml Milch\n- 150 g Mehl\n\n"
                  "## Zubereitung\n\n1. Alles verruehren.\n2. In der Pfanne backen.")
        page.locator("button.btn", has_text="Speichern").click()
        page.wait_for_load_state("networkidle")
        check("Test Pfannkuchen" in page.content(), "new recipe created")
        check(page.url.endswith("/recipe/test-pfannkuchen"), "slug generated correctly")
        page.screenshot(path=str(SHOTS / "06_new.png"), full_page=True)

        # Edit (slug stays stable)
        page.goto(BASE + "/recipe/test-pfannkuchen/edit", wait_until="networkidle")
        page.fill("input[name=title]", "Test Pfannkuchen Deluxe")
        page.locator("button.btn", has_text="Speichern").click()
        page.wait_for_load_state("networkidle")
        check("Deluxe" in page.content(), "recipe edited")

        # Delete (clears the test recipe again)
        page.locator(".recipe-more summary").click()
        page.locator("button.btn-text", has_text="Löschen").click()
        page.wait_for_load_state("networkidle")
        check("Test Pfannkuchen" not in page.content(), "recipe deleted")

        # 404
        resp = page.goto(BASE + "/recipe/gibtsnicht")
        check(resp.status == 404, "unknown recipe -> 404 page")

        # --- Shopping list (JS client takes over: renders into #shop-client) ---
        page.goto(BASE, wait_until="networkidle")
        check(page.locator(".nav a", has_text="Einkauf").count() >= 1, "nav link to the shopping list")

        # Put a recipe's ingredients onto the list (server form on the recipe page)
        page.goto(BASE + "/recipe/spaghetti-carbonara", wait_until="networkidle")
        page.locator("form[action$='/shopping'] button").click()
        page.wait_for_load_state("networkidle")
        check(page.url.endswith("/shopping"), "button leads to /shopping")
        page.wait_for_function("() => document.querySelectorAll('#shop-client .shop-item').length === 6")
        check(page.locator("#shop-client .shop-item").count() == 6, "6 Carbonara ingredients (client)")
        check(page.locator("#shop-client .shop-source", has_text="Spaghetti Carbonara").count() == 6,
              "recipe sources use the readable recipe title")
        page.screenshot(path=str(SHOTS / "09_shopping.png"), full_page=True)

        # Create a reusable shopping need and two ranked preferred products.
        page.locator(".shop-favorites-link").click()
        page.wait_for_load_state("networkidle")
        check(page.url.endswith("/favorites"), "preferred-product library opens from shopping")
        create_need = page.locator(".favorite-create-panel")
        page.fill(".favorite-create-panel input[name=name]", "Spaghetti")
        page.fill(".favorite-create-panel input[name=alias]", "200 g Spaghetti")
        create_need.locator("button", has_text="Anlegen").click()
        page.wait_for_load_state("networkidle")
        check(page.locator(".favorite-alias", has_text="200 g Spaghetti").count() == 1,
              "exact shopping text is stored as an alias")

        add_product = page.locator(".favorite-product-create")
        page.fill(".favorite-product-create input[name=name]", "De Cecco Spaghetti n. 12")
        page.fill(".favorite-product-create input[name=brand]", "De Cecco")
        page.fill(".favorite-product-create input[name=store]", "REWE")
        page.fill(".favorite-product-create textarea[name=note]", "Bleibt schön bissfest")
        page.set_input_files(".favorite-product-create input[name=image]", PRODUCT_PHOTO)
        add_product.locator("button", has_text="Produkt hinzufügen").click()
        page.wait_for_load_state("networkidle")
        page.locator(".favorite-product-create summary").click()
        page.fill(".favorite-product-create input[name=name]", "Barilla Spaghetti n. 5")
        page.fill(".favorite-product-create input[name=brand]", "Barilla")
        page.fill(".favorite-product-create input[name=store]", "EDEKA")
        page.set_input_files(".favorite-product-create input[name=image]", PRODUCT_PHOTO)
        page.locator(".favorite-product-create button", has_text="Produkt hinzufügen").click()
        page.wait_for_load_state("networkidle")
        check(page.locator(".favorite-admin-product").count() == 2,
              "two preferred product cards are managed centrally")
        page.locator(".favorite-admin-product").nth(1).locator("summary").click()
        page.locator(".favorite-admin-product").nth(1).locator("button", has_text="Weiter nach oben").click()
        page.wait_for_load_state("networkidle")
        check("Barilla" in page.locator(".favorite-admin-product").first.text_content(),
              "manual move changes the household ranking")
        page.screenshot(path=str(SHOTS / "09b_favorites_admin.png"), full_page=True)

        page.goto(BASE + "/shopping", wait_until="networkidle")
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
        page.wait_for_timeout(350)
        page.screenshot(path=str(SHOTS / "09c_favorite_sheet.png"), full_page=True)
        page.locator("#favorite-sheet .favorite-sheet-close").click()

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
        page.wait_for_load_state("networkidle")
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
        page.locator("#shop-client .shop-clear button").click()
        page.wait_for_function("() => document.querySelectorAll('#shop-client .shop-group-done').length === 0")
        check(page.locator("#shop-client .shop-item").count() == 6, "6 open items again (client)")
        page.screenshot(path=str(SHOTS / "10_shopping_after.png"), full_page=True)

        # No-JS fallback: server-rendered list works without JavaScript
        # (state-independent: before/after instead of a fixed count)
        nojs = browser.new_context(java_script_enabled=False)
        njp = nojs.new_page()
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
        m.goto(BASE, wait_until="networkidle")
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
        m.screenshot(path=str(SHOTS / "07_mobile_home.png"))
        m.goto(BASE + "/recipe/rotes-linsen-dal", wait_until="networkidle")
        check(m.locator(".recipe-jump").is_visible(),
              "mobile: recipe content has a direct jump action")
        check(m.locator(".recipe-more").get_attribute("open") is None,
              "mobile: administration stays in the closed More menu")
        m.screenshot(path=str(SHOTS / "08_mobile_recipe.png"))
        m.goto(BASE + "/shopping", wait_until="networkidle")
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
        m.wait_for_timeout(350)
        m.screenshot(path=str(SHOTS / "12_mobile_favorite_sheet.png"))

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
