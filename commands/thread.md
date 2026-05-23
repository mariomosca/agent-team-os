---
description: Show a thread's full history. Args - "<thread-id>" (or empty for recent threads).
---

Show thread `$1` history, or list recent open threads if no arg.

Steps:

1. **No arg** → list recent threads:

   ```bash
   echo "=== Recent threads ==="
   ls -t "$HOME"/.agent-team-os/threads/*.json 2>/dev/null | head -10 | while IFS= read -r f; do
     [[ -f "$f" ]] || continue
     jq -r '
       "─── " + .id + " ───" +
       "\n  subject: " + .subject +
       "\n  participants: " + (.participants | join(", ")) +
       "\n  status: " + .status +
       "\n  messages: " + (.messages | length | tostring) +
       "\n  opened: " + .opened
     ' "$f"
   done
   ```

2. **With arg** → show full thread:

   ```bash
   THREAD_ID="$1"
   THREAD_FILE="$HOME/.agent-team-os/threads/${THREAD_ID}.json"
   [[ -f "$THREAD_FILE" ]] || { echo "Thread $THREAD_ID not found."; exit 1; }
   jq '.' "$THREAD_FILE"
   ```

3. **Resolve each message** in the thread — for each entry in `.messages[]`, find the actual message file (check both `inboxes/<agent>/` and `inboxes/<agent>/.read/`) and print a short summary (`from`, `to`, `type`, `intent`, `summary`, `ts`).

4. After the listing, if status is `open` propose `/reply <last-msg-id> <type>` to close it, or `/read <msg-id>` for details on a specific message.
