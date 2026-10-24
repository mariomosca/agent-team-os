#!/usr/bin/env bash
# Agent Team OS — installer (bus v3, BR24)
#
# Copies the thin hooks, slash commands, skill and bus-band mod into your Claude
# config folder, and creates the ~/.agent-team-os/ runtime folder. The bus itself is
# the centralino (centralino/, docs/CENTRALINO.md): one daemon per machine, SQLite.
#
# Idempotent: re-running it overwrites the bundled files but leaves your
# AGENT_MAP.json untouched if it already exists. It also removes what the file bus
# (v1-v2.8) left installed: lib.sh, wake.py, the collector and their jobs.
#
# Options:
#   --host <name>       this machine's name on the bus (writes $AB_HOME/HOST),
#                       e.g. --host mini on the Mac mini. The hub needs none.
#   --daemon            run the centralino of this machine: a launchd job when there is a
#                       GUI login, otherwise (the headless Mac mini over SSH) a cron line
#                       that starts it every minute if its pid is gone.
#   --register-hooks    add the SessionStart, UserPromptSubmit and SessionEnd hooks and the
#                       PreToolUse hook on onda_agent_prompt ([delega] | [comunicazione]) to
#                       $CLAUDE_HOME/settings.json (backup first, idempotent). The Stop hook
#                       stays opt-in.
#   --custode           install the custode (README "Il custode"): a job at 7:00 that checks
#                       this host and warns on Telegram. Only on a remote host: with
#                       --host <name>, or with $AB_HOME/HOST set to a host that is not the hub.
#   --custode --uninstall   remove the custode (job + script). Nothing else.
#
# Env: CLAUDE_HOME (default ~/.claude, e.g. ~/.config/claude-mini), AB_HOME
# (default ~/.agent-team-os), LAUNCH_AGENTS_DIR (default ~/Library/LaunchAgents),
# AB_NO_LAUNCHCTL=1 to write the plist without loading it, UV (default: uv on PATH).

set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CLAUDE_HOME="${CLAUDE_HOME:-$HOME/.claude}"
BUS_HOME="${AB_HOME:-$HOME/.agent-team-os}"

HOST_NAME=""
DAEMON=0
REGISTER_HOOKS=0
CUSTODE=0
UNINSTALL=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --host) HOST_NAME="${2:?--host needs a name}"; shift 2 ;;
    --host=*) HOST_NAME="${1#--host=}"; shift ;;
    --daemon) DAEMON=1; shift ;;
    --register-hooks) REGISTER_HOOKS=1; shift ;;
    --custode) CUSTODE=1; shift ;;
    --uninstall) UNINSTALL=1; shift ;;
    --file-bus|--sveglia|--host-sync)
      echo "ERROR: $1 belonged to the file bus, removed in BR24-F5.7: every machine runs a centralino (--daemon)."
      exit 1 ;;
    --centralino) shift ;;  # accepted for old scripts: v3 is the only bus
    -h|--help) sed -n '2,29p' "$0"; exit 0 ;;
    *) echo "Unknown option: $1 (see --help)"; exit 1 ;;
  esac
done

echo "Agent Team OS installer"
echo "  source repo: $REPO_DIR"
echo "  Claude home: $CLAUDE_HOME"
echo "  Bus  home:   $BUS_HOME"
echo ""

if ! command -v jq >/dev/null 2>&1; then
  echo "ERROR: jq is required. Install with:"
  echo "  brew install jq         (macOS)"
  echo "  sudo apt install jq     (Debian/Ubuntu)"
  exit 1
fi

LA_DIR="${LAUNCH_AGENTS_DIR:-$HOME/Library/LaunchAgents}"
CUSTODE_LABEL="com.agent-team-os.custode"
CUSTODE_PLIST="$LA_DIR/${CUSTODE_LABEL}.plist"
UID_N=$(id -u)

