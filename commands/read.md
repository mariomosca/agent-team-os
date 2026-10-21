---
description: Read a message by id, resolve context_refs, then consume it with an outcome (chiuso | riarma | risposto | letto).
---

Read message `$ARG` (full or partial id, prefix match).

Steps:

1. **Resolve the message**:

   ```bash
   LIB="${CLAUDE_PLUGIN_ROOT:-${CLAUDE_CONFIG_DIR:-$HOME/.claude}}/scripts/agent-team-os-lib.sh"
   [[ -f "$LIB" ]] || LIB="${CLAUDE_CONFIG_DIR:-$HOME/.claude}/scripts/agent-team-os-lib.sh"
   source "$LIB"
   AGENT=$(ab_detect_agent "$PWD")
   MSG_ID="$ARG"
   MSG_PATH=$(ab_resolve_msg_path "$AGENT" "$MSG_ID")
   [[ -z "$MSG_PATH" ]] && { echo "Message $MSG_ID not found in $AGENT inbox."; exit 1; }
   ```

2. **Print the full message**:

   ```bash
   jq '.' "$MSG_PATH"
   ```

3. **Resolve `context_refs`**. For each ref in `.context_refs[]`:
   - `file:///abs/path` → read the file (truncate if huge) and inline an excerpt.
   - `gh://owner/repo/issues/N` → if `gh` CLI is available, run `gh issue view N --repo owner/repo`.
   - `graphiti://node/<uuid>` or `graphiti://search/<query>` → if a Graphiti MCP is wired up, query it.
   - `wiki://<slug>` → if a wiki backend is available, fetch the entry.
   - Other URIs → just print the value.

4. **Synthesize for the user** in 3-5 lines: who sent it, what they're asking, deadline, what's needed to respond. Propose next action: `/reply <msg-id> accept|decline|response|confirm`, or "start the work and reply later".

5. **Consume it with an outcome** (bus v2.8): a message leaves the inbox only this way.

   - A return on a delegation of yours (it has a `task_id` and `waits/<you>/@*/<task_id>.json`
     exists): **chiuso** if the task is done, **riarma** if you keep waiting, **risposto** if the
     executor asked something (send the answer first with `/reply`, same thread and task_id).
   - A message "da agganciare" (`.task_link.by == "da_agganciare"`): pick the task among
     `.task_link.candidates` and pass it as third argument.
   - Anything else: **letto** if you only read it, **chiuso** once you dealt with it.

   ```bash
   ab_gestisci "$MSG_PATH" <chiuso|riarma|risposto|letto> [<task_id>]
   ```

6. If the message has `requires_response: true` and `response_by` is set, remind about the deadline.
