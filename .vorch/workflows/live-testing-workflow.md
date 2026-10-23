# Live Testing Workflow

How to start Gusto from your checkout and check a change in the real app, in the browser or through the CLI, and show it to the user. Automated regression checks are the quality gates (`PROJECT.md` -> Testing); this workflow is for looking at a change while you build it.

## Rules

- Never test against the installed production service or its data store, and never against the shared `gusto-dev` store: every live run gets its own throwaway store under `.tmp/`.
- Use the `playwright-cli` Skill for every browser interaction and screenshot (`.agents/skills/playwright-cli/SKILL.md` where your client does not provide it). Never use the Codex in-app browser or the Codex Browser plugin: it is unstable in this workspace and can crash the Codex app.
- Keep screenshots and other output in the same `.tmp/` folder.

## Steps

1. **Pick the port and folder.** Never use port 8000: it belongs to the installed household service, which the checkout's CLI also targets by default. In the primary checkout use a free port such as 8090. In a worktree use the `port` from its `.vorch-worktree` marker, so parallel worktrees never collide. Folder: `<checkout>/.tmp/live-<task>/`.
2. **Seed the store** when the change needs data: copy the tracked sample store (`data/`, `recipes/`, `images/` of the checkout) into the folder, or create data through the CLI once the server runs.
3. **Start the server** from the checkout's own `.venv`, in the background:
   ```bash
   GUSTO_HOME=<checkout>/.tmp/live-<task> .venv/Scripts/python -m gusto serve --host 127.0.0.1 --port <port>
   ```
   Use `--host 0.0.0.0` only when the user wants to open it from another device in the LAN, such as a phone for the PWA. `--reload` restarts the server on source changes.
4. **Check that it is yours:** `http://127.0.0.1:<port>/api/v1/health` must report `data_path` equal to your `GUSTO_HOME`. Stop and fix the setup otherwise.
5. **Exercise the change.** CLI, always with `--server`, because without it the checkout's CLI talks to the installed service: `.venv/Scripts/python -m gusto --server http://127.0.0.1:<port> <command> --json`. Browser: drive the pages with `playwright-cli`, including narrow mobile widths for UI changes, and take screenshots of the states that matter.
6. **Show it to the user:** give the clickable `http://127.0.0.1:<port>/...` link of the page that shows the change, with the screenshots, instead of instructions for starting it.
7. **Clean up:** stop the server when the user is done looking. The `.tmp/` folder may stay; it is git-ignored.
