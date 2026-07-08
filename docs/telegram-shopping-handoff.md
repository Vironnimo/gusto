# Handoff: Einkaufsliste als Telegram-Checkliste

Vertrag zwischen **Gusto** (dieses Repo, liefert Daten & Logik über die
`recipe`-CLI) und der **Agent-/Telegram-App** (anderes Repo, baut die
Telegram-Darstellung). Ziel: die Einkaufsliste erscheint im Telegram-Chat als
**antippbare Checkliste** — ein Button pro Zutat, Tippen hakt ab (⬜ → ✅) und
schreibt sofort nach Gusto zurück. Zielgruppe u. a. ältere Menschen: **mehr als
„antippen" darf nicht nötig sein** (kein Tippen, kein Installieren).

## Arbeitsteilung

- **Gusto (fertig, nichts zu bauen):** Daten & Logik über `recipe shopping …`.
- **Andere App (zu bauen):** Telegram-Inline-Keyboard senden **+** `callback_query`
  empfangen → ruft die Gusto-CLI. Das geht **nicht** über Nachrichtentext/Markdown.

## Gusto-CLI-Kontrakt (verifiziert)

Aufruf: `recipe shopping <cmd>` bzw. `python -m recipe shopping <cmd>`, immer mit
`--json`. `RECIPE_HOME` zeigt auf den Datenordner (auf dem Pi gesetzt).

| Kommando | Rückgabe (`--json`) |
|---|---|
| `shopping list` | **flaches Array** aller nicht-`deleted` Items (gehakte inklusive, mit `checked:true`) |
| `shopping list --pending` | dasselbe Array, aber nur `checked:false` |
| `shopping check <id>` | das aktualisierte Item (`checked:true`), `updated_at` gebumpt |
| `shopping uncheck <id>` | das aktualisierte Item (`checked:false`) |
| `shopping add "<text>" [--quantity M]` | das erzeugte Item (Einzelobjekt) |
| `shopping add-recipe <slug>` | Array der erzeugten Items (`source=slug`) |
| `shopping remove <id>` | Tombstone (sync-sicher, kein Hard-Delete) |
| `shopping clear` | `{ "removed": N }` — entfernt (tombstoned) alle gehakten |
| unbekannte `id` | Fehlermeldung auf stderr + **exit code 1** |

Item-Form:

```json
{
  "id": "a04381611b2740d5944abc58993c2643",
  "text": "200 g Spaghetti",
  "quantity": "",
  "checked": false,
  "source": "spaghetti-carbonara",
  "created_at": "2026-07-08T19:25:38Z",
  "updated_at": "2026-07-08T19:25:38Z",
  "deleted": false
}
```

`id` = 32-stelliges Hex, stabil und eindeutig → **der Anker für die Buttons**
(`callback_data: "chk:<id>"` = 36 Bytes, weit unter dem 64-Byte-Limit).

## Was die andere App bauen muss

1. **Senden mit `reply_markup` (Inline-Keyboard):** ein Button pro Item, Label
   `⬜ 200 g Spaghetti` bzw. `✅ 200 g Spaghetti`, `callback_data: "chk:<id>"`.
   Beispiel-Payload (gegen die aktuelle Bot-API-Doku prüfen):
   ```json
   POST sendMessage
   {
     "chat_id": "<gruppe-oder-person>",
     "text": "🛒 Einkaufsliste",
     "reply_markup": { "inline_keyboard": [
       [{ "text": "⬜ 200 g Spaghetti", "callback_data": "chk:a0438161…" }],
       [{ "text": "✅ Eier",            "callback_data": "chk:4479aa91…" }]
     ]}
   }
   ```
2. **Empfangen von `callback_query`:** ein Tap ist **keine Chat-Nachricht**,
   sondern ein eigener Update-Typ — wer nur `message`-Updates liest, sieht ihn
   nicht. Beim Update:
   - `data` parsen (`chk:<id>`),
   - Status umschalten: `recipe shopping check <id>` bzw. `uncheck <id>`
     (es gibt kein `toggle` — anhand des aktuellen `checked` entscheiden),
   - **`answerCallbackQuery(callback_query_id)`** aufrufen (sonst dreht der
     Ladekreis beim Nutzer weiter),
   - die Nachricht per `editMessageReplyMarkup` / `editMessageText` mit dem neuen
     ⬜/✅ aktualisieren (dazu die `message_id` der Listen-Nachricht behalten).

## Ablauf (Ende-zu-Ende)

1. `recipe shopping list --json` → für jedes nicht-`deleted` Item eine
   Button-Reihe, ⬜ bei `checked:false`, ✅ bei `true`.
2. Nachricht senden, `message_id` merken.
3. Tap kommt als `callback_query` → `id` aus `data` → in Gusto umschalten →
   `answerCallbackQuery` → Nachricht neu rendern & editieren.
4. Optional: „Erledigte entfernen"-Button → `recipe shopping clear`; neue
   Einträge per Chat („+ Milch") → `recipe shopping add`; danach neu rendern.

## Zuerst untersuchen (bitte vor dem Bauen berichten)

- Wie holt die bestehende Telegram-Integration Updates — **Webhook oder
  Long-Polling**? (Bei Long-Polling **kein** HTTPS/offener Port nötig; der Bot
  ruft nur ausgehend bei Telegram an — genau das wollen wir.)
- Verarbeitet sie schon **andere Update-Typen als `message`**, insbesondere
  `callback_query`? Falls nein: **das ist das fehlende Kernstück.**
- Kann sie beim Senden ein `reply_markup` mitgeben und eine **bereits gesendete
  Nachricht editieren**?
- Vorschlag, wie sich das einfügt, **ohne** die bestehende
  Nachrichten-Verarbeitung zu brechen.

## Fallen / Details

- `callback_data` max **64 Bytes** — `chk:<id>` passt locker.
- Immer `answerCallbackQuery` aufrufen, auch wenn nichts sichtbar passiert.
- **Toggle-Semantik:** Tap auf ✅ soll wieder abhaken (Korrektur) → je nach
  aktuellem Status `check`/`uncheck`.
- Unbekannte `id` → exit 1: den Exit-Code prüfen, nicht nur stdout.
- **Gruppen-Chat** = geteilte Haushaltsliste (alle sehen/tippen dieselbe
  Nachricht) — gewünscht. In Gruppen ggf. Bot-„Privacy Mode" beachten
  (Button-Callbacks funktionieren unabhängig davon; nur Text-Kommandos bräuchten
  evtl. `/cmd@bot`).
- Wird die Liste neu aufgebaut (z. B. via `add-recipe`), entstehen **neue
  `id`s** → Listen-Nachricht neu senden/editieren statt alte Buttons weiter zu
  nutzen.
- Ein Edit pro Tap ist ok; Telegram-Rate-Limits nur im Hinterkopf behalten.

## In einem Satz

Gusto = Daten & Logik (fertig, über `recipe shopping …`). Andere App =
Telegram-Darstellung (Inline-Keyboard) + Tap-Empfang (`callback_query`), das die
Gusto-CLI aufruft.
