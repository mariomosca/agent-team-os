#!/bin/bash
# agent-team-os-custode.sh — il custode di un host remoto del bus (v2.5).
#
# Gira sulla macchina che resta accesa (il Mac mini): launchd lo lancia ogni
# mattina alle 7:00 (install.sh --custode), e lo si lancia a mano quando serve.
# Non e' un agente: controlla, scrive un report e avvisa su Telegram.
#
#   agent-team-os-custode.sh [--no-telegram] [--json]
#   agent-team-os-custode.sh --registro [--dry-run]
#
# --registro (v2.7, OR18-F3.3): solo la pulizia del registro, ogni 10 min da cron.
# Toglie da registry/<agente>.d/ le sessioni con pid morto (SessionEnd non e'
# girato: crash, tmux chiuso, riavvio) e mette active:false su <agente>.json
# quando non ne restano. Log in logs/custode.log. Gira anche sul laptop a mano.
#
# Controlli (ok|warn|fail, ciascuno con il comando per sistemare): Claude
# risponde, il server MCP del bus espone i suoi tool, il Python scelto da uv,
# la coda verso il laptop, l'identita' sul bus, i repo sotto ~/Projects
# (git pull --ff-only solo se puliti), il disco, i job launchd.
#
# Report a schermo, JSON atomico in $AB_HOME/hosts/self/custode.json.
# Uscita: 0 ok, 1 warn, 2 fail. Sul laptop (o senza HOST) esce 0 senza fare nulla.
#
# Env (tutte con default sensati, servono soprattutto ai test):
#   CLAUDE_CONFIG_DIR / CUSTODE_CLAUDE_CONFIG_DIR  config Claude (~/.config/claude-mini)
#   CUSTODE_CLAUDE_TIMEOUT (120)   CUSTODE_MCP_TIMEOUT (60)   CUSTODE_GIT_TIMEOUT (60)
#   CUSTODE_MCP_DIR                cartella del server agent-bus-mcp
#   CUSTODE_PROJECTS (~/Projects)  CUSTODE_TELEGRAM_ENV (~/.config/portineria-mini/telegram.env)
#   CUSTODE_DISK_WARN_GB (20)      CUSTODE_DISK_FAIL_GB (5)
#   CUSTODE_WEEKDAY (date +%u)     LAUNCH_AGENTS_DIR (~/Library/LaunchAgents)

set -uo pipefail
trap '' PIPE

LIB="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/agent-team-os-lib.sh"
# shellcheck source=/dev/null
source "$LIB"

# launchd parte con un PATH minimo: aggiungo in coda dove vivono claude, uv, jq.
PATH="$PATH:$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin"

TELEGRAM=1; PRINT_JSON=0; REGISTRO=0; DRY=""
for a in "$@"; do
  case "$a" in
    --no-telegram) TELEGRAM=0 ;;
    --json) PRINT_JSON=1 ;;
    --registro) REGISTRO=1 ;;
    --dry-run) DRY="--dry-run" ;;
    -h|--help) sed -n '2,30p' "$0"; exit 0 ;;
    *) echo "Opzione sconosciuta: $a (vedi --help)" >&2; exit 64 ;;
  esac
done

if (( REGISTRO )); then
  out=$(ab_registry_prune $DRY)
  [[ -n "$out" ]] && sed "s/^/$(ab_iso_now) /" <<<"$out"
  exit 0
fi

SELF=$(ab_host_self)
HUB=$(ab_hub_host)
if [[ ! -s "$AB_HOME/HOST" || -z "$SELF" || "$SELF" == "$HUB" || "$SELF" == "laptop" ]]; then
  echo "custode: non sono un host remoto (HOST='${SELF}', hub='${HUB:-laptop}'). Niente da fare."
  exit 0
fi
HUB="${HUB:-laptop}"

