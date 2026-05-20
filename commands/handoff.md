---
description: Hand off the current task to another agent with auto-generated state summary. Args - "<to> [intent]".
---

Hand off the current task to agent `$1` with optional intent `$2` (default: `handoff`).

Steps:

1. **Parse args**: first token = `to`, optional second = `intent` (default `handoff`).

2. **Detect sender** and **check routing** (see `/send`).

3. **Auto-generate the state summary** from session context:

   - `state_summary`: what's been done so far in 3-5 bullets.
   - `next_steps`: what the recipient needs to pick up.
   - `blockers`: anything stuck or unresolved.
   - `files_touched`: list of file paths recently edited (if known).
   - `tests_status`: pass/fail/unknown (if relevant).

   **Don't invent state** you don't have. If you're at the start of a session with no context, ask the user what they want to hand off.

4. **Resolve recipient's workspace** from `AGENT_MAP.json` so the user knows where to open the next session:

   ```bash
   TO_WORKSPACE=$(jq -r --arg a "$1" '.rules[] | select(.agent == $a) | .pattern' "$HOME/.agent-bus/AGENT_MAP.json" | head -1)
   ```

5. **Show a preview** before sending:

   ```
   Handoff:
     <FROM> → <TO> (workspace: <path>)
     intent: <intent>
     state_summary: ...
     next_steps: ...
     blockers: ...
   Confirm? (y/n)
   ```

6. **Send via helper** with `type=handoff` and priority `high`:

   ```bash
   AB_FROM="$AGENT" \
   AB_TO="$1" \
   AB_TYPE="handoff" \
   AB_INTENT="${2:-handoff}" \
   AB_PRIORITY="high" \
   AB_PAYLOAD_JSON='{...}' \
   AB_CONTEXT_REFS_JSON='[...]' \
   AB_REQUIRES_RESPONSE=false \
   MSG_ID=$(ab_write_message)
   ```

7. Confirm with msg-id + thread-id. Tell the user: "Open a session in `<TO_WORKSPACE>` to continue."

Notes:
- Routing rules apply: if denied, suggest routing via a hub agent.
- Handoff is a **special case of `send`** with a richer payload — the message type is `handoff` so the recipient knows to consume the state directly instead of asking.
