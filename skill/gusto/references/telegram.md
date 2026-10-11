# Telegram shopping checklist through vBot

This workflow posts the shopping list to a Telegram chat as a checklist the
user ticks on the phone, and saves the ticks the user submits to Gusto. It
needs vBot's `channel_send` tool with its `checklist` parameter. Without it,
answer with the list as text and say that no checklist was sent.

## Post the checklist

1. Read `gusto shopping list --json` from the Gusto instance selected under
   the main skill; add `--pending` when the user wants open items only. If the
   list is empty, say so and post nothing.
2. Post only when the user asked to send the list there, not merely to see it.
   The destination is the chat the user named or the current Telegram chat;
   do not invent a `platform_target`.
3. Call `channel_send` with a short `message`, one `checklist` item per Gusto
   item and `"done_label": "Fertig"`. Per item: `id` is the item's `id`
   copied verbatim (ids can contain hyphens), `label` is its quantity (when
   present) and text, `checked` is its `checked`. When the task names a store,
   use the item order and product picks prepared under the main skill's
   shopping-trip route, such as the label `Marmelade · Samt (Zentis)`.
4. Report the checklist as sent only after `channel_send` succeeded. If its
   outcome is unknown, do not send it again before checking the chat.

Example call; replace the ids and labels with the actual items:

```json
{
  "message": "Einkaufsliste für REWE",
  "checklist": [
    {"id": "a04381611b2740d5944abc58993c2643", "label": "200 g Spaghetti"},
    {"id": "4479aa11-1111-4111-8111-111111111111", "label": "Eier", "checked": true}
  ],
  "done_label": "Fertig"
}
```

## Save a submitted checklist

Taps only change the message. When the user taps Fertig, you receive a note
that the checklist was submitted, with the items changed since posting:
`checked` and `unchecked` lines, each with its item id.

1. On the Gusto instance you posted from, run
   `gusto shopping check <id> --json` for each `checked` id and
   `gusto shopping uncheck <id> --json` for each `unchecked` id. The calls are
   independent and can run in parallel. Unchanged items need no call, so
   changes made elsewhere since posting stay as they are.
2. Report each failed id separately and confirm only the saved changes. An
   item removed since posting is not created again.

Fertig removes no items. Run `shopping remove-done` only when the user also
asked to remove bought items; it removes every checked item of the list, also
ones outside this checklist. `shopping clear` empties the whole visible list.
Neither has a CLI restore.

After adding, removing or reimporting items, post a new checklist when the
user wants one; recipe imports create new ids.
