#!/bin/bash
# v2.6 — lato bash degli esiti attesi: task_id nei messaggi, eredità dalla
# risposta, timbro wake_chain, ab_expect. La sveglia ha i suoi test (test_wake.py).
set -uo pipefail
cd "$(dirname "$0")/.."; R="$PWD"
T=$(mktemp -d /tmp/abwakelib.XXXXXX)
export AB_HOME="$T/bus" AB_MAP="$T/bus/AGENT_MAP.json" HOME="$T/home"
mkdir -p "$AB_HOME"/{inboxes/kai,inboxes/alita,registry,threads,outbox,locks} "$HOME"
echo '{"agents":{"alita":{},"kai":{},"leo":{}},"rules":[],"routing_rules":{}}' > "$AB_MAP"
P=0; F=0
ck(){ if [[ "$2" == "$3" ]]; then echo "  PASS $1"; P=$((P+1)); else echo "  FAIL $1: atteso [$3] ottenuto [$2]"; F=$((F+1)); fi; }
send(){ # from slug to type [env...]
  local from="$1" slug="$2" to="$3" type="$4"; shift 4
  env AB_FROM="$from" AB_SESSION_SLUG="$slug" AB_TO="$to" AB_TYPE="$type" AB_INTENT=test \
    AB_PAYLOAD_JSON='{"summary":"s"}' "$@" "$BASH" -c 'source scripts/agent-team-os-lib.sh; ab_write_message' 2>/dev/null
}
msg(){ find "$AB_HOME/inboxes" -name "$1.json" | head -1; }

echo "── task_id"
id=$(send alita work-hub kai/f3 request AB_KIND=delega AB_TASK_ID=t-20261017-aaa111)
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

echo "── waiting_agent (v2.7)"
reg(){ mkdir -p "$AB_HOME/registry/kai.d"
  echo '{"name":"kai","slug":"f3","active":true,"last_seen":"2026-10-18T10:00:00Z","pid":1}' > "$AB_HOME/registry/kai.d/f3.json"; }
R3="$AB_HOME/registry/kai.d/f3.json"
reg
qid=$(send kai f3 alita/work-hub question AB_TASK_ID=t-20261018-qqq111)
ck "domanda con task_id segna l'attesa" "$(jq -r '.waiting_agent | .agent + " " + .address + " " + .task_id + " " + (.msg_id == "'"$qid"'" | tostring)' "$R3")" "alita alita/work-hub t-20261018-qqq111 true"
ck "summary senza spazi in coda" "$(jq -r '.waiting_agent.summary' "$R3")" "s"
"$BASH" -c "source scripts/agent-team-os-lib.sh; ab_update_session_registry kai true /w/f3 f3" 2>/dev/null
ck "il heartbeat la conserva" "$(jq -r '.waiting_agent.task_id' "$R3")" "t-20261018-qqq111"
eid=$(send alita work-hub kai/f3 brief AB_KIND=comunicazione)
AB_SESSION_SLUG=f3 "$BASH" -c "source scripts/agent-team-os-lib.sh; ab_mark_read kai '$(msg "$eid")'"
ck "un messaggio estraneo non la toglie" "$(jq -r 'has("waiting_agent")' "$R3")" "true"
aid=$(send alita work-hub kai/f3 response AB_IN_REPLY_TO="$qid")
AB_SESSION_SLUG=f3 "$BASH" -c "source scripts/agent-team-os-lib.sh; ab_mark_read kai '$(msg "$aid")'"
ck "leggere la risposta la toglie" "$(jq -r 'has("waiting_agent")' "$R3")" "false"
send kai f3 alita/work-hub question AB_TASK_ID=t-20261018-qqq222 >/dev/null
send kai f3 alita/work-hub response AB_TASK_ID=t-20261018-qqq222 >/dev/null
ck "mandare l'esito la toglie" "$(jq -r 'has("waiting_agent")' "$R3")" "false"
send kai f3 alita/work-hub question >/dev/null
ck "domanda senza task_id: niente attesa" "$(jq -r 'has("waiting_agent")' "$R3")" "false"

echo "── BS21: tipo di invio dichiarato e delega = attesa"
for t in request brief handoff; do
  out=$(env AB_FROM=alita AB_SESSION_SLUG=work-hub AB_TO=kai AB_TYPE=$t AB_INTENT=x AB_PAYLOAD_JSON='{"summary":"s"}' \
        "$BASH" -c 'source scripts/agent-team-os-lib.sh; ab_write_message' 2>&1); rc=$?
  ck "$t senza AB_KIND rifiutato" "$rc $([[ "$out" == *"'delega' if you want the outcome back"* ]] && echo spiega)" "5 spiega"
