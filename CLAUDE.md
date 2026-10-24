# CLAUDE.md

This repo is **Agent Team OS** — a protocol to coordinate multiple Claude Code sessions, run by a local daemon (the centralino) on each machine.

👉 **Read [AGENTS.md](./AGENTS.md) first** — it's the agent-oriented entry point (what it is, install, hooks, key files).

Quick orientation:
- The bus is `centralino/` (Python 3.12, uv, SQLite): contract `docs/CENTRALINO.md`, protocol `docs/PROTOCOL-v3.md`. Tests: `cd centralino && uv run --with pytest pytest -q` and `uv run centralino sim`; installer and custode: `bash tests/test-custode.sh`.
- `./install.sh` deploys hooks, commands and skill into `~/.claude/` (or `CLAUDE_HOME`) and, with `--daemon`, the centralino. The live laptop daemon runs from this repo's `main` checkout (launchd `com.agent-team-os.centralino`): restart it with launchctl or by its pid, never `pkill -f centralino` (CENTRALINO.md §16).
- The `Stop` hook is opt-in and NOT auto-registered in settings.json — see `docs/agentic-v2/SETTINGS-DIFF.md`.
- The MCP side is a separate repo, `agent-bus-mcp`: a thin client of the centralino socket.
