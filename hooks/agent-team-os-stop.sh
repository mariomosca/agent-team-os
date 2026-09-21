#!/usr/bin/env bash
# Stop hook — drain-on-Stop (v2.0 §4).
# Blocks session close if the agent's inbox has unread messages.
# Loop guard: after CLAUDE_CODE_STOP_HOOK_BLOCK_CAP consecutive blocks (default 3),
# resets the counter and allows stop to prevent the agent from getting trapped.
#
# Output: JSON { "decision": "block", "reason": "..." } to stdout (block)
#         or exit 0 with no output (allow stop).
#
# NOT registered in settings.json automatically. Activate manually via skill update-config:
#   "Stop": [{ "hooks": [{ "type": "command",
#              "command": "~/.claude/hooks/agent-team-os-stop.sh" }] }]

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
# v1.4: scope this session by workspace slug, so two sessions of the same agent
# (e.g. two Kai) keep separate inboxes, cursors and registry entries.
AB_SESSION_SLUG=$(ab_slug_for_cwd "$CWD")
export AB_SESSION_SLUG
[[ -z "$AGENT" ]] && exit 0

CAP="${CLAUDE_CODE_STOP_HOOK_BLOCK_CAP:-3}"

# ab_drain_for_stop handles all logic: cursor read, loop guard, JSON output.
# It exits 0 (pass) or exits 1 after printing block JSON.
ab_drain_for_stop "$AGENT" "$CAP"
