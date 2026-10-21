#!/bin/bash
# La lib caricata da zsh, come la carica la Bash di Claude Code (BS21-F3.3b).
# In zsh `path` e' legato a PATH: un `local path` nasconde ogni comando.
set -uo pipefail
cd "$(dirname "$0")/.."; R="$PWD"
command -v zsh >/dev/null || { echo "zsh assente: salto"; exit 0; }
T=$(mktemp -d /tmp/abzsh.XXXXXX)
export AB_HOME="$T/bus" AB_MAP="$T/bus/AGENT_MAP.json"
mkdir -p "$AB_HOME"/{inboxes/kai,inboxes/alita,registry,threads,outbox,locks}
echo '{"agents":{"alita":{},"kai":{}},"rules":[],"routing_rules":{}}' > "$AB_MAP"
P=0; F=0
ck(){ if [[ "$2" == "$3" ]]; then echo "  PASS $1"; P=$((P+1)); else echo "  FAIL $1: atteso [$3] ottenuto [$2]"; F=$((F+1)); fi; }
z(){ zsh -f -c "source '$R/scripts/agent-team-os-lib.sh'; $1" 2>&1; }

echo "── invio e gestione da zsh"
d=$(z 'AB_FROM=alita AB_SESSION_SLUG=work-hub AB_TO=kai/f3 AB_TYPE=request AB_KIND=delega AB_INTENT=t AB_PAYLOAD_JSON="{\"summary\":\"s\"}" ab_write_message 2>/dev/null')
ck "delega spedita" "$([[ "$d" =~ ^msg- ]] && echo ok)" "ok"
m=$(find "$AB_HOME/inboxes/kai" -name "$d.json")
ck "from_session sul messaggio della lib" "$(jq -r .from_session "$m")" "alita/work-hub"
tid=$(jq -r .task_id "$m")
r=$(z 'AB_FROM=kai AB_SESSION_SLUG=f3 AB_TO=alita/work-hub AB_TYPE=response AB_INTENT=t AB_TASK_ID='"$tid"' AB_PAYLOAD_JSON="{\"summary\":\"fatto\"}" ab_write_message 2>/dev/null')
out=$(z "AB_AGENT=alita AB_SESSION_SLUG=work-hub ab_gestisci $r chiuso")
ck "ab_gestisci funziona in zsh" "$(jq -r '.wait.state' <<<"$out" 2>/dev/null)" "closed"
ck "nessun 'command not found'" "$(grep -c 'command not found' <<<"$out")" "0"
x=$(z "AB_AGENT=kai AB_SESSION_SLUG=f3 ab_gestisci $d letto")
ck "letto dall'esecutore" "$(jq -r '.esito' <<<"$x" 2>/dev/null)" "letto"
ck "PATH intatto dopo le funzioni" "$(z 'AB_AGENT=kai ab_gestisci nessuno letto >/dev/null 2>&1; command -v head >/dev/null && echo ok')" "ok"

rm -rf "$T"
echo "── $P PASS, $F FAIL"
[[ $F -eq 0 ]]
