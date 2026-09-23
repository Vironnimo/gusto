# Shopping trip for a named store

Read this reference completely when the user says where they are going to shop
("wir fahren zu REWE") or asks for the list in store order.

## What Gusto does and does not store

- Gusto stores **no store layout**. The walking route through a store is your
  on-the-fly judgment for this answer only.
- The shopping list keeps its own order. Do not add, rename, remove, or reorder
  items to express the route.
- Store-specific preferences are stored: a preferred-product card's `store`
  names where it is sold (comma-separated for several, empty = everywhere).

## Workflow

1. Verify the instance (`home --json`) and read
   `gusto shopping list --pending --json`.
2. Assign every open item to a department and order the departments along the
   store's route:
   - Use a route the user has described for that store (in this conversation
     or your own host memory).
   - Otherwise use the typical German supermarket route:
     Obst & Gemüse → Brot & Backwaren → Kühlregal (Milch, Joghurt, Käse, Eier,
     Aufschnitt) → Fleisch & Fisch → Trockensortiment (Nudeln, Reis,
     Konserven, Backen, Gewürze) → Frühstück (Marmelade, Aufstriche, Müsli,
     Kaffee, Tee) → Süßes & Snacks → Getränke → Tiefkühl → Drogerie & Haushalt.
   - Drogeriemärkte and discounters differ; adapt when the user corrects you.
   - Place unclear items where they most likely are; never drop an item.
3. For each item run
   `gusto favorites match "<text>" --store "<Laden>" --json`. These reads may
   run in parallel.
   - `null`: no known need; show the item text alone.
   - `products[0]` is the pick for this store (ranking already applied).
   - `products` empty but `other_products` present: say that the household's
     preference is sold elsewhere (e.g. "Samt gibt es sonst bei Edeka").
   - Exact matching only; never guess a need from a substring. Offer to teach
     an alias with `favorites alias-add` when the user confirms one.
4. Present the list grouped by department in route order, one line per item,
   with the pick after it:

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

   For a tappable Telegram checklist, follow `references/telegram.md` and emit
   the item rows in exactly this order. Department names may stay in the
   message text; buttons are items only.

Checking items off afterward uses the normal ids (`shopping check|uncheck`);
the chosen order has no effect on Gusto.
