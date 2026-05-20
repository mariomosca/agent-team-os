---
description: Reply to a message in its thread. Args - "<msg-id> <type>" (type=accept|decline|response|confirm).
---

Reply to message `$1` with type `$2`.

Steps:

1. **Resolve the original message**:

   ```bash
   source ~/.claude/scripts/agent-bus-lib.sh
   AGENT=$(ab_detect_agent "$PWD")
   ORIG_PATH=$(ab_resolve_msg_path "$AGENT" "$1")
   [[ -z "$ORIG_PATH" ]] && { echo "Message $1 not found."; exit 1; }
   ORIG_FROM=$(jq -r '.from' "$ORIG_PATH")
   THREAD=$(jq -r '.thread_id' "$ORIG_PATH")
   ```

2. **Validate type**:
   - `accept` → I'm taking it on. Payload: `{status: "accepted", eta: "<iso>", notes: "..."}`
   - `decline` → I refuse with a reason. Payload: `{reason: "...", alternative: "<agent or approach>"}`
   - `response` → final deliverable. Payload: `{status: "done|partial", artifact_refs: [...], summary: "..."}`
   - `confirm` → silent ack for an event/info message.

3. **Compose the payload** appropriate for the type:
   - For `response` on technical intents: include `status`, `summary`, `artifact_refs` (file://, gh://, etc.), optional `commit_sha`, `tests_status`.
   - For `accept`: include `eta` (ISO datetime).
   - For `decline`: include a clear `reason`.

4. **Show a preview** and ask for confirmation.

5. **Send via helper** with `AB_IN_REPLY_TO` and `AB_THREAD_ID` set so the reply stays in the same thread:

   ```bash
   AB_FROM="$AGENT" \
   AB_TO="$ORIG_FROM" \
   AB_TYPE="<type>" \
   AB_INTENT="<keep-same-intent-as-original>" \
   AB_THREAD_ID="$THREAD" \
   AB_IN_REPLY_TO="$1" \
   AB_PAYLOAD_JSON='{...}' \
   AB_REQUIRES_RESPONSE=false \
   MSG_ID=$(ab_write_message)
   ```

6. **Archive the original** if it was still in the inbox:

   ```bash
   case "$ORIG_PATH" in
     */inboxes/*/.read/*) ;;  # already archived
     *) ab_mark_read "$AGENT" "$ORIG_PATH" ;;
   esac
   ```

7. If `type` is `response` or `confirm`, the helper auto-closes the thread. Tell the user "thread closed".
