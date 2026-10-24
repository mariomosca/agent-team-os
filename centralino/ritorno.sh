#!/usr/bin/env bash
# ritorno.sh — dal bus v3 al bus a file, in un comando (BR24). Inverso di passaggio.sh.
#
#   centralino/ritorno.sh [--backup DIR] [--si] [--ripristina-bus]
#
# Usa l'ultimo backup del passaggio (~/.agent-team-os-backup/ultimo) e fa, in ordine:
#   1. ferma il centralino (launchd o pid);
#   2. esporta lo stato di SQLite nei file di oggi: i messaggi e le attese nati durante la
#      prova non si perdono (con --ripristina-bus invece si rimette il bus del backup così
#      com'era, e quello che è successo dopo si perde: solo se l'esportazione non va);
#   3. rimette il modo ombra (centralino.json);
#   4. rimette le copie installate (script, hook, comandi, skill, bus-band) e alita-memoria-sync;
#   5. rimette servers.yaml e rilancia mcp_sync.py --apply (il MCP torna al checkout di prima);
#   6. riaccende i job launchd della sveglia e della raccolta del mini;
#   7. rimette Orbit 0.13.1 dal backup;
#   8. riavvia il centralino in ombra (sola lettura dei file) se il passaggio non era una prova.
# Dopo: chiudere e riaprire le sessioni Claude.
set -euo pipefail

QUI="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKUP_ROOT="$HOME/.agent-team-os-backup"
B=""
SI=0
RIPRISTINA_BUS=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --backup) B="$2"; shift 2 ;;
    --backup-root) BACKUP_ROOT="$2"; shift 2 ;;
    --si) SI=1; shift ;;
    --ripristina-bus) RIPRISTINA_BUS=1; shift ;;
    -h|--help) sed -n '2,19p' "$0"; exit 0 ;;
    *) echo "opzione sconosciuta: $1" >&2; exit 1 ;;
  esac
done
B="${B:-$BACKUP_ROOT/ultimo}"
[[ -f "$B/manifest.env" ]] || { echo "nessun manifest in $B" >&2; exit 1; }
B="$(cd "$B" && pwd -P)"
# shellcheck disable=SC1091
source "$B/manifest.env"
LA_DIR="$HOME/Library/LaunchAgents"
LABEL_CENTRALINO="com.agent-team-os.centralino"

passo() { echo; echo "== $*"; }
ok() { echo "   ok  $*"; }
centralino() { (cd "$QUI" && AB_HOME="$HOME_BUS" uv run --frozen --quiet centralino --home "$HOME_BUS" "$@"); }

echo "Ritorno al bus a file dal backup $B (passaggio del $TS, bus $HOME_BUS)."
if (( ! SI )); then
  read -r -p "Procedo? [s/N] " r
  [[ "$r" == "s" || "$r" == "S" ]] || { echo "annullato"; exit 1; }
fi

# ---------- 1. ferma il centralino ----------
passo "1/8 fermo il centralino"
if (( LAUNCHD )); then
  launchctl bootout "gui/$(id -u)/$LABEL_CENTRALINO" 2>/dev/null && ok "launchd $LABEL_CENTRALINO" || true
  rm -f "$LA_DIR/$LABEL_CENTRALINO.plist"
fi
if [[ -f "$HOME_BUS/centralino.pid" ]]; then
  kill "$(cat "$HOME_BUS/centralino.pid")" 2>/dev/null || true
fi
for _ in $(seq 1 30); do [[ -S "$HOME_BUS/centralino.sock" ]] || break; sleep 0.2; done
[[ -S "$HOME_BUS/centralino.sock" ]] && { echo "   il centralino risponde ancora" >&2; exit 1; }
ok "fermo"

# ---------- 2. stato nei file ----------
if (( RIPRISTINA_BUS )); then
  passo "2/8 rimetto il bus del backup così com'era"
  rsync -a --delete --exclude 'centralino.sock' "$B/agent-team-os/" "$HOME_BUS/"
  ok "bus ripristinato al $TS"
