# Service

The service domain is the versioned anonymous LAN API over Core (`gusto/api.py`) and the standard-library HTTP client that normal CLI commands use to reach it (`gusto/client.py`).

## Overview

`gusto/api.py` defines one FastAPI `APIRouter` under `/api/v1` with three routes:
health, command and events. It has no app of its own: `gusto.web:app` mounts it,
so API, browser UI and PWA run in one process (the service). The API validates
and materializes transport input, delegates the requested operation to Core
and serializes the result; some handlers also read related data for presentation.
Domain rules, locking and the change journal stay in
Core (`catalog.md`, `shopping.md`, `storage.md`).

`gusto/client.py` resolves the server URL, sends one command envelope per call,
unwraps `result` and classifies failures as definite rejections or uncertain
outcomes. The CLI and its private command units (`cli.md`) are its production callers.

Not owned here:
- CLI parsing, presentation, exit codes and local lifecycle commands: `cli.md`.
- `GET /api/shopping`, `POST /api/shopping/sync` and `GET /api/favorites`: unversioned PWA routes in `gusto/web.py` (`shopping.md`), not part of the command API.
- The page shell's EventSource consumer and reload rules: `web.md`.
- Journal persistence, revision numbering, idempotent no-op detection and locks: `storage.md`.
- Health verification during install, start and update (`gusto/service.py` builds its own local health URL): `lifecycle.md`.

The API is anonymous by design for a trusted home LAN: no tokens or auth
headers, also not for EventSource. Do not add authentication workarounds and do
not expose the port to the public internet. The web host checks browser-origin
metadata before writes, including command requests; headerless non-browser
clients remain supported (`web.md` -> Interfaces).

## Terms

### Command operation

A dotted name such as `recipe.content.set` registered in `api._OPERATIONS` that
selects one service action. Not a CLI command name: editor workflows compose
several operations, while lifecycle and local commands have no operation.

### Uncertain outcome

A failed command whose effect is unknown - it may already have been applied -
marked `outcome_unknown: true`. Not a rejection: a definite rejection proves
nothing changed.

### Change event

A notification that named sources of truth changed at a persisted change
revision, telling clients what to refresh rather than which entities changed.
The wire contract is in Interfaces; persisted revisions belong to `storage.md`.

## Interfaces

**Health** `GET /api/v1/health` returns `{ok:true, status:"ready", version,
data_path, revision, resources}`. `data_path` is the server's resolved data
root; `revision`/`resources` are the latest journal entry. It is every client's
identity check: `gusto home` reports it as `server_data_path`, `server_version`
and `server_revision`; lifecycle compares version and data path; browser/PWA
gates compare `data_path` before acting.

**Command** `POST /api/v1/command`:
- Request: a JSON object with only `operation` (non-empty string, required),
  `arguments` (object, default `{}`) and `attachments` (list, default `[]`;
  `null` is rejected). Invalid JSON, unknown top-level fields and duplicate keys
  at any nesting level are 400 before anything runs; a repeated key would
  otherwise silently pick the last value.
- Body limit 40 MiB, enforced on the bytes actually streamed even when
  `Content-Length` is absent or wrong; a larger declared length is refused
  before reading (413).
- Success: HTTP 200 `{ok:true, result}`. `result` is the serialized Core result
  and may be `null` (`favorites.match` without a match).
- Failure: `{ok:false, error}` with a German message and status 400
  (validation or domain rejection), 404 (unknown entity), 413 (size) or 500 with
  `outcome_unknown:true`. The web host rejects foreign browser writes with 403
  and the same definite-failure envelope before the API runs.
- Missing Core entities raise `gusto.core.errors.NotFoundError`, a `ValueError`
  subclass mapped to 404 by type. Other `ValueError` rejections stay 400;
  message wording does not classify status. Explicit lookup results of `None`
  use `CommandError(..., 404)`. Missing favorite aliases remain validation (400).
- Server-side uncertainty: `errors.PostMutationError` (Core saved, then the
  journal update, cleanup or a later step failed) and `OSError`/`ValueError`
  while building the result after the Core call (`_mutation_result`) become 500
  with `outcome_unknown:true`. Validation before the Core call stays a definite
  400. A Core lock timeout (30 s) is a `ValueError` and therefore a definite 400.
  Any other exception becomes Starlette's plain-text 500.
- `check.run` returns its diagnostics as a successful command even with hard
  errors (`result.ok:false`); the CLI maps that to exit 1.
- Decoding and execution run in a worker thread (`run_in_threadpool`), so Core
  work and lock waits never block the event loop. Concurrent commands run in
  parallel; Core's resource locks serialize writes that share a source of truth.
  The API adds no locking or ordering of its own.

**Operations:** `_OPERATIONS` covers every normal CLI domain command (49
entries); lifecycle and local commands are unknown operations (400). Handlers
validate strictly through `_arguments` (unknown or missing names are 400 and
named) and typed helpers. Nullable string, integer and list fields treat `null`
as absent; omittable boolean flags still require JSON booleans when supplied.
`recipe.update`, `image.set` and `favorites.product.set` reject a request
without an effective change, while an empty string is a real change.

**Attachments:** only `image.add` (requires `image`), `favorites.product.add`
and `favorites.product.set` (optional `image`) accept attachments; every other
operation rejects them. Each is exactly `{name, filename, content_base64}`;
`filename` is a bare name of at most 255 characters without `<>:"/\|?*`;
content is strict Base64 of at most 25 MiB decoded (413). The API writes them
into a private `tempfile.TemporaryDirectory(prefix="gusto-api-")`, passes the
paths to Core (which copies what it keeps) and always removes the directory.
Commands without attachments allocate no directory. Clients upload file bytes,
never a server-side source path; health and failure diagnostics can expose
server paths.