CLAUDE_CFG="${CUSTODE_CLAUDE_CONFIG_DIR:-${CLAUDE_CONFIG_DIR:-$HOME/.config/claude-mini}}"
PROJECTS="${CUSTODE_PROJECTS:-$HOME/Projects}"
TG_ENV="${CUSTODE_TELEGRAM_ENV:-$HOME/.config/portineria-mini/telegram.env}"
LA_DIR="${LAUNCH_AGENTS_DIR:-$HOME/Library/LaunchAgents}"
STATE="$AB_HOME/hosts/self"
OUT_JSON="$STATE/custode.json"
mkdir -p "$STATE"

# Un custode alla volta: launchd e una mano possono partire insieme.
LOCK="$STATE/custode.lock"
if ! mkdir "$LOCK" 2>/dev/null; then
  if [[ $(ab_file_age "$LOCK") -lt 900 ]]; then
    echo "custode: gia' in corso (lock $LOCK)." >&2; exit 0
  fi
  rm -rf "$LOCK"; mkdir "$LOCK" 2>/dev/null || true
fi
TMPD=$(mktemp -d "${TMPDIR:-/tmp}/custode.XXXXXX")
trap 'rm -rf "$TMPD" "$LOCK"' EXIT
CHECKS="$TMPD/checks.jsonl"; : > "$CHECKS"

# ---------- helpers ----------

add() {
  # name status detail [fix]
  jq -cn --arg n "$1" --arg s "$2" --arg d "$3" --arg f "${4:-}" \
    '{name:$n, status:$s, detail:$d, fix:$f}' >> "$CHECKS"
}

run_timeout() {
  # secs out err cmd... — rc del comando, 124 se scade il tempo. Bash 3.2: niente coproc.
  local s="$1" out="$2" err="$3"; shift 3
  "$@" >"$out" 2>"$err" </dev/null &
  local pid=$! i=0 lim=$(( s * 5 ))
  while kill -0 "$pid" 2>/dev/null; do
    if (( i >= lim )); then
      kill -TERM "$pid" 2>/dev/null; sleep 1; kill -KILL "$pid" 2>/dev/null
      wait "$pid" 2>/dev/null; return 124
    fi
    sleep 0.2; i=$((i+1))
  done
  wait "$pid"
}

last_line() { grep -v '^[[:space:]]*$' "$1" 2>/dev/null | tail -1 | cut -c1-300; }

# ---------- 1. identita' ----------

check_identity() {
  if ! jq empty "$AB_MAP" 2>/dev/null; then
    add identita fail "AGENT_MAP.json non valido o assente ($AB_MAP)" \
      "scp laptop:~/.agent-team-os/AGENT_MAP.json $AB_MAP   # stesso file su tutte e due le macchine"
    return
  fi
  if ! ab_host_exists "$SELF"; then
    add identita fail "HOST=$SELF ma \"hosts.$SELF\" manca in AGENT_MAP.json" \
      "ricopia AGENT_MAP.json dal laptop (deve contenere hosts.$SELF)"
    return
  fi
  add identita ok "$SELF sul bus, mappa valida"
}

# ---------- 2. Claude ----------

check_claude() {
  if ! command -v claude >/dev/null 2>&1; then
    add claude fail "claude non trovato nel PATH" "curl -fsSL https://claude.ai/install.sh | bash"
    return
  fi
  local rc out
  ( cd "$HOME" && export CLAUDE_CONFIG_DIR="$CLAUDE_CFG" && run_timeout "${CUSTODE_CLAUDE_TIMEOUT:-120}" \
      "$TMPD/claude.out" "$TMPD/claude.err" claude -p "Rispondi solo: OK" --model haiku )
  rc=$?
  out=$(cat "$TMPD/claude.out" "$TMPD/claude.err" 2>/dev/null)
  local fix="ssh mini -t \"export CLAUDE_CONFIG_DIR=\\\$HOME/.config/claude-mini; claude\"  poi /login"
  if printf '%s' "$out" | grep -qiE 'login|expired|invalid api key|not logged'; then
    add claude fail "login scaduto: $(printf '%s' "$out" | grep -iE 'login|expired|invalid api key|not logged' | head -1 | cut -c1-200)" "$fix"
  elif [[ $rc -eq 124 ]]; then
    add claude fail "nessuna risposta in ${CUSTODE_CLAUDE_TIMEOUT:-120} s" "$fix"
  elif [[ $rc -ne 0 || -z "$(tr -d '[:space:]' < "$TMPD/claude.out")" ]]; then
    add claude fail "claude -p esce con $rc: $(last_line "$TMPD/claude.err")" "$fix"
  else
    add claude ok "risponde ($(head -1 "$TMPD/claude.out" | cut -c1-40))"
  fi
}

