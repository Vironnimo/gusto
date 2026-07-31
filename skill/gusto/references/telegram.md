# Telegram shopping checklist through vBot

Read this reference completely before posting a tappable Gusto shopping list.
Gusto remains the source of truth; vBot's keyboard holds temporary visual state.

## Contract

The keyboard has two button types:

- Item: label starts with `⬜` or `✅`; data is `chk:<id>`. vBot flips only the
  leading glyph in the existing Telegram message. This does not write Gusto and
  does not wake the agent.
- Submit: label `Fertig ✅`; data `run:done`. This wakes the agent with the
  keyboard's current button labels and closes the keyboard.

Without the final `run:done` button, no tap ever reaches the agent or Gusto.

## Build and post

1. Verify the Gusto instance and read `gusto shopping list --json`. The result
   is a flat array.
2. Create one row per item:
   - label `⬜ <text>` when `checked:false`, otherwise `✅ <text>`;
   - the glyph must be the first character;
   - data `chk:<id>` using the item's 32-hex id.
3. Add the final row `[{"label":"Fertig ✅","data":"run:done"}]`.
4. Post with vBot's `channel_send`: supply `channel_id`, optional
   `platform_target`, a message such as `🛒 Einkaufsliste`, and `buttons` as rows
   of `{label,data}`. `buttons` cannot be combined with `file_paths`.

Example button payload:

```json
[
  [{"label":"⬜ 200 g Spaghetti","data":"chk:a04381611b2740d5944abc58993c2643"}],
  [{"label":"✅ Eier","data":"chk:4479aa11111111111111111111111111"}],
  [{"label":"Fertig ✅","data":"run:done"}]
]
```

## Handle the Fertig callback

The wakeup note lists the current buttons. For every `chk:` line:

- leading `✅` means `gusto shopping check <id> --json`;
- leading `⬜` means `gusto shopping uncheck <id> --json`;
- ignore `run:done`; it is only the submit trigger.

These state-setting commands are idempotent. Check every exit code; an unknown
id is an error. After successful synchronization, confirm in chat. The keyboard
is already closed, so put item lines in the original message text too if they
must remain visible afterward.

`Fertig` saves checked state; it does not remove items. Run `shopping
remove-done` afterward only when removal of bought items was also requested.
Use `shopping clear` only for a request to empty the entire visible list. Both
bulk removals use tombstones and have no CLI restore.

After adding/removing/reimporting items, post a fresh keyboard. Recipe imports
mint new item ids; never reuse buttons from an older list.
