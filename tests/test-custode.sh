#!/bin/bash
# Il custode del mini. Tutto in una cartella temporanea: claude, uv, curl,
# launchctl e df sono finti (tests/fake-bin/), git e' vero ma su remoti locali.
# Niente rete, niente mini vero, niente ~/.claude o ~/.agent-team-os reali.
set -uo pipefail
cd "$(dirname "$0")/.."; R="$PWD"
T=$(mktemp -d /tmp/abcustode.XXXXXX)
export HOME="$T/home"; mkdir -p "$HOME"
export PATH="$R/tests/fake-bin:$PATH"
cat > "$HOME/.gitconfig" <<'EOF'
[user]
  name = Test
  email = test@example.com
[init]
  defaultBranch = main
EOF

M="$T/mini"
mkdir -p "$M"/{inboxes/kai,registry,threads,outbox,locks}
jq -n --arg m "$M" '{agents:{alita:{domain:"side"},kai:{domain:"side"}}, rules:[],
  cc_awareness:{enabled:true,hub:"alita"},
  hosts:{laptop:{hub:true}, mini:{ssh:"mini", bus_home:$m}}}' > "$M/AGENT_MAP.json"
echo mini > "$M/HOST"

CFG="$T/claude-mini"; mkdir -p "$CFG"
MCP="$T/agent-bus-mcp"; mkdir -p "$MCP"; echo 3.12 > "$MCP/.python-version"
LA="$T/LaunchAgents"; mkdir -p "$LA"
LOADED="$T/loaded"; : > "$LOADED"
for l in com.portineria.bot com.agent-team-os.custode; do
  echo '<plist/>' > "$LA/$l.plist"; echo "$l" >> "$LOADED"
done
TG="$T/telegram.env"
printf 'export TELEGRAM_BOT_TOKEN="123:SEGRETO"\nTELEGRAM_CHAT_ID=4242\n' > "$TG"
CURL_LOG="$T/curl.log"; CLAUDE_LOG="$T/claude.log"

# --- repo: upstream nudi locali, cloni sotto due cartelle "Projects" ---
g(){ git -C "$1" "${@:2}" >/dev/null 2>&1; }
mkrepo(){ # dir upstream
  git init -q --bare "$2"; git clone -q "$2" "$1" 2>/dev/null
  echo a > "$1/f"; g "$1" add f; g "$1" commit -m init; g "$1" push -q origin main
}
upcommit(){ # upstream n
  local w; w=$(mktemp -d "$T/w.XXXX"); git clone -q "$1" "$w" 2>/dev/null
  for i in $(seq 1 "$2"); do echo "$i$RANDOM" >> "$w/f"; g "$w" commit -am "up $i"; done
  g "$w" push -q origin main; rm -rf "$w"
}
OK="$T/proj-ok"; MIX="$T/proj-mixed"; U="$T/up"; mkdir -p "$U"
mkrepo "$OK/01-Building/fresh" "$U/fresh.git";  upcommit "$U/fresh.git" 2
mkrepo "$OK/02-Experiments/still" "$U/still.git"
mkdir -p "$OK/solo"; git init -q "$OK/solo"
mkrepo "$MIX/01-Building/dirty" "$U/dirty.git"; upcommit "$U/dirty.git" 1
echo locale >> "$MIX/01-Building/dirty/f"
mkrepo "$MIX/04-Production/diverged" "$U/div.git"; upcommit "$U/div.git" 1
echo mio > "$MIX/04-Production/diverged/g"; g "$MIX/04-Production/diverged" add g
g "$MIX/04-Production/diverged" commit -m mio

P=0; F=0
ck(){ if [[ "$2" == "$3" ]]; then echo "  PASS $1"; P=$((P+1)); else echo "  FAIL $1: atteso [$3] ottenuto [$2]"; F=$((F+1)); fi; }
J="$M/hosts/self/custode.json"
st(){ jq -r --arg n "$1" '.checks[] | select(.name == $n) | .status' "$J"; }
de(){ jq -r --arg n "$1" '.checks[] | select(.name == $n) | .detail' "$J"; }
custode(){
  env AB_HOME="$M" CLAUDE_CONFIG_DIR="$CFG" CUSTODE_MCP_DIR="$MCP" CUSTODE_PROJECTS="${PROJ:-$OK}" \
    CUSTODE_TELEGRAM_ENV="${TGF:-$TG}" LAUNCH_AGENTS_DIR="$LA" CUSTODE_WEEKDAY="${WD:-3}" \
    FAKE_LAUNCHCTL_LOADED="$LOADED" FAKE_CURL_LOG="$CURL_LOG" FAKE_CLAUDE_LOG="$CLAUDE_LOG" \
    "$@" bash "$R/scripts/agent-team-os-custode.sh" > "$T/out" 2>"$T/err"
  echo $?
}
head_of(){ git -C "$1" rev-parse HEAD; }

