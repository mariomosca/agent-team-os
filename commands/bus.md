---
description: Agent Team OS hub — show roster, status, capabilities, routing rules.
---

Print the current state of the bus: which agents are configured, which sessions are live (an agent may have several at once), and what routing rules apply.

Run via Bash:

```bash
LIB="${CLAUDE_PLUGIN_ROOT:-$HOME/.claude}/scripts/agent-team-os-lib.sh"
   [[ -f "$LIB" ]] || LIB="$HOME/.claude/scripts/agent-team-os-lib.sh"
   source "$LIB"

echo "=== Agent Team OS Roster ==="
if [[ ! -f "$HOME/.agent-team-os/AGENT_MAP.json" ]]; then
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
  "Routing:",
  (if (.cc_awareness.enabled // false)
   then "  full-mesh (graph) + cross-domain cc → " + (.cc_awareness.hub // "hub")
   else "  hub-and-spoke (deny-based)" end),
  ( [ .routing_rules // {} | to_entries[] | select((.value.deny // []) | length > 0) ] as $blocked
    | if ($blocked | length) > 0
      then ($blocked[] | "  " + .key + " ↛ " + ((.value.deny // []) | join(",")))
      else "  no deny-pairs (all agents can message all)" end
  )
' "$HOME/.agent-team-os/AGENT_MAP.json"

echo ""
echo "Live sessions:"
# v1.4: an agent can have several sessions at once (one per workspace). Each has
# its own registry entry, inbox and address `agent/slug`; the flat registry file
# can only ever describe one of them, so list the per-session entries instead.
ANY=0
for AGENT in $(jq -r '.agents | keys[]' "$HOME/.agent-team-os/AGENT_MAP.json" 2>/dev/null); do
  SESSIONS=$(ab_list_sessions "$AGENT")
  [[ -z "$SESSIONS" ]] && continue
  N=$(printf '%s\n' "$SESSIONS" | grep -c .)
  for SLUG in $SESSIONS; do
    ANY=1
    F="$(ab_registry_dir "$AGENT")/${SLUG}.json"
    LAST=$(jq -r '.last_seen // "?"' "$F" 2>/dev/null)
    WS=$(jq -r '.workspace_path // "?"' "$F" 2>/dev/null)
    PENDING=$(AB_SESSION_SLUG="$SLUG" ab_count_inbox "$AGENT")
    # Only qualify the address when it is actually needed to disambiguate.
    if [[ "$N" -gt 1 ]]; then ADDR="${AGENT}/${SLUG}"; else ADDR="$AGENT"; fi
    printf '  - %-24s %s pending   last: %s\n' "$ADDR" "$PENDING" "$LAST"
    printf '    %s\n' "$WS"
  done
  [[ "$N" -gt 1 ]] && echo "    ^ ${N} parallel sessions: address them as ${AGENT}/<slug>, or ${AGENT} to reach all"
done
[[ "$ANY" -eq 0 ]] && echo "  (none registered — sessions appear here after their SessionStart hook runs)"
```

Sub-commands like `/bus status` or `/bus history` are not implemented in v1. Use `/inbox`, `/read`, `/send`, `/reply`, `/thread` directly.
