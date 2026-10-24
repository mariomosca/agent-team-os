# CLAUDE.md

This repo is **Agent Team OS** — a file-based protocol to coordinate multiple Claude Code sessions.

👉 **Read [AGENTS.md](./AGENTS.md) first** — it's the agent-oriented entry point (what it is, install, runtime contract, v2 layers, key files).

Quick orientation:
- It's bash + JSON + hooks, no build step. `./install.sh` deploys into `~/.claude/` + `~/.agent-team-os/`.
- Editing the protocol behavior = edit `scripts/agent-team-os-lib.sh` (core) or the `hooks/`, then re-run `install.sh` to redeploy.
- The `Stop` hook (drain-on-Stop) is opt-in and NOT auto-registered in settings.json — see `docs/agentic-v2/SETTINGS-DIFF.md`.
- Bus v3 (BR24): the centralino in `centralino/` (contract `docs/CENTRALINO.md`, protocol `docs/PROTOCOL-v3.md`). The file bus (lib, wake.py) stays for the Mac mini.
