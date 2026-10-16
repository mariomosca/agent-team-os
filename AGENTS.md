# AGENTS.md — Agent Team OS

> Entry point for an AI coding agent (Claude Code, etc.) reading this repo.
> Humans: see [README.md](./README.md) and the [landing page](https://mariomosca.github.io/agent-team-os/).

## What this is

A **file-based protocol** to coordinate multiple AI coding-agent sessions (one per project/role/repo) that otherwise can't talk to each other. No daemon, no server — just a folder, a JSON message schema, bash helpers, slash commands, and lifecycle hooks. Runtime lives in `~/.agent-team-os/`; the integration (hooks/commands/skill/scripts) is copied into `~/.claude/` by the installer.

## Install (one command)

```bash
git clone https://github.com/mariomosca/agent-team-os && cd agent-team-os && ./install.sh
```

`install.sh` is idempotent: copies the lib + v2 modules + hooks + commands + skill into `~/.claude/`, creates `~/.agent-team-os/`, and drops a starter `AGENT_MAP.json` if none exists. It does **not** touch `settings.json` (you register hooks yourself — see below). Requires `jq`.

After install:
1. Edit `~/.agent-team-os/AGENT_MAP.json` — map your workspace paths to agent names (+ v2: `gods[]`, per-agent `domain`/`god`).
2. Register hooks in `~/.claude/settings.json` (next section).
3. Open a session in a mapped workspace; try `/inbox` or `/bus`.

## Hooks — what to register in settings.json

| Hook | Required? | What it does |
|------|-----------|--------------|
| `SessionStart` → `agent-team-os-load.sh` | recommended | shows your inbox at session start |
| `UserPromptSubmit` → `agent-team-os-urgent.sh` | recommended | alerts on urgent/high messages |
| `Stop` → `agent-team-os-stop.sh` | **opt-in (v2)** | **drain-on-Stop**: blocks session close if your inbox has unhandled messages. See [docs/agentic-v2/SETTINGS-DIFF.md](./docs/agentic-v2/SETTINGS-DIFF.md). Test isolated before enabling — it affects every session. |

The `Stop` hook is copied by the installer but **NOT auto-registered** — it's a behavioral change that affects all sessions, so activation is explicit and gated.

## How an agent uses it (runtime contract)

- **Read your inbox**: at session start, read every file in `~/.agent-team-os/inboxes/<you>/`. After handling a message, move its file to `inboxes/<you>/.done/`.
- **Send a message**: use `/send <agent> <intent>`, or write a message JSON (schema below) — never write into another agent's folder directly; the protocol delivers it.
- **Message schema (v1.1)**: `{ id, version, from, to, thread_id, in_reply_to, type, intent, priority, delivery, payload, context_refs, requires_response, ts }`.
- **Reply in-thread**: `/reply <msg-id> <type>` (type = accept|decline|response|confirm).
- **Two machines (v2.5)**: address `agent@host[/slug]` (e.g. `kai@mini/crewmoor`). Laptop → mini goes over SSH (queued in `outbox/<host>/` if down); mini → laptop waits in the mini's `outbox/laptop/` until the laptop collects. `/bus` shows both machines. See README "Two machines".
- **Slash commands**: `/bus` (roster), `/inbox`, `/read <id>`, `/send`, `/reply`, `/handoff`, `/thread`.

## v2.0 — hive-GOD-agentic (optional layers)

- **Dual GOD orchestration**: GOD agents (`gods[]` in AGENT_MAP) orchestrate a domain — they decompose, propose to the human, then dispatch. Contract: [docs/agentic-v2/GOD-CONTRACT.md](./docs/agentic-v2/GOD-CONTRACT.md). Semi-autonomous: propose → human confirms → dispatch.
- **TaskProvider layer**: pluggable task manager per domain (`scripts/agent-team-os-taskprovider.sh`). `ab_task_provider <agent>` resolves the provider; `ab_task_mapping <provider>` returns the declared status/assignee/priority/dependsOn/meetingIngestion mapping. Reference impl: Orbit (side). Brandart has no provider (PMOHub dismissed on 2026-10-01).
- **drain-on-Stop hook**: keeps agents from closing with an unhandled inbox (opt-in, above).
- **harvest/telemetry**: `scripts/agent-team-os-harvest.sh <cwd>` → token + USD cost per agent from `~/.claude/projects/*.jsonl`, per-model pricing.

## Key files

| Path | Purpose |
|------|---------|
| `scripts/agent-team-os-lib.sh` | core bash helpers (`ab_*`); auto-sources taskprovider |
| `scripts/agent-team-os-taskprovider.sh` | v2 TaskProvider routing + mapping |
| `scripts/agent-team-os-harvest.sh` | v2 token/cost telemetry |
| `scripts/agent-team-os-collect.sh` | v2.5 one collection round with the other machines (launchd runs it) |
| `scripts/agent-team-os-custode.sh` | the custode: daily health check of a remote host, report + Telegram (`install.sh --custode`) |
| `schema/bus-message.schema.json` | message schema (incl. v2.5 `context.host`) |
| `tests/test-hosts.sh` | v2.5 two-machine tests with a fake `ssh` (`tests/fake-ssh/`) |
| `tests/test-custode.sh` | custode tests, fake `claude`/`uv`/`curl`/`launchctl`/`df` in `tests/fake-bin/`, real git on local remotes |
| `hooks/agent-team-os-{load,urgent,stop}.sh` | lifecycle hooks |
| `commands/*.md` | slash commands |
| `skills/agent-team-os/SKILL.md` | the skill (agent-invocable) |
| `examples/AGENT_MAP.example.json` | starter config (v2 shape) |
| `docs/agentic-v2/` | GOD contract, settings diff, briefs |

## Source of truth

The authoritative protocol spec lives in the consumer's `PLAN-AGENT-TEAM-OS.md` (symlinked at `~/.agent-team-os/PROTOCOL.md`). This repo is the distributable implementation.
