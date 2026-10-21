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
#   --custode           install the custode (README "Il custode"): a launchd job at
#                       7:00 that checks this host and warns on Telegram. Only on a
#                       remote host: with --host <name>, or with $AB_HOME/HOST set
#                       to a host that is not the hub.
#   --custode --uninstall   remove the custode (job + script). Nothing else.
#   --sveglia           install the wake (README "La sveglia", bus v2.6): a launchd job
#                       every 10 s that wakes an idle session when the result it waits
#                       for arrives. On every machine that runs sessions.
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
CUSTODE=0
SVEGLIA=0
UNINSTALL=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --host) HOST_NAME="${2:?--host needs a name}"; shift 2 ;;
    --host=*) HOST_NAME="${1#--host=}"; shift ;;
    --host-sync) HOST_SYNC=1; shift ;;
    --register-hooks) REGISTER_HOOKS=1; shift ;;
    --custode) CUSTODE=1; shift ;;
    --sveglia) SVEGLIA=1; shift ;;
    --uninstall) UNINSTALL=1; shift ;;
    -h|--help) sed -n '2,30p' "$0"; exit 0 ;;
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

# --- Custode: checks that need nothing installed ---

LA_DIR="${LAUNCH_AGENTS_DIR:-$HOME/Library/LaunchAgents}"
CUSTODE_LABEL="com.agent-team-os.custode"
CUSTODE_PLIST="$LA_DIR/${CUSTODE_LABEL}.plist"

if [[ "$UNINSTALL" -eq 1 ]]; then
  if [[ "$CUSTODE" -ne 1 ]]; then
    echo "ERROR: --uninstall works only together with --custode (it removes the custode, nothing else)."
    exit 1
  fi
  if [[ "${AB_NO_LAUNCHCTL:-0}" != "1" ]] && command -v launchctl >/dev/null 2>&1; then
    launchctl bootout "gui/$(id -u)/${CUSTODE_LABEL}" 2>/dev/null || true
    launchctl bootout "gui/$(id -u)/${CUSTODE_LABEL}-registro" 2>/dev/null || true
  fi
  if crontab -l 2>/dev/null | grep -q 'agent-team-os-custode.sh'; then
    { crontab -l 2>/dev/null | grep -v 'agent-team-os-custode.sh' || true; } | crontab -
  fi
  rm -f "$CUSTODE_PLIST" "$LA_DIR/${CUSTODE_LABEL}-registro.plist" "$CLAUDE_HOME/scripts/agent-team-os-custode.sh"
  echo "Custode removed ($CUSTODE_PLIST, $CLAUDE_HOME/scripts/agent-team-os-custode.sh)."
  echo "Its last report stays in $BUS_HOME/hosts/self/custode.json."
  exit 0
fi

if [[ "$CUSTODE" -eq 1 ]]; then
  C_SELF="$HOST_NAME"
  [[ -z "$C_SELF" && -s "$BUS_HOME/HOST" ]] && C_SELF=$(head -1 "$BUS_HOME/HOST" | tr -d '[:space:]')
  C_HUB=$(jq -r '(.hosts // {}) | to_entries[] | select(.value.hub == true) | .key' "$BUS_HOME/AGENT_MAP.json" 2>/dev/null | head -1 || true)
  if [[ -z "$C_SELF" || "$C_SELF" == "$C_HUB" || "$C_SELF" == "laptop" ]]; then
    echo "ERROR: --custode runs on a remote host (the Mac mini), not on the hub."
    echo "       Use it with --host <name>, or after $BUS_HOME/HOST names a non-hub host."
    exit 1
  fi
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
cp "$REPO_DIR/scripts/agent-team-os-wake.py"         "$CLAUDE_HOME/scripts/agent-team-os-wake.py"
chmod +x "$CLAUDE_HOME/scripts/agent-team-os-taskprovider.sh" "$CLAUDE_HOME/scripts/agent-team-os-harvest.sh" \
         "$CLAUDE_HOME/scripts/agent-team-os-collect.sh" "$CLAUDE_HOME/scripts/agent-team-os-wake.py"

echo "Copying hooks → $CLAUDE_HOME/hooks/"
cp "$REPO_DIR/hooks/agent-team-os-load.sh"   "$CLAUDE_HOME/hooks/agent-team-os-load.sh"
cp "$REPO_DIR/hooks/agent-team-os-urgent.sh" "$CLAUDE_HOME/hooks/agent-team-os-urgent.sh"
cp "$REPO_DIR/hooks/agent-team-os-stop.sh"   "$CLAUDE_HOME/hooks/agent-team-os-stop.sh"
chmod +x "$CLAUDE_HOME/hooks/agent-team-os-load.sh" "$CLAUDE_HOME/hooks/agent-team-os-urgent.sh" "$CLAUDE_HOME/hooks/agent-team-os-stop.sh"
# NOTE: the Stop hook is COPIED but NOT auto-registered in settings.json.
# Activate manually (gated) via skill update-config — see docs/agentic-v2/SETTINGS-DIFF.md

echo "Copying slash commands → $CLAUDE_HOME/commands/"
for cmd in bus inbox read send reply handoff delegate thread god; do
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

# --- v2.6: the wake, every 10 s on each machine that runs sessions ---

if [[ "$SVEGLIA" -eq 1 ]]; then
  W_LABEL="com.agent-team-os.sveglia"
  W_PLIST="$LA_DIR/${W_LABEL}.plist"
  mkdir -p "$LA_DIR" "$BUS_HOME/logs" "$BUS_HOME/waits"
  cat > "$W_PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>${W_LABEL}</string>
  <key>ProgramArguments</key>
  <array>
    <string>/usr/bin/python3</string>
    <string>-I</string>
    <string>${CLAUDE_HOME}/scripts/agent-team-os-wake.py</string>
    <string>tick</string>
  </array>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key><string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
    <key>AB_HOME</key><string>${BUS_HOME}</string>
  </dict>
  <key>StartInterval</key><integer>10</integer>
  <key>RunAtLoad</key><true/>
  <key>StandardOutPath</key><string>/dev/null</string>
  <key>StandardErrorPath</key><string>${BUS_HOME}/logs/sveglia-errori.log</string>
