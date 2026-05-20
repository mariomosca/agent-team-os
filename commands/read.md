---
description: Read a message by id, resolve context_refs, and archive it to .read/.
---

Read message `$ARG` (full or partial id, prefix match).

Steps:

1. **Resolve the message**:

   ```bash
   LIB="${CLAUDE_PLUGIN_ROOT:-$HOME/.claude}/scripts/agent-bus-lib.sh"
   [[ -f "$LIB" ]] || LIB="$HOME/.claude/scripts/agent-bus-lib.sh"
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

5. **Archive** the message (move to `.read/`):

   ```bash
   ab_mark_read "$AGENT" "$MSG_PATH"
   ```

6. If the message has `requires_response: true` and `response_by` is set, remind about the deadline.
