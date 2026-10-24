#!/usr/bin/env bash
# passaggio.sh — dal bus a file al bus v3 con il centralino (BR24, CENTRALINO.md §12-§13).
#
# Lo lancia Mario per la prova. Fa, in ordine:
#   1. controlli: worktree, uv, jq, app di Orbit già costruita;
#   2. backup completo in ~/.agent-team-os-backup/passaggio-<data>/ (bus, copie installate nei
#      due account, servers.yaml e .claude.json, job launchd, app di Orbit) con un manifest;
#   3. ferma chi scrive i file: launchd della sveglia (wake.py) e della raccolta del mini, e il
#      centralino in ombra;
#   4. installa: install.sh --centralino nei due account (hook v3, comandi, skill, bus-band) e
#      alita-memoria-sync.py nuovo;
#   5. migra lo storico in SQLite e scrive il modo attivo (centralino.json);
#   6. punta il MCP agent-bus al branch v3 (servers.yaml + mcp_sync.py --apply);
#   7. avvia il centralino con launchd;
#   8. installa Orbit da feat/br24-f43 (l'app 0.13.1 resta nel backup);
#   9. verifica e stampa cosa fare dopo (riavviare le sessioni Claude).
#
# Il ritorno indietro è un comando: centralino/ritorno.sh (usa l'ultimo backup).
#
# Opzioni:
#   --home DIR        radice del bus (default $AB_HOME o ~/.agent-team-os)
#   --claude "D1 D2"  cartelle di config di Claude (default "~/.claude ~/.claude-a")
#   --backup-root DIR dove mettere il backup (default ~/.agent-team-os-backup)
#   --prova           prova su una copia: niente launchd, niente Orbit, niente MCP; il
#                     centralino parte con nohup sulla radice data
#   --senza-orbit, --senza-mcp, --senza-launchd   salta solo quella parte
#   --si              non chiede conferma
set -euo pipefail

QUI="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(dirname "$QUI")"
HOME_BUS="${AB_HOME:-$HOME/.agent-team-os}"
CLAUDE_DIRS="$HOME/.claude $HOME/.claude-a"
BACKUP_ROOT="$HOME/.agent-team-os-backup"
MCP_DIR="${BR24_MCP_DIR:-$HOME/Projects/07-Tooling/mcp/agent-bus-mcp-feat-br24-centralino}"
SYNC_SRC="${BR24_SYNC_SRC:-$HOME/Projects/01-Building/alita-mobile-feat-br24-centralino/laptop/alita_memoria_sync.py}"
ORBIT_NEW="${BR24_ORBIT_APP:-$HOME/Projects/01-Building/Orbit/dist/mac-arm64/Orbit.app}"
ORBIT_APP="${BR24_ORBIT_DEST:-/Applications/Orbit.app}"
MCP_CONFIG="${BR24_MCP_CONFIG:-$HOME/Projects/07-Tooling/mcp-config}"
LA_DIR="$HOME/Library/LaunchAgents"
LABELS_VECCHI="com.agent-team-os.sveglia com.agent-team-os.raccolta-mini"
LABEL_CENTRALINO="com.agent-team-os.centralino"
ORBIT=1; MCP=1; LAUNCHD=1; PROVA=0; SI=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --home) HOME_BUS="$2"; shift 2 ;;
    --claude) CLAUDE_DIRS="$2"; shift 2 ;;
    --backup-root) BACKUP_ROOT="$2"; shift 2 ;;
    --prova) PROVA=1; ORBIT=0; MCP=0; LAUNCHD=0; shift ;;
    --senza-orbit) ORBIT=0; shift ;;
    --senza-mcp) MCP=0; shift ;;
    --senza-launchd) LAUNCHD=0; shift ;;
    --si) SI=1; shift ;;
    -h|--help) sed -n '2,30p' "$0"; exit 0 ;;
    *) echo "opzione sconosciuta: $1" >&2; exit 1 ;;
  esac
