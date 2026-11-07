#!/usr/bin/env bash
# install-roster-export.sh (RS07-F4.1): the roster-export LaunchAgent on this Mac.
#   scripts/install-roster-export.sh            install (or update) and load
#   scripts/install-roster-export.sh --remove   unload and remove (the files stay)
# Needs the `roster` CLI at ~/.local/bin/roster and a device login (`roster login`).
set -euo pipefail
LABEL=com.mindfullabai.roster.export
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LA_DIR="${LAUNCH_AGENTS_DIR:-$HOME/Library/LaunchAgents}"
PLIST="$LA_DIR/$LABEL.plist"
SHARE="$HOME/.local/share/roster"
UID_N="$(id -u)"
launchctl bootout "gui/$UID_N/$LABEL" 2>/dev/null || true   # launchd stops it: never pkill
if [[ "${1:-}" == "--remove" ]]; then rm -f "$PLIST" "$SHARE/roster-export.sh"; echo "removed $LABEL"; exit 0; fi
[[ -x "$HOME/.local/bin/roster" ]] || { echo "ERROR: ~/.local/bin/roster missing (build packages/cli in the roster repo)"; exit 1; }
mkdir -p "$SHARE" "$LA_DIR" "$HOME/Library/Logs"
install -m 755 "$HERE/roster-export.sh" "$SHARE/roster-export.sh"
sed "s#__HOME__#$HOME#g" "$HERE/launchd/$LABEL.plist" > "$PLIST"
plutil -lint "$PLIST" >/dev/null
if [[ "${AB_NO_LAUNCHCTL:-}" == "1" ]]; then echo "plist written, not loaded: $PLIST"; exit 0; fi
launchctl bootstrap "gui/$UID_N" "$PLIST" 2>/dev/null || { sleep 2; launchctl bootstrap "gui/$UID_N" "$PLIST"; }
echo "$LABEL loaded: every 10 minutes, log ~/Library/Logs/roster-export.log"
