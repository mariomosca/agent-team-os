# Roadmap

> Planned direction for Agent Team OS. Dates are best-effort, not commitments. Scope can shift; the foundation contract (v1.0 message schema, filesystem layout) won't.

## v1.0.0 — Foundation (shipped)

The current public release.

- File-based protocol with versioned JSON message schema (v1.0)
- Bash helper library (`agent-team-os-lib.sh`)
- Seven slash commands: `/inbox`, `/read`, `/send`, `/reply`, `/handoff`, `/thread`, `/bus`
- Two hooks: `SessionStart` inbox banner, `UserPromptSubmit` urgent alerter
- Auto-attaching skill that triggers on natural-language requests
- Routing rules with deny-list isolation
- Append-only outbox audit log + threaded conversations
- Native Claude Code plugin manifest + manual `install.sh`

## v1.1.0 — Realtime light (planned)

Make incoming messages reach a live session without waiting for the next `SessionStart`.

- `fswatch`-based delivery on macOS (Linux via `inotifywait` fallback)
- Optional background watcher per agent, started by the SessionStart hook, stopped at SessionEnd
- Cross-session ping when a new `urgent`/`high` message lands, surfaced via the next `UserPromptSubmit` and/or a macOS notification
- Zero new daemons in the architecture; the watcher is a session-lifetime child process
- Fully opt-in via `AGENT_MAP.json` per-agent flag — default behavior unchanged

## v1.2.0 — Conductor (pattern L1)

Run a dedicated "conductor" agent that owns a single daily plan and orchestrates the others.

- New skill `daily-orchestrator` for the conductor role
- New slash command `/daily-goals` — collect goals at the start of the day, decompose into streams, choose teammates, estimate wall-clock
- Daily plan written to `~/.agent-team-os/plans/DAILY-YYYY-MM-DD.md` (file-based, inspectable, version-controllable)
- Pure-file workflow: no MCP dependency, conductor still drives the bus via the same `/send`, `/read`, `/reply` it already speaks
- Adoption-friendly: any single workspace can be marked as the conductor's home in `AGENT_MAP.json`

## v1.3.0 — Active driver (pattern L2)

Let the conductor open workspaces and spawn teammates instead of pasting commands to a human.