echo "── tutto ok"
rc=$(custode)
ck "uscita 0" "$rc" "0"
ck "status ok" "$(jq -r .status "$J")" "ok"
ck "host nel json" "$(jq -r .host "$J")" "mini"
ck "nessun Telegram" "$([[ -s $CURL_LOG ]] && echo inviato || echo silenzio)" "silenzio"
ck "claude con la config del mini" "$(grep -c "^$CFG -p Rispondi solo: OK --model haiku" "$CLAUDE_LOG")" "1"
ck "bus-mcp: 6 tool" "$(de bus-mcp)" "6 tool"
ck "python ok" "$(st python)" "ok"
ck "fresh aggiornato" "$(head_of "$OK/01-Building/fresh")" "$(git -C "$U/fresh.git" rev-parse main)"
ck "riga repo" "$(de repo | grep -c '01-Building/fresh aggiornato (2 commit)')" "1"
ck "senza upstream contato" "$(de repo | grep -c '1 senza upstream')" "1"
ck "ok in una riga sola" "$(grep -c '^ok: ' "$T/out")" "1"
ck "report di 2 righe" "$(wc -l < "$T/out" | tr -d ' ')" "2"
ck "json con fix" "$(jq -r '.checks | all(has("fix"))' "$J")" "true"
ck "nessun tmp lasciato" "$(ls "$M/hosts/self" | grep -c tmp)" "0"
ck "lock rilasciato" "$([[ -e $M/hosts/self/custode.lock ]] && echo resta || echo via)" "via"
rc=$(custode); ck "seconda volta: fermo" "$(de repo | grep -c '0 aggiornati, 2 fermi')" "1"

echo "── login scaduto"
: > "$CURL_LOG"
rc=$(custode FAKE_CLAUDE_MODE=expired)
ck "uscita 2" "$rc" "2"
ck "claude fail" "$(st claude)" "fail"
ck "prima riga del report e' il fail" "$(sed -n 2p "$T/out" | cut -c1-12)" "FAIL claude:"
ck "fix con /login" "$(jq -r '.checks[] | select(.name=="claude") | .fix' "$J" | grep -c 'claude-mini; claude.*/login')" "1"
ck "Telegram inviato" "$(grep -c -- '--- call' "$CURL_LOG")" "1"
ck "testo con il comando" "$(grep -c 'text=Mac mini, custode delle' "$CURL_LOG")" "1"
ck "testo con /login" "$(grep -c '/login' "$CURL_LOG")" "2"
ck "chat_id" "$(grep -c '^chat_id=4242$' "$CURL_LOG")" "1"
ck "token solo su stdin" "$(sed -n '/--- call/,/--- stdin/p' "$CURL_LOG" | grep -c SEGRETO)" "0"
ck "url con token su stdin" "$(grep -c 'url = "https://api.telegram.org/bot123:SEGRETO/sendMessage"' "$CURL_LOG")" "1"
ck "json: notify.sent" "$(jq -r .notify.sent "$J")" "true"
rc=$(custode FAKE_CLAUDE_MODE=hang CUSTODE_CLAUDE_TIMEOUT=1)
ck "claude appeso: fail" "$(de claude)" "nessuna risposta in 1 s"

echo "── coda vecchia"
mkdir -p "$M/outbox/laptop/alita"
q="$M/outbox/laptop/alita/msg-20261014T050000Z-aaaa1111.json"; echo '{}' > "$q"
touch -t "$(date -v-2H +%Y%m%d%H%M.%S)" "$q"
rc=$(custode)
ck "uscita 1" "$rc" "1"
ck "coda warn" "$(st coda)" "warn"
ck "dettaglio" "$(de coda)" "il laptop non raccoglie da 2 ore, 1 messaggi fermi"
touch -t "$(date -v-25H +%Y%m%d%H%M.%S)" "$q"
rc=$(custode)
ck "oltre 24 h: fail" "$(st coda)" "fail"
rm -f "$q"

