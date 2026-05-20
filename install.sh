#!/usr/bin/env bash
# Agent Bus — installer
#
# Copies the helper library, hooks, slash commands and skill into your
# ~/.claude/ folder, and creates ~/.agent-bus/ runtime structure.
#
# Idempotent: re-running it overwrites the bundled files but leaves your
# AGENT_MAP.json untouched if it already exists.

set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CLAUDE_HOME="${CLAUDE_HOME:-$HOME/.claude}"
BUS_HOME="${AB_HOME:-$HOME/.agent-bus}"

echo "Agent Bus installer"
echo "  source repo: $REPO_DIR"
echo "  Claude home: $CLAUDE_HOME"
echo "  Bus  home:   $BUS_HOME"
echo ""

# --- Dependency check ---

if ! command -v jq >/dev/null 2>&1; then
  echo "ERROR: jq is required. Install with:"
  echo "  brew install jq         (macOS)"
  echo "  sudo apt install jq     (Debian/Ubuntu)"
  exit 1
fi

# --- Layout target directories ---

mkdir -p "$CLAUDE_HOME/scripts"
mkdir -p "$CLAUDE_HOME/hooks"
mkdir -p "$CLAUDE_HOME/commands"
mkdir -p "$CLAUDE_HOME/skills"
mkdir -p "$BUS_HOME/inboxes"
mkdir -p "$BUS_HOME/registry"
mkdir -p "$BUS_HOME/threads"
mkdir -p "$BUS_HOME/outbox"
mkdir -p "$BUS_HOME/locks"

# --- Copy bundled files ---

echo "Copying helper library → $CLAUDE_HOME/scripts/agent-bus-lib.sh"
cp "$REPO_DIR/scripts/agent-bus-lib.sh" "$CLAUDE_HOME/scripts/agent-bus-lib.sh"
chmod +x "$CLAUDE_HOME/scripts/agent-bus-lib.sh"

echo "Copying hooks → $CLAUDE_HOME/hooks/"
cp "$REPO_DIR/hooks/agent-bus-load.sh"   "$CLAUDE_HOME/hooks/agent-bus-load.sh"
cp "$REPO_DIR/hooks/agent-bus-urgent.sh" "$CLAUDE_HOME/hooks/agent-bus-urgent.sh"
chmod +x "$CLAUDE_HOME/hooks/agent-bus-load.sh" "$CLAUDE_HOME/hooks/agent-bus-urgent.sh"

echo "Copying slash commands → $CLAUDE_HOME/commands/"
for cmd in bus inbox read send reply handoff thread; do
  cp "$REPO_DIR/commands/${cmd}.md" "$CLAUDE_HOME/commands/${cmd}.md"
done

echo "Copying skill → $CLAUDE_HOME/skills/agent-bus/"
mkdir -p "$CLAUDE_HOME/skills/agent-bus"
cp "$REPO_DIR/skills/agent-bus/SKILL.md" "$CLAUDE_HOME/skills/agent-bus/SKILL.md"

# --- AGENT_MAP starter ---

if [[ -f "$BUS_HOME/AGENT_MAP.json" ]]; then
  echo "AGENT_MAP.json already exists at $BUS_HOME/AGENT_MAP.json — leaving it untouched."
else
  echo "Creating starter AGENT_MAP.json at $BUS_HOME/AGENT_MAP.json"
  cp "$REPO_DIR/examples/AGENT_MAP.example.json" "$BUS_HOME/AGENT_MAP.json"
  # Best-effort persona files (optional)
  if [[ -d "$REPO_DIR/examples/personas" ]]; then
    mkdir -p "$BUS_HOME/personas"
    cp "$REPO_DIR/examples/personas/"*.md "$BUS_HOME/personas/" 2>/dev/null || true
  fi
fi

# --- Per-agent inbox + registry bootstrap ---

jq -r '.agents | keys[]' "$BUS_HOME/AGENT_MAP.json" 2>/dev/null | while IFS= read -r agent; do
  [[ -z "$agent" ]] && continue
  mkdir -p "$BUS_HOME/inboxes/$agent"
  REG="$BUS_HOME/registry/${agent}.json"
  if [[ ! -f "$REG" ]]; then
    jq -n --arg name "$agent" '{
      name: $name,
      active: false,
      last_seen: null,
      workspace_path: null,
      session_started: null
    }' > "$REG"
  fi
done

# --- Settings.json hint ---

echo ""
echo "Install complete."
echo ""
echo "Next steps:"
echo "  1. Edit $BUS_HOME/AGENT_MAP.json — map your workspaces to agent names."
echo "  2. Register the hooks in ~/.claude/settings.json (or ~/.claude-code/settings.json):"
echo ""
cat <<'JSON'
       {
         "hooks": {
           "SessionStart": [
             { "type": "command", "command": "~/.claude/hooks/agent-bus-load.sh" }
           ],
           "UserPromptSubmit": [
             { "type": "command", "command": "~/.claude/hooks/agent-bus-urgent.sh" }
           ]
         }
       }
JSON
echo ""
echo "  3. Open a Claude Code session in a mapped workspace and try /inbox or /bus."
