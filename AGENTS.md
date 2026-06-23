# Hinweise fuer Agents

Dieses Projekt ist bewusst so gebaut, dass **Agents es genauso gut bedienen
koennen wie ein Mensch**. Wenn du ein Agent bist, ist das hier deine
Bedienungsanleitung.

## Grundprinzip

- **Eine Quelle der Wahrheit, mehrere Oberflaechen.** Alle Logik steckt in
  `recipe/core.py`. CLI (`recipe/cli.py`) und die spaetere Web-UI sind nur
  duenne Huellen. Es gibt **kein Feature, das nur die Web-UI kann** – alles
  geht auch per CLI.
- **Jedes CLI-Kommando versteht `--json`.** Nutze `--json` fuer maschinen-
  lesbare Ausgabe, die du sicher weiterverarbeiten kannst.

## Datenmodell

| Ort | Inhalt |
|-----|--------|
| `recipes/<slug>.md` | Der reine Rezept-Inhalt als Markdown. **Kein Frontmatter.** |
| `data/recipes.json` | Metadaten **aller** Rezepte: `slug`, `titel`, `tags`, `dauer_minuten`, `portionen`, `zuletzt_gekocht`. Quelle fuer Liste/Suche/Filter. |
| `data/log.json` | Koch-Logbuch: Liste von `{ "datum": "YYYY-MM-DD", "slug": ... }`. |

Der **`slug`** verbindet beides: `data/recipes.json[*].slug` ↔ `recipes/<slug>.md`.

> Weil Metadaten in der JSON und der Inhalt in der `.md` liegen, muessen beide
> zusammenpassen. Lege Rezepte daher mit `recipe new` an (schreibt beides).
> Wenn du eine `.md` von Hand hinzufuegst, ergaenze den passenden Eintrag in
> `data/recipes.json` und pruefe danach mit `recipe check`.

## CLI

```
recipe list   [--tag T] [--max-time N]        Rezepte auflisten/filtern
recipe search "<begriffe>" [--match any|all]  Volltext (inkl. Zutaten im Text)
recipe show   <slug>                          Rezept ausgeben
recipe new    "<Titel>" [--tags a,b] [--dauer N] [--portionen N]
recipe edit   <slug>                          .md im Editor oeffnen
recipe cooked <slug> [--date YYYY-MM-DD]      Ins Logbuch eintragen
recipe log    [--days N]                       Logbuch anzeigen
recipe set    <slug> [--titel ...] [--tags a,b] [--dauer N] [--portionen N]
recipe delete <slug>                           Rezept loeschen
recipe suggest [--days N] [--limit N]          Kandidaten fuers naechste Essen
recipe check                                   Konsistenz Index <-> .md
recipe serve  [--host H] [--port N]            Web-Oberflaeche starten (LAN)
```

Aufruf: `python -m recipe <kommando>` (oder `recipe <kommando>` nach `pip install -e .`).

## Typische Aufgaben

**„Was soll ich heute essen?"**
1. `recipe log --days 7 --json` → was war zuletzt dran.
2. `recipe suggest --json` → was schon laenger nicht dran war.
3. Auf dieser Basis entscheiden/vorschlagen (z.B. Abwechslung bei Tags,
   nicht dreimal Pasta hintereinander).

**„Was kann ich mit diesen Zutaten machen?"**
- `recipe search "haehnchen paprika" --match any --json` – durchsucht auch die
  Zutaten im Text der `.md`-Dateien.

**Rezept digitalisieren (vom Papier):**
- `recipe new "Titel" --tags ... --dauer ...` anlegen, dann den Inhalt in
  `recipes/<slug>.md` schreiben. (Aus Code: `core.add_recipe(..., inhalt=...)`.)

`suggest` ist absichtlich simpel und regelbasiert – die eigentliche
„Intelligenz" beim Vorschlagen kommt von **dir** (dem Agent), indem du `log`,
`search` und `list` kombinierst.
