---
name: agent-team-os
description: File-based communication bus between multiple Claude Code sessions. Use when the user says "send to <agent>", "tell <agent>", "hand off to <agent>", "check inbox", "reply to brief", "delegate to <agent>", or asks "who is active", "list agents", "open threads".
allowed-tools: Bash, Read, Write, Edit
---

# Agent Team OS — How to Operate

File-based communication between separate Claude Code sessions. Replaces manual copy-paste between terminals.

## Activate the skill when

- "send / tell / pass to <agent>"
- "delegate this to..."
- "hand off to..."
- "check inbox" / "do I have messages?"
- "reply to the brief from..."
- "who is active on the bus?"
- "what threads are open?"

**Do NOT activate for**:
- Local sub-agents (Explore, code-reviewer, Plan) — those are the `Agent` tool, not the bus.
- Generic chat between user and you in the same session.

## Step-by-step

### Step 1 — Determine sender

```bash
source "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/scripts/agent-team-os-lib.sh"
FROM=$(ab_detect_agent "$PWD")
```

If `FROM` is empty → "this workspace is outside AGENT_MAP, cannot send". Stop.

**Addresses**: `kai` (every session), `kai/crewmoor` (one session), and with two machines `kai@mini` / `kai@mini/crewmoor`. Without `@host` the message goes to the agent's own host or stays here. A message to an unreachable host is queued, not lost: say so to the user (stderr `queued`), do not retry. To answer a message use `ab_reply_address <file>` (or the MCP `reply_to`), never the bare `.from`: it brings the reply back to the right session and machine. `/bus` lists who is live on each machine.

### Step 2 — Check routing before proposing

Read `~/.agent-team-os/AGENT_MAP.json` field `.routing_rules.<from>.deny`. If `to` is in the deny list, do NOT propose a direct send. Suggest:
- "Routing blocks {from}→{to} ({reason}). Want me to route via a hub agent if one is configured?"

### Step 3 — Compose payload for the intent

Check `~/.agent-team-os/AGENT_MAP.json` field `.agents.<to>.capabilities` to see if the intent is supported. Add an intent only if documented.

**Payload schema for common intents**:

| Intent | Required fields | Recommended |
|--------|-----------------|-------------|
| `bug-fix` | `project`, `summary` | `project_path`, `deadline`, `acceptance_criteria` |
| `feature-spec` | `project`, `summary` | `deliverable`, `user_story`, `deadline` |
| `generate-screenshots` | `project`, `deliverable` | `spec_ref`, `count`, `dimensions` |
| `code-review` | `target` | `focus_areas`, `deadline` |
| `draft-post` | `topic`, `channel` | `audience`, `voice_refs`, `length`, `deadline` |
| `review-copy` | `target` | `voice_check`, `tone` |
| `plan-campaign` | `product`, `goal` | `timeline`, `channels`, `budget` |
| `handoff` | `state_summary`, `next_steps` | `blockers`, `files_touched`, `tests_status` |
| `question` | `question` | `context`, `deadline` |
| `architecture-review` | `target`, `decision_at_stake` | `options`, `constraints` |

**Don't invent** fields. If you're unsure what's needed, ask the user or just use `summary`.

### Step 4 — Fill `context_refs`

Messages should stay small. For heavy context use refs instead of embedding:

| URI scheme | When to use |
|------------|-------------|
| `file:///abs/path` | Local file (both sessions on same machine) |
| `graphiti://node/<uuid>` or `graphiti://search/<query>` | Decisions/history in a knowledge graph |
| `artifact://shared/<id>` | HTML/deck/PDF on shared storage |
| `wiki://<slug>` | Wiki/memory documents |
| `gh://<owner>/<repo>/issues/<n>` | GitHub issue/PR |
| `todoist://task/<id>` | Todoist task |

### Step 5 — Priority

- `urgent` → blocker, "can't proceed without", deadline passed. The UserPromptSubmit hook interrupts the recipient on their next turn.
- `high` → within 24h, important but not blocking.
- `normal` (default) → async, deadline in days.
- `low` → fire-and-forget, low priority.

Default to `normal` if the user doesn't specify.

### Step 6 — Preview required before sending

ALWAYS show a compact preview to the user BEFORE writing:

```
Draft message:
  ops → dev
  type: request | intent: bug-fix | priority: normal
  summary: scroll home stutters on Macbook M1
  deadline: 2026-05-20T18:00
  context_refs: 1 (file <project>/CLAUDE.md)
Confirm? (y to send)
```

Do NOT send without explicit confirmation.

### Step 7 — Write via helper

```bash
AB_FROM="$FROM" \
AB_TO="<to>" \
AB_TYPE="request" \
AB_INTENT="<intent>" \
AB_PRIORITY="normal" \
AB_PAYLOAD_JSON='{...valid JSON...}' \
AB_CONTEXT_REFS_JSON='["file://...", "..."]' \
AB_REQUIRES_RESPONSE=true \
AB_RESPONSE_BY="2026-05-20T18:00:00Z" \
MSG_ID=$(ab_write_message)
```

