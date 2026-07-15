"""PWA / offline / sync check of the shopping list with Playwright.

Extends scripts/browser_check.py with the phase-2 flows (PWA + offline + sync):
- sync API (GET /api/shopping, POST /api/shopping/sync, "last writer wins")
- offline: load online -> set_offline(True) -> tick optimistically -> back
  online -> sync verifies the change reaches the server
- two-device merge (two browser contexts = two localStorage)
- manifest linked/valid + service worker served/active + offline reload from the
  SW cache

Like browser_check.py everything runs against a THROWAWAY copy of the data
(GUSTO_HOME -> .testdata-pwa); the real data stays untouched.

Usage:  .venv/Scripts/python.exe scripts/pwa_check.py
"""
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from playwright.sync_api import sync_playwright
from playwright.sync_api import TimeoutError as PWTimeout

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT = Path(__file__).resolve().parent.parent
TESTDATA = ROOT / ".testdata-pwa"
PORT = "8012"
BASE = f"http://127.0.0.1:{PORT}"
SHOPPING = TESTDATA / "data" / "shopping_list.json"
FAVORITES = TESTDATA / "data" / "favorites.json"
FAVORITE_IMAGE = "offline-spaghetti.png"

# --- fresh throwaway data ---
if TESTDATA.exists():
    shutil.rmtree(TESTDATA)
TESTDATA.mkdir()
shutil.copytree(ROOT / "recipes", TESTDATA / "recipes")
shutil.copytree(ROOT / "data", TESTDATA / "data")
(TESTDATA / "images" / "_favorites").mkdir(parents=True)
shutil.copy2(
    next((ROOT / "images" / "spaghetti-carbonara").glob("*.png")),
    TESTDATA / "images" / "_favorites" / FAVORITE_IMAGE,
)
FAVORITES.write_text(json.dumps({"needs": [{
    "id": "spaghetti", "name": "Spaghetti", "aliases": ["200 g Spaghetti"],
    "created_at": "2026-01-01T00:00:00.000Z", "products": [{
        "id": "de-cecco", "name": "De Cecco Spaghetti n. 12",
        "brand": "De Cecco", "store": "REWE", "note": "Bleibt schön bissfest",
        "image_filename": FAVORITE_IMAGE,
        "created_at": "2026-01-01T00:00:00.000Z",
    }],
}]}, ensure_ascii=False), encoding="utf-8")

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
            return True
        except Exception:
            if srv.poll() is not None:
                return False
            time.sleep(0.2)
    return False

# --- Helpers --------------------------------------------------------------

def past_iso():
    """A clearly-in-the-past, server-compatible timestamp so later client
    changes reliably win on merge."""
    return "2026-01-01T00:00:00Z"

def now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

def mk(text, ts):
    return {"id": text.lower(), "text": text, "quantity": "", "checked": False,
            "source": None, "created_at": ts, "updated_at": ts, "deleted": False}

def reset_shopping():
    SHOPPING.write_text('{"items": []}\n', encoding="utf-8")

def seed_shopping(items):
    SHOPPING.write_text(json.dumps({"items": items}, ensure_ascii=False), encoding="utf-8")

def api_get():
    with urllib.request.urlopen(BASE + "/api/shopping") as r:
        return json.loads(r.read())["items"]

def api_sync(items):
    req = urllib.request.Request(
        BASE + "/api/shopping/sync",
        data=json.dumps({"items": items}).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())["items"]

def api_favorites():
    with urllib.request.urlopen(BASE + "/api/favorites") as r:
        return json.loads(r.read())["needs"]

def wait_server(pred, timeout=8.0):
    end = time.time() + timeout
    while time.time() < end:
        try:
            if pred(api_get()):
                return True
        except Exception:
            pass
        time.sleep(0.2)
    return False

