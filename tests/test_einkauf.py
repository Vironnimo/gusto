"""Hermetische Tests fuer die Einkaufslisten-Logik in recipe/core.py.

Lauffaehig OHNE pytest:  python tests/test_einkauf.py

WICHTIG: RECIPE_HOME wird ganz oben auf ein frisches Temp-Verzeichnis gesetzt,
BEVOR recipe.core importiert oder eine Funktion aufgerufen wird. Sonst wuerden
die echten Daten unter recipes/ und data/ veraendert – das ist verboten.
"""
import os
import sys
import tempfile

# --- Hermetik: RECIPE_HOME auf ein Wegwerf-Verzeichnis, VOR dem Import -------
os.environ["RECIPE_HOME"] = tempfile.mkdtemp(prefix="gusto-test-")

# Projektwurzel in den Pfad, damit `recipe` importierbar ist, egal von wo aus
# das Skript gestartet wird.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from recipe import core  # noqa: E402

REZEPT_MD = (
    "# T\n"
    "\n"
    "## Zutaten\n"
    "\n"
    "- 200 g Spaghetti\n"
    "- 100 g Speck\n"
    "\n"
    "## Zubereitung\n"
    "\n"
    "1. kochen"
)

checks = 0


def pruefe(bedingung, nachricht):
    global checks
    assert bedingung, nachricht
    checks += 1


def erwarte_valueerror(fn, *args, **kwargs):
    try:
        fn(*args, **kwargs)
    except ValueError:
        return
    raise AssertionError(f"ValueError erwartet von {fn.__name__}, kam aber nicht.")