echo "── repo sporco e repo divergente"
d0=$(head_of "$MIX/01-Building/dirty"); v0=$(head_of "$MIX/04-Production/diverged")
rc=$(PROJ="$MIX" custode)
ck "uscita 1" "$rc" "1"
ck "sporco: warn" "$(de repo:01-Building/dirty)" "non toccato, 1 file modificati"
ck "sporco: HEAD fermo" "$(head_of "$MIX/01-Building/dirty")" "$d0"
ck "sporco: modifica intatta" "$(tail -1 "$MIX/01-Building/dirty/f")" "locale"
ck "divergente: warn" "$(st repo:04-Production/diverged)" "warn"
ck "divergente: HEAD fermo" "$(head_of "$MIX/04-Production/diverged")" "$v0"
ck "nessuno stash" "$(git -C "$MIX/01-Building/dirty" stash list | wc -l | tr -d ' ')" "0"

echo "── disco"
rc=$(custode FAKE_DF_AVAIL_KB=$((3*1024*1024)))
ck "3 GB: fail" "$(st disco)/$rc" "fail/2"
rc=$(custode FAKE_DF_AVAIL_KB=$((10*1024*1024)))
ck "10 GB: warn" "$(st disco)/$rc" "warn/1"

echo "── file Telegram mancante"
: > "$CURL_LOG"
rc=$(TGF="$T/nessuno.env" custode FAKE_DF_AVAIL_KB=$((10*1024*1024)))
ck "uscita sui controlli (warn)" "$rc" "1"
ck "scritto nel report" "$(grep -c "^nota: file Telegram assente" "$T/out")" "1"
ck "nel json" "$(jq -r .notify.sent "$J")" "false"
ck "nessun curl" "$([[ -s $CURL_LOG ]] && echo si || echo no)" "no"
rc=$(TGF="$T/nessuno.env" custode)
ck "tutto ok senza Telegram: 0" "$rc" "0"

echo "── server MCP e Python"
rc=$(custode FAKE_MCP_MODE=broken)
ck "server rotto: fail" "$(st bus-mcp)" "fail"
ck "con l'ultima riga di stderr" "$(de bus-mcp | grep -c 'beartype')" "1"
rc=$(custode FAKE_MCP_MODE=few)
ck "tool mancanti nominati" "$(de bus-mcp)" "4 tool, mancano: bus_archive bus_thread"
rc=$(custode FAKE_UV_PYTHON=3.15.0)
ck "Python 3.15: fail" "$(st python)" "fail"
rm "$MCP/.python-version"
rc=$(custode)
ck ".python-version assente: warn" "$(st python)/$rc" "warn/1"
echo 3.12 > "$MCP/.python-version"
rc=$(custode CUSTODE_MCP_DIR="$T/non-c-e")
ck "cartella assente: fail" "$(st bus-mcp)" "fail"
# Il comando del server viene dalla config di Claude del mini.
mkdir -p "$T/altro-mcp"; echo 3.12 > "$T/altro-mcp/.python-version"
jq -n --arg d "$T/altro-mcp" '{mcpServers:{"agent-bus":{command:"uv",
  args:["run","--directory",$d,"python","-m","agent_bus_mcp.server"]}}}' > "$CFG/.claude.json"
rc=$(env -u CUSTODE_MCP_DIR AB_HOME="$M" CLAUDE_CONFIG_DIR="$CFG" CUSTODE_PROJECTS="$OK" CUSTODE_TELEGRAM_ENV="$TG" \
  LAUNCH_AGENTS_DIR="$LA" CUSTODE_WEEKDAY=3 FAKE_LAUNCHCTL_LOADED="$LOADED" bash "$R/scripts/agent-team-os-custode.sh" >/dev/null 2>&1; echo $?)
ck "server dalla config di Claude" "$(st bus-mcp)/$rc" "ok/0"
rm -f "$CFG/.claude.json"

echo "── launchd"
echo '<plist/>' > "$LA/com.agent-team-os.raccolta-x.plist"
rc=$(custode)
ck "plist non caricato: warn" "$(st launchd:com.agent-team-os.raccolta-x)/$rc" "warn/1"
ck "fix con bootstrap" "$(jq -r '.checks[] | select(.name=="launchd:com.agent-team-os.raccolta-x") | .fix' "$J" | grep -c 'launchctl bootstrap gui/')" "1"
rm "$LA/com.agent-team-os.raccolta-x.plist"