def wait_count(page, selector, n, timeout=8000):
    try:
        page.wait_for_function(
            "([s, n]) => document.querySelectorAll(s).length === n",
            arg=[selector, n], timeout=timeout)
        return True
    except PWTimeout:
        return False

def sw_ready(page, timeout_ms=6000):
    return page.evaluate(
        """async (to) => {
            if (!('serviceWorker' in navigator)) return false;
            const ready = navigator.serviceWorker.ready.then(r => !!r.active);
            const t = new Promise(res => setTimeout(() => res(false), to));
            return await Promise.race([ready, t]);
        }""", timeout_ms)

try:
    if not wait_up():
        print("!! server not reachable (uvicorn start failed)")
        raise SystemExit(2)

    # --- 1) sync API directly ------------------------------------------------
    # Strictly increasing timestamps so "last writer wins" is deterministic.
    reset_shopping()
    check(api_get() == [], "GET /api/shopping: empty list at start")
    check(api_favorites()[0]["products"][0]["image_url"].startswith("/media/favorite/"),
          "GET /api/favorites: ranked catalog includes its product image URL")

    def itm(ts, **kw):
        base = {"id": "milch", "text": "Milch", "quantity": "", "checked": False,
                "source": None, "created_at": ts, "updated_at": ts,
                "deleted": False}
        base.update(kw)
        return base

    merged = api_sync([itm("2026-06-23T12:00:01Z")])
    check(len(merged) == 1 and merged[0]["text"] == "Milch", "POST sync: new item taken over")
    merged = api_sync([itm("2026-06-23T12:00:02Z", checked=True)])
    check(any(i["id"] == "milch" and i["checked"] for i in merged), "POST sync: newer version wins")
    merged = api_sync([itm("2026-06-23T12:00:00Z", checked=False)])
    check(any(i["id"] == "milch" and i["checked"] for i in merged), "POST sync: older version loses")
    merged = api_sync([itm("2026-06-23T12:00:03Z", deleted=True)])
    check(any(i["id"] == "milch" and i["deleted"] for i in merged), "POST sync: tombstone propagates")

    # New millisecond timestamps must compare correctly against old stored
    # second-precision values during the backwards-compatible transition.
    reset_shopping()
    api_sync([itm("2026-06-23T12:00:00Z", checked=False)])
    merged = api_sync([itm("2026-06-23T12:00:00.001Z", checked=True)])
    check(any(i["id"] == "milch" and i["checked"] for i in merged),
          "POST sync: millisecond update beats old same-second value")

    with sync_playwright() as p:
        browser = p.chromium.launch()

        # --- 2) offline: tick optimistically, online -> sync -----------------
        reset_shopping()
        seed_shopping([mk("Milch", past_iso()), mk("Brot", past_iso())])
        ctx = browser.new_context()
        page = ctx.new_page()
        page.goto(BASE + "/shopping", wait_until="networkidle")
        check(wait_count(page, "#shop-client .shop-item", 2), "online: 2 items loaded from server")

        ctx.set_offline(True)
        page.locator("#shop-client .shop-group:not(.shop-group-done) .shop-box").first.click()
        check(wait_count(page, "#shop-client .shop-group-done .shop-item", 1),
              "offline: checkmark set optimistically (UI)")
        check(all(not i["checked"] for i in api_get()),
              "offline: server still unchanged (no sync)")

        ctx.set_offline(False)
        check(wait_server(lambda its: any(i["checked"] for i in its)),
              "back online: checkmark synced to server")
        ctx.close()

        # --- 3) two-device merge (two contexts = two localStorage) -----------
        reset_shopping()
        ca = browser.new_context(); a = ca.new_page()
        cb = browser.new_context(); b = cb.new_page()
        a.goto(BASE + "/shopping", wait_until="networkidle")
        b.goto(BASE + "/shopping", wait_until="networkidle")

        a.locator("#shop-client .shop-add-panel summary").click()
        a.fill("#shop-client input[name=text]", "Apfel")
        a.locator("#shop-client form.shop-add button").click()
        check(wait_server(lambda its: any(i["text"] == "Apfel" for i in its)),
              "device A: 'Apfel' on server")

        # Immediate follow-up mutations used to reuse the same second and
        # could leave client and server disagreeing indefinitely.
        a.locator("#shop-client .shop-item", has_text="Apfel").locator(".shop-box").click()
        check(wait_server(lambda its: any(i["text"] == "Apfel" and i["checked"] for i in its)),
              "rapid add-then-check reaches the server")

        b.locator("#shop-client .shop-add-panel summary").click()
        b.fill("#shop-client input[name=text]", "Banane")
        b.locator("#shop-client form.shop-add button").click()
        check(wait_server(lambda its: any(i["text"] == "Banane" for i in its)),
              "device B: 'Banane' on server")

        a.reload(wait_until="networkidle")
        b.reload(wait_until="networkidle")
        check(wait_count(a, "#shop-client .shop-item", 2), "device A sees both items")
        check(wait_count(b, "#shop-client .shop-item", 2), "device B sees both items")
        check("Banane" in a.content(), "device A sees 'Banane' (from B)")
        check("Apfel" in b.content(), "device B sees 'Apfel' (from A)")
        ca.close(); cb.close()

        # --- 4) manifest + service worker ------------------------------------
        cp = browser.new_context(); pg = cp.new_page()
        pg.goto(BASE + "/shopping", wait_until="networkidle")
        check(pg.locator("link[rel=manifest]").count() == 1, "manifest is linked")
        with urllib.request.urlopen(BASE + "/static/manifest.webmanifest") as r:
            man = json.loads(r.read())
        check(man.get("start_url") == "/shopping" and len(man.get("icons", [])) >= 2,
              "manifest valid (start_url + icons)")
        with urllib.request.urlopen(BASE + "/static/sw.js") as r:
            sw = r.read().decode("utf-8")
        check("addEventListener" in sw and "caches" in sw, "service worker is served")
        check(sw_ready(pg, 8000) is True, "service worker registered + active")

        # Catalog and its image are cached locally while online, then remain
        # available inside the shopping sheet after a fully offline reload.
        seed_shopping([mk("200 g Spaghetti", past_iso())])
        pg.evaluate("localStorage.removeItem('gusto.shopping')")
        pg.reload(wait_until="networkidle")
        check(wait_count(pg, "#shop-client .shop-item", 1),
              "preferred products: matching shopping item loaded")
        pg.wait_for_function(
            "() => document.querySelector('#shop-client .shop-favorite-hint').textContent.includes('1 Favorit')"
        )
        pg.locator("#shop-client .shop-favorite-trigger").click()
        check(pg.locator("#favorite-sheet .favorite-product-card").count() == 1,
              "preferred products: ranked card opens online")
        check(pg.locator("#favorite-sheet .favorite-product-card img").evaluate(
            "img => img.complete && img.naturalWidth > 0"),
            "preferred products: product image loads online")
        pg.locator("#favorite-sheet .favorite-sheet-close").click()

        # Offline reload: page comes from the SW cache
        cp.set_offline(True)
        served = False
        try:
            resp = pg.reload(wait_until="domcontentloaded")
            served = resp is not None and resp.ok
        except Exception:
            served = False
        check(served, "offline reload served from the SW cache")
        check(pg.locator("#shop-client, #shop-server").count() >= 1,
              "offline-loaded page has the shopping structure")
        pg.locator("#shop-client .shop-favorite-trigger").click()
        check(pg.locator("#favorite-sheet .favorite-product-card").count() == 1,
              "offline: preferred-product ranking remains readable")
        check(pg.locator("#favorite-sheet .favorite-product-card img").evaluate(
            "img => img.complete && img.naturalWidth > 0"),
            "offline: cached product image remains recognizable")
        cp.close()

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
print("ALL PWA CHECKS PASSED.")