# ---------- 3+4. server MCP del bus e Python di uv ----------

MCP_DIR=""; MCP_CMD=()
resolve_mcp() {
  # Lo stesso comando che usa Claude Code, dalla sua config; altrimenti il default.
  local cfg="$CLAUDE_CFG/.claude.json" line
  if [[ -s "$cfg" ]] && jq -e '.mcpServers["agent-bus"].command' "$cfg" >/dev/null 2>&1; then
    while IFS= read -r line; do MCP_CMD+=("$line"); done \
      < <(jq -r '.mcpServers["agent-bus"] | .command, (.args // [])[]' "$cfg")
    MCP_DIR=$(jq -r '.mcpServers["agent-bus"].args // [] | . as $a
      | (index("--directory") // -1) as $i | if $i >= 0 then $a[$i+1] else "" end' "$cfg")
  fi
  MCP_DIR="${CUSTODE_MCP_DIR:-${MCP_DIR:-$HOME/Projects/07-Tooling/mcp/agent-bus-mcp}}"
  if [[ ${#MCP_CMD[@]} -eq 0 ]]; then
    MCP_CMD=(uv run --directory "$MCP_DIR" --with fastmcp --with pydantic python -m agent_bus_mcp.server)
  fi
}

wait_rpc() {
  # file id pid secs — vero quando nel file c'e' la risposta a quell'id.
  local f="$1" id="$2" pid="$3" lim=$(( $4 * 5 )) i=0
  while (( i < lim )); do
    jq -Rce --argjson id "$id" 'fromjson? | select(.id == $id)' "$f" >/dev/null 2>&1 && return 0
    kill -0 "$pid" 2>/dev/null || { jq -Rce --argjson id "$id" 'fromjson? | select(.id == $id)' "$f" >/dev/null 2>&1; return $?; }
    sleep 0.2; i=$((i+1))
  done
  return 1
}

check_mcp() {
  local fix="cd $MCP_DIR && cat .python-version && uv sync && agent-team-os-custode.sh --no-telegram"
  if [[ ! -d "$MCP_DIR" ]]; then
    add bus-mcp fail "cartella del server assente: $MCP_DIR" \
      "git clone <agent-bus-mcp> $MCP_DIR"
    return
  fi
  local w="$TMPD/mcp" t="${CUSTODE_MCP_TIMEOUT:-60}"
  mkdir -p "$w"; mkfifo "$w/in"
  ( cd "$MCP_DIR" && exec "${MCP_CMD[@]}" ) <"$w/in" >"$w/out" 2>"$w/err" &
  local pid=$!
  exec 3>"$w/in"
  # Come Claude Code: initialize, aspetta, initialized + tools/list, aspetta, chiudi.
  printf '%s\n' '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"agent-team-os-custode","version":"1"}}}' >&3 2>/dev/null
  local ok=0
  if wait_rpc "$w/out" 1 "$pid" "$t"; then
    printf '%s\n' '{"jsonrpc":"2.0","method":"notifications/initialized"}' >&3 2>/dev/null
    printf '%s\n' '{"jsonrpc":"2.0","id":2,"method":"tools/list"}' >&3 2>/dev/null
    wait_rpc "$w/out" 2 "$pid" "$t" && ok=1
  fi
  exec 3>&-
  kill "$pid" 2>/dev/null; wait "$pid" 2>/dev/null
  if [[ $ok -ne 1 ]]; then
    local why; why=$(last_line "$w/err")
    add bus-mcp fail "nessuna risposta a tools/list: ${why:-il server non ha risposto in $t s}" "$fix"
    return
  fi
  local names want missing="" n
  names=$(jq -Rr 'fromjson? | select(.id == 2) | .result.tools[]?.name' "$w/out" | sort -u)
  for want in bus_archive bus_expect bus_inbox bus_read bus_send bus_status bus_thread bus_waits; do
    printf '%s\n' "$names" | grep -qx "$want" || missing="$missing $want"
  done
  n=$(printf '%s\n' "$names" | grep -c . )
  if [[ -n "$missing" ]]; then
    add bus-mcp fail "$n tool, mancano:$missing" "$fix"
  else
    add bus-mcp ok "$n tool"
  fi
}

check_python() {
  [[ -d "$MCP_DIR" ]] || return 0   # gia' segnalato da bus-mcp
  local pv="$MCP_DIR/.python-version" ver rc
  ( cd "$MCP_DIR" && run_timeout "${CUSTODE_MCP_TIMEOUT:-60}" "$TMPD/py.out" "$TMPD/py.err" \
      uv run --directory "$MCP_DIR" python -c 'import sys; print("%d.%d.%d" % sys.version_info[:3])' )
  rc=$?
  ver=$(grep -E '^[0-9]+\.[0-9]+' "$TMPD/py.out" | tail -1)
  local fix="echo 3.12 > $pv && cd $MCP_DIR && uv sync"
  if [[ $rc -ne 0 || -z "$ver" ]]; then
    add python warn "uv non risolve Python: $(last_line "$TMPD/py.err")" "$fix"
    return
  fi
  local maj min; maj=${ver%%.*}; min=${ver#*.}; min=${min%%.*}
  if (( maj > 3 || (maj == 3 && min >= 15) )); then
    add python fail "uv usa Python $ver (>= 3.15: beartype non si importa)" "$fix"
  elif [[ ! -s "$pv" ]]; then
    add python warn "Python $ver, ma .python-version manca: al prossimo Python uv puo' cambiare" "$fix"
  else
    add python ok "$ver (.python-version $(head -1 "$pv" | tr -d '[:space:]'))"
  fi
}

# ---------- 5. coda verso il laptop ----------

check_queue() {
  local q="$AB_HOME/outbox/$HUB" files total old1 old24 oldest=0 a f
  files="$TMPD/queue"
  find "$q" -type f -name 'msg-*.json' 2>/dev/null > "$files"
  total=$(grep -c . "$files")
  old1=0; old24=0
  while IFS= read -r f; do
    [[ -z "$f" ]] && continue
    a=$(ab_file_age "$f")
    (( a > oldest )) && oldest=$a
    (( a > 3600 )) && old1=$((old1+1))
    (( a > 86400 )) && old24=$((old24+1))
  done < "$files"
  local h=$(( oldest / 3600 ))
  local fix="sul $HUB: apri il coperchio (la raccolta com.agent-team-os.raccolta-$SELF riparte da sola) o lancia agent-team-os-collect.sh $SELF"
  if (( old24 > 0 )); then
    add coda fail "il $HUB non raccoglie da $h ore, $total messaggi fermi" "$fix"
  elif (( old1 > 0 )); then
    add coda warn "il $HUB non raccoglie da $h ore, $total messaggi fermi" "$fix"
  else
    add coda ok "$total in attesa per il $HUB"
  fi
}

# ---------- 6. repo ----------

REPO_UPDATED=0
check_repos() {
  local d name before after n dirty up rc fresh="" stuck=0 total=0 skipped=0
  [[ -d "$PROJECTS" ]] || { add repo ok "nessuna cartella $PROJECTS"; return; }
  export GIT_TERMINAL_PROMPT=0
  export GIT_SSH_COMMAND="${GIT_SSH_COMMAND:-ssh -o BatchMode=yes -o ConnectTimeout=10}"
  for d in "$PROJECTS"/* "$PROJECTS"/*/*; do
    [[ -e "$d/.git" ]] || continue
    name="${d#"$PROJECTS"/}"
    total=$((total+1))
    git -C "$d" symbolic-ref -q HEAD >/dev/null 2>&1 || { skipped=$((skipped+1)); continue; }
    up=$(git -C "$d" rev-parse --abbrev-ref --symbolic-full-name '@{u}' 2>/dev/null) \
      || { skipped=$((skipped+1)); continue; }
    # Pulito = nessun file tracciato modificato. Gli untracked non contano:
    # git pull --ff-only si ferma da solo se dovesse sovrascriverne uno.
    dirty=$(git -C "$d" status --porcelain --untracked-files=no 2>/dev/null | grep -c .)
    if (( dirty > 0 )); then
      add "repo:$name" warn "non toccato, $dirty file modificati" "cd $d && git status"
      continue
    fi
    before=$(git -C "$d" rev-parse HEAD 2>/dev/null)
    run_timeout "${CUSTODE_GIT_TIMEOUT:-60}" "$TMPD/git.out" "$TMPD/git.err" \
      git -C "$d" pull --ff-only --quiet
    rc=$?
    if [[ $rc -ne 0 ]]; then
      if grep -qiE 'fast-forward|diverg' "$TMPD/git.err"; then
        add "repo:$name" warn "diverso da $up, non toccato" "cd $d && git status && git log --oneline HEAD...$up"
      else
        add "repo:$name" warn "pull fallito: $(last_line "$TMPD/git.err")" "cd $d && git pull --ff-only"
      fi
      continue
    fi
    after=$(git -C "$d" rev-parse HEAD 2>/dev/null)
    if [[ "$before" != "$after" ]]; then
      n=$(git -C "$d" rev-list --count "$before..$after" 2>/dev/null || echo "?")
      fresh="$fresh, $name aggiornato ($n commit)"
      REPO_UPDATED=$((REPO_UPDATED+1))
    else
      stuck=$((stuck+1))
    fi
  done
  local detail="$total repo: $REPO_UPDATED aggiornati, $stuck fermi"
  (( skipped > 0 )) && detail="$detail, $skipped senza upstream"
  add repo ok "$detail$fresh"
}

# ---------- 7. disco ----------

check_disk() {
  local kb gb
  kb=$(df -k "$HOME" 2>/dev/null | awk 'NR>1 {v=$4} END {print v+0}')
  gb=$(( kb / 1024 / 1024 ))
  local fix="du -sh ~/Library/Caches ~/Library/Developer ~/.cache 2>/dev/null; brew cleanup"
  if (( gb < ${CUSTODE_DISK_FAIL_GB:-5} )); then
    add disco fail "$gb GB liberi" "$fix"
  elif (( gb < ${CUSTODE_DISK_WARN_GB:-20} )); then
    add disco warn "$gb GB liberi" "$fix"
  else
    add disco ok "$gb GB liberi"
  fi
}

# ---------- 8. launchd ----------

check_launchd() {
  local p label n=0 bad=0 uid; uid=$(id -u)
  for p in "$LA_DIR"/com.portineria.*.plist "$LA_DIR"/com.agent-team-os.*.plist; do
    [[ -f "$p" ]] || continue
    label=$(basename "$p" .plist)
    n=$((n+1))
    if ! launchctl print "gui/$uid/$label" >/dev/null 2>&1; then
      bad=$((bad+1))
      add "launchd:$label" warn "plist presente ma non caricato" "launchctl bootstrap gui/$uid $p"
    fi
  done
  (( bad == 0 )) && add launchd ok "$n job caricati"
  return 0
}

# ---------- run ----------

check_identity
check_claude
resolve_mcp
check_mcp
check_python
check_queue
check_repos
check_disk
check_launchd

STATUS=$(jq -rs 'map(.status) | if index("fail") then "fail" elif index("warn") then "warn" else "ok" end' "$CHECKS")
NOW=$(ab_iso_now)
jq -s --arg at "$NOW" --arg host "$SELF" --arg st "$STATUS" \
  '{at:$at, host:$host, status:$st,
    checks: (sort_by({"fail":0,"warn":1,"ok":2}[.status]))}' "$CHECKS" > "$TMPD/custode.json"

# ---------- report ----------

report_lines() {
  # le righe fail e warn, con il comando
  jq -r '.checks[] | select(.status != "ok")
    | "\(.status | ascii_upcase) \(.name): \(.detail)" + (if .fix != "" then "\n     -> \(.fix)" else "" end)' "$1"
}
ok_line() {
  jq -r '[.checks[] | select(.status == "ok") | "\(.name) (\(.detail))"] | if length > 0 then "ok: " + join("; ") else empty end' "$1"
}

HOST_LABEL="Host $SELF"; [[ "$SELF" == "mini" ]] && HOST_LABEL="Mac mini"
REPORT="$TMPD/report.txt"
{
  echo "$HOST_LABEL, custode delle $(date +%H:%M): $(printf '%s' "$STATUS" | tr '[:lower:]' '[:upper:]')"
  report_lines "$TMPD/custode.json"
  ok_line "$TMPD/custode.json"
} > "$REPORT"

# ---------- Telegram ----------

NOTIFY_SENT=false; NOTIFY_NOTE=""
tg_val() {
  sed -n "s/^[[:space:]]*\(export[[:space:]]*\)\{0,1\}$1=//p" "$TG_ENV" | tail -1 \
    | sed -e 's/^"\(.*\)"$/\1/' -e "s/^'\(.*\)'\$/\1/"
}
send_telegram() {
  local text="$1" tok chat
  if [[ ! -r "$TG_ENV" ]]; then NOTIFY_NOTE="file Telegram assente ($TG_ENV): nessun avviso inviato"; return; fi
  tok=$(tg_val TELEGRAM_BOT_TOKEN); chat=$(tg_val TELEGRAM_CHAT_ID)
  if [[ -z "$tok" || -z "$chat" ]]; then NOTIFY_NOTE="TELEGRAM_BOT_TOKEN o TELEGRAM_CHAT_ID vuoti in $TG_ENV"; return; fi
  # Il token passa da stdin (curl -K -), non dalla riga di comando: ps non lo vede.
  if printf 'url = "https://api.telegram.org/bot%s/sendMessage"\n' "$tok" \
      | curl -sS -m 20 -K - --data-urlencode "chat_id=$chat" --data-urlencode "text=$text" \
        -o "$TMPD/tg.out" 2>"$TMPD/tg.err" \
      && jq -e '.ok == true' "$TMPD/tg.out" >/dev/null 2>&1; then
    NOTIFY_SENT=true
  else
    NOTIFY_NOTE="invio Telegram fallito: $(cat "$TMPD/tg.out" "$TMPD/tg.err" 2>/dev/null | tail -1 | cut -c1-200)"
  fi
}

if [[ $TELEGRAM -eq 1 ]]; then
  if [[ "$STATUS" != "ok" ]]; then
    { head -1 "$REPORT"; report_lines "$TMPD/custode.json"; } > "$TMPD/tg.txt"
    send_telegram "$(cat "$TMPD/tg.txt")"
  elif [[ "${CUSTODE_WEEKDAY:-$(date +%u)}" == "1" ]]; then
    stamp="$STATE/custode-weekly"
    if [[ "$(cat "$stamp" 2>/dev/null)" != "$(date +%Y-%m-%d)" ]]; then
      send_telegram "$HOST_LABEL, custode del lunedi': tutto a posto, $REPO_UPDATED repo aggiornati."
      [[ "$NOTIFY_SENT" == true ]] && date +%Y-%m-%d > "$stamp"
    fi
  fi
else
  NOTIFY_NOTE="Telegram disattivato (--no-telegram)"
fi
[[ -n "$NOTIFY_NOTE" ]] && echo "nota: $NOTIFY_NOTE" >> "$REPORT"

jq --argjson sent "$NOTIFY_SENT" --arg note "$NOTIFY_NOTE" \
  '. + {notify: {sent: $sent, note: $note}}' "$TMPD/custode.json" > "$OUT_JSON.tmp.$$" \
  && mv "$OUT_JSON.tmp.$$" "$OUT_JSON"

if [[ $PRINT_JSON -eq 1 ]]; then cat "$OUT_JSON"; else cat "$REPORT"; fi

case "$STATUS" in ok) exit 0 ;; warn) exit 1 ;; *) exit 2 ;; esac