done

passo() { echo; echo "== $*"; }
ok() { echo "   ok  $*"; }
muori() { echo "   ERRORE: $*" >&2; exit 1; }
centralino() { (cd "$QUI" && AB_HOME="$HOME_BUS" uv run --frozen --quiet centralino --home "$HOME_BUS" "$@"); }

# ---------- 1. controlli ----------
passo "1/9 controlli"
command -v uv >/dev/null || muori "uv non trovato"
command -v jq >/dev/null || muori "jq non trovato"
[[ -f "$HOME_BUS/AGENT_MAP.json" ]] || muori "nessun AGENT_MAP.json in $HOME_BUS"
[[ -f "$QUI/hooks/agent-team-os-centralino.py" ]] || muori "hook v3 mancanti in $QUI/hooks"
if [[ -f "$HOME_BUS/centralino.json" ]] && jq -e '.fonte == "sqlite"' "$HOME_BUS/centralino.json" >/dev/null 2>&1; then
  muori "il bus in $HOME_BUS è già in modo attivo (centralino.json): niente da fare"
fi
(( MCP == 0 )) || [[ -d "$MCP_DIR/src/agent_bus_mcp" ]] || muori "MCP v3 non trovato in $MCP_DIR"
(( MCP == 0 )) || [[ -f "$MCP_CONFIG/servers.yaml" ]] || muori "servers.yaml non trovato in $MCP_CONFIG"
(( ORBIT == 0 )) || [[ -d "$ORBIT_NEW" ]] || muori "app di Orbit non costruita: $ORBIT_NEW (npm run release:local su feat/br24-f43)"
(( ORBIT == 0 )) || codesign --verify --deep --strict "$ORBIT_NEW" 2>/dev/null || muori "l'app di Orbit nuova non ha una firma valida"
if (( PROVA )); then
  # Su una copia il centralino non deve toccare niente di vero: niente prompt nei pannelli di
  # Onda (socket finto), niente SSH verso il mini (raccolta spenta, ssh = false).
  export ONDA_SOCKET="$HOME_BUS/onda-finto.sock" AB_SSH_BIN=false
fi
centralino sim >/dev/null || muori "il banco di prova del centralino non passa: niente passaggio"
ok "banco di prova del centralino verde"
echo "   bus: $HOME_BUS"
echo "   account Claude: $CLAUDE_DIRS"
(( PROVA )) && echo "   MODO PROVA: niente launchd, Orbit, MCP"
if (( ! SI )); then
  read -r -p "   Procedo con il passaggio? [s/N] " r
  [[ "$r" == "s" || "$r" == "S" ]] || { echo "   annullato"; exit 1; }
fi

# ---------- 2. backup ----------
TS="$(date +%Y%m%d-%H%M%S)"
B="$BACKUP_ROOT/passaggio-$TS"
passo "2/9 backup in $B"
mkdir -p "$B"
chmod 700 "$BACKUP_ROOT" "$B"
rsync -a --exclude 'centralino.sock' "$HOME_BUS/" "$B/agent-team-os/"
ok "bus copiato ($(du -sh "$B/agent-team-os" | cut -f1))"
i=0
for C in $CLAUDE_DIRS; do
  [[ -d "$C" ]] || continue
  i=$((i + 1)); D="$B/claude/$i"; mkdir -p "$D/scripts" "$D/hooks" "$D/commands" "$D/skills" "$D/mods"
  echo "$C" > "$D/PERCORSO"
  cp -p "$C"/scripts/agent-team-os-* "$D/scripts/" 2>/dev/null || true
  [[ -f "$C/scripts/alita-memoria-sync.py" ]] && cp -p "$C/scripts/alita-memoria-sync.py" "$D/scripts/"
  cp -p "$C"/hooks/agent-team-os-* "$D/hooks/" 2>/dev/null || true
  for cmd in bus inbox read send reply handoff delegate thread god; do
    [[ -f "$C/commands/$cmd.md" ]] && cp -p "$C/commands/$cmd.md" "$D/commands/"
  done
  [[ -d "$C/skills/agent-team-os" ]] && cp -Rp "$C/skills/agent-team-os" "$D/skills/"
  [[ -d "$C/mods/bus-band" ]] && cp -Rp "$C/mods/bus-band" "$D/mods/"
  ok "copie installate di $C"
