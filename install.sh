#!/usr/bin/env bash
# Agent Team OS — installer
#
# Copies the helper library, hooks, slash commands and skill into your
# ~/.claude/ folder, and creates ~/.agent-team-os/ runtime structure.
#
# Idempotent: re-running it overwrites the bundled files but leaves your
# AGENT_MAP.json untouched if it already exists.
#
# Options (v2.5, two machines — see README "Two machines"):
#   --host <name>       this machine's name on the bus (writes $AB_HOME/HOST),
#                       e.g. --host mini on the Mac mini. The hub needs none.
#   --host-sync         install the launchd collector (every 60 s) for each host this
#                       machine reaches over SSH (hosts.<h>.ssh in AGENT_MAP.json).
#                       Run it on the laptop. No such host → nothing installed.
#   --register-hooks    add the SessionStart + UserPromptSubmit hooks to
#                       $CLAUDE_HOME/settings.json (backup first, idempotent).
#                       The Stop hook stays opt-in.
#
# Env: CLAUDE_HOME (default ~/.claude, e.g. ~/.config/claude-mini), AB_HOME
# (default ~/.agent-team-os), LAUNCH_AGENTS_DIR (default ~/Library/LaunchAgents),
# AB_NO_LAUNCHCTL=1 to write the plist without loading it.

set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CLAUDE_HOME="${CLAUDE_HOME:-$HOME/.claude}"
BUS_HOME="${AB_HOME:-$HOME/.agent-team-os}"

HOST_NAME=""
HOST_SYNC=0
REGISTER_HOOKS=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --host) HOST_NAME="${2:?--host needs a name}"; shift 2 ;;
    --host=*) HOST_NAME="${1#--host=}"; shift ;;
    --host-sync) HOST_SYNC=1; shift ;;
    --register-hooks) REGISTER_HOOKS=1; shift ;;
    -h|--help) sed -n '2,24p' "$0"; exit 0 ;;
    *) echo "Unknown option: $1 (see --help)"; exit 1 ;;
  esac
done

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
cp "$REPO_DIR/scripts/agent-team-os-collect.sh"      "$CLAUDE_HOME/scripts/agent-team-os-collect.sh"
chmod +x "$CLAUDE_HOME/scripts/agent-team-os-taskprovider.sh" "$CLAUDE_HOME/scripts/agent-team-os-harvest.sh" \
         "$CLAUDE_HOME/scripts/agent-team-os-collect.sh"

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

# --- v2.5: this machine's host name ---

if [[ -n "$HOST_NAME" ]]; then
  if ! jq -e --arg h "$HOST_NAME" '(.hosts // {}) | has($h)' "$BUS_HOME/AGENT_MAP.json" >/dev/null 2>&1; then
    echo "WARN: host '$HOST_NAME' is not in \"hosts\" of $BUS_HOME/AGENT_MAP.json."
    echo "      Copy the hub's AGENT_MAP.json here (same file on every machine), then re-run."
  fi
  echo "$HOST_NAME" > "$BUS_HOME/HOST"
  echo "This machine is '$HOST_NAME' on the bus ($BUS_HOME/HOST)."
fi

# --- v2.5: register hooks in settings.json ---

