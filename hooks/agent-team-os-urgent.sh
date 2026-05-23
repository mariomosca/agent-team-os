#!/usr/bin/env bash
# UserPromptSubmit hook — alert if the current agent's inbox has urgent/high messages.
# Cost: ~15ms per turn. Silent if no match.

set -uo pipefail

# Resolve the helper library (plugin mode or manual install).
if [[ -n "${CLAUDE_PLUGIN_ROOT:-}" && -f "$CLAUDE_PLUGIN_ROOT/scripts/agent-team-os-lib.sh" ]]; then
    LIB="$CLAUDE_PLUGIN_ROOT/scripts/agent-team-os-lib.sh"
elif [[ -f "$HOME/.claude/scripts/agent-team-os-lib.sh" ]]; then
    LIB="$HOME/.claude/scripts/agent-team-os-lib.sh"
else
    exit 0
fi
# shellcheck disable=SC1090
source "$LIB"

CWD="${CLAUDE_PROJECT_DIR:-$PWD}"
AGENT=$(ab_detect_agent "$CWD" 2>/dev/null)
[[ -z "$AGENT" ]] && exit 0

# Update last_seen silently
ab_update_registry "$AGENT" true "$CWD" 2>/dev/null

# Count urgent/high pending
HIGH_COUNT=$(ab_count_inbox_priority "$AGENT" 2>/dev/null || echo 0)
[[ "$HIGH_COUNT" -eq 0 ]] && exit 0

# Build brief listing (max 3 messages, plain text)
LIST=""
COUNT=0
while IFS= read -r f; do
  [[ -z "$f" ]] && continue
  PRIO=$(jq -r '.priority // "normal"' "$f" 2>/dev/null)
  if [[ "$PRIO" == "urgent" || "$PRIO" == "high" ]]; then
    SUMMARY=$(jq -r '
      "  - [" + .priority + "] " + .type + "/" + .intent + " from " + .from +
      " (id: " + (.id | .[0:30]) + ")" +
      (if .payload.summary then "\n    " + (.payload.summary | .[0:120]) else "" end)
    ' "$f" 2>/dev/null)
    LIST="${LIST}${SUMMARY}
"
    COUNT=$((COUNT+1))
    [[ $COUNT -ge 3 ]] && break
  fi
done < <(ab_list_inbox "$AGENT")

# Build full text then JSON-encode via python (safe with newlines and quotes)
MSG="[agent-team-os] You have ${HIGH_COUNT} urgent/high messages in inbox for ${AGENT}:
${LIST}
Consider pausing to read them with /read <msg-id>, or /inbox for an overview."

python3 -c "
import json, sys
msg = sys.stdin.read()
out = {
    'hookSpecificOutput': {
        'hookEventName': 'UserPromptSubmit',
        'additionalContext': msg
    }
}
print(json.dumps(out))
" <<< "$MSG"
