---
description: Toggle GOD orchestration mode for the current agent (on/off/status). Default OFF.
---

GOD mode makes the current agent behave as a domain orchestrator (v2): decompose an objective into tasks, PROPOSE assignments to the human, and dispatch only after confirmation. Default is OFF — the agent behaves normally. This command toggles it for the current session.

Parse the argument (`on`, `off`, or empty for status) and run:

```bash
LIB="${CLAUDE_PLUGIN_ROOT:-${CLAUDE_CONFIG_DIR:-$HOME/.claude}}/scripts/agent-team-os-lib.sh"
[[ -f "$LIB" ]] || LIB="${CLAUDE_CONFIG_DIR:-$HOME/.claude}/scripts/agent-team-os-lib.sh"
source "$LIB"

AB_HOME="${AB_HOME:-$HOME/.agent-team-os}"
AGENT=$(ab_detect_agent "$PWD" 2>/dev/null)
if [[ -z "$AGENT" ]]; then
  echo "No agent mapped to this workspace (cwd). GOD mode is per-agent."
  exit 0
fi

GOD_DIR="$AB_HOME/god-mode"
FLAG="$GOD_DIR/${AGENT}.on"
ARG="$(echo "$1" | tr '[:upper:]' '[:lower:]' | tr -d ' ')"
mkdir -p "$GOD_DIR"

case "$ARG" in
  on)
    # only agents declared as gods[] can enter GOD mode
    IS_GOD=$(jq -r --arg a "$AGENT" '(.gods // []) | index($a) | if . == null then "no" else "yes" end' "$AB_HOME/AGENT_MAP.json" 2>/dev/null)
    if [[ "$IS_GOD" != "yes" ]]; then
      echo "Agent '$AGENT' is not in gods[] — GOD mode is reserved for orchestrator agents. Edit AGENT_MAP.json gods[] to allow."
      exit 0
    fi
    touch "$FLAG"
    echo "=== GOD mode ON for '$AGENT' ==="
    echo "Load and follow the GOD contract below for this session."
    echo ""
    CONTRACT="${CLAUDE_PLUGIN_ROOT:-$HOME/.claude}/skills/agent-team-os/GOD-CONTRACT.md"
    [[ -f "$CONTRACT" ]] || CONTRACT="$HOME/.claude/skills/agent-team-os/GOD-CONTRACT.md"
    if [[ -f "$CONTRACT" ]]; then cat "$CONTRACT"; else
      echo "(GOD-CONTRACT.md not found in deployment — see repo docs/agentic-v2/GOD-CONTRACT.md)"
    fi
    ;;
  off)
    rm -f "$FLAG"
    echo "=== GOD mode OFF for '$AGENT' — back to normal behavior. ==="
    ;;
  *)
    if [[ -f "$FLAG" ]]; then
      echo "GOD mode: ON for '$AGENT'. Use /god off to disable."
    else
      echo "GOD mode: OFF for '$AGENT'. Use /god on to enable orchestration."
    fi
    ;;
esac
```

**When GOD mode is ON**, follow the contract: maintain awareness (listTasks on your TaskProvider via `ab_task_provider`), decompose objectives, PROPOSE assignments and wait for the human's confirmation, then dispatch via the bus + write tasks to the provider. Never dispatch without confirmation.
