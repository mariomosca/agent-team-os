#!/usr/bin/env bash
# roster-export.sh (RS07-F4.1): regenerate the files the other tools read from Roster, the agent registry.
#   ~/.agent-team-os/AGENT_MAP.json, ~/.agent-team-os/personas/*.md, ~/.lighthouse/agenti.json,
#   ~/.agent-team-os/avatars/{local,default}/*.png, each folder with a .roster-export.json beside it.
# Run by the LaunchAgent com.mindfullabai.roster.export every 10 minutes (and by hand).
# Fail-open: the CLI writes a file only after a 200 from the API, atomically; with Roster down it fails and every
# file stays as it was. Brandart personas (leo, nico, teo) are not exported (no DPA yet): the CLI never touches them.
# Device: ROSTER_DEVICE (default: the device in ~/.roster/config.json, here "laptop").
set -u
ROSTER_BIN="${ROSTER_BIN:-$HOME/.local/bin/roster}"
ATO="${ATO_HOME:-$HOME/.agent-team-os}"
LOG="${ROSTER_EXPORT_LOG:-$HOME/Library/Logs/roster-export.log}"
mkdir -p "$(dirname "$LOG")"
# the log stays small: past 1 MB the last 2000 lines are kept
if [[ -f "$LOG" && $(stat -f %z "$LOG" 2>/dev/null || echo 0) -gt 1048576 ]]; then
  tail -n 2000 "$LOG" > "$LOG.tmp" && mv "$LOG.tmp" "$LOG"
fi
dev=()
[[ -n "${ROSTER_DEVICE:-}" ]] && dev=(--device "$ROSTER_DEVICE")
{
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] roster export all"
  "$ROSTER_BIN" export all ${dev[@]+"${dev[@]}"} \
    --agent-map "$ATO/AGENT_MAP.json" \
    --personas-dir "$ATO/personas" \
    --lighthouse "$HOME/.lighthouse/agenti.json" \
    --avatars-dir "$ATO/avatars"
  rc=$?
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] exit $rc"
} >> "$LOG" 2>&1
# a failure is in the log; the job is not "failed": the files are the ones of the last good run
exit 0