- Pluggable `driver` adapter so the conductor can act on a terminal multiplexer of your choice
- Reference adapter for [Onda](https://onda.dev) bundled in `examples/drivers/onda/`
- Adapter contract: open workspace, split pane, run command, list active sessions, focus session, send keystrokes
- Conductor stays terminal-agnostic — bring your own driver for tmux/zellij/Warp/etc.

## v3 — Centralino (24 ott 2026, piano BR24)

Un daemon locale, unico scrittore dello stato in SQLite; sei tool MCP; una regola di sessione viva, una tabella di sveglia, una regola dello Stop; pulizia automatica. Il mini resta sul bus a file. Protocollo: `docs/PROTOCOL-v3.md`, contratto del daemon: `docs/CENTRALINO.md`.

## v2.0 — Hive-GOD-Agentic (SHIPPED 2026-06-06)

Dual-GOD orchestration + TaskProvider layer + drain-on-Stop + harvest. Dual GOD, TaskProvider e harvest sono stati tolti il 24 ott 2026 (BR24-F5.2: nessun lettore, nessun uso recente); resta la drain-on-Stop.

- ✅ **drain-on-Stop hook** (`hooks/agent-team-os-stop.sh`): blocks Stop on non-empty inbox, loop guard (`CLAUDE_CODE_STOP_HOOK_BLOCK_CAP`). NOT auto-registered — gated, see `docs/agentic-v2/SETTINGS-DIFF.md`.
- ↪️ The "MCP server wrapper" listed here shipped in v1.4 as `agent-bus-mcp`.

## v2.5 — Two machines, one bus (2026-10-14)

A laptop that gets closed and a Mac mini that stays on, where only the laptop can open SSH. Agents on the mini must receive work from the laptop and report back to the hub, with the laptop possibly asleep in between.

- ✅ **Host in the address**: `kai@mini`, `kai@mini/crewmoor`; no `@host` = the agent's own host (`agents.<a>.host`, hub agent → hub host) or this machine. `hosts` in AGENT_MAP, this machine's name in `$AB_HOME/HOST`.
- ✅ **One-way transport**: laptop → mini over SSH (atomic write), queued in `outbox/mini/` when the mini is down; mini → laptop via the mini's `outbox/laptop/`, pulled by the laptop. Copy, verify, then delete; no duplicates.
- ✅ **Collection** every 30 s at most from `/inbox`, `/bus`, `bus_inbox`, `bus_status` and the `UserPromptSubmit` hook (background), plus a launchd job every 60 s (`install.sh --host-sync`).
- ✅ **Status across machines**: `/bus` and `bus_status` show the mini's sessions (SSH, cached 20 s) with a host column.
- ✅ **`context.host`** in the message (additive), replies find their way back. Schema: `schema/bus-message.schema.json`.
- ✅ `install.sh --host`, `--host-sync`, `--register-hooks`; hooks and commands work with any `CLAUDE_HOME` / `CLAUDE_CONFIG_DIR`.
- ⏳ Not in: the mini cannot see the laptop's sessions (it never connects); thread logs are written on the side that sends or collects, not mirrored; more than two machines works in principle (any host with `ssh`) but is untested; no realtime push (worst case 60 s with the laptop open).

## v2.2 — bus-band shows the team (2026-10-14)

- ✅ **"Squadra" section** under the messages: sibling sessions read from `$AB_HOME/onda/sessions.json` (written by Onda; contract in `schema/CONTRACT.md` "Onda → mod"). Stuck ones first: `TESTO FERMO da N min` (text left in the prompt box, no Enter), waiting for a permission or an answer. Hidden when the file is missing or Onda is not alive.
- ✅ **"Rimetti nel mosaico"** button (only inside Onda): writes `onda/requests/req-*.json`, Onda mounts the workspace and focuses the pane.
- ✅ Toast once per episode when a sibling becomes stuck. `/bus-band squadra` toggles just the section.

## v2.1 — Mods: bus-band (SHIPPED 2026-10-03)

Claude Code 2.1.287 (1 Oct 2026) added mods: TypeScript hooks that run inside Claude Code and can draw UI. The SessionStart hook only told a session what was waiting when it opened; a message that arrived mid-session went unseen until the Stop hook or `/inbox`.

- ✅ **`mods/bus-band`**: a band above the prompt with the session's pending messages (v1.4 scoping: shared root + `@<slug>`, `context.project_path` filter), priorities spelled out, toast on new `urgent`/`high`, `/bus-band` toggle. Polls every 20 s and on every turn end. Read-only.
- ✅ Separate plugin, so manual installs don't double-register the classic hooks. `install.sh` copies it to `~/.claude/mods/bus-band/`; opt-in via `CLAUDE_CODE_PLUGIN_DIRS`.
- ✅ Pure bus logic in `hooks/bus.ts` with tests (`claude plugin test mods/bus-band`).
- ⏳ Next: a "Leggi" button that opens the message in place of `/read`; a pane with the thread.

## v1.4 — Session identity (SHIPPED 2026-09-21)

An agent name covers a whole tree of projects, so the same agent routinely has several sessions open at once. The bus addressed them by name only, so they shared one queue, one registry card and one drain cursor. Found in production: two `kai` sessions, the registry card holding one's workspace and the other's start time.

- ✅ **Two-level address**: `agent` reaches every live session, `agent/slug` reaches one. The message keeps the bare agent in `to`; the session lives in the path, so nothing existing needed migrating.
- ✅ **Per-session inbox** `inboxes/<agent>/@<slug>/`, with the shared root still readable by all.
- ✅ **Per-session registry** `registry/<agent>.d/<slug>.json`, TTL-based liveness (`AB_SESSION_TTL_MIN`, default 240). `ab_list_sessions` answers "which sessions exist right now" — previously unanswerable.
- ✅ **Per-session drain cursor** `cursor@<slug>.json`: the Stop hook no longer blocks one session over another's unread mail.
- ✅ `/inbox` scoped to the session (`--all` to widen), `/bus` lists live sessions with pending counts.
- ✅ **MCP server** [`agent-bus-mcp`](https://github.com/mariomosca/agent-bus-mcp): six typed tools over the same filesystem. This was listed as a v2.0 item; it shipped here instead. `delegate_inline` is not implemented.

Two defects surfaced on the way: `ab_mark_read` hoisted session messages into the shared archive, and under zsh a bare `local a b c` followed by assignments printed `name=value` to stdout, corrupting `ab_list_sessions` output when the lib was sourced outside a bash hook.

Tests: `tests/test-session-identity.sh` (18 assertions, including a reproduction of the bug).

## v2.x — Autonomous with guard-rails (pattern L3)

Run unattended for hours, escalate only when needed.

- Per-message `budget_minutes` and `auto_escalate_after` fields (message schema bump → 2.0)
- Hard caps: no public push, no production deploy, no outbound email without explicit human go
- Safety word recognized everywhere (`stop`, `pause`) — halts every active teammate in seconds
- Hourly digests for the human, blockers escalated immediately
- Schema migration tool from 1.x → 2.x

## Out of scope (for now)

These are intentionally **not** on the roadmap. PRs welcome if they fit the project, but they aren't on the maintainer's path:

- Encrypted message payloads (the bus assumes a single-user trusted machine)
- A graphical UI / web dashboard (CLI-first, plain JSON inspectable with `jq`)
- A hosted/managed version (the entire pitch is "no daemon, no server, no cloud")

## How decisions get made

- The message schema is versioned — additive changes only within a major version.
- Breaking changes wait for a major bump and ship with a migration tool.
- Anything that adds a daemon, server, or required network call needs strong justification.
- "Boring and correct" beats "clever and fragile". Always.
