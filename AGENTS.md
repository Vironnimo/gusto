# Gusto — agent entry point

Read `AGENTS.override.md` completely. It is the canonical project guide for
all agents, including repository architecture, CLI usage, instance safety,
verification, documentation, and commits. `CLAUDE.md` carries the same guide
for clients that discover that filename.

At session start, first read `.vorch/PROJECT.md` and `.vorch/GLOSSARY.md`
completely. Work as a standalone agent with your normal tools; do not invoke
or impersonate Vorch roles under `.opencode/agents/`. Maintain the project
knowledge base and perform the authorized git work yourself. Delegate only
when the user or applicable instructions authorize it.

Normal Gusto operations use the selected running service. Verify the exact
invocation with `home --json` before domain work; verify `server_url` and
`server_data_path`, since local `path` need not identify the remote store.
If a mutation loses its
response (`outcome_unknown:true`), inspect state on the same server before
retrying; do not replay it automatically. Failed interactive edits retain
`recovery_path`; compare `show` before uploading that copy again.
