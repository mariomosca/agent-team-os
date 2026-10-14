---
description: Agent Team OS hub — show roster, status, capabilities, routing rules.
---

Print the current state of the bus: which agents are configured, which sessions are live (an agent may have several at once, on this machine or on another host), and what routing rules apply.

Run via Bash:

```bash
LIB="${CLAUDE_PLUGIN_ROOT:-${CLAUDE_CONFIG_DIR:-$HOME/.claude}}/scripts/agent-team-os-lib.sh"
   [[ -f "$LIB" ]] || LIB="${CLAUDE_CONFIG_DIR:-$HOME/.claude}/scripts/agent-team-os-lib.sh"
   source "$LIB"

echo "=== Agent Team OS Roster ==="
if [[ ! -f "${AB_MAP:-$HOME/.agent-team-os/AGENT_MAP.json}" ]]; then
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
' "${AB_MAP:-$HOME/.agent-team-os/AGENT_MAP.json}"

# v2.5: on the laptop, exchange with the mini first (throttled to once per 30 s).
ab_collect_outbox >/dev/null 2>&1
SELF=$(ab_host_self)

echo ""
echo "Live sessions${SELF:+ on $SELF}:"
# v1.4: an agent can have several sessions at once (one per workspace). Each has
# its own registry entry, inbox and address `agent/slug`; the flat registry file
# can only ever describe one of them, so list the per-session entries instead.
ANY=0
for AGENT in $(jq -r '.agents | keys[]' "${AB_MAP:-$HOME/.agent-team-os/AGENT_MAP.json}" 2>/dev/null); do
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

# v2.5: the other machines. Sessions are read over SSH from their registry (cached
# 20 s); address them as agent@host or agent@host/slug.
for H in $(ab_remote_hosts); do
  CACHE=$(ab_remote_status "$H")
  OK=$(jq -r '.ok' "$CACHE" 2>/dev/null)
  echo ""
  if [[ "$OK" == "true" ]]; then
    echo "Live sessions on $H (via ssh, $(jq -r '.fetched_at' "$CACHE")):"
  else
    echo "Live sessions on $H: UNREACHABLE, last known ($(jq -r '.fetched_at // "never"' "$CACHE")):"
  fi
  ROWS=$(ab_list_remote_sessions "$H")
  [[ -z "$ROWS" ]] && echo "  (none)"
  printf '%s\n' "$ROWS" | while IFS=$'\t' read -r A S LAST PEND WS; do
    [[ -z "$A" ]] && continue
    printf '  - %-24s %s pending   last: %s\n' "${A}@${H}/${S}" "$PEND" "$LAST"
    printf '    %s\n' "$WS"
  done
  Q=$(find "$AB_HOME/outbox/$H" -name 'msg-*.json' -type f 2>/dev/null | wc -l | tr -d ' ')
  [[ "$Q" -gt 0 ]] && echo "  queued here for $H: $Q (delivered when it is reachable)"
  LC="$(ab_host_state_dir "$H")/last-collect.json"
  [[ -s "$LC" ]] && echo "  last collection: $(jq -r '.at + " " + .status + ", pulled " + (.pulled|tostring) + ", pushed " + (.pushed|tostring)' "$LC")"
done
# On the mini: what waits for a machine that collects from us (the laptop).
# find, not a glob: under zsh an outbox with no subfolder would abort the command.
find "$AB_HOME/outbox" -mindepth 1 -maxdepth 1 -type d 2>/dev/null | while read -r D; do
  H=$(basename "$D")
  ab_remote_hosts | grep -qx "$H" && continue
  Q=$(find "$D" -name 'msg-*.json' -type f 2>/dev/null | wc -l | tr -d ' ')
  [[ "$Q" -gt 0 ]] && echo "" && echo "Waiting for $H to collect: $Q message(s) in outbox/$H/"
done
```

Addresses with a host: `kai@mini` (every session of kai on the mini), `kai@mini/crewmoor` (one). Without `@host` a message goes to the agent's own host (`agents.<a>.host`, the hub agent to the hub host) or stays on this machine.

Sub-commands like `/bus status` or `/bus history` are not implemented in v1. Use `/inbox`, `/read`, `/send`, `/reply`, `/thread` directly.
