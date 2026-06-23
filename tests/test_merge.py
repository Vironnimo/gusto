"""Hermetische Tests fuer den Voll-State-Sync core.einkauf_merge.

Lauffaehig OHNE pytest:  python tests/test_merge.py

Deckt die Merge-Regel aus docs/sync-kontrakt.md ab ("letzter gewinnt" pro id +
Tombstones, Voll-State).

WICHTIG: RECIPE_HOME wird ganz oben auf ein frisches Temp-Verzeichnis gesetzt,
BEVOR recipe.core importiert oder eine Funktion aufgerufen wird. Sonst wuerden
die echten Daten unter recipes/ und data/ veraendert – das ist verboten.
"""
import os
import sys
import tempfile

# --- Hermetik: RECIPE_HOME auf ein Wegwerf-Verzeichnis, VOR dem Import -------
os.environ["RECIPE_HOME"] = tempfile.mkdtemp(prefix="gusto-merge-test-")

# Projektwurzel in den Pfad, damit `recipe` importierbar ist, egal von wo aus
# das Skript gestartet wird.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from recipe import core  # noqa: E402

checks = 0


def pruefe(bedingung, nachricht):
    global checks
    assert bedingung, nachricht
    checks += 1


def item(id, geaendert_am, *, text="x", checked=False, geloescht=False,
         erstellt_am="2026-06-23T18:00:00Z", menge="", quelle=None) -> dict:
    """Ein rohes Item-Dict (so wie es vom Client kaeme)."""
    return {
        "id": id, "text": text, "menge": menge, "checked": checked,
        "quelle": quelle, "erstellt_am": erstellt_am,
        "geaendert_am": geaendert_am, "geloescht": geloescht,
    }


def reset_lokal(items: list[dict]) -> None:
    """Lokalen Stand frisch setzen (ueber einkauf_save)."""
    core.einkauf_save([core.EinkaufItem.from_dict(d) for d in items])


def by_id(items: list[core.EinkaufItem]) -> dict[str, core.EinkaufItem]:
    return {i.id: i for i in items}


# --- 1) Leeres lokal + remote-Items -> uebernommen --------------------------
reset_lokal([])
ergebnis = core.einkauf_merge([item("a", "2026-06-23T18:00:00Z", text="Milch")])
m = by_id(ergebnis)
pruefe(len(ergebnis) == 1, "leeres lokal: genau 1 Item nach Merge")
pruefe("a" in m and m["a"].text == "Milch", "leeres lokal: remote-Item uebernommen")

# Rueckgabetyp ist EinkaufItem (nicht dict).
pruefe(isinstance(ergebnis[0], core.EinkaufItem), "Rueckgabe besteht aus EinkaufItem")


# --- 2) gleiche id, remote neuer (groesseres geaendert_am) -> remote gewinnt -
reset_lokal([item("a", "2026-06-23T18:00:00Z", text="alt", checked=False)])
ergebnis = core.einkauf_merge([item("a", "2026-06-23T19:00:00Z", text="neu", checked=True)])
m = by_id(ergebnis)
pruefe(len(ergebnis) == 1, "remote neuer: weiterhin 1 Item")
pruefe(m["a"].text == "neu" and m["a"].checked is True, "remote neuer: remote gewinnt")


# --- 3) gleiche id, remote aelter -> lokal bleibt ---------------------------
reset_lokal([item("a", "2026-06-23T19:00:00Z", text="lokal-neu")])
ergebnis = core.einkauf_merge([item("a", "2026-06-23T18:00:00Z", text="remote-alt")])
m = by_id(ergebnis)
pruefe(m["a"].text == "lokal-neu", "remote aelter: lokale Version bleibt")


# --- 4) Gleichstand geaendert_am -> lokal bleibt ----------------------------
reset_lokal([item("a", "2026-06-23T18:00:00Z", text="lokal")])
ergebnis = core.einkauf_merge([item("a", "2026-06-23T18:00:00Z", text="remote")])
m = by_id(ergebnis)
pruefe(m["a"].text == "lokal", "Gleichstand: lokale Version bleibt")


