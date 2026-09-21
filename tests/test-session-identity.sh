set -uo pipefail
export AB_HOME=$(mktemp -d /tmp/abtest.XXXXXX)
mkdir -p "$AB_HOME"/{inboxes/kai,inboxes/alita,registry,agents,threads,outbox,locks}
cp ~/.agent-team-os/AGENT_MAP.json "$AB_HOME/AGENT_MAP.json"
echo '{}' > "$AB_HOME/registry/kai.json"; echo '{}' > "$AB_HOME/registry/alita.json"
source scripts/agent-team-os-lib.sh
P=0; F=0
ck(){ if [[ "$2" == "$3" ]]; then echo "  PASS $1"; P=$((P+1)); else echo "  FAIL $1: atteso [$3] ottenuto [$2]"; F=$((F+1)); fi; }

echo "── slug"
ck "basename lowercased" "$(ab_slug_for_cwd /Users/mariomosca/Projects/02-Experiments/noi-calendar)" "noi-calendar"
ck "parse agent" "$(ab_parse_addr_agent kai/noi-calendar)" "kai"
ck "parse slug"  "$(ab_parse_addr_slug  kai/noi-calendar)" "noi-calendar"
ck "bare = broadcast" "$(ab_parse_addr_slug kai)" ""

echo "── due sessioni non si sovrascrivono (IL BUG)"
ab_update_session_registry kai true /Users/mariomosca/Projects/07-Tooling/mcp/microsoft-mcp
sleep 1
ab_update_session_registry kai true /Users/mariomosca/Projects/02-Experiments/noi-calendar
ck "due sessioni vive" "$(ab_list_sessions kai | sort | tr '\n' ',')" "microsoft-mcp,noi-calendar,"

echo "── consegna mirata"
export AB_FROM=alita AB_TYPE=brief AB_INTENT=new-app AB_PAYLOAD_JSON='{"summary":"per noi-calendar"}'
AB_TO=kai/noi-calendar ab_write_message >/dev/null 2>&1
AB_PAYLOAD_JSON='{"summary":"per microsoft-mcp"}' AB_TO=kai/microsoft-mcp ab_write_message >/dev/null 2>&1
AB_PAYLOAD_JSON='{"summary":"broadcast"}' AB_TO=kai ab_write_message >/dev/null 2>&1
ck "sessione noi-calendar vede 2 (suo + broadcast)" "$(AB_SESSION_SLUG=noi-calendar ab_count_inbox kai)" "2"
ck "sessione microsoft-mcp vede 2 (suo + broadcast)" "$(AB_SESSION_SLUG=microsoft-mcp ab_count_inbox kai)" "2"
ck "senza slug le vede tutte e 3" "$(ab_count_inbox kai)" "3"

echo "── il messaggio giusto alla sessione giusta"
got=$(AB_SESSION_SLUG=noi-calendar ab_list_inbox kai | xargs -I{} jq -r '.payload.summary' {} | sort | tr '\n' ',')
ck "contenuto noi-calendar" "$got" "broadcast,per noi-calendar,"

echo "── stop hook non conta i messaggi dell'altra sessione"
ck "drain noi-calendar" "$(AB_SESSION_SLUG=noi-calendar ab_drain_fresh_count kai '')" "2"

echo "── cursor separati"
ck "cursor per sessione" "$(basename $(ab_cursor_path kai noi-calendar))" "cursor@noi-calendar.json"

echo "── .read resta nella dir di sessione"
f=$(AB_SESSION_SLUG=noi-calendar ab_list_inbox kai | grep '@noi-calendar' | head -1)
ab_mark_read kai "$f"
ck "archiviato accanto" "$(ls $AB_HOME/inboxes/kai/@noi-calendar/.read/*.json 2>/dev/null | wc -l | tr -d ' ')" "1"
ck "dopo read ne resta 1" "$(AB_SESSION_SLUG=noi-calendar ab_count_inbox kai)" "1"
ck "l'altra sessione intatta" "$(AB_SESSION_SLUG=microsoft-mcp ab_count_inbox kai)" "2"

echo "── la dir di sessione nasce con .read e .done (segnalato da Kai)"
AB_PAYLOAD_JSON='{"summary":"nuova"}' AB_TO=kai/mai-vista ab_write_message >/dev/null 2>&1
ck "dir .done creata alla consegna" "$([[ -d $AB_HOME/inboxes/kai/@mai-vista/.done ]] && echo si)" "si"
ck "dir .read creata alla consegna" "$([[ -d $AB_HOME/inboxes/kai/@mai-vista/.read ]] && echo si)" "si"

echo "── retrocompat: destinatario nudo"
ck "to nel JSON resta 'kai'" "$(jq -r '.to' $AB_HOME/inboxes/kai/msg-*.json | head -1)" "kai"
mid=$(basename $(ls $AB_HOME/inboxes/kai/@microsoft-mcp/msg-*.json|head -1) .json)
ck "resolve trova nelle dir sessione" "$(basename $(ab_resolve_msg_path kai "$mid") .json)" "$mid"

echo; echo "PASS=$P FAIL=$F"; rm -rf "$AB_HOME"; [[ $F -eq 0 ]]