echo "── lunedi': una volta"
: > "$CURL_LOG"
rc=$(WD=1 custode)
ck "riepilogo settimanale" "$(grep -c 'tutto a posto, 0 repo aggiornati' "$CURL_LOG")" "1"
rc=$(WD=1 custode)
ck "non due volte lo stesso giorno" "$(grep -c -- '--- call' "$CURL_LOG")" "1"
rc=$(WD=1 custode FAKE_CLAUDE_MODE=expired)
ck "ma i fail si' (lunedi')" "$(grep -c -- '--- call' "$CURL_LOG")" "2"
: > "$CURL_LOG"
rc=$(env AB_HOME="$M" CLAUDE_CONFIG_DIR="$CFG" CUSTODE_MCP_DIR="$MCP" CUSTODE_PROJECTS="$OK" CUSTODE_TELEGRAM_ENV="$TG" \
  LAUNCH_AGENTS_DIR="$LA" FAKE_LAUNCHCTL_LOADED="$LOADED" FAKE_CURL_LOG="$CURL_LOG" FAKE_CLAUDE_MODE=expired \
  bash "$R/scripts/agent-team-os-custode.sh" --no-telegram >/dev/null 2>&1; echo $?)
ck "--no-telegram: niente invio" "$rc/$([[ -s $CURL_LOG ]] && echo si || echo no)" "2/no"

echo "── sul laptop non fa nulla"
Lp="$T/laptop"; mkdir -p "$Lp"; cp "$M/AGENT_MAP.json" "$Lp/"
out=$(AB_HOME="$Lp" bash "$R/scripts/agent-team-os-custode.sh"; echo "rc=$?")
ck "dice che non e' remoto" "$(echo "$out" | grep -c 'non sono un host remoto')" "1"
ck "esce 0" "$(echo "$out" | tail -1)" "rc=0"
ck "nessun json" "$([[ -e $Lp/hosts/self/custode.json ]] && echo si || echo no)" "no"
echo laptop > "$Lp/HOST"
ck "HOST=laptop: idem" "$(AB_HOME="$Lp" bash "$R/scripts/agent-team-os-custode.sh" | grep -c 'non sono')" "1"

echo "── install.sh --custode"
IC="$T/inst-claude"; IB="$T/inst-bus"; ILA="$T/inst-la"; LC_LOG="$T/launchctl.log"
inst(){ env CLAUDE_HOME="$IC" AB_HOME="$IB" LAUNCH_AGENTS_DIR="$ILA" FAKE_LAUNCHCTL_LOG="$LC_LOG" \
  bash "$R/install.sh" "$@" > "$T/inst.out" 2>&1; echo $?; }
mkdir -p "$IB"; cp "$M/AGENT_MAP.json" "$IB/"
ck "senza host remoto: rifiutato" "$(inst --custode)" "1"
ck "e lo dice" "$(grep -c -- '--custode' "$T/inst.out")" "1"
ck "con --host mini" "$(inst --host mini --custode)" "0"
PL="$ILA/com.agent-team-os.custode.plist"
ck "script copiato" "$([[ -x $IC/scripts/agent-team-os-custode.sh ]] && echo si)" "si"
ck "plist scritto" "$([[ -f $PL ]] && echo si)" "si"
if command -v plutil >/dev/null 2>&1; then
  ck "plist valido" "$(plutil -lint "$PL" >/dev/null && echo ok)" "ok"
  ck "alle 7:00" "$(plutil -extract StartCalendarInterval.Hour raw "$PL"):$(plutil -extract StartCalendarInterval.Minute raw "$PL")" "7:0"
  ck "RunAtLoad no" "$(plutil -extract RunAtLoad raw "$PL")" "false"
  ck "config Claude del mini" "$(plutil -extract EnvironmentVariables.CLAUDE_CONFIG_DIR raw "$PL")" "$IC"
  ck "log" "$(plutil -extract StandardOutPath raw "$PL")" "$IB/logs/custode.log"
fi
ck "bootout + bootstrap" "$(grep -c 'com.agent-team-os.custode' "$LC_LOG")" "2"
ck "HOST esistente basta" "$(inst --custode)" "0"
ck "--uninstall" "$(inst --custode --uninstall)" "0"
ck "plist tolto" "$([[ -e $PL ]] && echo resta || echo via)" "via"
ck "script tolto" "$([[ -e $IC/scripts/agent-team-os-custode.sh ]] && echo resta || echo via)" "via"
ck "--uninstall da solo rifiutato" "$(inst --uninstall)" "1"
echo laptop > "$IB/HOST"
ck "sull'hub rifiutato" "$(inst --custode)" "1"

echo; echo "PASS=$P FAIL=$F"; rm -rf "$T"; [[ $F -eq 0 ]]
