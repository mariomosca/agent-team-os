#!/bin/bash
# v2.6 — lato bash degli esiti attesi: task_id nei messaggi, eredità dalla
# risposta, timbro wake_chain, ab_expect. La sveglia ha i suoi test (test_wake.py).
set -uo pipefail
cd "$(dirname "$0")/.."; R="$PWD"
T=$(mktemp -d /tmp/abwakelib.XXXXXX)
export AB_HOME="$T/bus" AB_MAP="$T/bus/AGENT_MAP.json" HOME="$T/home"
mkdir -p "$AB_HOME"/{inboxes/kai,inboxes/alita,registry,threads,outbox,locks} "$HOME"
echo '{"agents":{"alita":{},"kai":{}},"rules":[],"routing_rules":{}}' > "$AB_MAP"
P=0; F=0
ck(){ if [[ "$2" == "$3" ]]; then echo "  PASS $1"; P=$((P+1)); else echo "  FAIL $1: atteso [$3] ottenuto [$2]"; F=$((F+1)); fi; }
send(){ # from slug to type [env...]
  local from="$1" slug="$2" to="$3" type="$4"; shift 4
  env AB_FROM="$from" AB_SESSION_SLUG="$slug" AB_TO="$to" AB_TYPE="$type" AB_INTENT=test \
    AB_PAYLOAD_JSON='{"summary":"s"}' "$@" "$BASH" -c 'source scripts/agent-team-os-lib.sh; ab_write_message' 2>/dev/null
}
msg(){ find "$AB_HOME/inboxes" -name "$1.json" | head -1; }

echo "── task_id"
id=$(send alita work-hub kai/f3 request AB_TASK_ID=t-20261017-aaa111)
ck "richiesta con task_id" "$(jq -r .task_id "$(msg "$id")")" "t-20261017-aaa111"
rid=$(send kai f3 alita/work-hub response AB_IN_REPLY_TO="$id")
ck "risposta eredita task_id" "$(jq -r .task_id "$(msg "$rid")")" "t-20261017-aaa111"
nid=$(send kai f3 alita event)
ck "senza task_id niente campo" "$(jq -r 'has("task_id")' "$(msg "$nid")")" "false"

echo "── wake_chain"
mkdir -p "$AB_HOME/waits/kai/@f3"
echo '{"task_id":"t-9","at":"2026-10-17T10:00:00Z"}' > "$AB_HOME/waits/kai/@f3/.woken.json"
cid=$(send kai f3 alita/work-hub response AB_TASK_ID=t-20261017-bbb222)
ck "sessione svegliata timbra" "$(jq -c .wake_chain "$(msg "$cid")")" '{"task_id":"t-9","at":"2026-10-17T10:00:00Z","session":"kai/f3"}'
touch -t 202601010000 "$AB_HOME/waits/kai/@f3/.woken.json"
oid=$(send kai f3 alita/work-hub response AB_TASK_ID=t-20261017-ccc333)
ck "sveglia vecchia non timbra" "$(jq -r 'has("wake_chain")' "$(msg "$oid")")" "false"

echo "── ab_expect"
tid=$(cd "$T" && AB_AGENT=alita AB_SESSION_SLUG=work-hub ONDA_TERMINAL_ID=pane-9 \
      "$BASH" -c "source '$R/scripts/agent-team-os-lib.sh'; ab_expect 'spike orbit' kai/f3")
ck "task_id generato" "$([[ "$tid" =~ ^t-[0-9]{8}-[a-z0-9]{6}$ ]] && echo ok)" "ok"
w="$AB_HOME/waits/alita/@work-hub/$tid.json"
ck "atteso scritto" "$(jq -r '.state + " " + .delegated_to + " " + .target.onda_terminal_id' "$w")" "expected kai/f3 pane-9"
dup=$(AB_AGENT=kai AB_SESSION_SLUG=f3 "$BASH" -c "source scripts/agent-team-os-lib.sh; ab_expect x '' '' $tid" 2>&1)
ck "un solo atteso per task" "$([[ "$dup" == *"un solo atteso"* ]] && echo rifiutato)" "rifiutato"

rm -rf "$T"
echo "── $P PASS, $F FAIL"
[[ $F -eq 0 ]]
