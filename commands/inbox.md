---
description: List pending messages for the current agent (determined by cwd).
---

Show the current agent's inbox.

Run via Bash:

```bash
source ~/.claude/scripts/agent-bus-lib.sh

AGENT=$(ab_detect_agent "$PWD")
if [[ -z "$AGENT" ]]; then
  echo "This workspace is not mapped to any agent in AGENT_MAP.json."
  exit 0
fi

DISPLAY=$(ab_agent_display "$AGENT")
COUNT=$(ab_count_inbox "$AGENT")

echo "=== Inbox of ${DISPLAY} (${AGENT}) ==="
if [[ "$COUNT" -eq 0 ]]; then
  echo "Empty."
  exit 0
fi

echo "${COUNT} pending messages:"
echo ""
ab_list_inbox "$AGENT" | while IFS= read -r f; do
  [[ -z "$f" ]] && continue
  echo "─── $(basename "$f" .json) ───"
  jq -r '
    "from: " + .from +
    "\ntype: " + .type + " / " + .intent +
    "\npriority: " + .priority +
    (if .response_by then "\nresponse_by: " + .response_by else "" end) +
    "\nthread: " + .thread_id +
    (if .payload.summary then "\nsummary: " + .payload.summary else "" end)
  ' "$f"
  echo ""
done
```

After the listing, suggest `/read <msg-id>` to open a message in full (with `context_refs` resolved), or `/reply <msg-id> <type>` to respond.
