---
description: List pending messages for the current agent session (cwd decides agent and session). Use --all for every session of this agent.
---

Show the current agent's inbox.

Run via Bash:

```bash
LIB="${CLAUDE_PLUGIN_ROOT:-$HOME/.claude}/scripts/agent-team-os-lib.sh"
   [[ -f "$LIB" ]] || LIB="$HOME/.claude/scripts/agent-team-os-lib.sh"
   source "$LIB"

AGENT=$(ab_detect_agent "$PWD")
if [[ -z "$AGENT" ]]; then
  echo "This workspace is not mapped to any agent in AGENT_MAP.json."
  exit 0
fi

# v1.4: one agent can have several live sessions (e.g. two Kai in different
# workspaces). By default show only this session's messages — its own dir plus
# the shared broadcasts. `--all` shows every session's, labelled.
SLUG=$(ab_slug_for_cwd "$PWD")
SHOW_ALL=0
[[ "${1:-}" == "--all" ]] && SHOW_ALL=1

DISPLAY=$(ab_agent_display "$AGENT")
if [[ "$SHOW_ALL" -eq 1 ]]; then
  export AB_SESSION_SLUG=""
  COUNT=$(ab_count_inbox "$AGENT")
  echo "=== Inbox of ${DISPLAY} (${AGENT}) — ALL SESSIONS ==="
else
  export AB_SESSION_SLUG="$SLUG"
  COUNT=$(ab_count_inbox "$AGENT")
  echo "=== Inbox of ${DISPLAY} (${AGENT}/${SLUG}) ==="
fi

# Show sibling sessions so it is obvious that other queues exist.
SESSIONS=$(ab_list_sessions "$AGENT")
if [[ $(printf '%s\n' "$SESSIONS" | grep -c .) -gt 1 ]]; then
  echo "live sessions: $(printf '%s ' $SESSIONS)"
  [[ "$SHOW_ALL" -eq 0 ]] && echo "(use /inbox --all to see the other sessions' messages)"
fi

if [[ "$COUNT" -eq 0 ]]; then
  echo "Empty."
  exit 0
fi

echo "${COUNT} pending messages:"
echo ""
ab_list_inbox "$AGENT" | while IFS= read -r f; do
  [[ -z "$f" ]] && continue
  # Label where the message lives: a session dir (@slug) or the shared root.
  DIR=$(basename "$(dirname "$f")")
  if [[ "$DIR" == @* ]]; then
    WHERE="→ ${DIR#@}"
    [[ "${DIR#@}" != "$SLUG" ]] && WHERE="$WHERE [other session]"
  else
    WHERE="→ all sessions"
  fi
  echo "─── $(basename "$f" .json)  ${WHERE} ───"
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

Messages are labelled by destination: `→ <slug>` for one session, `→ all sessions` for a broadcast. If a sibling session has its own queue, it is named at the top.