def main():
    # Sicherheitsnetz: wir arbeiten wirklich im Temp-Verzeichnis.
    pruefe(str(core.project_root()).startswith(tempfile.gettempdir()),
           "RECIPE_HOME zeigt nicht ins Temp-Verzeichnis – Abbruch.")

    # --- parse_zutaten ------------------------------------------------------
    pruefe(core.parse_zutaten(REZEPT_MD) == ["200 g Spaghetti", "100 g Speck"],
           "parse_zutaten liest die beiden Zutaten nicht korrekt.")
    pruefe(core.parse_zutaten("# Nur Titel\n\nKein Abschnitt hier.") == [],
           "parse_zutaten ohne Zutaten-Abschnitt muss [] liefern.")
    # Stoppt an naechster Ueberschrift, ueberspringt leere Bullets, '*' zaehlt.
    gemischt = ("## Zutaten\n"
                "- Mehl\n"
                "* Zucker\n"
                "-   \n"            # leerer Bullet -> uebersprungen
                "\n"
                "## Zubereitung\n"
                "- nicht mitzaehlen\n")
    pruefe(core.parse_zutaten(gemischt) == ["Mehl", "Zucker"],
           "parse_zutaten: leere Bullets/zweiter Abschnitt/'*' falsch behandelt.")

    # Rezept anlegen (ueber den oeffentlichen Core-Weg, im Temp-Home).
    core.add_recipe("T", inhalt=REZEPT_MD, slug="t")

    # --- load/save round-trip auf leerer Liste ------------------------------
    pruefe(core.einkauf_load() == [], "Frische Einkaufsliste muss leer sein.")
    pruefe(core.einkauf_list() == [], "Leere Liste -> einkauf_list() == [].")

    # --- einkauf_add --------------------------------------------------------
    a = core.einkauf_add("Milch", menge="1 L")
    pruefe(a.id and a.erstellt_am and a.geaendert_am,
           "einkauf_add muss id/erstellt_am/geaendert_am setzen.")
    pruefe(a.checked is False and a.geloescht is False,
           "Neues Item: checked und geloescht muessen False sein.")
    pruefe(a.text == "Milch" and a.menge == "1 L" and a.quelle is None,
           "einkauf_add uebernimmt text/menge/quelle nicht korrekt.")
    pruefe(len(core.einkauf_load()) == 1, "Nach add muss genau 1 Item da sein.")

    # load/save round-trip: gespeicherte Werte == zurueckgelesene Werte.
    geladen = core.einkauf_load()[0]
    pruefe(geladen.to_dict() == a.to_dict(),
           "load/save round-trip veraendert das Item.")

    # --- einkauf_add_rezept -------------------------------------------------
    neu = core.einkauf_add_rezept("t")
    pruefe([i.text for i in neu] == ["200 g Spaghetti", "100 g Speck"],
           "einkauf_add_rezept liefert die falschen Zutaten.")
    pruefe(all(i.quelle == "t" for i in neu),
           "einkauf_add_rezept muss quelle=slug setzen.")
    pruefe(len(core.einkauf_load()) == 3, "Insgesamt 3 Items erwartet (1 + 2).")
    erwarte_valueerror(core.einkauf_add_rezept, "gibt-es-nicht")

    # --- einkauf_list: Reihenfolge nach erstellt_am -------------------------
    texte = [i.text for i in core.einkauf_list()]
    pruefe(texte == ["Milch", "200 g Spaghetti", "100 g Speck"],
           "einkauf_list muss nach Einfuegereihenfolge (erstellt_am) sortieren.")

    # --- einkauf_toggle -----------------------------------------------------
    t1 = core.einkauf_toggle(a.id)               # umschalten -> True
    pruefe(t1.checked is True, "toggle(None) muss von False auf True schalten.")
    t2 = core.einkauf_toggle(a.id)               # umschalten -> False
    pruefe(t2.checked is False, "toggle(None) erneut muss zurueck auf False.")
    t3 = core.einkauf_toggle(a.id, checked=True)  # explizit setzen
    pruefe(t3.checked is True, "toggle(checked=True) muss True setzen.")
    erwarte_valueerror(core.einkauf_toggle, "unbekannte-id")

    # --- einkauf_list-Filter: done -----------------------------------------
    pruefe([i.text for i in core.einkauf_list(include_done=False)]
           == ["200 g Spaghetti", "100 g Speck"],
           "include_done=False muss erledigte Items ausblenden.")
    pruefe(len(core.einkauf_list(include_done=True)) == 3,
           "include_done=True muss alle (nicht-geloeschten) Items zeigen.")

    # --- einkauf_remove: Tombstone -----------------------------------------
    spaghetti = next(i for i in core.einkauf_load() if i.text == "200 g Spaghetti")
    core.einkauf_remove(spaghetti.id)
    geloescht = next(i for i in core.einkauf_load() if i.id == spaghetti.id)
    pruefe(geloescht.geloescht is True,
           "einkauf_remove muss das Item als Tombstone markieren.")
    # Tombstone ist in load() vorhanden, in list() (default) aber nicht.
    pruefe(any(i.id == spaghetti.id for i in core.einkauf_load()),
           "Tombstone muss in einkauf_load() weiter auftauchen.")
    pruefe(not any(i.id == spaghetti.id for i in core.einkauf_list()),
           "Tombstone darf in einkauf_list() (default) NICHT erscheinen.")
    pruefe(any(i.id == spaghetti.id
               for i in core.einkauf_list(include_deleted=True)),
           "include_deleted=True muss Tombstones zeigen.")
    # Tombstones lassen sich nicht mehr togglen.
    erwarte_valueerror(core.einkauf_toggle, spaghetti.id)
    erwarte_valueerror(core.einkauf_remove, "unbekannte-id")

    # --- einkauf_clear_done -------------------------------------------------
    # Aktuell erledigt + nicht-Tombstone: nur "Milch" (a). Speck ist offen,
    # Spaghetti bereits Tombstone.
    anzahl = core.einkauf_clear_done()
    pruefe(anzahl == 1,
           f"clear_done sollte genau 1 Item entfernen, war {anzahl}.")
    milch = next(i for i in core.einkauf_load() if i.id == a.id)
    pruefe(milch.geloescht is True,
           "clear_done muss erledigte Items zu Tombstones machen.")
    # Erneuter Aufruf entfernt nichts mehr.
    pruefe(core.einkauf_clear_done() == 0,
           "clear_done ohne erledigte Items muss 0 liefern.")
    # Es bleibt genau das offene Speck-Item sichtbar.
    sichtbar = core.einkauf_list()
    pruefe([i.text for i in sichtbar] == ["100 g Speck"],
           "Nach clear_done darf nur das offene Speck-Item sichtbar sein.")

    print(f"OK - {checks} Checks bestanden (RECIPE_HOME={core.project_root()})")


if __name__ == "__main__":
    main()
