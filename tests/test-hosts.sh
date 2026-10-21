#!/bin/bash
# v2.5 — bus tra due macchine. Laptop e mini sono due AB_HOME in una cartella
# temporanea; `ssh` è tests/fake-ssh/ssh, che esegue in locale. Niente SSH vero.
set -uo pipefail
cd "$(dirname "$0")/.."; R="$PWD"
T=$(mktemp -d /tmp/abhosts.XXXXXX)
export HOME="$T/home"; mkdir -p "$HOME"
export PATH="$PWD/tests/fake-ssh:$PATH"
export FAKE_SSH_DOWN="$T/mini-down"
L="$T/laptop"; M="$T/mini"
for H in "$L" "$M"; do
  mkdir -p "$H"/{inboxes/kai,inboxes/alita,inboxes/leo,registry,agents,threads,outbox,locks}
  jq -n --arg mini "$M" '{
    agents: {alita:{domain:"side"}, kai:{domain:"side"}, leo:{domain:"work"}},
    rules: [], routing_rules: {},
    cc_awareness: {enabled:true, hub:"alita"},
    hosts: {laptop:{hub:true}, mini:{ssh:"mini", bus_home:$mini}}
  }' > "$H/AGENT_MAP.json"
done
echo mini > "$M/HOST"   # il laptop non ha HOST: vale l'host hub

source scripts/agent-team-os-lib.sh
on_laptop(){ ( AB_HOME="$L"; AB_MAP="$L/AGENT_MAP.json"; "$@" ); }
on_mini(){ ( AB_HOME="$M"; AB_MAP="$M/AGENT_MAP.json"; "$@" ); }
# ab_write_message gira in una bash figlia: AB_HOME/AB_MAP arrivano dall'ambiente.
send(){
  local m="$1" H; [[ "$1" == laptop ]] && H="$L" || H="$M"
  AB_HOME="$H" AB_MAP="$H/AGENT_MAP.json" AB_FROM="$2" AB_TO="$3" AB_TYPE=request AB_KIND=comunicazione \
    AB_INTENT=test AB_PRIORITY="${5:-normal}" AB_PAYLOAD_JSON="{\"summary\":\"$4\"}" \
    "$BASH" -c 'source scripts/agent-team-os-lib.sh; ab_write_message' 2>"$T/err"
}
n(){ find "$1" -name 'msg-*.json' -type f -not -path '*/.read/*' -not -path '*/.done/*' 2>/dev/null | wc -l | tr -d ' '; }
P=0; F=0
ck(){ if [[ "$2" == "$3" ]]; then echo "  PASS $1"; P=$((P+1)); else echo "  FAIL $1: atteso [$3] ottenuto [$2]"; F=$((F+1)); fi; }

echo "── indirizzi con host"
ck "agent da kai@mini/crewmoor" "$(ab_parse_addr_agent kai@mini/crewmoor)" "kai"
ck "slug da kai@mini/crewmoor"  "$(ab_parse_addr_slug kai@mini/crewmoor)" "crewmoor"
ck "host da kai@mini/crewmoor"  "$(ab_parse_addr_host kai@mini/crewmoor)" "mini"
ck "host da kai/crewmoor@mini"  "$(ab_parse_addr_host kai/crewmoor@mini)" "mini"
ck "slug da kai/crewmoor@mini"  "$(ab_parse_addr_slug kai/crewmoor@mini)" "crewmoor"
ck "kai@mini è broadcast"       "$(ab_parse_addr_slug kai@mini)" ""
ck "senza host, host vuoto"     "$(ab_parse_addr_host kai/crewmoor)" ""

echo "── chi sono e dove va"
ck "laptop senza HOST = hub" "$(on_laptop ab_host_self)" "laptop"
ck "mini da HOST"            "$(on_mini ab_host_self)" "mini"
ck "laptop: kai resta qui"   "$(on_laptop ab_resolve_host kai '')" ""
ck "laptop: kai@mini va al mini" "$(on_laptop ab_resolve_host kai mini)" "mini"
ck "laptop: kai@laptop è qui"    "$(on_laptop ab_resolve_host kai laptop)" ""
ck "mini: alita (hub) va al laptop" "$(on_mini ab_resolve_host alita '')" "laptop"
ck "mini: kai resta sul mini"    "$(on_mini ab_resolve_host kai '')" ""
on_laptop ab_resolve_host kai marte >/dev/null 2>"$T/err"; rc=$?
ck "host sconosciuto = errore" "$rc" "1"
ck "errore leggibile" "$(grep -c "unknown host 'marte'" "$T/err")" "1"
ck "send a host sconosciuto rifiutato" "$(send laptop alita kai@marte x >/dev/null; echo $?)" "1"

