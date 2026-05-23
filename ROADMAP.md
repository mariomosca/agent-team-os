# Roadmap

> Planned direction for Agent Bus. Dates are best-effort, not commitments. Scope can shift; the foundation contract (v1.0 message schema, filesystem layout) won't.

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

## v2.0.0 — Autonomous with guard-rails (pattern L3)

Run unattended for hours, escalate only when needed.

- Per-message `budget_minutes` and `auto_escalate_after` fields (message schema bump → 2.0)
- Hard caps: no public push, no production deploy, no outbound email without explicit human go
- Safety word recognized everywhere (`stop`, `pause`) — halts every active teammate in seconds
- Hourly digests for the human, blockers escalated immediately
- Schema migration tool from 1.x → 2.x

## Out of scope (for now)

These are intentionally **not** on the roadmap. PRs welcome if they fit the project, but they aren't on the maintainer's path:

- Encrypted message payloads (the bus assumes a single-user trusted machine)
- Multi-machine sync (the bus is local; cross-machine is a separate problem — `rsync` or git already works)
- A graphical UI / web dashboard (CLI-first, plain JSON inspectable with `jq`)
- A hosted/managed version (the entire pitch is "no daemon, no server, no cloud")

## How decisions get made

- The message schema is versioned — additive changes only within a major version.
- Breaking changes wait for a major bump and ship with a migration tool.
- Anything that adds a daemon, server, or required network call needs strong justification.
- "Boring and correct" beats "clever and fragile". Always.
