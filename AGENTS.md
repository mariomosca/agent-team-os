# AGENTS.md — Agent Team OS

> Entry point for an AI coding agent (Claude Code, etc.) reading this repo.
> Humans: see [README.md](./README.md) and the [landing page](https://mariomosca.github.io/agent-team-os/).

## What this is

A protocol to coordinate multiple AI coding-agent sessions (one per project/role/repo) that otherwise can't talk to each other. Since bus v3 (BR24, October 2026) every machine runs a **centralino**: a local daemon that is the only writer of the bus state (SQLite in `~/.agent-team-os/centralino.db`), decides who to wake and how, and talks to the other machines over SSH. Agents reach it through the MCP server [agent-bus-mcp](https://github.com/mariomosca/agent-bus-mcp) and six tools: `bus_send`, `bus_reply`, `bus_close`, `bus_inbox`, `bus_read`, `bus_status`. Hooks and slash commands are thin: they pass the caller to the centralino and print its answer.

- Protocol: [docs/PROTOCOL-v3.md](./docs/PROTOCOL-v3.md)
- Daemon contract: [docs/CENTRALINO.md](./docs/CENTRALINO.md)
- Skill: `skills/agent-team-os/SKILL.md`

## Install

```bash
git clone https://github.com/mariomosca/agent-team-os && cd agent-team-os
./install.sh --daemon --register-hooks
```

`install.sh` is idempotent: copies hooks, commands, skill and the bus-band mod into `~/.claude/` (or `CLAUDE_HOME`), creates `~/.agent-team-os/`, drops a starter `AGENT_MAP.json` if none exists, and with `--daemon` starts the centralino (launchd with a GUI login, a cron line on a headless Mac). Requires `jq` and `uv`. A second machine: `--host <name>` there, and on the hub an entry in `hosts` with `ssh` and `centralino` (the command that runs that machine's centralino, used as `ssh <host> '<centralino> consegna'`).

## Hooks

| Hook | Required? | What it does |
|------|-----------|--------------|
| `SessionStart` → `agent-team-os-load.sh` | recommended | registers the session, shows its inbox |
| `UserPromptSubmit` → `agent-team-os-urgent.sh` | recommended | alerts on urgent/high messages |
| `SessionEnd` → `agent-team-os-end.sh` | recommended | closes the session in the registry |
| `PreToolUse` on `onda_agent_prompt` → `agent-team-os-onda-prompt.py` | recommended with Onda | `[delega]` / `[comunicazione]` on prompts sent to other sessions |
| `UserPromptSubmit`, `Stop`, `SessionStart`, `SessionEnd` → `agent-team-os-mobile.py` | recommended | mobile mode per session (MM31): see below |
| `Stop` → `agent-team-os-stop.sh` | **opt-in** | blocks the turn end while a message asks for an action. See [docs/agentic-v2/SETTINGS-DIFF.md](./docs/agentic-v2/SETTINGS-DIFF.md) |

All of them except the mobile one `exec` `~/.claude/scripts/agent-team-os-centralino.py`, which calls the centralino socket; with the centralino down they stay silent and never block.

## Mobile mode (MM31)

Mario follows a session from the phone (Claude Code Remote Control) while the agent works on the Mac. `hooks/agent-team-os-mobile.py` keeps a per-session switch in `$AB_HOME/mobile/<session_id>.json`:

- **On**: a prompt that starts with `mobile on`, `/mobile on [Nh]`, `modalità mobile`, `sono al telefono`, `sono fuori`; or automatically when the transcript's last `remote_session_change` attachment carries a claude.ai url (Remote Control connected). **Off**: `mobile off`, `/mobile off`, `sono tornato`, `sono al mac`, the deadline, the end of the session, or Remote Control disconnecting when the automatic switch had turned it on. A manual off holds for that Remote Control connection.
- **While on**: every prompt gets the mobile rules (no `# VOICE` line, short answers, SendUserFile and code blocks instead of pbcopy, anything that needs hands on the Mac said first as a blocker); voice-notify skips that session (it reads the same file); one `caffeinate -ims` runs until the latest deadline of the mobile sessions (default: Monday 08:00 on a weekend, otherwise 8 hours). A lid warning comes with the activation when there is no external display.
- **CLI**: `python3 ~/.claude/hooks/agent-team-os-mobile.py on|off|status|list [--session ID] [--hours N]`. Tests: `/usr/bin/python3 -m unittest tests/test_mobile.py`.

## How an agent uses it

- **Delegating**: `bus_send(kind='delega')` registers the wait and returns a `task_id`; every return on it wakes you. `kind='comunicazione'` wakes nobody.
- **Executing a delegation**: questions with `bus_reply(message_type='question')` on the delegation (it stays in your inbox and does not hold your Stop until the answer arrives), the outcome with `bus_reply` on the delegation.
- **Consuming**: `bus_close` with `chiuso`, `riarma` (keep waiting) or `letto`.
- **Two machines**: address `agent@host[/slug]` (e.g. `kai@mini/crewmoor`). Laptop → mini goes to the mini's centralino over SSH (queued in `outbox/<host>/` if down); mini → laptop waits in the mini's `outbox/laptop/` until the laptop collects.
- **Slash commands**: `/bus`, `/inbox`, `/read <id>`, `/send`, `/reply`, `/handoff`, `/thread`, `/delegate`.

## Key files

| Path | Purpose |
|------|---------|
| `centralino/` | the daemon (Python 3.12, uv): `src/centralino/`, tests in `centralino/tests/`, `centralino sim` for the simulation bench |
| `centralino/launchd/` | launchd model used by `install.sh --daemon` |
| `hooks/agent-team-os-centralino.py` | the thin hooks' client of the centralino |
| `hooks/agent-team-os-*.sh`, `hooks/agent-team-os-onda-prompt.py` | lifecycle hooks |
| `hooks/agent-team-os-mobile.py` | mobile mode per session (MM31), tests in `tests/test_mobile.py` |
| `hooks/onda-agent-event.mjs` | bridge from Claude Code hook events to the Onda terminal (separate from the bus) |
| `scripts/agent-team-os-custode.sh` | the custode: daily health check of a remote host, report + Telegram (`install.sh --custode`) |
| `tests/test-custode.sh` | custode and installer tests, fake `claude`/`uv`/`curl`/`launchctl`/`df`/`tmux` in `tests/fake-bin/` |
| `tests/fake-ssh/ssh` | fake ssh for the two-machine tests (`centralino/tests/test_remoto.py`) |
| `commands/*.md` | slash commands |
| `skills/agent-team-os/SKILL.md` | the skill |
| `mods/bus-band/` | the band above the prompt, reads the centralino's view |
| `examples/AGENT_MAP.example.json` | starter config |
| `schema/` | message and event schemas |

## Source of truth

The authoritative protocol is [docs/PROTOCOL-v3.md](./docs/PROTOCOL-v3.md). The file bus (v1-v2.8: `lib.sh`, `wake.py`, files as state) was removed in BR24-F5.5 and F5.7; its history is in git (tag `br24-pre-rimozione`).