done
n_before=$(find "$AB_HOME/inboxes" -name 'msg-*.json' | wc -l | tr -d ' ')
send alita work-hub kai request AB_KIND=forse >/dev/null; ck "AB_KIND sconosciuto rifiutato" "$?" "5"
send kai f3 alita response AB_KIND=delega >/dev/null; ck "una response non e' una delega" "$?" "5"
ck "nessun messaggio scritto dai rifiuti" "$(find "$AB_HOME/inboxes" -name 'msg-*.json' | wc -l | tr -d ' ')" "$n_before"
cid=$(send leo pm alita brief AB_KIND=comunicazione)
ck "comunicazione: kind, niente task_id" "$(jq -r '.kind + " " + (has("task_id")|tostring)' "$(msg "$cid")")" "comunicazione false"
did=$(env AB_FROM=kai AB_SESSION_SLUG=f3 AB_TO=alita/work-hub AB_TYPE=request AB_KIND=delega AB_INTENT=review \
      AB_PAYLOAD_JSON='{"summary":"guarda il diff"}' AB_RESPONSE_BY=2026-10-30T18:00:00Z \
      "$BASH" -c 'source scripts/agent-team-os-lib.sh; ab_write_message' 2>"$T/err")
m=$(msg "$did"); dt=$(jq -r .task_id "$m")
ck "delega kai->alita: task_id sul messaggio" "$([[ "$dt" =~ ^t-[0-9]{8}-[a-z0-9]{6}$ ]] && echo ok) $(jq -r '.kind + " " + (.requires_response|tostring)' "$m")" "ok delega true"
W="$AB_HOME/waits/kai/@f3/$dt.json"
ck "attesa registrata da sola" "$(jq -r '.state + " " + .delegated_to + " " + .deadline' "$W")" "expected alita/work-hub 2026-10-30T18:00:00Z"
ck "attesa sa messaggio e thread" "$(jq -r '(.delegation.msg_id == "'"$did"'") and (.delegation.thread_id == "'"$(jq -r .thread_id "$m")"'")' "$W")" "true"
ck "stderr conferma l'attesa" "$(grep -c "attesa registrata $dt" "$T/err")" "1"

echo "── BS21: ab_gestisci e hook di Stop"
G(){ AB_AGENT=kai AB_SESSION_SLUG=f3 "$BASH" -c "source scripts/agent-team-os-lib.sh; ab_gestisci $*" 2>&1; }
# ritorni di alita sulla delega kai->alita di sopra (attesa $W, di kai)
rid=$(send alita work-hub kai/f3 response AB_TASK_ID="$dt")
r2=$(send alita work-hub kai/f3 event AB_TASK_ID="$dt")
out=$(G "$r2" riarma)
ck "riarma: attesa expected" "$(jq -r '.wait.state' <<<"$out") $(jq -r '.rearmed_at != null' "$W")" "expected true"
ck "riarma: messaggio in .done con l'esito" "$(jq -r '.handled.esito' "$(msg "$r2")") $(basename "$(dirname "$(msg "$r2")")")" "riarma .done"
out=$(G "$rid" chiuso)
ck "chiuso: attesa chiusa" "$(jq -r '.state' "$W")" "closed"
lid=$(send alita work-hub kai/f3 event)
G "$lid" letto >/dev/null
ck "letto: in .read" "$(basename "$(dirname "$(msg "$lid")")")" ".read"
e=$(send alita work-hub kai/f3 event); out=$(G "$e" riarma); rc=$?
ck "riarma senza delega rifiutato" "$rc $([[ "$out" == *"delega"* ]] && echo spiega)" "1 spiega"
sid=$(send alita work-hub kai/f3 event)
stop=$(cd "$T" && AB_SESSION_SLUG=f3 "$BASH" -c "source '$R/scripts/agent-team-os-lib.sh'; ab_drain_for_stop kai 3")
ck "Stop chiede un esito" "$([[ "$stop" == *"bus_gestisci"* && "$stop" == *"riarma"* ]] && echo si)" "si"
ck "Stop: JSON valido" "$(jq -r .decision <<<"$stop")" "block"

rm -rf "$T"
echo "── $P PASS, $F FAIL"
[[ $F -eq 0 ]]