can_launchctl() { [[ "${AB_NO_LAUNCHCTL:-0}" != "1" ]] && command -v launchctl >/dev/null 2>&1; }
# A GUI login loads LaunchAgents; the headless mini reached over SSH has none and uses cron.
has_gui() { can_launchctl && launchctl print "gui/$UID_N" >/dev/null 2>&1; }
cron_drop() { # remove the crontab lines matching $1
  if crontab -l 2>/dev/null | grep -q -- "$1"; then
    { crontab -l 2>/dev/null | grep -v -- "$1" || true; } | crontab -
  fi
}
job_drop() { # unload and remove a LaunchAgent by label
  can_launchctl && launchctl bootout "gui/$UID_N/$1" 2>/dev/null || true
  rm -f "$LA_DIR/$1.plist"
}

if [[ "$UNINSTALL" -eq 1 ]]; then
  if [[ "$CUSTODE" -ne 1 ]]; then
    echo "ERROR: --uninstall works only together with --custode (it removes the custode, nothing else)."
    exit 1
  fi
  job_drop "$CUSTODE_LABEL"
  cron_drop 'agent-team-os-custode.sh'
  rm -f "$CLAUDE_HOME/scripts/agent-team-os-custode.sh"
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

mkdir -p "$CLAUDE_HOME/scripts" "$CLAUDE_HOME/hooks" "$CLAUDE_HOME/commands" "$CLAUDE_HOME/skills" \
         "$BUS_HOME/registry" "$BUS_HOME/outbox" "$BUS_HOME/logs"

# --- What the file bus left installed (BR24-F5.7) ---

for old in agent-team-os-lib.sh agent-team-os-wake.py agent-team-os-collect.sh \
           agent-team-os-taskprovider.sh agent-team-os-harvest.sh; do
  [[ -e "$CLAUDE_HOME/scripts/$old" ]] && echo "Removing old $CLAUDE_HOME/scripts/$old"
  rm -f "$CLAUDE_HOME/scripts/$old"
done
for lb in com.agent-team-os.sveglia com.agent-team-os.custode-registro $(ls "$LA_DIR" 2>/dev/null \
          | sed -n 's/^\(com\.agent-team-os\.raccolta-[A-Za-z0-9_-]*\)\.plist$/\1/p'); do
  [[ -e "$LA_DIR/$lb.plist" ]] && echo "Removing old job $lb"
  job_drop "$lb"
done
cron_drop 'agent-team-os-wake.py'
cron_drop 'agent-team-os-custode.sh --registro'

# --- Bundled files ---

echo "Copying hooks → $CLAUDE_HOME/hooks/ and agent-team-os-centralino.py → $CLAUDE_HOME/scripts/"
cp "$REPO_DIR/hooks/agent-team-os-centralino.py" "$CLAUDE_HOME/scripts/agent-team-os-centralino.py"
chmod +x "$CLAUDE_HOME/scripts/agent-team-os-centralino.py"
for h in agent-team-os-load.sh agent-team-os-urgent.sh agent-team-os-stop.sh agent-team-os-end.sh \
         agent-team-os-onda-prompt.py; do
  cp "$REPO_DIR/hooks/$h" "$CLAUDE_HOME/hooks/$h"
  chmod +x "$CLAUDE_HOME/hooks/$h"
done
# NOTE: the Stop hook is COPIED but NOT auto-registered in settings.json.
# An installed custode gets the new script on every install; its job only with --custode.
if [[ -f "$CLAUDE_HOME/scripts/agent-team-os-custode.sh" ]]; then
  cp "$REPO_DIR/scripts/agent-team-os-custode.sh" "$CLAUDE_HOME/scripts/agent-team-os-custode.sh"
  chmod +x "$CLAUDE_HOME/scripts/agent-team-os-custode.sh"
fi

echo "Copying slash commands → $CLAUDE_HOME/commands/"
rm -f "$CLAUDE_HOME/commands/god.md"  # BR24-F5.2: GOD mode is gone
for cmd in bus inbox read send reply handoff delegate thread; do
  cp "$REPO_DIR/commands/${cmd}.md" "$CLAUDE_HOME/commands/${cmd}.md"
done

echo "Copying skill → $CLAUDE_HOME/skills/agent-team-os/"
mkdir -p "$CLAUDE_HOME/skills/agent-team-os"
cp "$REPO_DIR/skills/agent-team-os/SKILL.md" "$CLAUDE_HOME/skills/agent-team-os/SKILL.md"
rm -f "$CLAUDE_HOME/skills/agent-team-os/GOD-CONTRACT.md"

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
  if [[ -d "$REPO_DIR/examples/personas" ]]; then
    mkdir -p "$BUS_HOME/personas"
    cp "$REPO_DIR/examples/personas/"*.md "$BUS_HOME/personas/" 2>/dev/null || true
  fi
