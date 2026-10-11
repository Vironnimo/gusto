# Shopping trip for a named store

This workflow presents the current shopping list in a store's walking order,
with the household's known product preferences for that store.

## What Gusto does and does not store

- Gusto stores no store layout. The walking route through a store is your
  on-the-fly judgment for this answer only.
- The shopping list keeps its own order. Do not add, rename, remove, or reorder
  items to express the route.
- Store-specific preferences are stored: a preferred-product card's `store`
  names where it is sold (comma-separated for several, empty = everywhere).

## Workflow

1. Reuse the verified instance and read `gusto shopping list --pending --json`.
   If the result is empty, report that there are no open items and stop.
2. Assign every open item to a department and order the departments along the
   store's route:
   - Use a route the user has described for that store (in this conversation
     or your own host memory).
   - Otherwise use the typical German supermarket route:
     Obst & Gemüse → Brot & Backwaren → Kühlregal (Milch, Joghurt, Käse, Eier,
     Aufschnitt) → Fleisch & Fisch → Trockensortiment (Nudeln, Reis,
     Konserven, Backen, Gewürze) → Frühstück (Marmelade, Aufstriche, Müsli,
     Kaffee, Tee) → Süßes & Snacks → Getränke → Tiefkühl → Drogerie & Haushalt.
   - Present this default as an assumed route, not a known layout of this branch.
     Drogeriemärkte and discounters differ; adapt when the user corrects you.
   - Place unclear items where they most likely are; never drop an item.
3. For each item run
   `gusto favorites match "<text>" --store "<Laden>" --json`. Replace `<text>`
   with the item's returned `text` and `<Laden>` with the named store. These reads can
   run in parallel.
   - `null`: no known need; show the item text alone.
   - `products[0]` is the pick for this store (ranking already applied).
   - `products` empty but `other_products` present: report where the preference
     is recorded (e.g. "Samt ist bei euch für Edeka hinterlegt"). These cards are
     household records, not verified live availability or stock.
   - Exact matching only; do not guess a need from a substring or create an
     alias during a read-only trip-planning request. Save an alias when the user
     supplies that equivalence and requests remembering it.
4. Present the list grouped by department in route order, one line per item,
   with its separate `quantity` when present and the pick after it. Keep each
   original item and id, even when two labels look the same:

   ```text
   🛒 REWE
   Kühlregal
   ⬜ Eier
   Frühstück
   ⬜ Marmelade · Samt (Zentis)
   ⬜ Nutella
   Drogerie & Haushalt
   ⬜ Toilettenpapier
   ```

   If a Telegram checklist was also requested, pass these items and their order
   to the main skill's Telegram workflow. Department names can stay in its
   message text; checklist items are Gusto items only.

Checking items off afterward uses the normal ids (`shopping check|uncheck`);
the chosen order has no effect on Gusto.
