"""PWA-/Offline-/Sync-Check der Einkaufsliste mit Playwright.

Ergaenzt scripts/browser_check.py um die Phase-2-Flows (PWA + Offline + Sync):
- Sync-API (GET /api/einkauf, POST /api/einkauf/sync, "letzter gewinnt")
- Offline: online laden -> set_offline(True) -> Haekchen optimistisch ->
  wieder online -> Sync prueft, dass die Aenderung am Server ankommt
- Zwei-Geraete-Merge (zwei Browser-Kontexte = zwei localStorage)
- Manifest verlinkt/gueltig + Service-Worker ausgeliefert/aktiv + Offline-Reload
  aus dem SW-Cache

Wie browser_check.py laeuft alles gegen eine WEGWERF-Kopie der Daten
(RECIPE_HOME -> .testdata-pwa); die echten Daten bleiben unberuehrt.

Aufruf:  .venv/Scripts/python.exe scripts/pwa_check.py
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
EINKAUF = TESTDATA / "data" / "einkaufsliste.json"

# --- frische Wegwerf-Daten ---
if TESTDATA.exists():
    shutil.rmtree(TESTDATA)
TESTDATA.mkdir()
shutil.copytree(ROOT / "recipes", TESTDATA / "recipes")
shutil.copytree(ROOT / "data", TESTDATA / "data")

env = {**os.environ, "RECIPE_HOME": str(TESTDATA)}
srv = subprocess.Popen(
    [sys.executable, "-m", "uvicorn", "recipe.web:app",
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

# --- Helfer ---------------------------------------------------------------

def past_iso():
    """Ein klar in der Vergangenheit liegender, serverkonformer Zeitstempel,
    damit spaetere Client-Aenderungen beim Merge sicher gewinnen."""
    return "2026-01-01T00:00:00Z"

def now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

def mk(text, ts):
    return {"id": text.lower(), "text": text, "menge": "", "checked": False,
            "quelle": None, "erstellt_am": ts, "geaendert_am": ts, "geloescht": False}

def reset_einkauf():
    EINKAUF.write_text('{"items": []}\n', encoding="utf-8")

def seed_einkauf(items):
    EINKAUF.write_text(json.dumps({"items": items}, ensure_ascii=False), encoding="utf-8")

def api_get():
    with urllib.request.urlopen(BASE + "/api/einkauf") as r:
        return json.loads(r.read())["items"]

def api_sync(items):
    req = urllib.request.Request(
        BASE + "/api/einkauf/sync",
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
        print("!! Server nicht erreichbar (uvicorn-Start fehlgeschlagen)")
        raise SystemExit(2)

    # --- 1) Sync-API direkt --------------------------------------------------
    # Strikt steigende Zeitstempel, damit "letzter gewinnt" deterministisch ist.
    reset_einkauf()
    check(api_get() == [], "GET /api/einkauf: leere Liste am Start")

    def itm(ts, **kw):
        base = {"id": "milch", "text": "Milch", "menge": "", "checked": False,
                "quelle": None, "erstellt_am": ts, "geaendert_am": ts,
                "geloescht": False}
        base.update(kw)
        return base

    merged = api_sync([itm("2026-06-23T12:00:01Z")])
    check(len(merged) == 1 and merged[0]["text"] == "Milch", "POST sync: neues Item uebernommen")
    merged = api_sync([itm("2026-06-23T12:00:02Z", checked=True)])
    check(any(i["id"] == "milch" and i["checked"] for i in merged), "POST sync: neuere Version gewinnt")
    merged = api_sync([itm("2026-06-23T12:00:00Z", checked=False)])
    check(any(i["id"] == "milch" and i["checked"] for i in merged), "POST sync: aeltere Version verliert")
    merged = api_sync([itm("2026-06-23T12:00:03Z", geloescht=True)])
    check(any(i["id"] == "milch" and i["geloescht"] for i in merged), "POST sync: Tombstone propagiert")

    with sync_playwright() as p:
        browser = p.chromium.launch()

        # --- 2) Offline: optimistisch abhaken, online -> Sync ----------------
        reset_einkauf()
        seed_einkauf([mk("Milch", past_iso()), mk("Brot", past_iso())])
        ctx = browser.new_context()
        page = ctx.new_page()
        page.goto(BASE + "/einkauf", wait_until="networkidle")
        check(wait_count(page, "#eink-client .eink-item", 2), "Online: 2 Items vom Server geladen")

        ctx.set_offline(True)
        page.locator("#eink-client .eink-group:not(.eink-group-done) .eink-box").first.click()
        check(wait_count(page, "#eink-client .eink-group-done .eink-item", 1),
              "Offline: Haekchen optimistisch gesetzt (UI)")
        check(all(not i["checked"] for i in api_get()),
              "Offline: Server noch unveraendert (kein Sync)")

        ctx.set_offline(False)
        check(wait_server(lambda its: any(i["checked"] for i in its)),
              "Wieder online: Haekchen zum Server gesynct")
        ctx.close()

        # --- 3) Zwei-Geraete-Merge (zwei Kontexte = zwei localStorage) -------
        reset_einkauf()
        ca = browser.new_context(); a = ca.new_page()
        cb = browser.new_context(); b = cb.new_page()
        a.goto(BASE + "/einkauf", wait_until="networkidle")
        b.goto(BASE + "/einkauf", wait_until="networkidle")

        a.fill("#eink-client input[name=text]", "Apfel")
        a.locator("#eink-client form.eink-add button").click()
        check(wait_server(lambda its: any(i["text"] == "Apfel" for i in its)),
              "Geraet A: 'Apfel' am Server")

        b.fill("#eink-client input[name=text]", "Banane")
        b.locator("#eink-client form.eink-add button").click()
        check(wait_server(lambda its: any(i["text"] == "Banane" for i in its)),
              "Geraet B: 'Banane' am Server")

        a.reload(wait_until="networkidle")
        b.reload(wait_until="networkidle")
        check(wait_count(a, "#eink-client .eink-item", 2), "Geraet A sieht beide Items")
        check(wait_count(b, "#eink-client .eink-item", 2), "Geraet B sieht beide Items")
        check("Banane" in a.content(), "Geraet A sieht 'Banane' (von B)")
        check("Apfel" in b.content(), "Geraet B sieht 'Apfel' (von A)")
        ca.close(); cb.close()

        # --- 4) Manifest + Service-Worker ------------------------------------
        cp = browser.new_context(); pg = cp.new_page()
        pg.goto(BASE + "/einkauf", wait_until="networkidle")
        check(pg.locator("link[rel=manifest]").count() == 1, "Manifest ist verlinkt")
        with urllib.request.urlopen(BASE + "/static/manifest.webmanifest") as r:
            man = json.loads(r.read())
        check(man.get("start_url") == "/einkauf" and len(man.get("icons", [])) >= 2,
              "Manifest gueltig (start_url + Icons)")
        with urllib.request.urlopen(BASE + "/static/sw.js") as r:
            sw = r.read().decode("utf-8")
        check("addEventListener" in sw and "caches" in sw, "Service-Worker wird ausgeliefert")
        check(sw_ready(pg, 8000) is True, "Service-Worker registriert + aktiv")

        # Offline-Reload: Seite kommt aus dem SW-Cache
        cp.set_offline(True)
        served = False
        try:
            resp = pg.reload(wait_until="domcontentloaded")
            served = resp is not None and resp.ok
        except Exception:
            served = False
        check(served, "Offline-Reload wird aus dem SW-Cache bedient")
        check(pg.locator("#eink-client, #eink-server").count() >= 1,
              "Offline geladene Seite hat die Einkauf-Struktur")
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
    print(f"{len(fails)} CHECK(S) FEHLGESCHLAGEN:")
    for f in fails:
        print("   - " + f)
    sys.exit(1)
print("ALLE PWA-CHECKS BESTANDEN.")
