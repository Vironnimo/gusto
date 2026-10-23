# CLI

The CLI domain is the `gusto` command line (`gusto/cli.py`, `gusto/__main__.py`) - a thin client of the running service plus a few local lifecycle and recovery commands - and the shipped agent skill (`skill/gusto/`) that teaches agents to operate Gusto through it.

## Overview

Entry points: console script `gusto` and `python -m gusto` both run
`gusto.cli:main`; the windowless GUI script `gusto-autostart` runs
`gusto.cli:autostart_main` (registered by `lifecycle.md`).

Normal domain commands call `_remote`, which sends exactly one command
operation through `gusto/client.py` (`service.md`) and only formats the
result. Local commands act on the CLI machine without the service: `home`,
`check --offline`, `serve`, `status|start|stop|restart`, `update`, `uninstall`
and `install-skill`. They are thin wrappers over Core (`home`,
`check --offline`), uvicorn with `gusto.web:app` (`serve`) and the lifecycle
modules (`gusto/service.py`, `gusto/update.py`, `gusto/uninstall.py`,
`gusto/skill_install.py`; semantics in `lifecycle.md`). They import their
module inside the command, so importing `gusto.cli` loads neither Core nor the
web stack (asserted in `tests/test_cli.py`).

Not owned here: domain validation (Core), the wire contract and failure
classification (`service.md`), lifecycle behavior and result shapes
(`lifecycle.md`).

## Terms

### Leaf command

**Definition:** A complete command path whose parser has no subcommands, such as `shopping add`. The agent contract fixes the set (59 today) in `tests/test_agent_contract.py`.

### Local command

**Definition:** A leaf that runs on the CLI machine without the service. **Not** a fallback: domain commands never become local when the service is unreachable.

## Interfaces

**Parsing:**
- `--json` exists only on leaf parsers and must follow the command path
  (`gusto shopping list --json`); `gusto --json list` is a usage error.
- `--server URL` is accepted before the command and after it on every leaf
  except `install-skill`, which rejects it (exit 2). Local commands other than
  `home` accept and ignore it. Resolution order: `service.md`.
- `--tag` is repeatable and comma-separated and is flattened; `--tags`
  (`new`, `set`) is one comma-separated value, and `set --tags ""` clears all
  tags.
- argparse rejects before any request, with exit 2: `serve --port` outside
  1..65535 and the exclusive groups `content set --file|--stdin` (one
  required), `set --duration|--clear-duration` and
  `--servings|--clear-servings`, `favorites product-set --image|--remove-image`,
  `uninstall --keep-data|--delete-data`.
- `_complete_help` makes each leaf's summary its description and adds an epilog
  with exit codes 0/1/2 and the `--json` error placement. Help and messages are
  German. `update --manifest-url` is hidden.

**Output and exit contract** (`main`):
- Exit 0 is success, including empty arrays and `null`. Exit 1 is a command
  failure. Exit 2 is an argparse usage error on stderr with empty stdout, also
  under `--json`.
- With `--json`, success prints exactly one JSON document on stdout (indent 2,
  non-ASCII kept): the API `result` unchanged or the local command's result.
  `ClientError`, `ValueError` and `sys.exit("message")` become
  `{ok:false, error}` on stdout with empty stderr, plus `outcome_unknown:true`
  when uncertain and `slug` and `recovery_path` for a failed editor round-trip,
  also when that failure is the cause of a wrapping error (`new --edit`).
- Exceptions to that shape: `check` prints its full diagnostics with
  `ok:false` and exits 1 on hard errors; `update` and `uninstall` failures that
  carry a structured result print that result and exit 1 (plain `uninstall`
  writes it to stderr).
- Plain mode prints German text on stdout and errors on stderr with exit 1.
  Plain output prints reusable slugs and ids. Stored single-line values pass
  through `_text`, which escapes non-printable characters so stored data cannot
  inject lines or terminal controls; `show` and `archive show` print raw
  Markdown. Plain `log` makes two extra reads to resolve titles.
- Other exceptions (for example `KeyboardInterrupt`, an unwrapped `OSError`)
  end with a traceback; new code must raise `ValueError`/`ClientError` or wrap
  `OSError` to keep the JSON contract.
- `main` reconfigures stdin, stdout and stderr to UTF-8 so umlauts and JSON
  survive a cp1252 Windows console.

**Recipe text:**
- `content set` reads exactly one of `--file` (UTF-8 with optional BOM or
  UTF-16 with BOM; UTF-32 and invalid bytes are rejected before upload; CR and
  CRLF become LF; the source file is never removed) or `--stdin` (UTF-8 only)
  and uploads through `recipe.content.set`, so Core rewrites the H1
  (`catalog.md`).
- `edit` and `new --edit` download through `recipe.show` into a
  `NamedTemporaryFile` (`gusto-<slug>-*.md` in Python's temp directory; in a
  checkout that is the repository `.tmp/`), run the editor and wait, read the
  file back with the same decoding and upload it. `EDITOR` defaults to
  `notepad` or `nano`. An existing file path is used as is (spaces allowed). On
  Windows the first token, quoted or bare, is resolved through `shutil.which`
  (so `code --wait` finds `code.cmd`) and the remaining arguments pass through
  with native quoting; on POSIX `shlex.split` is used. No `shell=True`. Under
  `--json` the editor's stdout and stderr are discarded so the output stays one
  JSON document. Editors that detach need their own wait flag.
