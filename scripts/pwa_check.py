"""PWA / offline / sync check of the shopping list with Playwright.

Extends scripts/browser_check.py with the phase-2 flows (PWA + offline + sync):
- live sync, in-flight changes and bounded retry through real browser requests
- offline: load online -> set_offline(True) -> tick optimistically -> back
  online -> sync verifies the change reaches the server
- two-device merge (two browser contexts = two localStorage)
- manifest linked/valid + service worker served/active + offline reload from the
  SW cache

Like browser_check.py everything runs against a THROWAWAY copy of the data
(GUSTO_HOME -> a unique .tmp/pwa-* directory); real data stays untouched.

Usage:  .venv/Scripts/python.exe scripts/pwa_check.py
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
from datetime import datetime, timezone
from pathlib import Path

from playwright.sync_api import sync_playwright
from playwright.sync_api import TimeoutError as PWTimeout

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import gusto  # noqa: E402, F401 - configure temp for this process and its children

TESTDATA = Path(tempfile.mkdtemp(prefix="pwa-"))
print(f"PWA test data: {TESTDATA}", flush=True)
with socket.socket() as port_socket:
    port_socket.bind(("127.0.0.1", 0))
    PORT = str(port_socket.getsockname()[1])
BASE = f"http://127.0.0.1:{PORT}"

# --- fresh throwaway data ---
shutil.copytree(ROOT / "recipes", TESTDATA / "recipes")
shutil.copytree(ROOT / "data", TESTDATA / "data")
shutil.copytree(ROOT / "images", TESTDATA / "images")
os.environ["GUSTO_HOME"] = str(TESTDATA)
import gusto.core.favorites as core_favorites  # noqa: E402
import gusto.core.records as core_records  # noqa: E402
import gusto.core.shopping as core_shopping  # noqa: E402

core_favorites.favorites_save([])
need = core_favorites.favorite_add_need("Spaghetti", aliases=["200 g Spaghetti"])
core_favorites.favorite_add_product(
    need.id, "De Cecco Spaghetti n. 12", brand="De Cecco", store="REWE",
    note="Bleibt schön bissfest",
    image=next((ROOT / "images" / "spaghetti-carbonara").glob("*.png")),
)

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
    core_shopping.shopping_save([])

def seed_shopping(items):
    core_shopping.shopping_save([core_records.ShoppingItem.from_dict(item) for item in items])

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

def wait_item_once(page, text, timeout=8000):
    """Wait for one item and reject accidental duplicate renders."""
    try:
        page.wait_for_function(
            "(t) => Array.from(document.querySelectorAll('#shop-client .shop-item .shop-text'))"
            ".filter(e => e.textContent.trim() === t).length === 1",
            arg=text, timeout=timeout)
        return True
    except PWTimeout:
        return False

PROBE = """(() => {
    window.gustoProbe = {open: false, revisions: [], pending: 0, responses: 0};
    const NativeEvents = window.EventSource;
    window.EventSource = class extends NativeEvents {
        constructor(...args) {
            super(...args);
            this.addEventListener('open', () => { window.gustoProbe.open = true; });
            this.addEventListener('message', event => {
                window.gustoProbe.revisions.push(JSON.parse(event.data).revision);
            });
        }
    };
    const nativeFetch = window.fetch;
    window.fetch = function (...args) {
        if (!String(args[0]).includes('/api/shopping/sync')) return nativeFetch(...args);
        window.gustoProbe.pending++;
        const done = () => {
            window.gustoProbe.responses++;
            setTimeout(() => { window.gustoProbe.pending--; }, 0);
        };
        return nativeFetch(...args).then(response => {
            response.clone().text().then(done, done);
            return response;
        }, error => { done(); throw error; });
    };
})();"""


def new_context(browser, **kwargs):
    context = browser.new_context(**kwargs)
    context.add_init_script(PROBE)
    return context


def drain_and_close(context):
    # Stop new sends, then consume existing replies before replacing fixtures.
    # Closing a page alone can abort a request while its server write continues.
    context.route("**/api/shopping/sync", lambda route: route.abort())
    for page in context.pages:
        page.wait_for_function("() => window.gustoProbe.pending === 0")
    context.close()


def live_ready(page):
    page.wait_for_function("() => window.gustoProbe.open")


def wait_clock_condition(page, expression, timeout=3):
    """Poll observable state while application timers deliberately stay paused."""
    deadline = time.monotonic() + timeout
    while not page.evaluate(expression):
        if time.monotonic() >= deadline:
            raise AssertionError(f"browser condition did not settle: {expression}")
        page.clock.run_for(0)
        page.wait_for_timeout(10)

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

    def itm(ts, **kw):
        base = {"id": "milch", "text": "Milch", "quantity": "", "checked": False,
                "source": None, "created_at": ts, "updated_at": ts,
                "deleted": False}
        base.update(kw)
        return base

    with sync_playwright() as p:
        browser = p.chromium.launch()

        # --- 2) live server change -> existing client merges -----------------
        reset_shopping()
        seed_shopping([mk("Milch", past_iso())])
        live_context = new_context(browser)
        live_page = live_context.new_page()
        live_page.goto(BASE + "/shopping", wait_until="load")
        check(wait_count(live_page, "#shop-client .shop-item", 1),
              "live: initial server item loaded")
        # app.js intentionally waits until the initial navigation has settled
        # before opening its long-lived EventSource.
        live_ready(live_page)
        api_sync([*api_get(), mk("Brot", now_iso())])
        check(wait_count(live_page, "#shop-client .shop-item", 2),
              "live: server mutation reaches the open PWA without reload")
        check("Brot" in live_page.content(),
              "live: merged server item is rendered")
        drain_and_close(live_context)

        # --- 3) offline: tick optimistically, online -> sync -----------------
        reset_shopping()
        seed_shopping([mk("Milch", past_iso()), mk("Brot", past_iso())])
        ctx = new_context(browser)
        page = ctx.new_page()
        page.goto(BASE + "/shopping", wait_until="load")
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

        page.locator("#shop-client .shop-remove-done button").click()
        check(wait_count(page, "#shop-client .shop-item", 1),
              "remove-done: only the checked item disappears")
        check(wait_server(
            lambda its: any(i["text"] == "Milch" and i["deleted"] for i in its)
            and any(i["text"] == "Brot" and not i["deleted"] for i in its)
        ), "remove-done: checked-only tombstone reaches the server")

        page.locator("#shop-client .shop-clear-all summary").click()
        page.locator("#shop-client .shop-clear-all button").focus()
        # Force a foreign update while the clear action is open. A late sync
        # response must not collapse the disclosure or move keyboard focus.
        api_sync([*api_get(), mk("Reis", now_iso())])
        page.evaluate("document.dispatchEvent(new CustomEvent('gusto:change', "
                      "{detail: {resources: ['shopping']}}))")
        check(wait_item_once(page, "Reis"),
              "clear panel: foreign change re-renders the open list")
        check(page.locator("#shop-client .shop-clear-all").evaluate(
            "panel => panel.open && panel.querySelector('button') === document.activeElement"
        ), "clear panel: disclosure and keyboard focus survive background sync")
        ctx.set_offline(True)
        page.once("dialog", lambda dialog: dialog.accept())
        page.locator("#shop-client .shop-clear-all button").click()
        check(wait_count(page, "#shop-client .shop-item", 0),
              "offline clear: every visible item disappears optimistically")
        check(any(i["text"] == "Brot" and not i["deleted"] for i in api_get()),
              "offline clear: server stays unchanged until connectivity returns")
        ctx.set_offline(False)
        check(wait_server(lambda its: all(i["deleted"] for i in its)),
              "back online: complete-list tombstones reach the server")
        drain_and_close(ctx)

        # --- 4) two-device merge (two contexts = two localStorage) -----------
        reset_shopping()
        ca = new_context(browser)
        a = ca.new_page()
        cb = new_context(browser)
        b = cb.new_page()
        a.goto(BASE + "/shopping", wait_until="load")
        b.goto(BASE + "/shopping", wait_until="load")

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

        a.reload(wait_until="load")
        b.reload(wait_until="load")
        check(wait_count(a, "#shop-client .shop-item", 2), "device A sees both items")
        check(wait_count(b, "#shop-client .shop-item", 2), "device B sees both items")
        check("Banane" in a.content(), "device A sees 'Banane' (from B)")
        check("Apfel" in b.content(), "device B sees 'Apfel' (from A)")
        drain_and_close(ca)
        drain_and_close(cb)

        # --- 5) manifest + service worker ------------------------------------
        reset_shopping()
        seed_shopping([mk("200 g Spaghetti", past_iso())])
        cp = new_context(browser)
        pg = cp.new_page()
        pg.goto(BASE + "/shopping", wait_until="load")
        check(pg.locator("link[rel=manifest]").count() == 1, "manifest is linked")
        with urllib.request.urlopen(BASE + "/static/manifest.webmanifest") as r:
            man = json.loads(r.read())
        check(man.get("start_url") == "/shopping" and len(man.get("icons", [])) >= 2,
              "manifest valid (start_url + icons)")
        check(sw_ready(pg, 8000) is True, "service worker registered + active")
        pg.wait_for_function("() => !!navigator.serviceWorker.controller")
        icon_assets = pg.evaluate("""async icons => {
            const entries = [
                ...icons.map(icon => ({src: icon.src, size: icon.sizes})),
                ...Array.from(document.querySelectorAll(
                    'link[rel="icon"], link[rel="apple-touch-icon"]'),
                    link => ({src: link.getAttribute('href'), size: link.sizes.value})),
            ];
            const cache = await caches.open((await caches.keys())[0]);
            return Promise.all(entries.map(async entry => {
                const response = await cache.match(entry.src);
                if (!response || !response.ok) return false;
                const image = await createImageBitmap(await response.blob());
                const valid = `${image.width}x${image.height}` === entry.size;
                image.close();
                return valid;
            }));
        }""", man["icons"])
        check(all(icon_assets),
              "PWA, favicon and Apple touch PNGs are precached at their declared sizes")
        with urllib.request.urlopen(BASE + "/static/photo-input.js") as response:
            photo_script = response.read().decode("utf-8")
        cache_result = pg.evaluate("""async () => {
            const keys = await caches.keys();
            const active = await caches.open(keys[0]);
            const precached = await active.match('/static/photo-input.js');
            const old = await caches.open('gusto-test-obsolete');
            await old.put('/static/photo-input.js', new Response('obsolete asset',
                {headers: {'Content-Type': 'application/javascript'}}));
            await active.delete('/static/photo-input.js');
            const current = await fetch('/static/photo-input.js').then(r => r.text());
            for (const path of ['/media/recipe/missing/image', '/media/archive/missing/image']) {
                await active.put(path, new Response('stale moved image'));
            }
            const media = await Promise.all(
                ['/media/recipe/missing/image', '/media/archive/missing/image'].map(
                    async path => { const r = await fetch(path); return [r.status, await r.text()]; }));
            await caches.delete('gusto-test-obsolete');
            return {precached: precached && await precached.text(), current, media};
        }""")
        check(cache_result["precached"] == photo_script,
              "service worker precaches the actual shared photo-picker asset")
        check(cache_result["current"] == photo_script,
              "static fetch ignores an obsolete cache when the active cache misses")
        check(all(status == 404 and body != "stale moved image"
                  for status, body in cache_result["media"]),
              "active and archived recipe images use the network despite seeded stale cache entries")

        # Catalog and its image are cached locally while online, then remain
        # available inside the shopping sheet after a fully offline reload.
        pg.evaluate("localStorage.removeItem('gusto.shopping')")
        pg.reload(wait_until="load")
        check(wait_count(pg, "#shop-client .shop-item", 1),
              "preferred products: matching shopping item loaded")
        pg.wait_for_function(
            "() => document.querySelector('#shop-client .shop-favorite-hint').textContent.includes('1 Favorit')"
        )
        pg.locator("#shop-client .shop-favorite-trigger").click()
        check(pg.locator("#favorite-sheet .favorite-product-card").count() == 1,
              "preferred products: ranked card opens online")
        check(pg.locator("#favorite-sheet .favorite-product-card img").evaluate(
            "async img => { try { await img.decode(); return img.naturalWidth > 0; }"
            " catch (error) { return false; } }"),
            "preferred products: product image loads online")
        pg.locator("#favorite-sheet .favorite-sheet-close").click()

        # Offline reload: page comes from the SW cache
        cp.set_offline(True)
        check(pg.evaluate("""async () => {
            const paths = ['/media/recipe/missing/image', '/media/archive/missing/image'];
            const results = await Promise.all(paths.map(path => fetch(path).then(
                () => false, () => true)));
            return results.every(Boolean);
        }"""), "offline recipe and archive media cannot fall back to stale cached images")
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
        drain_and_close(cp)

        # --- 6) client robustness: uuid fallback, re-render survival, merge ---
        # E1: "Hinzufügen" must work in non-secure contexts (plain HTTP LAN)
        # where crypto.randomUUID does not exist. Match the added item's text
        # exactly so duplicate renders fail too.
        reset_shopping()
        ctx = new_context(browser)
        ctx.add_init_script("delete Crypto.prototype.randomUUID;")
        page = ctx.new_page()
        page.goto(BASE + "/shopping", wait_until="load")
        # Observe the actual subscription before testing live refresh.
        live_ready(page)
        page.locator("#shop-client .shop-add-panel summary").click()
        page.fill("#shop-client input[name=text]", "Toast")
        page.locator("#shop-client form.shop-add button").click()
        check(wait_item_once(page, "Toast"),
              "no randomUUID: added item appears locally")
        check(wait_server(lambda its: any(i["text"] == "Toast" for i in its)),
              "no randomUUID: added item reaches the server")
        drain_and_close(ctx)

        # E2a: re-renders (sync/SSE) must not destroy a half-typed add input.
        reset_shopping()
        sourced_milk = mk("Milch", past_iso())
        sourced_milk["source"] = "rotes-linsen-dal"
        seed_shopping([sourced_milk])
        ctx = new_context(browser)
        page = ctx.new_page()
        page.goto(BASE + "/shopping", wait_until="load")
        check(wait_item_once(page, "Milch"),
              "render survivor: initial item loaded")
        # app.js opens its EventSource only after the initial navigation settled.
        live_ready(page)
        page.locator("#shop-client .shop-add-panel summary").click()
        page.fill("#shop-client input[name=text]", "halb getippt")
        # A foreign server change (API + SSE) triggers a full re-render.
        api_sync([*api_get(), mk("Brot", now_iso())])
        check(wait_item_once(page, "Brot"),
              "render survivor: foreign change re-renders the list")
        check(page.input_value("#shop-client input[name=text]") == "halb getippt",
              "render survivor: half-typed text survives the re-render")

        page.evaluate("window.catalogDraftSurvived = true")
        recipe_snapshot = page.request.post(BASE + "/api/v1/command", data={
            "operation": "recipe.show", "arguments": {"slug": "rotes-linsen-dal"},
        }).json()["result"]
        rename = page.request.post(BASE + "/api/v1/command", data={
            "operation": "recipe.update",
            "arguments": {"slug": "rotes-linsen-dal", "title": "Dal vom anderen Gerät",
                          "revision": recipe_snapshot["revision"]},
        })
        check(rename.ok, "catalog live: foreign recipe rename succeeds")
        page.wait_for_function(
            "() => [...document.querySelectorAll('.shop-source')]"
            ".some(el => el.textContent.includes('Dal vom anderen Gerät'))")
        check(page.evaluate("window.catalogDraftSurvived === true")
              and page.input_value("#shop-client input[name=text]") == "halb getippt",
              "catalog live: source title refresh preserves the draft without reloading")

        milk = page.locator("#shop-client .shop-item", has_text="Milch")
        milk.locator(".shop-box").focus()
        page.keyboard.press("Space")
        page.wait_for_function(
            "() => document.activeElement.matches('.shop-item.is-done .shop-box')"
            " && document.activeElement.closest('.shop-item').dataset.itemId === 'milch'")
        # Inspect identity in one browser turn: a locator handle can refer to
        # the previous row when a queued sync rebuilds it before evaluate runs.
        check(page.evaluate(
            "() => document.activeElement.matches('.shop-item.is-done .shop-box')"
            " && document.activeElement.closest('.shop-item').dataset.itemId === 'milch'"),
              "keyboard: checking an item keeps focus after moving it to completed")
        check(wait_server(lambda its: any(i["text"] == "Milch" and i["checked"] for i in its)),
              "keyboard: checked state reaches the server")
        milk.locator(".shop-favorite-trigger").click()
        page.wait_for_function("() => !document.getElementById('favorite-sheet').hidden")
        api_sync([*api_get(), mk("Salz", now_iso())])
        check(wait_item_once(page, "Salz"), "dialog: background list syncs while the sheet stays open")
        page.keyboard.press("Escape")
        check(page.evaluate(
            "() => document.activeElement.matches('.shop-favorite-trigger')"
            " && document.activeElement.closest('.shop-item').dataset.itemId === 'milch'"),
              "dialog: closing restores focus even after the opener was re-rendered")
        drain_and_close(ctx)

        # E2b: after adding, focus must land on the same, still-connected input.
        reset_shopping()
        ctx = new_context(browser)
        page = ctx.new_page()
        page.goto(BASE + "/shopping", wait_until="load")
        page.locator("#shop-client .shop-add-panel summary").click()
        page.fill("#shop-client input[name=text]", "Apfel")
        page.locator("#shop-client form.shop-add button").click()
        check(wait_item_once(page, "Apfel"),
              "focus return: added item joins the list")
        check(page.evaluate(
            "() => document.activeElement === document.querySelector('#shop-client input[name=text]')"
        ), "focus return: focus is back in the add input after adding")
        check(page.locator("#shop-client details.shop-add-panel").count() == 1,
              "focus return: add panel is never duplicated")
        check(wait_server(lambda its: any(i["text"] == "Apfel" for i in its)),
              "focus return: added item reaches the server")
        drain_and_close(ctx)

        # E3: the client merge must rank a VALID timestamp over a malformed one
        # exactly like the server (_version_key) -- even if the malformed raw
        # string sorts above every ISO value lexicographically.
        reset_shopping()
        ctx = new_context(browser)
        page = ctx.new_page()
        page.goto(BASE + "/shopping", wait_until="load")
        live_ready(page)
        page.evaluate("""() => {
            const item = {id: "merge-x", text: "lokal", quantity: "",
                          checked: false, source: null,
                          created_at: "zzz", updated_at: "zzz", deleted: false};
            localStorage.setItem("gusto.shopping", JSON.stringify({items: [item]}));
        }""")
        # The server holds a NEWER VALID timestamp for the same id; the change
        # event plus an explicit replay-safe dispatch trigger the client sync.
        api_sync([itm(now_iso(), id="merge-x", text="server")])
        page.evaluate(
            "document.dispatchEvent(new CustomEvent('gusto:change', {detail: {resources: ['shopping']}}))")
        try:
            merged_ok = page.wait_for_function(
                """() => {
                    const raw = localStorage.getItem("gusto.shopping") || "{}";
                    const items = (JSON.parse(raw).items) || [];
                    const mine = items.filter(i => i.id === "merge-x");
                    return mine.length === 1 && mine[0].text === "server";
                }""", timeout=6000)
            merged_ok = True
        except PWTimeout:
            merged_ok = False
        check(merged_ok,
              "malformed local timestamp loses to valid server timestamp")
        drain_and_close(ctx)

        # The server's comparison is authoritative even when Date.parse
        # truncates a fraction, parses malformed text, or sees an equal version.
        for label, local_time, server_time in (
            ("sub-millisecond", "2099-01-01T00:00:00.123100Z", "2099-01-01T00:00:00.123900Z"),
            ("date-like malformed", "0", "1990-01-01T00:00:00Z"),
            ("equal version", past_iso(), past_iso()),
        ):
            reset_shopping()
            local = itm(local_time, id="precision", text="local")
            remote = itm(server_time, id="precision", text="server")
            seed_shopping([remote])
            ctx = new_context(browser, service_workers="block")
            ctx.route("**/api/v1/events?*", lambda route: route.abort())
            ctx.add_init_script("localStorage.setItem('gusto.shopping', "
                                + json.dumps(json.dumps({"items": [local]})) + ");")
            page = ctx.new_page()
            page.goto(BASE + "/shopping", wait_until="load")
            check(wait_item_once(page, "server"),
                  f"server comparison: {label} converges to the server winner")
            drain_and_close(ctx)

        for label, version, minimum in (
            ("basic", "20990101T000000Z", "2099-01-01T00:00:00.001Z"),
            ("week date", "2099-W01-1T00:00:00Z", "2098-12-29T00:00:00.001Z"),
            ("sub-millisecond", "2099-01-01T00:00:00.123900Z", "2099-01-01T00:00:00.125Z"),
            ("offset", "2099-01-01T00:00:00+01:30:30.2", "2098-12-31T22:29:29.801Z"),
        ):
            reset_shopping()
            initial = itm(version, id="legacy", text="Legacy")
            seed_shopping([initial])
            ctx = new_context(browser, service_workers="block")
            page = ctx.new_page()
            page.goto(BASE + "/shopping", wait_until="load")
            check(wait_item_once(page, "Legacy"), f"legacy {label}: item loaded")
            page.locator("#shop-client .shop-box").click()
            def advanced(items):
                matching = [i for i in items if i["id"] == "legacy" and i["checked"]]
                if not matching:
                    return False
                return matching[0]["updated_at"] >= minimum
            check(wait_server(advanced),
                  f"legacy {label}: browser edit advances and reaches the server")
            drain_and_close(ctx)

        # E4: failed browser persistence must not erase the current page's
        # server snapshot or offline edits. Quota failures still allow reads
        # of a stale disk copy; blocked storage fails both reads and writes.
        for storage_failure in ("quota", "blocked"):
            reset_shopping()
            seed_shopping([mk("Bestand", past_iso())])
            ctx = new_context(browser)
            ctx.add_init_script("""(() => {
                if (sessionStorage.getItem('gusto.test.storage-restored')) return;
                const read = Storage.prototype.getItem;
                const write = Storage.prototype.setItem;
                window.restoreShoppingStorage = () => {
                    Storage.prototype.getItem = read;
                    Storage.prototype.setItem = write;
                    sessionStorage.setItem('gusto.test.storage-restored', '1');
                };
                if (STORAGE_BLOCKED) {
                    Storage.prototype.getItem = function (key) {
                        if (key === 'gusto.shopping') {
                            throw new DOMException('Storage blocked', 'SecurityError');
                        }
                        return read.call(this, key);
                    };
                }
                Storage.prototype.setItem = function (key, value) {
                    if (key === 'gusto.shopping') {
                        throw new DOMException('Storage unavailable', 'QuotaExceededError');
                    }
                    return write.call(this, key, value);
                };
            })();""".replace("STORAGE_BLOCKED", str(storage_failure == "blocked").lower()))
            page = ctx.new_page()
            page.goto(BASE + "/shopping", wait_until="load")
            check(wait_item_once(page, "Bestand"),
                  f"storage {storage_failure}: server items stay visible")
            check(sw_ready(page, 8000) is True,
                  f"storage {storage_failure}: offline shell is ready")
            ctx.set_offline(True)
            page.locator("#shop-client .shop-add-panel summary").click()
            page.fill("#shop-client input[name=text]", "Offlinezugang")
            page.locator("#shop-client form.shop-add button").click()
            check(wait_item_once(page, "Offlinezugang"),
                  f"storage {storage_failure}: offline add remains in memory")
            added = page.locator("#shop-client .shop-item", has_text="Offlinezugang")
            added.locator(".shop-box").click()
            check(wait_count(page, "#shop-client .shop-group-done .shop-item", 1),
                  f"storage {storage_failure}: offline follow-up edit survives")
            ctx.set_offline(False)
            check(wait_server(lambda its: any(
                i["text"] == "Offlinezugang" and i["checked"] for i in its
            )), f"storage {storage_failure}: buffered edits sync when online")
            page.evaluate("window.restoreShoppingStorage()")
            page.evaluate(
                "document.dispatchEvent(new CustomEvent('gusto:change', "
                "{detail: {resources: ['shopping']}}))")
            page.wait_for_function("""() => {
                const items = JSON.parse(localStorage.getItem('gusto.shopping') || '{}').items || [];
                return items.some(item => item.text === 'Offlinezugang' && item.checked);
            }""")
            ctx.set_offline(True)
            page.reload(wait_until="domcontentloaded")
            check(wait_item_once(page, "Offlinezugang"),
                  f"storage {storage_failure}: recovered storage survives reload")
            page.screenshot(path=str(TESTDATA / f"storage-{storage_failure}.png"))
            drain_and_close(ctx)

        # E5: sync accepts opaque string IDs, including JavaScript property
        # names. Such an item must render and propagate its removal normally.
        reset_shopping()
        api_sync([itm(past_iso(), id="__proto__", text="Sonderkennung")])
        ctx = new_context(browser)
        page = ctx.new_page()
        page.goto(BASE + "/shopping", wait_until="load")
        check(wait_item_once(page, "Sonderkennung"),
              "opaque IDs: __proto__ item appears after client merge")
        page.locator("#shop-client .shop-item", has_text="Sonderkennung").locator(".shop-x").click()
        check(wait_server(lambda its: any(
            i["id"] == "__proto__" and i["deleted"] for i in its
        )), "opaque IDs: __proto__ tombstone syncs back to the server")
        check(wait_count(page, "#shop-client .shop-item", 0),
              "opaque IDs: deleted item stays absent after sync")
        drain_and_close(ctx)

        # E6: a stale visible source can outlive its recipe on an offline
        # device. Reject atomically, explain recovery, and keep other edits.
        reset_shopping()
        stale = itm("2099-01-01T00:00:00.000Z", id="stale-source", text="Milch",
                    source="purged-sync-source")
        api_sync([dict(stale, deleted=True, updated_at=past_iso())])
        valid = itm(past_iso(), id="unsynced-bread", text="Brot")
        ctx = new_context(browser, viewport={"width": 320, "height": 740},
                                  service_workers="block")
        ctx.add_init_script(
            "localStorage.setItem('gusto.shopping', "
            + json.dumps(json.dumps({"items": [stale, valid]})) + ");")
        page = ctx.new_page()
        page.clock.install()
        sync_requests = []
        page.on("request", lambda request: sync_requests.append(request)
                if request.url.endswith("/api/shopping/sync") else None)
        page.goto(BASE + "/shopping", wait_until="load")
        notice = page.locator("#shop-sync-error")
        notice.wait_for(state="visible")
        check("purged-sync-source" in notice.inner_text(),
              "rejected sync: notice identifies the invalid recipe source")
        check(len(api_get()) == 1 and api_get()[0]["deleted"]
              and wait_item_once(page, "Milch") and wait_item_once(page, "Brot"),
              "rejected sync: atomic rejection keeps both local edits")
        page.clock.fast_forward(65000)
        page.evaluate("""() => {
            window.dispatchEvent(new Event('online'));
            document.dispatchEvent(new CustomEvent('gusto:change',
                {detail: {resources: ['shopping']}}));
        }""")
        check(len(sync_requests) == 1,
              "rejected sync: unchanged state is not retried by timers or events")
        check(page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"),
              "rejected sync: recovery notice fits a 320px phone")
        page.screenshot(path=str(TESTDATA / "sync-rejected.png"), full_page=True)
        page.locator("#shop-client .shop-item", has_text="Milch").locator(".shop-x").click()
        check(wait_server(lambda its: any(
            item["id"] == "unsynced-bread" and not item["deleted"] for item in its
        ) and any(item["id"] == "stale-source" and item["deleted"] for item in its)),
              "rejected sync: removing the stale item saves the unrelated offline edit")
        notice.wait_for(state="detached")
        check(wait_item_once(page, "Brot"),
              "rejected sync: successful recovery clears the notice and keeps the valid item")
        drain_and_close(ctx)

        # E7: a reply belongs to the submitted snapshot. Local changes made
        # while it is held must survive, then travel in a follow-up request.
        reset_shopping()
        initial = mk("Bestand", past_iso())
        removable = mk("Entfernen", past_iso())
        seed_shopping([initial, removable])
        ctx = new_context(browser, service_workers="block")
        ctx.add_init_script("localStorage.setItem('gusto.shopping', "
                            + json.dumps(json.dumps({"items": [initial, removable]})) + ");")
        ctx.route("**/api/v1/events?*", lambda route: route.abort())
        held = {}
        requests = []

        def hold_first_sync(route):
            requests.append(route.request.post_data_json)
            if len(requests) == 1:
                held["response"] = route.fetch()
                held["route"] = route
            else:
                route.continue_()

        ctx.route("**/api/shopping/sync", hold_first_sync)
        page = ctx.new_page()
        page.goto(BASE + "/shopping", wait_until="load")
        check(wait_item_once(page, "Bestand") and "response" in held,
              "in-flight edit: the initial sync reply is held after server completion")
        page.locator("#shop-client .shop-item", has_text="Bestand").locator(".shop-box").click()
        page.locator("#shop-client .shop-item", has_text="Entfernen").locator(".shop-x").click()
        page.locator("#shop-client .shop-add-panel summary").click()
        page.fill("#shop-client input[name=text]", "Während der Anfrage")
        page.locator("#shop-client form.shop-add button").click()
        check(len(requests) == 1, "in-flight edit: local changes coalesce behind the pending request")
        held["route"].fulfill(response=held["response"])
        page.wait_for_function("() => window.gustoProbe.pending === 0")
        check(wait_server(lambda items: len(items) == 3
                          and any(i["id"] == initial["id"] and i["checked"] for i in items)
                          and any(i["id"] == removable["id"] and i["deleted"] for i in items)),
              "in-flight edit: old response preserves toggle, removal and addition on the server")
        check(len(requests) == 2 and wait_item_once(page, "Während der Anfrage")
              and page.locator("#shop-client .shop-group-done .shop-item").count() == 1,
              "in-flight edit: exactly one follow-up synchronizes the current local state")
        drain_and_close(ctx)

        # E8: run browser timers explicitly. Deterministic jitter makes the
        # backoff boundaries observable without waiting seconds in real time.
        reset_shopping()
        ctx = new_context(browser, service_workers="block")
        ctx.route("**/api/v1/events?*", lambda route: route.abort())
        ctx.add_init_script("Math.random = () => 0;")
        state = {"calls": 0, "success": False}

        def retry_reply(route):
            state["calls"] += 1
            if state["calls"] == 1:
                state["initial"] = route
                return
            if state["success"]:
                route.continue_()
            else:
                route.fulfill(status=503, content_type="application/json", body='{"error":"busy"}')

        ctx.route("**/api/shopping/sync", retry_reply)
        page = ctx.new_page()
        page.clock.install(time=1_700_000_000_000)
        page.clock.pause_at(1_700_000_000_100)
        page.goto(BASE + "/shopping", wait_until="load")
        page.evaluate("""() => {
            window.retryDelays = [];
            const nativeTimeout = window.setTimeout;
            window.setTimeout = (callback, delay, ...args) => {
                if (delay >= 2000 && delay <= 60000) window.retryDelays.push(delay);
                return nativeTimeout(callback, delay, ...args);
            };
        }""")
        state["initial"].fulfill(status=503, content_type="application/json", body='{"error":"busy"}')
        wait_clock_condition(page, "window.retryDelays.length === 1")
        for index, delay in enumerate((2000, 4000, 8000, 16000, 32000), start=1):
            page.clock.run_for(delay - 1)
            check(state["calls"] == index,
                  f"backoff: retry {index} does not run before its {delay} ms deadline")
            page.clock.run_for(1)
            wait_clock_condition(page, f"window.gustoProbe.responses >= {index + 1}")
            if index < 5:
                wait_clock_condition(page, f"window.retryDelays.length === {index + 1}")
        page.clock.run_for(120000)
        check(state["calls"] == 6 and page.evaluate("window.retryDelays") == [2000, 4000, 8000, 16000, 32000],
              "backoff: initial failure allows five retries then stops")
        state["success"] = True
        with page.expect_response("**/api/shopping/sync") as restored:
            page.evaluate("window.dispatchEvent(new Event('online'))")
        restored.value.body()
        page.clock.run_for(0)
        wait_clock_condition(page, "window.gustoProbe.responses === 7")
        check(state["calls"] == 7, "backoff: online resets the exhausted budget and synchronizes again")
        page.clock.run_for(0)
        page.clock.resume()
        drain_and_close(ctx)

        # A restore must not be undone by an offline device reconnecting. Keep
        # that device's unsent entries available as a separate download.
        from gusto.core import backup

        reset_shopping()
        seed_shopping([mk("Aus Sicherung", past_iso())])
        archive = backup.create_backup()
        ctx = new_context(browser, viewport={"width": 390, "height": 844})
        page = ctx.new_page()
        page.goto(BASE + "/shopping", wait_until="load")
        check(wait_item_once(page, "Aus Sicherung"), "restore: device has the original list")
        page.wait_for_function("() => window.gustoProbe.pending === 0")
        ctx.set_offline(True)
        page.locator("#shop-client .shop-add-panel summary").click()
        page.fill("#shop-client input[name=text]", "Alte Offline-Notiz")
        page.locator("#shop-client form.shop-add button").click()
        preview = backup.preview_restore(archive)
        backup.restore_backup(archive, preview["token"])
        ctx.set_offline(False)
        page.wait_for_selector("#shop-restore-notice")
        page.wait_for_function("() => window.gustoProbe.pending === 0")
        check([entry["text"] for entry in api_get()] == ["Aus Sicherung"]
              and page.locator("#shop-client .shop-text", has_text="Alte Offline-Notiz").count() == 0,
              "restore: reconnect does not upload old offline changes")
        check(page.evaluate("JSON.parse(localStorage.getItem('gusto.shopping.before-restore'))"
                            ".some(snapshot => snapshot.items.some(item => item.text === 'Alte Offline-Notiz'))"),
              "restore: the previous local snapshot is retained separately")
        with page.expect_download() as exported:
            page.get_by_role("button", name="Vorherige lokale Listen herunterladen").click()
        check("Alte Offline-Notiz" in Path(exported.value.path()).read_text(encoding="utf-8"),
              "restore: the user can recover the previous local list through a download")
        page.screenshot(path=str(TESTDATA / "restore-shopping-mobile.png"), full_page=True)
        drain_and_close(ctx)

        # A late success from before a restore cannot move a shared browser
        # store back to its previous generation or erase another tab's draft.
        ctx = new_context(browser, service_workers="block")
        ctx.route("**/api/v1/events?*", lambda route: route.abort())
        before_restore = core_shopping.shopping_exchange([])
        ctx.add_init_script("localStorage.setItem('gusto.shopping', "
                            + json.dumps(json.dumps(before_restore)) + ");")
        page = ctx.new_page()
        held_restore = {}

        def hold_before_restore(route):
            if "response" not in held_restore:
                held_restore["response"] = route.fetch()
                held_restore["route"] = route
            else:
                route.continue_()

        page.route("**/api/shopping/sync", hold_before_restore)
        page.goto(BASE + "/shopping", wait_until="load")
        preview = backup.preview_restore(archive)
        backup.restore_backup(archive, preview["token"])
        current = core_shopping.shopping_exchange([])
        current["items"].append(mk("Neuer Entwurf", now_iso()))
        page.evaluate("state => localStorage.setItem('gusto.shopping', JSON.stringify(state))", current)
        held_restore["route"].fulfill(response=held_restore["response"])
        check(wait_item_once(page, "Neuer Entwurf")
              and wait_server(lambda items: any(i["text"] == "Neuer Entwurf" for i in items)),
              "restore: late response preserves another tab's new-generation draft")
        check(page.evaluate("JSON.parse(localStorage.getItem('gusto.shopping')).generation")
              == current["generation"], "restore: late response cannot reverse the generation")
        drain_and_close(ctx)

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
