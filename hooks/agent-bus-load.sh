#!/bin/bash
# SessionStart hook — register the current agent on the bus and show inbox summary.
# Identifies the agent from cwd via ~/.agent-bus/AGENT_MAP.json.
# If cwd doesn't match any rule, exits silently ("anonymous" session).

LIB="$HOME/.claude/scripts/agent-bus-lib.sh"
[[ -f "$LIB" ]] || exit 0
source "$LIB"

CWD="${CLAUDE_PROJECT_DIR:-$PWD}"
AGENT=$(ab_detect_agent "$CWD")

# No agent → silent exit (session outside AGENT_MAP)
[[ -z "$AGENT" ]] && exit 0

# Update registry
ab_update_registry "$AGENT" true "$CWD"

# Read inbox
COUNT=$(ab_count_inbox "$AGENT")
HIGH_COUNT=$(ab_count_inbox_priority "$AGENT")
DISPLAY=$(ab_agent_display "$AGENT")
ROLE=$(ab_agent_role "$AGENT")

echo "=== Agent Bus ==="
echo "You are ${DISPLAY} (${AGENT}). Role: ${ROLE}"
echo "Workspace: ${CWD}"
echo ""

if [[ "$COUNT" -eq 0 ]]; then
    echo "Inbox: empty."
else
    if [[ "$HIGH_COUNT" -gt 0 ]]; then
        echo "Inbox: ${COUNT} pending (${HIGH_COUNT} urgent/high)"
    else
        echo "Inbox: ${COUNT} pending"
    fi
    echo ""
    # List up to 5 most recent
    ab_list_inbox "$AGENT" | tail -5 | while IFS= read -r f; do
        [[ -z "$f" ]] && continue
        ab_msg_summary "$f"
    done
fi

echo ""
echo "Commands: /inbox  /read <msg-id>  /send <to> <intent>  /reply <msg-id> <type>  /bus"
echo "=== End Agent Bus ==="

# Inject persona file (if defined and existing) so the model adopts the
# correct identity/voice for this session. Failing silently is fine: the
# global CLAUDE.md still provides a sensible default behavior.
PERSONA_FILE=$(ab_agent_persona_file "$AGENT")
if [[ -n "$PERSONA_FILE" ]]; then
    echo ""
    echo "=== Persona: ${DISPLAY} ==="
    cat "$PERSONA_FILE"
    echo "=== End Persona ==="
fi

exit 0