else
  passo "2/8 esporto lo stato di SQLite nei file"
  centralino esporta | tee "$B/esporta.json" | sed 's/^/   /'
fi

# ---------- 3. modo ombra ----------
passo "3/8 modo ombra"
centralino modo ombra >/dev/null
ok "centralino.json: $(tr -d '\n ' < "$HOME_BUS/centralino.json")"

# ---------- 4. copie installate ----------
passo "4/8 rimetto script, hook, comandi, skill e bus-band"
for D in "$B"/claude/*; do
  [[ -f "$D/PERCORSO" ]] || continue
  C="$(cat "$D/PERCORSO")"
  rm -f "$C/scripts/agent-team-os-centralino.py"
  cp -p "$D"/scripts/* "$C/scripts/" 2>/dev/null || true
  cp -p "$D"/hooks/* "$C/hooks/" 2>/dev/null || true
  cp -p "$D"/commands/* "$C/commands/" 2>/dev/null || true
  if [[ -d "$D/skills/agent-team-os" ]]; then
    rm -rf "$C/skills/agent-team-os"; cp -Rp "$D/skills/agent-team-os" "$C/skills/"
  fi
  if [[ -d "$D/mods/bus-band" ]]; then
    rm -rf "$C/mods/bus-band"; cp -Rp "$D/mods/bus-band" "$C/mods/"
  fi
  ok "$C"
done

# ---------- 5. MCP ----------
if (( MCP )); then
  passo "5/8 MCP agent-bus al checkout di prima"
  cp -p "$B/mcp/servers.yaml" "$MCP_CONFIG/servers.yaml"
  python3 "$MCP_CONFIG/mcp_sync.py" --apply > "$B/mcp_sync-ritorno.log" 2>&1 \
    && ok "servers.yaml rimesso, mcp_sync --apply" \
    || echo "   ATTENZIONE: mcp_sync --apply fallito ($B/mcp_sync-ritorno.log); i .claude.json di prima sono in $B/mcp/"
else
  passo "5/8 MCP: niente da fare"
fi

# ---------- 6. launchd ----------
if (( LAUNCHD )); then
  passo "6/8 riaccendo la sveglia a file e la raccolta del mini"
  while IFS= read -r L; do
    [[ -n "$L" ]] || continue
    [[ -f "$LA_DIR/$L.plist" ]] || cp -p "$B/launchd/$L.plist" "$LA_DIR/"
    launchctl bootstrap "gui/$(id -u)" "$LA_DIR/$L.plist" 2>/dev/null && ok "$L" || ok "$L (già caricato)"
  done < "$B/launchd-caricati"
else
  passo "6/8 launchd: niente da fare"
fi

# ---------- 7. Orbit ----------
if (( ORBIT )) && [[ -d "$B/Orbit.app" ]]; then
  passo "7/8 Orbit dal backup"
  osascript -e 'quit app "Orbit"' 2>/dev/null || true
  for _ in $(seq 1 25); do pgrep -xq Orbit || break; sleep 0.2; done
  rm -rf "$ORBIT_APP"
  ditto "$B/Orbit.app" "$ORBIT_APP"
  open "$ORBIT_APP"
  ok "Orbit $(defaults read "$ORBIT_APP/Contents/Info.plist" CFBundleShortVersionString 2>/dev/null) rimessa"
else
  passo "7/8 Orbit: niente da fare"
fi

# ---------- 8. centralino in ombra ----------
passo "8/8 centralino in ombra"
if (( PROVA )); then
  ok "passaggio di prova: il centralino resta fermo"
else
  AB_HOME="$HOME_BUS" nohup uv run --project "$QUI" --frozen --quiet centralino --home "$HOME_BUS" serve \
     </dev/null >/dev/null 2>>"$HOME_BUS/logs/centralino-errori.log" &
  disown
  ok "riavviato in ombra (legge i file, scrive solo il suo db)"
fi
echo
echo "Fatto. Chiudi e riapri le sessioni Claude: tornano al bus a file."
