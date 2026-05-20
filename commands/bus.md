---
description: Agent Bus hub — show roster, status, capabilities, routing rules.
---

Print the current state of the bus: which agents are configured, who is active, and what routing rules apply.

Run via Bash:

```bash
LIB="${CLAUDE_PLUGIN_ROOT:-$HOME/.claude}/scripts/agent-bus-lib.sh"
   [[ -f "$LIB" ]] || LIB="$HOME/.claude/scripts/agent-bus-lib.sh"
   source "$LIB"

echo "=== Agent Bus Roster ==="
if [[ ! -f "$HOME/.agent-bus/AGENT_MAP.json" ]]; then
  echo "AGENT_MAP.json not found. Run install.sh first."
  exit 0
fi

jq -r '
  "Agents (" + (.agents | length | tostring) + "):",
  (.agents | to_entries[] | "  - " + .key +
    (if .value.role then " — " + .value.role else "" end) +
    (if .value.capabilities then "  caps: " + (.value.capabilities | join(", ")) else "" end)
  ),
  "",
  "Routing rules:",
  (.routing_rules // {} | to_entries[] |
    if (.value.deny // []) | length > 0
    then "  " + .key + " ↛ " + ((.value.deny // []) | join(","))
    else empty end
  )
' "$HOME/.agent-bus/AGENT_MAP.json"

echo ""
echo "Active sessions (last 5min):"
for f in "$HOME"/.agent-bus/registry/*.json; do
  [[ -f "$f" ]] || continue
  AGENT=$(jq -r '.name // (input_filename | split("/")[-1] | rtrimstr(".json"))' "$f" 2>/dev/null)
  LAST=$(jq -r '.last_seen // ""' "$f")
  ACTIVE=$(jq -r '.active // false' "$f")
  [[ "$ACTIVE" == "true" ]] && echo "  - $AGENT  (last: $LAST)"
done
```

Sub-commands like `/bus status` or `/bus history` are not implemented in v1. Use `/inbox`, `/read`, `/send`, `/reply`, `/thread` directly.
