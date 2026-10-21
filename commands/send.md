---
description: Send a new message from the current agent. Args - "<to> <intent>" (e.g. "dev bug-fix"). Wizard prompts for payload.
---

Send a new message to agent `$1` with intent `$2`.

Steps:

1. **Parse args** — format `<to> <intent>`. Examples:
   - `dev bug-fix` → ask dev to fix a bug
   - `growth draft-post` → ask growth to draft a post
   - `ops handoff` → hand off current task to ops

   `<to>` is an address: `kai` (every session of kai), `kai/crewmoor` (one session),
   and with two machines (v2.5) `kai@mini` or `kai@mini/crewmoor`. Without `@host`
   the message goes to the agent's own host (`agents.<a>.host`; the hub agent lives on
   the hub host) or stays on this machine. Run `/bus` to see who is live where.

2. **Detect sender** from cwd:

   ```bash
   LIB="${CLAUDE_PLUGIN_ROOT:-${CLAUDE_CONFIG_DIR:-$HOME/.claude}}/scripts/agent-team-os-lib.sh"
   [[ -f "$LIB" ]] || LIB="${CLAUDE_CONFIG_DIR:-$HOME/.claude}/scripts/agent-team-os-lib.sh"
   source "$LIB"
   FROM=$(ab_detect_agent "$PWD")
   ```

   If `FROM` is empty → stop: "Workspace not mapped to any agent. Cannot send."

3. **Check routing** in `~/.agent-team-os/AGENT_MAP.json` under `.routing_rules[$FROM].deny`. If `TO` is denied, explain the reason and suggest routing via a hub agent if one is configured.

4. **Compose the payload** by asking the user the fields appropriate for the intent:

   - `bug-fix / feature-spec / refactor`:
     - `project` (required), `project_path` (required), `summary` (required)
     - `deadline`, `acceptance_criteria`, `context_refs` (optional)

   - `generate-screenshots / ui-review`:
     - `project`, `project_path`, `summary`, `deliverable`, `deadline`

   - `draft-post / review-copy / plan-campaign`:
     - `topic` or `subject`, `audience`, `channel`, `voice_refs`, `deadline`

   - `code-review / architecture-review`:
     - `target` (file/PR/commit), `focus_areas`, `deadline`

   - `handoff`:
     - `state_summary`, `next_steps`, `blockers` (optional)

   - `question`:
     - `question`, `context` (optional)

5. **Kind (mandatory for request, brief, handoff — BS21)**: ask the user, or decide from what
   they said, whether this is a **delega** (they want the outcome back: the bus registers the
   wait on this session and wakes it on every return) or a **comunicazione** (nothing comes
   back, nobody is woken). There is no default and no guessing from other fields: if it is
   not clear, ask. `response`, `question` and `event` take no kind. For a delegation the
   shortcut is `/delegate`.

   **Priority**: default `normal`. If the user says "urgent" / "blocker" / "asap" → `urgent` or `high`.

6. **Show a preview** before sending:

   ```
   Sending:
     from: <FROM> → to: <TO>
     intent: <INTENT> (priority: <PRIO>)
     kind: delega | comunicazione
     summary: ...
     deadline: ...
   Confirm? (y/n)
   ```

7. **Only after explicit confirmation**, write via helper:

   ```bash
   AB_FROM="$FROM" \
   AB_TO="<to>" \
   AB_TYPE="<type>" \
   AB_INTENT="<intent>" \
   AB_KIND="<delega|comunicazione>" \
   AB_PRIORITY="<priority>" \
   AB_PAYLOAD_JSON='{...}' \
   AB_CONTEXT_REFS_JSON='[...]' \
   AB_REQUIRES_RESPONSE=true \
   AB_RESPONSE_BY="<iso>" \
   MSG_ID=$(ab_write_message)
   echo "Sent: $MSG_ID"
   ```

   Notes:
   - Without `AB_KIND` a request, brief or handoff is refused (exit 5, the message
     explains the choice). With `AB_KIND=delega` stderr says `attesa registrata t-...`
     and the message carries that `task_id`; `requires_response` is forced true.
   - Another host: on the laptop the message is written straight into the mini's inbox
     over SSH; if the mini is unreachable it waits in `outbox/mini/` (stderr says
     `queued`) and goes at the next collection. On the mini, messages for the laptop
     always wait in `outbox/laptop/` until the laptop collects them. Not an error.
   - `type` defaults to `request`. Use `brief` for long tasks, `question` for quick questions, `event` for fire-and-forget notifications.
   - `requires_response` = true if you need a reply; false for events.
   - If routing is denied (exit code 2), explain and suggest an alternative.

8. Confirm with msg-id and thread-id, and for a delegation the task_id of the wait. The user can run `/thread <id>` to see the full history.