- A failure once the temporary file exists keeps it and raises `EditError`
  (`slug`, `recovery_path`, inheriting `outcome_unknown` from a failed upload).
  Success deletes the file; a failed delete adds `retained_path` and still
  reports success, so the upload is never repeated. A failed `new --edit` says
  that the recipe was already created.

**Local commands:**
- `home` never needs the service: local `core.storage_info()` fields (`path`,
  `source` = `environment|settings|legacy|platform_default`,
  `platform_default`, `settings_path` when the file exists) plus `server_url`,
  `server_reachable`, `service_status` and, when reachable, `server_data_path`,
  `server_version`, `server_revision`, otherwise `service_error`. It exits 0
  when the server is unreachable. Local fields describe this machine's data
  selection; `server_data_path` is what the selected server uses.
- `check --offline` runs `core.check()` on the local data selection; it is an
  explicit recovery tool, never a fallback.
- `serve` preflights the web dependencies (fastapi, uvicorn, jinja2, markdown,
  python-multipart, pillow) and fails through the normal error contract naming
  both install paths before any startup output. Then `--json` prints one
  `{status:"starting", host, port, url, reload}` notice and runs uvicorn in the
  foreground; the notice is not readiness. Defaults: host `0.0.0.0`, port 8000.
- `autostart_main` runs `serve` with its arguments, appends stdout and stderr
  to `gusto-autostart.log` in the active data root (platform default when
  settings resolution fails), preserves `SystemExit` codes, returns 130 on
  `KeyboardInterrupt` and 1 after printing a traceback.

## Shipped skill

`skill/gusto/` is the canonical usage documentation for agents operating Gusto;
`AGENTS.md` routes household operation to `SKILL.md`. `SKILL.md` is the compact
entry; `references/cli.md` names every leaf with flags and JSON shapes,
`references/installation.md` covers install, repair, update, service control,
uninstall and skill delivery, `references/telegram.md` the vBot checklist
round-trip and `references/shopping-trip.md` store-ordered lists.

`tests/test_agent_contract.py` enforces: frontmatter `name: gusto` equal to the
folder, a description of at most 250 characters containing `Use when`, a
`SKILL.md` of at most 12,000 bytes, a list of required identifiers (for example
`home --json`, `server_data_path`, `outcome_unknown`, `recovery_path`),
references routed only from `SKILL.md` (a reference must not link another), and
every leaf named in `references/cli.md`.

Every managed runtime carries the skill below `share/gusto/skill/gusto`;
`gusto install-skill vbot` copies it to `~/.vbot/skills/gusto`
(`lifecycle.md`). An app update does not refresh that copy.

## Conventions

- Adding or removing a leaf: Core and API first (`service.md`), then the
  parser and `cmd_*` function with `--json` and plain output that prints
  reusable ids, then `EXPECTED_COMMANDS` in `tests/test_agent_contract.py`, the
  leaf in `references/cli.md`, and `SKILL.md` when agents need it for a common
  goal.
- Adding a skill reference: route it from `SKILL.md` and list it in
  `pyproject.toml` `[tool.setuptools.data-files]` (an explicit list, not a
  glob) and in the expected wheel files of `tests/test_packaging.py`; otherwise
  installed runtimes and `install-skill vbot` ship without it.
- Domain commands never fall back to Core or files when the service is
  unreachable, and local commands never route through the API
  (`tests/test_cli.py` makes `client.command` fail for `status` and
  `install-skill`).
- A few client-side checks duplicate API/Core rules (`set`, `image set` and
  `favorites product-set` need a change, `archive purge` needs `--yes`); Core
  and the API stay authoritative and the CLI does not otherwise validate domain
  values.
- Contract tests: `tests/test_cli.py` runs the CLI end to end against an
  isolated `serve` subprocess with a throwaway `GUSTO_HOME` (JSON and exit
  contract, real quoted `EDITOR` commands, encodings, lost-response recovery
  through a dropping proxy, local lifecycle commands);
  `tests/test_agent_contract.py` owns the leaf set, `--json` on every leaf, the
  skill checks above, and the `AGENTS.md`/`CLAUDE.md` routing checks.

## Constraints & Gotchas

- In a checkout, `gusto.settings.json` has no `server_url`, so
  `python -m gusto <domain command>` without `--server` or `GUSTO_URL` talks to
  `http://127.0.0.1:8000` - the installed household service when it runs -
  while `home` shows the local `gusto-dev` path. `python -m gusto serve` on the
  default port collides with that service. Development and tests start their
  own server on another port with `GUSTO_HOME` and pass `--server`, then verify
  `server_data_path`.
- `serve --host ::` prints `http://127.0.0.1:<port>`, while
  `gusto/service.py` uses `[::1]` for that bind; on a v6-only socket the
  printed URL may not answer.
- `gusto/cli.py` is about 1,900 lines, above the project's soft limit; split it
  along command groups behind `main` and `build_parser` before growing it.

## Agent-facing text

This domain owns the shipped skill (`skill/gusto/SKILL.md` and
`skill/gusto/references/*.md`), the CLI help texts and the CLI result and error
messages in `gusto/cli.py`. A reasons file (`cli/agent-facing-text.md`) does
not exist yet; until it does, treat every sentence of these texts as
load-bearing, check `docs/audit-2026-09-22.md` and `docs/audit-2026-10-10.md`
for the failures they record, and record the reason for each sentence you add
or change.