echo "── laptop → mini: consegna diretta"
id=$(send laptop alita kai@mini/crewmoor "ciao crewmoor")
ck "arriva nella dir di sessione del mini" "$(ls "$M/inboxes/kai/@crewmoor/$id.json" 2>/dev/null | wc -l | tr -d ' ')" "1"
ck "con .read e .done" "$([[ -d $M/inboxes/kai/@crewmoor/.done && -d $M/inboxes/kai/@crewmoor/.read ]] && echo si)" "si"
ck "context.host = laptop" "$(jq -r .context.host "$M/inboxes/kai/@crewmoor/$id.json")" "laptop"
ck "to resta nudo" "$(jq -r .to "$M/inboxes/kai/@crewmoor/$id.json")" "kai"
ck "niente nella inbox locale" "$(n "$L/inboxes/kai")" "0"
ck "niente in coda" "$(n "$L/outbox/mini")" "0"
ck "nessun .tmp lasciato" "$(find "$M/inboxes" -name '*.tmp' | wc -l | tr -d ' ')" "0"

echo "── mini giù: il messaggio resta in coda"
touch "$FAKE_SSH_DOWN"
qid=$(send laptop alita kai@mini "mentre dormi")
ck "send non fallisce" "$([[ -n "$qid" ]] && echo si)" "si"
ck "avvisa che è in coda" "$(grep -c "Queued in outbox/mini" "$T/err")" "1"
ck "in outbox/mini/kai/" "$(ls "$L/outbox/mini/kai/$qid.json" 2>/dev/null | wc -l | tr -d ' ')" "1"
ck "non arrivato al mini" "$([[ -e $M/inboxes/kai/$qid.json ]] && echo arrivato || echo no)" "no"

echo "── mini → laptop: outbox del mini"
aid=$(send mini kai alita "per alita" high)
ck "alita dal mini va in outbox/laptop" "$(ls "$M/outbox/laptop/alita/$aid.json" 2>/dev/null | wc -l | tr -d ' ')" "1"
ck "context.host = mini" "$(jq -r .context.host "$M/outbox/laptop/alita/$aid.json")" "mini"
sid=$(send mini kai kai@laptop/agent-team-os "a una sessione del laptop")
ck "slug viaggia nel percorso" "$(ls "$M/outbox/laptop/kai/@agent-team-os/$sid.json" 2>/dev/null | wc -l | tr -d ' ')" "1"
lid=$(send mini alita kai "locale sul mini")
ck "mini → kai resta sul mini" "$(ls "$M/inboxes/kai/$lid.json" 2>/dev/null | wc -l | tr -d ' ')" "1"

echo "── cc-awareness attraversa le macchine"
send mini kai leo "cross-domain sul mini" >/dev/null
ck "cc per alita in outbox/laptop" "$(grep -l '"cc-notify"' "$M"/outbox/laptop/alita/*.json 2>/dev/null | wc -l | tr -d ' ')" "1"
ck "leo lo riceve sul mini" "$(n "$M/inboxes/leo")" "1"

echo "── raccolta a mini giù: nulla si perde"
out=$(on_laptop ab_collect_outbox --force)
ck "dice unreachable" "$(echo "$out" | grep -c 'mini unreachable')" "1"
ck "coda intatta" "$(n "$L/outbox/mini")" "1"
ck "outbox del mini intatta" "$(n "$M/outbox/laptop")" "3"

echo "── raccolta a mini su"
rm -f "$FAKE_SSH_DOWN"
out=$(on_laptop ab_collect_outbox --force)
ck "esito" "$out" "mini ok pushed=1 pulled=3 queued=0"
ck "coda svuotata" "$(n "$L/outbox/mini")" "0"
ck "il messaggio in coda è sul mini" "$(ls "$M/inboxes/kai/$qid.json" 2>/dev/null | wc -l | tr -d ' ')" "1"
ck "alita ha il messaggio del mini" "$(ls "$L/inboxes/alita/$aid.json" 2>/dev/null | wc -l | tr -d ' ')" "1"
ck "e il cc" "$(n "$L/inboxes/alita")" "2"
ck "sessione del laptop servita" "$(ls "$L/inboxes/kai/@agent-team-os/$sid.json" 2>/dev/null | wc -l | tr -d ' ')" "1"
ck "outbox del mini svuotata" "$(n "$M/outbox/laptop")" "0"
ck "thread registrato sul laptop" "$(jq -r '.messages[].id' "$L/threads/$(jq -r .thread_id "$L/inboxes/alita/$aid.json").json" | grep -c "$aid")" "1"
ck "urgenti visti dall'hook" "$(on_laptop ab_count_inbox_priority alita)" "1"

