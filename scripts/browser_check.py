"""End-to-End-Check der Web-UI mit Playwright.

Startet einen eigenen uvicorn-Server gegen eine WEGWERF-Kopie der Daten
(RECIPE_HOME -> .testdata), klickt die App im echten Chromium durch, macht
Screenshots und prueft die wichtigsten Flows. Die echten Beispieldaten bleiben
unberuehrt.

Aufruf:  .venv/Scripts/python.exe scripts/browser_check.py
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

SHOTS.mkdir(exist_ok=True)

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
            return True  # Server antwortet (auch mit Fehlerseite) -> er laeuft
        except Exception:
            if srv.poll() is not None:
                return False
            time.sleep(0.2)
    return False

try:
    if not wait_up():
        print("!! Server nicht erreichbar (uvicorn-Start fehlgeschlagen)")
        raise SystemExit(2)

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        page.on("dialog", lambda d: d.accept())  # confirm() beim Loeschen

        # Startseite
        page.goto(BASE, wait_until="networkidle")
        check("Küchenbuch" in page.content(), "Startseite zeigt die Marke")
        check(page.locator(".card").count() == 3, "3 Rezeptkarten sichtbar")
        page.screenshot(path=str(SHOTS / "01_home.png"), full_page=True)

        # Suche (durchsucht auch Zutaten im Text)
        page.goto(BASE + "/?q=kokos", wait_until="networkidle")
        check(page.locator(".card").count() == 1, "Suche 'kokos' -> 1 Treffer (Dal)")
        page.screenshot(path=str(SHOTS / "02_search.png"), full_page=True)

        # Tag-Filter
        page.goto(BASE + "/?tag=vegan", wait_until="networkidle")
        check(page.locator(".card").count() == 1, "Tag-Filter 'vegan' -> 1 Treffer")

        # Rezeptseite
        page.goto(BASE, wait_until="networkidle")
        page.locator(".card-title", has_text="Carbonara").click()
        page.wait_for_load_state("networkidle")
        check("Zutaten" in page.content() and "Zubereitung" in page.content(),
              "Rezept zeigt Zutaten + Zubereitung")
        check(page.locator(".recipe-body ol li").count() >= 4, "Schritte als Liste gerendert")
        page.screenshot(path=str(SHOTS / "03_recipe.png"), full_page=True)

        # "Heute gekocht" -> Logbuch
        page.locator("button.btn", has_text="Heute gekocht").click()
        page.wait_for_load_state("networkidle")
        page.goto(BASE + "/logbuch", wait_until="networkidle")
        check(page.locator(".timeline li").count() >= 3, "Logbuch hat neuen Eintrag")
        page.screenshot(path=str(SHOTS / "04_log.png"), full_page=True)

        # Vorschlaege
        page.goto(BASE + "/vorschlaege", wait_until="networkidle")
        check("Was koche ich" in page.content(), "Vorschlagsseite laedt")
        page.screenshot(path=str(SHOTS / "05_suggest.png"), full_page=True)

        # Neues Rezept anlegen
        page.goto(BASE + "/neu", wait_until="networkidle")
        page.fill("input[name=titel]", "Test Pfannkuchen")
        page.fill("input[name=tags]", "test, suess")
        page.fill("input[name=dauer]", "20")
        page.fill("input[name=portionen]", "2")
        page.fill("textarea[name=inhalt]",
                  "## Zutaten\n\n- 2 Eier\n- 250 ml Milch\n- 150 g Mehl\n\n"
                  "## Zubereitung\n\n1. Alles verruehren.\n2. In der Pfanne backen.")
        page.locator("button.btn", has_text="Speichern").click()
        page.wait_for_load_state("networkidle")
        check("Test Pfannkuchen" in page.content(), "Neues Rezept angelegt")
        check(page.url.endswith("/rezept/test-pfannkuchen"), "Slug korrekt erzeugt")
        page.screenshot(path=str(SHOTS / "06_new.png"), full_page=True)

        # Bearbeiten (Slug bleibt stabil)
        page.goto(BASE + "/rezept/test-pfannkuchen/bearbeiten", wait_until="networkidle")
        page.fill("input[name=titel]", "Test Pfannkuchen Deluxe")
        page.locator("button.btn", has_text="Speichern").click()
        page.wait_for_load_state("networkidle")
        check("Deluxe" in page.content(), "Rezept bearbeitet")

        # Loeschen (raeumt das Testrezept wieder weg)
        page.locator("button.btn-text", has_text="Löschen").click()
        page.wait_for_load_state("networkidle")
        check("Test Pfannkuchen" not in page.content(), "Rezept geloescht")

        # 404
        resp = page.goto(BASE + "/rezept/gibtsnicht")
        check(resp.status == 404, "Unbekanntes Rezept -> 404-Seite")

        # --- Einkaufsliste ---
        page.goto(BASE, wait_until="networkidle")
        check(page.locator(".nav a", has_text="Einkauf").count() >= 1, "Nav-Link zur Einkaufsliste")

        # Zutaten eines Rezepts auf die Einkaufsliste schicken
        page.goto(BASE + "/rezept/spaghetti-carbonara", wait_until="networkidle")
        page.locator("form[action$='/einkauf'] button").click()
        page.wait_for_load_state("networkidle")
        check(page.url.endswith("/einkauf"), "Knopf fuehrt auf /einkauf")
        check(page.locator(".eink-item").count() == 6, "6 Carbonara-Zutaten auf der Liste")
        page.screenshot(path=str(SHOTS / "09_einkauf.png"), full_page=True)

        # Manuell etwas hinzufuegen
        page.fill("input[name=text]", "Backpapier")
        page.fill("input[name=menge]", "1 Rolle")
        page.locator("form.eink-add button[type=submit]").click()
        page.wait_for_load_state("networkidle")
        check(page.locator(".eink-item").count() == 7, "Manuelles Item hinzugefuegt -> 7")
        check("Backpapier" in page.content(), "Manuelles Item sichtbar")

        # Ein Item abhaken -> wandert nach 'Erledigt'
        page.locator(".eink-group:not(.eink-group-done) .eink-box").first.click()
        page.wait_for_load_state("networkidle")
        check(page.locator(".eink-group-done .eink-item").count() == 1, "Ein Item ist erledigt")

        # Erledigte entfernen (Tombstone, bleibt aus der Ansicht)
        page.locator("form.eink-clear button").click()
        page.wait_for_load_state("networkidle")
        check(page.locator(".eink-group-done").count() == 0, "Keine Erledigt-Gruppe mehr")
        check(page.locator(".eink-item").count() == 6, "Wieder 6 offene Items")
        page.screenshot(path=str(SHOTS / "10_einkauf_after.png"), full_page=True)

        # Mobile-Ansichten
        m = browser.new_page(viewport={"width": 390, "height": 844}, device_scale_factor=2)
        m.goto(BASE, wait_until="networkidle")
        m.screenshot(path=str(SHOTS / "07_mobile_home.png"), full_page=True)
        m.goto(BASE + "/rezept/rotes-linsen-dal", wait_until="networkidle")
        m.screenshot(path=str(SHOTS / "08_mobile_recipe.png"), full_page=True)
        m.goto(BASE + "/einkauf", wait_until="networkidle")
        m.screenshot(path=str(SHOTS / "11_mobile_einkauf.png"), full_page=True)

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
print("ALLE CHECKS BESTANDEN.")