Exit codes:
- 0 = OK, `$MSG_ID` contains the new id
- 1 = recipient agent unknown
- 2 = routing denied
- 3 = lock failed (retry in a few seconds)
- 4 = malformed JSON

### Step 8 — Confirm and suggest next

After a successful send:

```
✓ Message sent: msg-... (thread: thread-...)
The recipient will see this in their inbox on next SessionStart.
View full history: /thread <thread-id>
```

## Reply patterns

For `/reply`:
- **accept**: I'm taking it on. `{status: "accepted", eta: "<iso>", notes: "..."}`
- **decline**: refused with reason. `{reason: "...", alternative: "<agent or approach>"}`
- **response**: deliverable complete. `{status: "done|partial", artifact_refs: [...], summary: "..."}`
- **confirm**: silent ack for event/info.

For `/handoff`: see `/handoff.md` command — pass full `state_summary` + explicit `next_steps`.

## Anti-patterns

- Payload >10KB inline — use `context_refs`.
- Secrets or credentials in messages — never.
- Sending without preview to the user.
- Inventing intents outside AGENT_MAP capabilities.
- Replying "done" without real output (no `artifact_refs`).
- Changing `thread_id` when replying (breaks the conversation).
- Broadcasts for specific tasks — only for global status/events.
- Force-sending if routing is denied — route via a hub agent.

## Example flow

```
User (in ops workspace): "Tell dev to look at the home scroll bug on MyApp, by Wednesday"
Skill:
  1. detect_agent → ops
  2. routing ops→dev OK
  3. compose preview:
     ops → dev (request, bug-fix, normal)
     summary: home scroll stutters on MyApp
     deadline: 2026-05-20
  4. User: "y"
  5. ab_write_message → msg-... sent
  6. "Sent. When you open the dev session, the brief will be in the inbox."
```

## GOD Dispatch playbook (Onda) — open & drive work sessions fast

When in GOD mode and dispatching agents inside Onda (see GOD-CONTRACT.md §Dispatch & Monitor), follow these guidelines — learned from real runs — to spawn fast and cheap:

**Before spawning:**
- Write the brief to the agent's inbox FIRST. The agent reads it via `/inbox` at boot and starts immediately — no long manual prompt needed.
- Keep the launch prompt minimal: `"You are <agent>. Run /inbox, /read <msg-id>, do the task, print ===DONE=== then /reply."`

**Spawning (per agent):**
- **Reuse the workspace's default pane.** Creating a workspace already spawns one terminal. Call `onda_workspace_layout` → use `activePaneId`. Do NOT `add_terminal` unless you need an extra pane — otherwise you get an empty stray pane.
- Spawn with `claude --dangerously-skip-permissions` for trusted repos/sandboxes → no Bash permission prompts mid-task (the #1 thing that stalls an agent).
- Handle the trust-folder prompt automatically: `wait_for("trust|Welcome")` → if trust, `send_keys(["Enter"])`.
- **Path gotcha (macOS):** `/tmp` resolves to `/private/tmp`; symlinked paths break `ab_detect_agent` (agent shows as `[Anon]`). Spawn on real, mapped paths (`~/Projects/...`); the detect normalizes symlinks (realpath) so canonical paths match.
- Submit the prompt: `terminal_run(prompt)` then ALWAYS `send_keys(["Enter"])` — `terminal_run` alone does NOT submit in the Claude TUI.

**Layout by agent count:**
- 1 agent → the workspace's default pane.
- 2 → `add_terminal direction:right` (side-by-side).
- 3–4 → `workspace_tile` quad (2×2 grid, all visible).
- >4 → separate tabs (avoid infinite rightward growth).

**Monitoring — cheap, not live:**
- NEVER `subscribe`+`poll` a Claude TUI continuously (100+ chunks/sec of spinner = token burn).
- Use `wait_for(/===DONE===/)` (blocks, costs nothing until match) + the agent's `/reply` on the bus (passive, lands in your inbox).
- `terminal_read`/`screenshot` only at intervals or on demand for visual checks.
- On completion: collect `/reply`, sign off, optionally `agent-team-os-harvest.sh <cwd>` for token+cost. Unmount workspaces you created (not Mario's existing ones).

**Fallback (no Onda):** if `mcp__onda__*` tools are absent, queue briefs and hand Mario `cd <ws> && claude` commands. Don't pretend to spawn.

## Where things live

- Authoritative spec: `~/agent-team-os/README.md` and `~/agent-team-os/scripts/agent-team-os-lib.sh`
- Runtime: `~/.agent-team-os/` (inboxes, registry, threads, outbox, locks)
- Bash helper: `~/.claude/scripts/agent-team-os-lib.sh`
- SessionStart hook: `~/.claude/hooks/agent-team-os-load.sh`
- UserPromptSubmit hook (urgent): `~/.claude/hooks/agent-team-os-urgent.sh`
- Slash commands: `~/.claude/commands/{bus,inbox,read,send,reply,handoff,thread}.md`

Everything is JSON, inspectable with `jq`, `ls`, `cat`.
