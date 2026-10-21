#!/bin/bash
# SessionEnd hook: la sessione esce dal registro (registry/<agente>.d/<slug>.json).
# Identifica l'agente dalla cartella via AGENT_MAP.json, la sessione dal suo slug.
# Exit 0 anche sugli errori: non deve mai bloccare la chiusura.
# Le sessioni morte senza SessionEnd (kill -9, tmux chiuso) le toglie la sveglia
# ogni 10 minuti (ab_registry_prune, OR18-F3.3).

_HOOK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ -n "${CLAUDE_PLUGIN_ROOT:-}" && -f "$CLAUDE_PLUGIN_ROOT/scripts/agent-team-os-lib.sh" ]]; then
    LIB="$CLAUDE_PLUGIN_ROOT/scripts/agent-team-os-lib.sh"
elif [[ -f "$_HOOK_DIR/../scripts/agent-team-os-lib.sh" ]]; then
    # Accanto all'hook: vale per qualunque CLAUDE_HOME (es. ~/.config/claude-mini).
    LIB="$_HOOK_DIR/../scripts/agent-team-os-lib.sh"
else
    LIB="$HOME/.claude/scripts/agent-team-os-lib.sh"
fi
[[ -f "$LIB" ]] || exit 0
# shellcheck disable=SC1090
source "$LIB" 2>/dev/null || exit 0

CWD="${CLAUDE_PROJECT_DIR:-$PWD}"
AGENT=$(ab_detect_agent "$CWD" 2>/dev/null)
[[ -z "$AGENT" ]] && exit 0

# Stesso slug della sessione (vedi agent-team-os-load.sh): chi porta AB_SESSION_SLUG
# chiude solo la propria voce, non quella dell'altra sessione nella stessa cartella
# (BS21-F1.5: ab_update_registry ora lo passa davvero).
AB_SESSION_SLUG="${AB_SESSION_SLUG:-$(ab_slug_for_cwd "$CWD")}"
export AB_SESSION_SLUG

ab_update_registry "$AGENT" false "$CWD" 2>/dev/null
exit 0
