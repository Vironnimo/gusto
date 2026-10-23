# Service: Agent-facing text

Reasons for runtime text owned by `gusto/api.py` and `gusto/client.py`, read by
agents through the CLI's error and warning output. The rows describe what the
current code and contract tests support; they do not claim a past incident.
Text cells give a complete sentence or its unmistakable start; braces name
runtime substitutions. Keep one row per sentence when a message has several.

Core's domain errors pass through unchanged and belong to their domain's
contract. The CLI's surrounding presentation and shipped skill are covered by
`cli/agent-facing-text.md`. The API's success payload contains data rather than
instructions; preserve its shapes under `service.md` -> Interfaces.

## Outcome and recovery

| Text | Reason |
|---|---|
| `Die Ausführung ist nicht bestätigt; der Befehl kann bereits ausgeführt sein.` | `client._response_failure` cannot distinguish a lost reply from a request that never ran. Prevents interpreting transport failure as permission to repeat a mutation; `test_api.check_client_failure_recovery` and `test_cli.check_uncertain_delivery_contract` cover this boundary. |
| `Prüfe den Zustand auf demselben Server, bevor du eine Änderung erneut sendest.` | A read from another server cannot establish whether the uncertain mutation happened. Directs recovery to the target already named by the error without replaying a possibly non-idempotent action. |
| `Dieser Befehl liest nur Daten und kann erneut ausgeführt werden.` | Only operations in `READ_ONLY_OPERATIONS` get this sentence. Allows read recovery without extending retry permission to mutations or unknown operation names. |
| `Der Befehl wurde ausgeführt, aber sein Ergebnis konnte nicht vollständig gelesen werden: {error}` | `api._mutation_result` runs after the Core mutation returns. Tag warnings, content or image-cover rereads can fail after saving; treating that failure as rejection would invite a duplicate write. Covered by `test_api.check_post_mutation_failures`. |
| `Tags ohne Kategorie (Facet „Sonstige“): {tags}` | `_tag_warnings` reports an organization warning after a successful recipe write. The tags remain usable in the fallback facet; they are not rejected or silently removed. Covered by recipe create/update cases in `test_api.py`. |

## Command decoding and validation

These messages precede execution and identify how to correct the envelope or
typed arguments. `tests/test_api.py` owns the corresponding rejection cases.

| Text | Reason |
|---|---|
| `JSON-Feld '{key}' wurde mehrfach angegeben; gib jedes Feld genau einmal an.` | `_json_object` rejects repeated keys at every nesting level before JSON decoding discards an instruction; names the ambiguous field. |
| `'arguments' muss ein JSON-Objekt sein.` | `_arguments` prevents malformed containers from reaching handlers as if they were valid argument maps. |
| `Unbekannte Argumente: {names}.` | Lists every unsupported argument rather than silently ignoring a misspelled or unimplemented change. |
| `Fehlende Argumente: {names}.` | Names the missing required data so the caller can form a complete request. |
| `'{name}' muss ein Text sein.` | `_string` rejects non-string input; nullable fields are handled separately and are not coerced to text. |
| `'{name}' muss true oder false sein.` | `_boolean` accepts actual JSON booleans, preventing truthiness from turning strings or numbers into unintended flags. |
| `'{name}' muss eine ganze Zahl sein.` | `_integer` rejects booleans and non-integers instead of silently changing a requested quantity or position. |
| `'{name}' muss eine Liste aus Texten sein.` | `_strings` distinguishes a list from one string and rejects a malformed member before any write. |
| `Kein Rezept mit Slug '{slug}'.` | `_recipe_show` makes a missing active target explicit and returns 404 rather than an empty success. |
| `Kein archiviertes Rezept mit Slug '{slug}'.` | `_archive_show` distinguishes the archive lookup from the active cookbook; callers retain the requested slug. |
| `Kein Einkaufsbedarf mit id oder Name '{identifier}'.` | `_favorites_show` names the unresolved identifier and the accepted lookup forms; it does not choose a similarly named need. |
| `Gib mindestens eine Änderung an.` | Recipe and product update handlers reject a request containing only target identifiers or nullable fields with no effective change. |
| `Gib 'role' und/oder 'caption' an.` | `_image_set` identifies the fields needed for an image metadata change; `null` alone does not acknowledge a change. |
| `Endgültiges Löschen braucht 'yes: true'.` | `_archive_purge` requires explicit irreversible-delete intent in the API envelope before calling Core. |
| `Der Archiveintrag kann danach nicht wiederhergestellt werden.` | Explains the consequence of that confirmation so purge is not confused with reversible archive. |
| `'operation' muss ein nicht-leerer Text sein.` | `execute_command` requires a usable action selector rather than interpreting an empty or non-string value. |
| `Unbekannte Operation '{operation}'.` | Makes unsupported or local-only command names a definite rejection; no fallback action runs. |
| `Ungültiges JSON.` | Malformed or interrupted request bodies cannot be executed safely. The message identifies a request-format failure before any command has run. |
| `Der Request-Body muss ein JSON-Objekt sein.` | Rejects a JSON scalar or list before command-envelope interpretation. |
| `Unbekannte Felder: {names}.` | Names unsupported envelope fields instead of silently dropping them. |
| `'operation' fehlt.` | Names the missing action selector before any handler is invoked. |
| `Der Request-Body ist größer als {limit} MB.` | Reports the 40 MiB transport boundary; enforcement uses actual streamed bytes as well as declared length. |
| `Der Content-Length-Header muss eine ganze Zahl sein.` | Refuses malformed declared sizes rather than using an invalid value to decide the body limit. |
| `Der Content-Length-Header darf nicht negativ sein.` | A negative size cannot describe the request or exempt it from transport limits. |
| `'after' muss eine nichtnegative ganze Zahl sein.` | SSE replay requires a canonical nonnegative revision; rejects negative, signed, padded or non-integer cursors before opening a stream. |