if [[ "$REGISTER_HOOKS" -eq 1 ]]; then
  SETTINGS="$CLAUDE_HOME/settings.json"
  [[ -s "$SETTINGS" ]] || echo '{}' > "$SETTINGS"
  if ! jq empty "$SETTINGS" 2>/dev/null; then
    echo "ERROR: $SETTINGS is not valid JSON; hooks not registered."
    exit 1
  fi
  cp "$SETTINGS" "$SETTINGS.bak-$(date +%Y%m%d-%H%M%S)"
  jq --arg load "$CLAUDE_HOME/hooks/agent-team-os-load.sh" \
     --arg urgent "$CLAUDE_HOME/hooks/agent-team-os-urgent.sh" '
    def ensure($event; $cmd; $name):
      if ([.hooks[$event][]?.hooks[]?.command // empty] | any(endswith($name)))
      then .
      else .hooks[$event] = ((.hooks[$event] // []) + [{hooks: [{type: "command", command: $cmd}]}])
      end;
    .hooks = (.hooks // {})
    | ensure("SessionStart"; $load; "agent-team-os-load.sh")
    | ensure("UserPromptSubmit"; $urgent; "agent-team-os-urgent.sh")
  ' "$SETTINGS" > "$SETTINGS.tmp"
  if [[ -s "$SETTINGS.tmp" ]] && jq empty "$SETTINGS.tmp" 2>/dev/null; then
    mv "$SETTINGS.tmp" "$SETTINGS"
    echo "Hooks registered in $SETTINGS (SessionStart, UserPromptSubmit)."
  else
    rm -f "$SETTINGS.tmp"
    echo "ERROR: could not update $SETTINGS; left untouched."
    exit 1
  fi
fi

# --- v2.5: launchd collector on the machine that opens SSH (the laptop) ---

if [[ "$HOST_SYNC" -eq 1 ]]; then
  SELF="${AB_HOST:-}"
  [[ -z "$SELF" && -s "$BUS_HOME/HOST" ]] && SELF=$(head -1 "$BUS_HOME/HOST" | tr -d '[:space:]')
  [[ -z "$SELF" ]] && SELF=$(jq -r '(.hosts // {}) | to_entries[] | select(.value.hub == true) | .key' "$BUS_HOME/AGENT_MAP.json" 2>/dev/null | head -1)
  REMOTES=$(jq -r --arg self "$SELF" '(.hosts // {}) | to_entries[]
    | select(.key != $self and ((.value.ssh // "") != "")) | .key' "$BUS_HOME/AGENT_MAP.json" 2>/dev/null || true)
  if [[ -z "$REMOTES" ]]; then
    echo "--host-sync: no host with \"ssh\" in AGENT_MAP.json other than '$SELF'. Nothing installed."
  fi
  LA_DIR="${LAUNCH_AGENTS_DIR:-$HOME/Library/LaunchAgents}"
  mkdir -p "$LA_DIR" "$BUS_HOME/logs"
  for H in $REMOTES; do
    LABEL="com.agent-team-os.raccolta-${H}"
    PLIST="$LA_DIR/${LABEL}.plist"
    cat > "$PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>${LABEL}</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string>${CLAUDE_HOME}/scripts/agent-team-os-collect.sh</string>
    <string>${H}</string>
  </array>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key><string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
    <key>AB_HOME</key><string>${BUS_HOME}</string>
  </dict>
  <key>StartInterval</key><integer>60</integer>
  <key>RunAtLoad</key><true/>
  <key>StandardOutPath</key><string>${BUS_HOME}/logs/raccolta-${H}.log</string>
  <key>StandardErrorPath</key><string>${BUS_HOME}/logs/raccolta-${H}.log</string>
</dict>
</plist>
PLIST
    echo "Collector for '$H' → $PLIST (every 60 s, log $BUS_HOME/logs/raccolta-${H}.log)"
    if [[ "${AB_NO_LAUNCHCTL:-0}" != "1" ]] && command -v launchctl >/dev/null 2>&1; then
      launchctl bootout "gui/$(id -u)/${LABEL}" 2>/dev/null || true
      launchctl bootstrap "gui/$(id -u)" "$PLIST" && echo "  loaded ($LABEL)"
    fi
  done
fi

# --- Settings.json hint ---

echo ""
echo "Install complete."
echo ""
echo "Next steps:"
echo "  1. Edit $BUS_HOME/AGENT_MAP.json — map your workspaces to agent names."
echo "  2. Register the hooks in $CLAUDE_HOME/settings.json (or re-run with --register-hooks):"
echo ""
cat <<JSON
       {
         "hooks": {
           "SessionStart": [
             { "hooks": [ { "type": "command", "command": "$CLAUDE_HOME/hooks/agent-team-os-load.sh" } ] }
           ],
           "UserPromptSubmit": [
             { "hooks": [ { "type": "command", "command": "$CLAUDE_HOME/hooks/agent-team-os-urgent.sh" } ] }
           ]
         }
       }
JSON
echo ""
echo "  3. (Optional, v2) Enable the drain-on-Stop hook — blocks a session from"
echo "     closing while its inbox has unhandled messages. NOT auto-registered."
echo "     Add this to the \"hooks\" block above, then test per docs/agentic-v2/SETTINGS-DIFF.md:"
echo ""
cat <<JSON
           "Stop": [
             { "hooks": [ { "type": "command", "command": "$CLAUDE_HOME/hooks/agent-team-os-stop.sh" } ] }
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
echo "  6. (Optional, two machines) See README \"Two machines\": --host on the second"
echo "     machine, --host-sync on the hub."