echo "── copia, verifica, poi cancella"
# Cancellazione remota fallita l'altra volta: il file ricompare sul mini.
mkdir -p "$M/outbox/laptop/alita"; cp "$L/inboxes/alita/$aid.json" "$M/outbox/laptop/alita/"
echo '{rotto' > "$M/outbox/laptop/alita/msg-20990101T000000Z-broken01.json"
out=$(on_laptop ab_collect_outbox --force)
ck "nessun doppione" "$(n "$L/inboxes/alita")" "2"
ck "il doppione sparisce dal mini" "$([[ -e $M/outbox/laptop/alita/$aid.json ]] && echo resta || echo via)" "via"
ck "il file rotto resta sul mini" "$([[ -e $M/outbox/laptop/alita/msg-20990101T000000Z-broken01.json ]] && echo resta)" "resta"
ck "pulled=0" "$(echo "$out" | grep -c 'pulled=0')" "1"
rm -f "$M/outbox/laptop/alita/msg-20990101T000000Z-broken01.json"
# Già archiviato dal destinatario: non rinasce.
mkdir -p "$L/inboxes/alita/.done"; mv "$L/inboxes/alita/$aid.json" "$L/inboxes/alita/.done/"
cp "$L/inboxes/alita/.done/$aid.json" "$M/outbox/laptop/alita/"
on_laptop ab_collect_outbox --force >/dev/null
ck "archiviato non rinasce" "$([[ -e $L/inboxes/alita/$aid.json ]] && echo rinato || echo no)" "no"

echo "── throttle"
ck "subito dopo, nessuna raccolta" "$(on_laptop ab_collect_outbox)" ""
ck "il mini non raccoglie (non raggiunge nessuno)" "$(on_mini ab_remote_hosts)" ""

echo "── risposta che torna indietro"
f0="$L/inboxes/alita/.done/$aid.json"
# BS21-F3.3b: la lib timbra from_session (qui lo slug della cartella dei test).
ck "il messaggio della lib porta from_session" "$(jq -r .from_session "$f0")" "kai/$(ab_slug_for_cwd "$PWD")"
f="$T/nofs.json"; jq 'del(.from_session)' "$f0" > "$f"   # i messaggi di prima, senza
ck "indirizzo di risposta" "$(on_laptop ab_reply_address "$f")" "kai@mini"
rid=$(AB_THREAD_ID=$(jq -r .thread_id "$f") send laptop alita "$(on_laptop ab_reply_address "$f")" "risposta")
ck "la risposta arriva sul mini" "$(ls "$M/inboxes/kai/$rid.json" 2>/dev/null | wc -l | tr -d ' ')" "1"
ck "stesso thread" "$(jq -r .thread_id "$M/inboxes/kai/$rid.json")" "$(jq -r .thread_id "$f")"
jq '. + {from_session:"kai/crewmoor"}' "$f" > "$T/fs.json"
ck "con from_session torna alla sessione" "$(on_laptop ab_reply_address "$T/fs.json")" "kai@mini/crewmoor"

echo "── stato del mini visto dal laptop"
on_mini ab_update_session_registry kai true /x/Projects/crewmoor
ck "registry del mini con host" "$(jq -r .host "$M/registry/kai.d/crewmoor.json")" "mini"
row=$(on_laptop ab_list_remote_sessions mini)
ck "sessione del mini elencata" "$(echo "$row" | cut -f1,2)" "kai	crewmoor"
ck "pending = suoi + broadcast" "$(echo "$row" | cut -f4)" "$(( $(n "$M/inboxes/kai/@crewmoor") + $(find "$M/inboxes/kai" -maxdepth 1 -name 'msg-*.json' | wc -l | tr -d ' ') ))"
touch "$FAKE_SSH_DOWN"
ck "cache 20 s: nessuna chiamata" "$(on_laptop ab_list_remote_sessions mini | cut -f2)" "crewmoor"
cache=$(on_laptop ab_remote_status mini --force)
ck "mini giù: ok=false" "$(jq -r .ok "$cache")" "false"
ck "ma le sessioni restano" "$(jq -r '.sessions[0].slug' "$cache")" "crewmoor"
rm -f "$FAKE_SSH_DOWN"