## Attachments

API validation happens before the Core call. Client-side file errors happen
before any HTTP request. `test_api.py` and `test_cli.check_client_contract`
cover limits, accepted filenames and the definite-failure classification.

| Text | Reason |
|---|---|
| `'attachments' muss eine Liste sein.` | Rejects malformed containers, including explicit `null`, instead of losing attachment instructions. |
| `Jeder Anhang braucht genau 'name', 'filename' und 'content_base64'.` | Makes the transport schema explicit and rejects missing or extra attachment fields. |
| `Der Anhangsname darf nicht leer sein.` | Attachment names select handler inputs; an empty or non-string name cannot identify one. |
| `Anhang '{name}' wurde mehrfach angegeben.` | Prevents one attachment silently replacing another with the same logical name. |
| `Anhang '{name}' ist für Operation '{operation}' nicht erlaubt.` | Rejects bytes a handler would ignore and prevents attachment use outside the allowed operations. |
| `Der Originaldateiname eines Anhangs ist ungültig.` | Rejects missing names, directory components and dot paths before materialization. |
| `Der Originaldateiname eines Anhangs enthält unzulässige Zeichen oder ist zu lang.` | The API applies portable filename characters and a 255-character bound before touching the server filesystem. |
| `'content_base64' muss ein Text sein.` | Base64 decoding takes text; rejecting other JSON types before materialization keeps malformed attachment input a definite failure. |
| `Der Anhang ist größer als 25 MB.` | Encoded and decoded size checks enforce the same 25 MiB payload limit; a rejected upload never reaches Core. |
| `Fehlende Anhänge: {names}.` | Identifies the named bytes required for the operation, particularly `image.add`. |
| `Anhang '{name}' enthält kein gültiges Base64.` | Rejects malformed encoded bytes rather than importing altered or partial content. |
| `Anhang '{name}' konnte nicht materialisiert werden: {error}` | Identifies a server scratch-write failure before Core runs; this remains a definite command failure. |
| `Datei '{source}' ist keine reguläre Datei.` | The client requires a regular local file before attempting to read or transmit an attachment. |
| `Datei '{source}' konnte nicht gelesen werden: {error}` | Names the failed local input; no command was delivered. |
| `Datei '{source}' ist größer als {limit} MiB.` | Client-side bounded reading rejects oversized uploads before Base64 encoding and HTTP delivery. |
| `Es wurde nichts hochgeladen.` | Distinguishes that preflight failure from an uncertain server-side mutation; the caller can prepare a smaller input without checking for a prior upload. |

## Server selection and replies

Source evidence is in `client.resolve_server_url`, `_decode_response`,
`_request_json`, `command` and `health`. The uncertainty suffix above is added
according to operation semantics, not merely the HTTP method. A server's
structured definite rejection keeps its German error text verbatim.

| Text | Reason |
|---|---|
| `Ungültige Gusto-Server-URL aus {source}: '{value}'.` | Identifies the selected configuration source and value; invalid higher-precedence settings must not fall through to another server. |
| `Erwartet wird http://… oder https://…` | Gives the scheme requirement needed to correct that URL without guessing an address. |
| `Ungültige Gusto-Server-URL aus {source}: Query und Fragment sind nicht erlaubt.` | A base server URL is extended with API paths; query or fragment components would change request targeting. |
| `Ungültige Gusto-Server-URL aus --server: Erwartet wird http://… oder https://…` | An explicitly empty selection is an error, not permission to use a different configured server. |
| `Ungültige Gusto-Settings in '{path}': {error}` | Names the invalid selected settings file and parse/read problem rather than falling back silently. |
| `Ungültige Gusto-Settings in '{path}': JSON-Objekt erwartet.` | The settings root must be an object before `server_url` can be interpreted. |
| `Ungültige Gusto-Settings in '{path}': 'server_url' muss ein nicht-leerer Text sein.` | Prevents an invalid explicit server value from behaving like absent configuration. |
| `Antwort von Gusto unter {url} konnte nicht gelesen werden: {error}` | Names the target whose reply was interrupted; the failure classifier adds mutation uncertainty where appropriate. |
| `Gusto-Server unter {url} lieferte keine gültige JSON-Antwort.` | An HTML/plain-text/error response cannot confirm execution; prevents treating parse failure as a definite rejection. |
| `Gusto-Server unter {url} lieferte ein ungültiges Antwortformat.` | A valid JSON scalar/list still lacks the confirmation envelope required to classify the result. |
| `Gusto-Server unter {url} antwortete mit HTTP {status}.` | Preserves the target and HTTP evidence when the error response itself is unreadable. |
| `Gusto-Server antwortete mit HTTP {status}.` | Supplies a factual fallback when a decoded error response has no usable message; status and envelope still determine uncertainty. |
| `Gusto-Server unter {url}: {message}` | Locates a server-reported failure at the request target before the read-only or uncertainty guidance is appended. |
| `Gusto-Server unter {url} ist nicht erreichbar oder die Antwort wurde unterbrochen: {reason}` | Does not assume that connection, timeout or socket failure happened before execution. A mutation gets uncertainty guidance even for connection refusal. |
| `Gusto-Server unter {base_url} lieferte keine gültige Befehlsbestätigung.` | Missing/invalid `ok` or malformed rejection data does not prove an operation failed cleanly. |
| `Gusto-Serverantwort von {base_url} enthält kein 'result'-Feld.` | Even `ok: true` is insufficient without the result field; `result: null` is valid and is not confused with omission. |
| `Gusto-Server meldet keinen gültigen Health-Status.` | Health must explicitly report `ok: true`; a missing or unusable failure message cannot certify readiness. |