done
if (( MCP )); then
  mkdir -p "$B/mcp"
  cp -p "$MCP_CONFIG/servers.yaml" "$B/mcp/servers.yaml"
  for C in $CLAUDE_DIRS; do
    f="$C.json"; [[ "$C" == "$HOME/.claude" ]] && f="$HOME/.claude.json"; [[ "$C" == "$HOME/.claude-a" ]] && f="$HOME/.claude-a/.claude.json"
    [[ -f "$f" ]] && cp -p "$f" "$B/mcp/$(echo "$f" | tr '/' '_')"
  done
  ok "servers.yaml e .claude.json"
fi
: > "$B/launchd-caricati"
if (( LAUNCHD )); then
  mkdir -p "$B/launchd"
  for L in $LABELS_VECCHI; do
    [[ -f "$LA_DIR/$L.plist" ]] && cp -p "$LA_DIR/$L.plist" "$B/launchd/"
    launchctl print "gui/$(id -u)/$L" >/dev/null 2>&1 && echo "$L" >> "$B/launchd-caricati"
  done
  ok "job launchd: $(tr '\n' ' ' < "$B/launchd-caricati")"
fi
if (( ORBIT )) && [[ -d "$ORBIT_APP" ]]; then
  ditto "$ORBIT_APP" "$B/Orbit.app"
  ok "Orbit $(defaults read "$ORBIT_APP/Contents/Info.plist" CFBundleShortVersionString 2>/dev/null) copiata"
fi
cat > "$B/manifest.env" <<EOF
TS=$TS
HOME_BUS=$HOME_BUS
CLAUDE_DIRS="$CLAUDE_DIRS"
PROVA=$PROVA
ORBIT=$ORBIT
MCP=$MCP
LAUNCHD=$LAUNCHD
MCP_CONFIG=$MCP_CONFIG
ORBIT_APP=$ORBIT_APP
REPO=$REPO
EOF
ln -sfn "$B" "$BACKUP_ROOT/ultimo"
ok "manifest scritto; ultimo -> $B"

# ---------- 3. ferma chi scrive i file ----------
passo "3/9 fermo la sveglia a file, la raccolta e il centralino in ombra"
if (( LAUNCHD )); then
  for L in $LABELS_VECCHI; do
    launchctl bootout "gui/$(id -u)/$L" 2>/dev/null && ok "fermato $L" || true
  done
fi
if [[ -f "$HOME_BUS/centralino.pid" ]]; then
  kill "$(cat "$HOME_BUS/centralino.pid")" 2>/dev/null || true
  for _ in $(seq 1 30); do [[ -S "$HOME_BUS/centralino.sock" ]] || break; sleep 0.2; done
  ok "centralino in ombra fermato"
fi
[[ -S "$HOME_BUS/centralino.sock" ]] && muori "il centralino risponde ancora su $HOME_BUS/centralino.sock"

# ---------- 4. installa ----------
passo "4/9 installo bus v3 (hook, comandi, skill, bus-band)"
for C in $CLAUDE_DIRS; do
  [[ -d "$C" ]] || continue
  CLAUDE_HOME="$C" AB_HOME="$HOME_BUS" AB_NO_LAUNCHCTL=1 bash "$REPO/install.sh" --centralino > "$B/install-$(basename "$C").log" 2>&1 \
    || muori "install.sh in $C fallito: $B/install-$(basename "$C").log"
  if [[ -f "$C/scripts/alita-memoria-sync.py" && -f "$SYNC_SRC" ]]; then
    cp "$SYNC_SRC" "$C/scripts/alita-memoria-sync.py"
  fi
  ok "$C"