**Events** `GET /api/v1/events?after=N` (Server-Sent Events):
- `after` must be a canonical nonnegative integer; `-1`, `+1` or `01` are 400
  before streaming.
- Each event is one `data:` line with JSON `{revision, resources}`, without
  `id:` or `event:` fields; clients track the revision and reconnect with
  `after`.
- Without `after` the first event is the current state. With `after=N` the
  stream replays retained events after N in order; a compacted range arrives as
  one event carrying the union of the dropped resources. If N is above the
  current revision (reset or different store), the current state is sent
  instead, so a stale client is never muted.
- Afterwards it emits new events, polling the journal every 0.2 s in a worker
  thread, sends a `: heartbeat` comment after 15 s without output, and ends
  when the client disconnects.

**Client** (`gusto/client.py`):
- `resolve_server_url(explicit)`: `--server` value, then `GUSTO_URL` (empty
  means unset), then `server_url` in the runtime's `gusto.settings.json` (beside
  `pyproject.toml` in a checkout, else at `sys.prefix`), then
  `http://127.0.0.1:8000`. An invalid value at any level is an error, never a
  fall-through; an empty `--server` included. URLs need `http`/`https` and a
  host, no query or fragment; a trailing `/` is stripped, a path prefix kept.
- `command(...)`: one POST, never retried; returns `result`. Timeout 15 s plus
  1 s per MiB of the request, which is serialized once.
- `health(server_url, timeout=2, retries=1)`: GET requiring `ok:true`; bounded
  retries are allowed because it is read-only (`gusto home` uses `retries=0`).
- `encode_attachment(name, path)`: a regular file only; above 25 MiB it fails
  before encoding or sending, as a definite failure.
- `ClientError(message, outcome_unknown=...)` is the only expected failure type.

**Failure classification** (`_request_json`, `command`, `_response_failure`):
- Definite rejection only when the body is `{ok:false, error:<non-empty
  string>}` without `outcome_unknown:true` and the status is below 500. The API
  message is kept verbatim.
- Everything else is uncertain for any operation not in `READ_ONLY_OPERATIONS`:
  connection refused or reset, timeout, HTTP >= 500, non-JSON or non-object
  body, missing `ok`, `ok:true` without `result`, or `outcome_unknown:true` from
  the server. The message gets German guidance to inspect the same server
  before resending. Unknown operation names count as mutations.
- A failed read-only operation gets "can be retried" guidance and no flag.
- Connection refused also reports `outcome_unknown:true` for mutations; the
  client deliberately does not guess at which phase a request failed.

## Conventions

- Adding a domain command: Core function, then an `_OPERATIONS` handler with
  strict argument validation and its result shape, then, only if read-only,
  its name in `client.READ_ONLY_OPERATIONS`, then the CLI command (`cli.md`).
  Which resources an event names is decided by the Core mutation decorator
  (`storage.md`).
- Handlers that reread data after a Core write (tag warnings, content, cover
  id) wrap that in `_mutation_result()`, so a reread failure reports
  uncertainty instead of a rejection.
- Errors are German strings that clients show verbatim.
- The client stays standard-library only and does not import Core, so the CLI
  works without the `web` extra.
- The client never replays a command. Only `health` retries.
- Contract tests: `tests/test_api.py` owns the envelope, validation, statuses,
  attachments, events, post-save uncertainty and the client's failure
  classification against a real socket; the CLI owner gate (`tests/test_cli.py`)
  assembles `tests/_cli_client.py` and `tests/_cli_editor.py` for URL resolution,
  attachment encoding, timeout scaling and lost-response recovery end to end.

## Constraints & Gotchas

- `READ_ONLY_OPERATIONS` must stay a subset of `api._OPERATIONS` (asserted in
  `tests/test_api.py`). It alone decides retry guidance; the HTTP method cannot,
  because reads also use POST. Never list a mutation there.
- Health reads the change journal on every call. A corrupt `data/changes.json`
  makes `/api/v1/health` a plain-text 500: `gusto home` then reports the server
  as unreachable, lifecycle health checks fail, committed mutations report an
  uncertain outcome and the event stream breaks, while domain reads still work.
  `check` reports the journal with its file path in `invalid_data_files`; it
  neither repairs nor resets it. Recovery belongs to `storage/change-journal.md`.
- A lost response cannot be told apart from a failure before execution. Do not
  "fix" uncertainty by retrying or by downgrading connection errors to
  rejections.
- The client only distinguishes below-500 rejections from uncertainty; the
  400/404/413 split is still asserted by `tests/test_api.py`.
- The 25 MiB attachment limit exists in both modules; keep them equal. The
  client limit is inclusive. The 40 MiB body limit leaves room for one Base64
  attachment plus the envelope.
- Each open EventSource reads the journal every 0.2 s in the thread pool.

## Agent-facing text

This domain owns the API validation/errors and client failure/recovery messages
in `gusto/api.py` and `gusto/client.py`, which the CLI passes to agents. Before
changing, adding or deleting a sentence, read `service/agent-facing-text.md`
and update its reason row. Domain messages passed through from Core keep their
owning catalog, shopping or storage contract; CLI presentation and the shipped
skill belong to `cli.md`.

## References

Read these only when your task matches - not by default.

- Changing API or client runtime messages shown to agents -> `service/agent-facing-text.md`
- Changing health revisions, event replay or journal-failure recovery -> `storage/change-journal.md`