# --- 5) remote-Tombstone neuer -> Item wird Tombstone -----------------------
reset_lokal([item("a", "2026-06-23T18:00:00Z", text="da", geloescht=False)])
ergebnis = core.einkauf_merge([item("a", "2026-06-23T19:00:00Z", geloescht=True)])
m = by_id(ergebnis)
pruefe(m["a"].geloescht is True, "remote-Tombstone neuer: Item wird Tombstone")
# Tombstone ist Teil der Rueckgabe (nicht herausgefiltert).
pruefe(len(ergebnis) == 1, "Tombstone bleibt in der Rueckgabe enthalten")

# Gegenprobe: lokaler Tombstone, remote aelter+lebendig -> Tombstone bleibt.
reset_lokal([item("a", "2026-06-23T19:00:00Z", geloescht=True)])
ergebnis = core.einkauf_merge([item("a", "2026-06-23T18:00:00Z", geloescht=False)])
m = by_id(ergebnis)
pruefe(m["a"].geloescht is True, "lokaler Tombstone neuer: bleibt geloescht")


# --- 6) nur-lokale id bleibt erhalten ---------------------------------------
reset_lokal([item("lokal-only", "2026-06-23T18:00:00Z", text="nur lokal")])
ergebnis = core.einkauf_merge([item("remote-only", "2026-06-23T18:00:00Z", text="nur remote")])
m = by_id(ergebnis)
pruefe("lokal-only" in m, "nur-lokale id bleibt erhalten")
pruefe("remote-only" in m, "nur-remote id wird uebernommen")
pruefe(len(ergebnis) == 2, "Union beider ids im Ergebnis")


# --- 7) Rueckgabe enthaelt Tombstones (gemischter Stand) --------------------
reset_lokal([
    item("offen", "2026-06-23T18:00:00Z"),
    item("tot", "2026-06-23T18:00:00Z", geloescht=True),
])
ergebnis = core.einkauf_merge([])
m = by_id(ergebnis)
pruefe("tot" in m and m["tot"].geloescht is True,
       "leerer Remote-Stand: lokale Tombstones bleiben in der Rueckgabe")
pruefe(len(ergebnis) == 2, "leerer Remote-Stand: lokaler Stand vollstaendig zurueck")


# --- 8) Ergebnis ist persistiert (einkauf_load nach merge) ------------------
reset_lokal([item("a", "2026-06-23T18:00:00Z", text="alt")])
core.einkauf_merge([
    item("a", "2026-06-23T19:00:00Z", text="neu"),
    item("b", "2026-06-23T19:00:00Z", text="frisch", geloescht=True),
])
geladen = by_id(core.einkauf_load())
pruefe(geladen["a"].text == "neu", "persistiert: gemergte Aenderung auf Platte")
pruefe("b" in geladen and geladen["b"].geloescht is True,
       "persistiert: neuer remote-Tombstone auf Platte")
pruefe(len(geladen) == 2, "persistiert: alle ids inkl. Tombstone auf Platte")


# --- 9) Robustheit: unbekannte/fehlende Felder im Remote-Dict ---------------
reset_lokal([])
# 'extra_feld' ist unbekannt und muss ignoriert werden; fehlende Felder ->
# Defaults aus EinkaufItem (from_dict).
ergebnis = core.einkauf_merge([
    {"id": "x", "text": "Brot", "geaendert_am": "2026-06-23T18:00:00Z",
     "extra_feld": "ignoriert mich"},
])
m = by_id(ergebnis)
pruefe(m["x"].text == "Brot", "robust: bekannte Felder uebernommen")
pruefe(m["x"].menge == "" and m["x"].checked is False and m["x"].geloescht is False,
       "robust: fehlende Felder fallen auf Defaults zurueck")


print(f"OK - {checks} Checks bestanden (test_merge.py)")