</dict>
</plist>
PLIST
  echo "Wake → $W_PLIST (every 10 s, log $BUS_HOME/logs/sveglia.log)"
  if [[ "${AB_NO_LAUNCHCTL:-0}" != "1" ]] && command -v launchctl >/dev/null 2>&1; then
    if launchctl print "gui/$(id -u)" >/dev/null 2>&1; then
      launchctl bootout "gui/$(id -u)/${W_LABEL}" 2>/dev/null || true
      launchctl bootstrap "gui/$(id -u)" "$W_PLIST" && echo "  loaded ($W_LABEL)"
    else
      # No GUI login (the headless Mac mini, reached over SSH): LaunchAgents do not
      # load there, cron does. Six rounds a minute, 10 s apart; the wake lock keeps
      # rounds from overlapping. Same line replaced on every install.
      rm -f "$W_PLIST"
      W_SCRIPT="$CLAUDE_HOME/scripts/agent-team-os-wake.py"
      W_LINE="* * * * * AB_HOME=$BUS_HOME PATH=/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin; for i in 1 2 3 4 5 6; do /usr/bin/python3 -I $W_SCRIPT tick >/dev/null 2>>$BUS_HOME/logs/sveglia-errori.log; sleep 10; done"
      { crontab -l 2>/dev/null | grep -v 'agent-team-os-wake.py' || true; echo "$W_LINE"; } | crontab -
      echo "  no GUI session here: installed in crontab instead (every minute, 6 rounds)"
    fi
  fi
fi

# --- Custode: launchd job at 7:00 on the remote host ---

if [[ "$CUSTODE" -eq 1 ]]; then
  cp "$REPO_DIR/scripts/agent-team-os-custode.sh" "$CLAUDE_HOME/scripts/agent-team-os-custode.sh"
  chmod +x "$CLAUDE_HOME/scripts/agent-team-os-custode.sh"
  mkdir -p "$LA_DIR" "$BUS_HOME/logs"
  cat > "$CUSTODE_PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>${CUSTODE_LABEL}</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string>${CLAUDE_HOME}/scripts/agent-team-os-custode.sh</string>
  </array>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key><string>${HOME}/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
    <key>AB_HOME</key><string>${BUS_HOME}</string>
    <key>CLAUDE_CONFIG_DIR</key><string>${CLAUDE_HOME}</string>
  </dict>
  <key>StartCalendarInterval</key>
  <dict>
    <key>Hour</key><integer>7</integer>
    <key>Minute</key><integer>0</integer>
  </dict>
  <key>RunAtLoad</key><false/>
  <key>StandardOutPath</key><string>${BUS_HOME}/logs/custode.log</string>
  <key>StandardErrorPath</key><string>${BUS_HOME}/logs/custode.log</string>
</dict>
</plist>
PLIST
  echo "Custode → $CUSTODE_PLIST (every day at 7:00, log $BUS_HOME/logs/custode.log)"
  echo "  by hand: $CLAUDE_HOME/scripts/agent-team-os-custode.sh [--no-telegram]"
  # v2.7 (OR18-F3.3): the registry cleanup, every 10 min.
  R_LABEL="com.agent-team-os.custode-registro"
  R_PLIST="$LA_DIR/${R_LABEL}.plist"
  sed -e "s|<string>${CUSTODE_LABEL}</string>|<string>${R_LABEL}</string>|" \
      -e "s|<string>${CLAUDE_HOME}/scripts/agent-team-os-custode.sh</string>|&<string>--registro</string>|" \
      -e '/<key>StartCalendarInterval<\/key>/,/<\/dict>/c\
  <key>StartInterval</key><integer>600</integer>' "$CUSTODE_PLIST" > "$R_PLIST"
  echo "Registry cleanup → $R_PLIST (every 10 min, log $BUS_HOME/logs/custode.log)"
  if [[ "${AB_NO_LAUNCHCTL:-0}" != "1" ]] && command -v launchctl >/dev/null 2>&1; then
    if launchctl print "gui/$(id -u)" >/dev/null 2>&1; then
      for pl in "$CUSTODE_PLIST" "$R_PLIST"; do
        lb=$(basename "$pl" .plist)
        launchctl bootout "gui/$(id -u)/${lb}" 2>/dev/null || true
        launchctl bootstrap "gui/$(id -u)" "$pl" && echo "  loaded ($lb)"
      done
    else
      # No GUI login (the headless mini over SSH): cron, like the wake. Both lines
      # replaced on every install.
      rm -f "$CUSTODE_PLIST" "$R_PLIST"
      C_SCRIPT="$CLAUDE_HOME/scripts/agent-team-os-custode.sh"
      C_ENV="AB_HOME=$BUS_HOME CLAUDE_CONFIG_DIR=$CLAUDE_HOME PATH=\$HOME/.local/bin:\$HOME/.npm-global/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"
      { crontab -l 2>/dev/null | grep -v 'agent-team-os-custode.sh' || true
        echo "0 7 * * * $C_ENV $C_SCRIPT >> $BUS_HOME/logs/custode.log 2>&1"
        echo "*/10 * * * * $C_ENV $C_SCRIPT --registro >> $BUS_HOME/logs/custode.log 2>&1"
      } | crontab -
      echo "  no GUI session here: installed in crontab instead (7:00 and --registro every 10 min)"
    fi
  fi
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
