# Shopping trip for a named store

Present open items in walking order with the household's known preferences; do not change the saved list to express a route.

1. Read items and preferences together, replacing `<store>` with the named store:

   ```text
   gusto shopping list --pending --store "<store>"
   ```

   Follow continuation commands for the entire list. If there are no open items, say so and stop.
2. Use a route the user described for this store, from the conversation or host memory. Otherwise assume the usual supermarket department order: Obst & Gemüse, Brot & Backwaren, Kühlregal, Fleisch & Fisch, Trockensortiment, Frühstück, Süßes & Snacks, Getränke, Tiefkühl, Drogerie & Haushalt. Label it as an assumed route; Gusto stores no branch layout, and discounters/drugstores can differ. Place unclear items where they most likely belong and keep every item.
3. Use the first ranked applicable product, if present; if preferences exist only at other stores, say where they are recorded. These records do not verify current stock. Exact matching does not infer substrings or strip quantities; do not create aliases during this read-only request.
4. Present one line per item, retaining quantity and ID even when labels repeat:

   ```text
   REWE · angenommene Laufreihenfolge
   Kühlregal
   ⬜ 6 Eier
   Frühstück
   ⬜ Marmelade · Samt (Zentis)
   Drogerie & Haushalt
   ⬜ Toilettenpapier
   ```

For a separately requested Telegram checklist, use this order and these product picks with the main skill's Telegram workflow; department headings are message text, not invented checklist items. Later check-offs use the original Gusto item IDs.
