# Web: agent-facing text

Reasons for the host-level JSON messages in `gusto/web.py`. Entry point: `web.md` -> Agent-facing text. These messages also reach human browser users; ordinary page copy follows `web/design.md`.

| Text | Reason |
|---|---|
| `Anfragen von einer anderen Website sind nicht erlaubt.` | Names the actual rejection boundary instead of implying that an operation or its arguments were invalid. A foreign-origin text/plain command previously executed before any browser permission check; `test_browser_writes_require_same_origin` covers the host boundary. |
| `Es wurde nichts geändert.` | The guard rejects before routing and Core execution. This is a definite 403 rejection, not a possibly committed write; the same test asserts unchanged state. |
| `Öffne Gusto direkt oder verwende die Gusto-CLI.` | Gives the supported same-origin browser or normal CLI path without teaching a browser-origin bypass or adding authentication. Headerless CLI acceptance is covered in the same test. |
| `Die Aktion konnte nicht vollständig abgeschlossen werden.` | Reports failure after execution began without incorrectly claiming a rejected request. `test_saved_mutations_never_look_rejected` exercises committed writes followed by journal failure. |
| `Die Änderung kann bereits gespeichert sein.` | Prevents interpreting HTTP 500 as proof that nothing happened; partial effects can remain when Core raises `PostMutationError`. The JSON carries `outcome_unknown:true` as the machine-readable contract. |
| `Prüfe den aktuellen Stand, bevor du die Aktion erneut ausführst.` | Prevents duplicate effects, especially cooking-log entries. Browser responses supply a GET link to the affected area and no resubmit form or live reload; sync clients retain their state for recovery (`shopping.md`). |
