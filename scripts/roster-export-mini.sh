#!/usr/bin/env bash
# roster-export-mini.sh (RS07-F4.3): rigenera sul Mac mini i file che gli altri strumenti leggono dal registro Roster.
#   ~/.agent-team-os/personas/*.md (con .roster-export.json accanto)
# Lanciato da cron ogni 10 minuti (il mini non ha login grafico: niente LaunchAgent) e a mano.
# Fail-open: il CLI scrive un file solo dopo un 200 dell'API, in modo atomico; con Roster giu' i file restano
# quelli dell'ultimo giro buono. Le persona di leo, nico, nora, otto, teo non si esportano (nessun DPA): il CLI non le
# tocca e quelle gia' presenti restano.
# AGENT_MAP.json NON e' qui: l'export toglie `agents.teo.host`, che serve al centralino per instradare verso il mini.
# Si aggiunge quando il server di Roster emette `host` dal home_device (vedi docs/ROSTER-MINI.md).
set -u
export PATH="/opt/homebrew/bin:$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin"
export ROSTER_TOKEN_STORE="${ROSTER_TOKEN_STORE:-file}"
ROSTER_BIN="${ROSTER_BIN:-$HOME/.local/bin/roster}"
ATO="${ATO_HOME:-$HOME/.agent-team-os}"
LOG="${ROSTER_EXPORT_LOG:-$ATO/logs/roster-export.log}"
mkdir -p "$(dirname "$LOG")"
# il log resta piccolo: oltre 1 MB si tengono le ultime 2000 righe
if [[ -f "$LOG" && $(stat -f %z "$LOG" 2>/dev/null || echo 0) -gt 1048576 ]]; then
  tail -n 2000 "$LOG" > "$LOG.tmp" && mv "$LOG.tmp" "$LOG"
fi
{
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] roster export personas --device ${ROSTER_DEVICE:-mini}"
  "$ROSTER_BIN" export personas --device "${ROSTER_DEVICE:-mini}" --personas-dir "$ATO/personas"
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] exit $?"
  # RS07-F4.1b: da attivare quando l'export emette `host` dal home_device e il bundle del mini e' aggiornato
  # (Alita avvisa). Prima confrontare con il file attuale.
  # "$ROSTER_BIN" export agent-map --device "${ROSTER_DEVICE:-mini}" --agent-map "$ATO/AGENT_MAP.json"
} >> "$LOG" 2>&1
exit 0
