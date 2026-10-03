#!/usr/bin/env bash
# Agent Team OS — installer
#
# Copies the helper library, hooks, slash commands and skill into your
# ~/.claude/ folder, and creates ~/.agent-team-os/ runtime structure.
#
# Idempotent: re-running it overwrites the bundled files but leaves your
# AGENT_MAP.json untouched if it already exists.

set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CLAUDE_HOME="${CLAUDE_HOME:-$HOME/.claude}"
BUS_HOME="${AB_HOME:-$HOME/.agent-team-os}"

echo "Agent Team OS installer"
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

echo "Copying helper library → $CLAUDE_HOME/scripts/agent-team-os-lib.sh"
cp "$REPO_DIR/scripts/agent-team-os-lib.sh" "$CLAUDE_HOME/scripts/agent-team-os-lib.sh"
chmod +x "$CLAUDE_HOME/scripts/agent-team-os-lib.sh"

echo "Copying v2.0 modules → $CLAUDE_HOME/scripts/"
cp "$REPO_DIR/scripts/agent-team-os-taskprovider.sh" "$CLAUDE_HOME/scripts/agent-team-os-taskprovider.sh"
cp "$REPO_DIR/scripts/agent-team-os-harvest.sh"      "$CLAUDE_HOME/scripts/agent-team-os-harvest.sh"
chmod +x "$CLAUDE_HOME/scripts/agent-team-os-taskprovider.sh" "$CLAUDE_HOME/scripts/agent-team-os-harvest.sh"

echo "Copying hooks → $CLAUDE_HOME/hooks/"
cp "$REPO_DIR/hooks/agent-team-os-load.sh"   "$CLAUDE_HOME/hooks/agent-team-os-load.sh"
cp "$REPO_DIR/hooks/agent-team-os-urgent.sh" "$CLAUDE_HOME/hooks/agent-team-os-urgent.sh"
cp "$REPO_DIR/hooks/agent-team-os-stop.sh"   "$CLAUDE_HOME/hooks/agent-team-os-stop.sh"
chmod +x "$CLAUDE_HOME/hooks/agent-team-os-load.sh" "$CLAUDE_HOME/hooks/agent-team-os-urgent.sh" "$CLAUDE_HOME/hooks/agent-team-os-stop.sh"
# NOTE: the Stop hook is COPIED but NOT auto-registered in settings.json.
# Activate manually (gated) via skill update-config — see docs/agentic-v2/SETTINGS-DIFF.md

echo "Copying slash commands → $CLAUDE_HOME/commands/"
for cmd in bus inbox read send reply handoff thread god; do
  cp "$REPO_DIR/commands/${cmd}.md" "$CLAUDE_HOME/commands/${cmd}.md"
done

echo "Copying skill → $CLAUDE_HOME/skills/agent-team-os/"
mkdir -p "$CLAUDE_HOME/skills/agent-team-os"
cp "$REPO_DIR/skills/agent-team-os/SKILL.md" "$CLAUDE_HOME/skills/agent-team-os/SKILL.md"
# v2: GOD contract shipped alongside the skill (used by /god on)
cp "$REPO_DIR/skills/agent-team-os/GOD-CONTRACT.md" "$CLAUDE_HOME/skills/agent-team-os/GOD-CONTRACT.md" 2>/dev/null || true

echo "Copying bus-band mod → $CLAUDE_HOME/mods/bus-band/"
rm -rf "$CLAUDE_HOME/mods/bus-band"
mkdir -p "$CLAUDE_HOME/mods/bus-band"
cp -R "$REPO_DIR/mods/bus-band/.claude-plugin" "$REPO_DIR/mods/bus-band/hooks" "$REPO_DIR/mods/bus-band/types" "$CLAUDE_HOME/mods/bus-band/"
rm -rf "$CLAUDE_HOME/mods/bus-band/.claude-plugin/types"
# NOTE: copied, NOT loaded. Opt in via CLAUDE_CODE_PLUGIN_DIRS (see step 4 below).

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
             { "type": "command", "command": "~/.claude/hooks/agent-team-os-load.sh" }
           ],
           "UserPromptSubmit": [
             { "type": "command", "command": "~/.claude/hooks/agent-team-os-urgent.sh" }
           ]
         }
       }
JSON
echo ""
echo "  3. (Optional, v2) Enable the drain-on-Stop hook — blocks a session from"
echo "     closing while its inbox has unhandled messages. NOT auto-registered."
echo "     Add this to the \"hooks\" block above, then test per docs/agentic-v2/SETTINGS-DIFF.md:"
echo ""
cat <<'JSON'
           "Stop": [
             { "hooks": [ { "type": "command", "command": "~/.claude/hooks/agent-team-os-stop.sh" } ] }
           ]
JSON
echo ""
echo "  4. (Optional, Claude Code 2.1.287+) Load the bus-band mod in every session —"
echo "     a band above the prompt with pending messages. Add to the \"env\" block:"
echo ""
cat <<JSON
           "CLAUDE_CODE_PLUGIN_DIRS": "$CLAUDE_HOME/mods/bus-band"
JSON
echo ""
echo "  5. Open a Claude Code session in a mapped workspace and try /inbox or /bus."