echo "── gli hook non aspettano l'SSH"
mkdir -p "$T/ws/alita-home"
jq --arg ws "$T/ws/alita-home" '.rules = [{pattern:$ws, agent:"alita"}]' "$L/AGENT_MAP.json" > "$T/map" && mv "$T/map" "$L/AGENT_MAP.json"
bid=$(send mini kai alita "mentre il laptop scrive" urgent)
rm -f "$L/hosts/mini/last-collect"
t0=$(date +%s)
out=$(cd "$T/ws/alita-home" && AB_HOME="$L" FAKE_SSH_SLEEP=2 CLAUDE_PROJECT_DIR="$T/ws/alita-home" bash "$R/hooks/agent-team-os-urgent.sh" </dev/null)
t1=$(date +%s)
ck "UserPromptSubmit torna subito con SSH lento" "$(( t1 - t0 < 2 ))" "1"
for i in 1 2 3 4 5 6 7 8 9 10; do [[ -e "$L/inboxes/alita/$bid.json" ]] && break; sleep 0.5; done
ck "la raccolta in background consegna" "$([[ -e $L/inboxes/alita/$bid.json ]] && echo si)" "si"
out=$(cd "$T/ws/alita-home" && AB_HOME="$L" CLAUDE_PROJECT_DIR="$T/ws/alita-home" bash "$R/hooks/agent-team-os-urgent.sh" </dev/null)
ck "e al turno dopo l'urgente si vede" "$(echo "$out" | grep -c 'urgent/high')" "1"
out=$(cd "$T/ws/alita-home" && AB_HOME="$L" CLAUDE_PROJECT_DIR="$T/ws/alita-home" bash "$R/hooks/agent-team-os-load.sh" </dev/null)
ck "SessionStart dice l'host" "$(echo "$out" | grep -c 'alita@laptop')" "1"
ck "registry con host" "$(jq -r .host "$L/registry/alita.d/alita-home.json")" "laptop"

echo "── payload di default e messaggio malformato"
did=$(AB_HOME="$M" AB_MAP="$M/AGENT_MAP.json" AB_FROM=kai AB_TO=alita AB_TYPE=event AB_INTENT=t "$BASH" -c 'source scripts/agent-team-os-lib.sh; ab_write_message' 2>"$T/err")
ck "senza payload il messaggio è valido" "$(jq -c .payload "$M/outbox/laptop/alita/$did.json")" "{}"
bad=$(AB_HOME="$M" AB_MAP="$M/AGENT_MAP.json" AB_FROM=kai AB_TO=alita AB_TYPE=event AB_INTENT=t AB_PAYLOAD_JSON='{rotto' "$BASH" -c 'source scripts/agent-team-os-lib.sh; ab_write_message' 2>/dev/null; echo "rc=$?")
ck "payload rotto rifiutato" "$bad" "rc=4"
ck "nessun file senza id in coda" "$(find "$M/outbox" -name '.json' | wc -l | tr -d ' ')" "0"

echo "── bus su una macchina sola: invariato"
jq 'del(.hosts)' "$L/AGENT_MAP.json" > "$T/map" && mv "$T/map" "$L/AGENT_MAP.json"
oid=$(send laptop alita kai "come prima")
ck "consegna locale" "$(ls "$L/inboxes/kai/$oid.json" | wc -l | tr -d ' ')" "1"
ck "niente context" "$(jq -r 'has("context")' "$L/inboxes/kai/$oid.json")" "false"
ck "niente host nel registry" "$(on_laptop ab_update_session_registry kai true /x/solo; jq -r 'has("host")' "$L/registry/kai.d/solo.json")" "false"
ck "@host senza hosts = errore" "$(send laptop alita kai@mini x >/dev/null; echo $?)" "1"
ck "nessuna raccolta" "$(on_laptop ab_collect_outbox --force)" ""

echo; echo "PASS=$P FAIL=$F"; rm -rf "$T"; [[ $F -eq 0 ]]