fi

# --- This machine's host name ---

if [[ -n "$HOST_NAME" ]]; then
  if ! jq -e --arg h "$HOST_NAME" '(.hosts // {}) | has($h)' "$BUS_HOME/AGENT_MAP.json" >/dev/null 2>&1; then
    echo "WARN: host '$HOST_NAME' is not in \"hosts\" of $BUS_HOME/AGENT_MAP.json."
  fi
  echo "$HOST_NAME" > "$BUS_HOME/HOST"
  echo "This machine is '$HOST_NAME' on the bus ($BUS_HOME/HOST)."
fi

# --- Register hooks in settings.json ---

if [[ "$REGISTER_HOOKS" -eq 1 ]]; then
  SETTINGS="$CLAUDE_HOME/settings.json"
  # A settings.json shared through a symlink (~/.claude-a/settings.json -> ~/.claude/...)
  # is written through its real path: tmp + mv on the link would replace it with a
  # regular file and silently split the two accounts' config.
  [[ -L "$SETTINGS" ]] && SETTINGS=$(/usr/bin/python3 -c 'import os,sys; print(os.path.realpath(sys.argv[1]))' "$SETTINGS")
  [[ -s "$SETTINGS" ]] || echo '{}' > "$SETTINGS"
  if ! jq empty "$SETTINGS" 2>/dev/null; then
    echo "ERROR: $SETTINGS is not valid JSON; hooks not registered."
    exit 1
  fi
  cp "$SETTINGS" "$SETTINGS.bak-$(date +%Y%m%d-%H%M%S)"
  jq --arg load "$CLAUDE_HOME/hooks/agent-team-os-load.sh" \
     --arg urgent "$CLAUDE_HOME/hooks/agent-team-os-urgent.sh" \
     --arg end "$CLAUDE_HOME/hooks/agent-team-os-end.sh" \
     --arg onda "/usr/bin/python3 -I $CLAUDE_HOME/hooks/agent-team-os-onda-prompt.py" '
    def ensure($event; $cmd; $name; $entry):
      if ([.hooks[$event][]?.hooks[]?.command // empty] | any(endswith($name)))
      then .
      else .hooks[$event] = ((.hooks[$event] // []) + [$entry])
      end;
    def ensure($event; $cmd; $name): ensure($event; $cmd; $name; {hooks: [{type: "command", command: $cmd}]});
    .hooks = (.hooks // {})
    | ensure("SessionStart"; $load; "agent-team-os-load.sh")
    | ensure("UserPromptSubmit"; $urgent; "agent-team-os-urgent.sh")
    | ensure("SessionEnd"; $end; "agent-team-os-end.sh")
    | ensure("PreToolUse"; $onda; "agent-team-os-onda-prompt.py";
             {matcher: "mcp__onda__onda_agent_prompt", hooks: [{type: "command", command: $onda, timeout: 10}]})
  ' "$SETTINGS" > "$SETTINGS.tmp"
  if [[ -s "$SETTINGS.tmp" ]] && jq empty "$SETTINGS.tmp" 2>/dev/null; then
    mv "$SETTINGS.tmp" "$SETTINGS"
    echo "Hooks registered in $SETTINGS (SessionStart, UserPromptSubmit, SessionEnd, PreToolUse onda_agent_prompt)."
  else
    rm -f "$SETTINGS.tmp"
    echo "ERROR: could not update $SETTINGS; left untouched."
    exit 1
  fi
fi

# --- The centralino of this machine ---

if [[ "$DAEMON" -eq 1 ]]; then
  UV_BIN="${UV:-$(command -v uv || true)}"
  if [[ -z "$UV_BIN" ]]; then
    echo "ERROR: --daemon needs uv (brew install uv)."
    exit 1
  fi
  C_DIR="$REPO_DIR/centralino"
  (cd "$C_DIR" && "$UV_BIN" sync --frozen --quiet) || { echo "ERROR: uv sync in $C_DIR failed."; exit 1; }
  D_LABEL="com.agent-team-os.centralino"
  if has_gui || [[ "${AB_NO_LAUNCHCTL:-0}" == "1" ]]; then
    D_PLIST="$LA_DIR/${D_LABEL}.plist"
    mkdir -p "$LA_DIR"
    sed -e "s|__UV__|$UV_BIN|g" -e "s|__CENTRALINO_DIR__|$C_DIR|g" -e "s|__AB_HOME__|$BUS_HOME|g" \
        -e '/^<!--$/,/^-->$/d' "$C_DIR/launchd/${D_LABEL}.plist" > "$D_PLIST"
    echo "Centralino → $D_PLIST (launchd, KeepAlive)"
    if can_launchctl; then
      launchctl bootout "gui/$UID_N/$D_LABEL" 2>/dev/null || true  # launchd stops it: never pkill
      launchctl bootstrap "gui/$UID_N" "$D_PLIST" && echo "  loaded ($D_LABEL)"
    fi
  else
    # No GUI login: cron every minute starts it when the pid in centralino.pid is gone.
    # serve itself refuses to start when a centralino already answers on the socket.
    D_CMD="$UV_BIN run --frozen --project $C_DIR centralino serve"
    D_LINE="* * * * * AB_HOME=$BUS_HOME PATH=/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin; kill -0 \$(cat $BUS_HOME/centralino.pid 2>/dev/null || echo 0) 2>/dev/null || nohup $D_CMD >/dev/null 2>>$BUS_HOME/logs/centralino-errori.log &"
    { crontab -l 2>/dev/null | grep -v 'centralino serve' || true; echo "$D_LINE"; } | crontab -
    echo "Centralino → crontab (no GUI session here: started within a minute if its pid is gone)"
  fi
fi

# --- Custode: job at 7:00 on the remote host ---

if [[ "$CUSTODE" -eq 1 ]]; then
  cp "$REPO_DIR/scripts/agent-team-os-custode.sh" "$CLAUDE_HOME/scripts/agent-team-os-custode.sh"
  chmod +x "$CLAUDE_HOME/scripts/agent-team-os-custode.sh"
  mkdir -p "$LA_DIR"
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
  if has_gui; then
    launchctl bootout "gui/$UID_N/${CUSTODE_LABEL}" 2>/dev/null || true
    launchctl bootstrap "gui/$UID_N" "$CUSTODE_PLIST" && echo "  loaded ($CUSTODE_LABEL)"
  elif can_launchctl; then
    rm -f "$CUSTODE_PLIST"
    C_ENV="AB_HOME=$BUS_HOME CLAUDE_CONFIG_DIR=$CLAUDE_HOME PATH=\$HOME/.local/bin:\$HOME/.npm-global/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"
    { crontab -l 2>/dev/null | grep -v 'agent-team-os-custode.sh' || true
      echo "0 7 * * * $C_ENV $CLAUDE_HOME/scripts/agent-team-os-custode.sh >> $BUS_HOME/logs/custode.log 2>&1"
    } | crontab -
    echo "  no GUI session here: installed in crontab instead (7:00)"
  fi
fi

echo ""
echo "Install complete."
echo ""
echo "Next steps:"
echo "  1. Edit $BUS_HOME/AGENT_MAP.json — map your workspaces to agent names. A second machine"
echo "     goes in \"hosts\" with \"ssh\" and \"centralino\" (the command that runs its centralino)."
echo "  2. Start the centralino: re-run with --daemon. Register the hooks with --register-hooks."
echo "  3. (Optional) The Stop hook ($CLAUDE_HOME/hooks/agent-team-os-stop.sh) is not registered:"
echo "     add it under \"Stop\" in settings.json, see docs/agentic-v2/SETTINGS-DIFF.md."
echo "  4. (Optional, Claude Code 2.1.287+) Load the bus-band mod: in the \"env\" block,"
echo "     \"CLAUDE_CODE_PLUGIN_DIRS\": \"$CLAUDE_HOME/mods/bus-band\""
echo "  5. Point the agent-bus MCP server at agent-bus-mcp, open a session in a mapped workspace"
echo "     and try /inbox or /bus."