done

# ---------- 5. migra ----------
passo "5/9 migro lo storico in SQLite e accendo il modo attivo"
centralino migra | tee "$B/migra.json" | sed 's/^/   /'
centralino modo attivo >/dev/null
if (( PROVA )); then
  jq '.raccolta = false' "$HOME_BUS/centralino.json" > "$HOME_BUS/centralino.json.tmp" && mv "$HOME_BUS/centralino.json.tmp" "$HOME_BUS/centralino.json"
fi
ok "centralino.json: $(cat "$HOME_BUS/centralino.json" | tr -d '\n ')"

# ---------- 6. MCP ----------
if (( MCP )); then
  passo "6/9 MCP agent-bus sul branch v3"
  sed -i '' "s#--directory, ~/Projects/07-Tooling/mcp/agent-bus-mcp,#--directory, ${MCP_DIR/#$HOME/\~},#" "$MCP_CONFIG/servers.yaml"
  grep -q "agent-bus-mcp-feat-br24-centralino" "$MCP_CONFIG/servers.yaml" || muori "servers.yaml non aggiornato"
  python3 "$MCP_CONFIG/mcp_sync.py" --apply > "$B/mcp_sync.log" 2>&1 || muori "mcp_sync --apply fallito: $B/mcp_sync.log"
  ok "servers.yaml -> $MCP_DIR, mcp_sync --apply"
else
  passo "6/9 MCP: saltato"
fi

# ---------- 7. avvio del centralino ----------
passo "7/9 avvio il centralino"
if (( LAUNCHD )); then
  PL="$LA_DIR/$LABEL_CENTRALINO.plist"
  sed -e "s#__UV__#$(command -v uv)#g" -e "s#__CENTRALINO_DIR__#$QUI#g" -e "s#__AB_HOME__#$HOME_BUS#g" \
    "$QUI/launchd/$LABEL_CENTRALINO.plist" > "$PL"
  plutil -lint "$PL" >/dev/null || muori "plist non valido: $PL"
  launchctl bootstrap "gui/$(id -u)" "$PL"
  ok "launchd $LABEL_CENTRALINO"
else
  AB_HOME="$HOME_BUS" nohup uv run --project "$QUI" --frozen --quiet centralino --home "$HOME_BUS" serve \
     </dev/null >/dev/null 2>>"$HOME_BUS/logs/centralino-errori.log" &
  disown
  ok "nohup (senza launchd)"
fi
for _ in $(seq 1 50); do [[ -S "$HOME_BUS/centralino.sock" ]] && break; sleep 0.2; done
STATO="$(centralino status 2>/dev/null)" || muori "il centralino non risponde dopo l'avvio"
echo "$STATO" | jq -c '.stats.result' | sed 's/^/   /'

# ---------- 8. Orbit ----------
if (( ORBIT )); then
  passo "8/9 Orbit da feat/br24-f43"
  osascript -e 'quit app "Orbit"' 2>/dev/null || true
  for _ in $(seq 1 25); do pgrep -xq Orbit || break; sleep 0.2; done
  rm -rf "$ORBIT_APP"
  ditto "$ORBIT_NEW" "$ORBIT_APP"
  open "$ORBIT_APP"
  ok "Orbit installata e aperta (backup della 0.13.1 in $B/Orbit.app)"
else
  passo "8/9 Orbit: saltato"
fi

# ---------- 9. fine ----------
passo "9/9 fatto"
echo "   Backup: $B"
echo "   Ritorno indietro: $QUI/ritorno.sh"
echo "   ADESSO: chiudi e riapri le sessioni Claude (il MCP e gli hook nuovi valgono solo per le sessioni nuove)."
echo "   Checklist di prova: $REPO/docs/PROVA-BR24.md"
