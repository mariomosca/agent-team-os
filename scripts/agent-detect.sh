#!/usr/bin/env bash
# ab_detect_agent <cwd>: agente della cartella secondo l'agent-map (cache del CLI Roster, ~/.roster/cache/agent-map.json;
# in mancanza il file a mano ~/.agent-team-os/AGENT_MAP.json; AB_AGENT_MAP indica un file preciso; RS07-F4.10)
# (vince il prefisso piu' lungo, poi il fallback della mappa). Stessa regola di
# hooks/voice-notify/identity.mjs e del centralino (indirizzi.agent_for_cwd).
# Nato il 30/10/2026 (DC29-F5.5): install.sh di agent-team-os (BR24-F5.7) toglie agent-team-os-lib.sh,
# che statusline.sh e mvd-nudge.sh usavano tramite il link agent-bus-lib.sh.
ab_agent_map_file() {
  local c="${ROSTER_HOME:-$HOME/.roster}/cache/agent-map.json"
  if [ -n "${AB_AGENT_MAP:-}" ]; then printf '%s' "$AB_AGENT_MAP"
  elif [ -f "$c" ]; then printf '%s' "$c"
  else printf '%s' "${AB_HOME:-$HOME/.agent-team-os}/AGENT_MAP.json"; fi
}
ab_detect_agent() {
  /usr/bin/python3 -I - "${1:-$PWD}" "$(ab_agent_map_file)" <<'PY' 2>/dev/null
import json, sys
cwd, path = sys.argv[1].rstrip("/") or "/", sys.argv[2]
try:
    m = json.load(open(path))
except Exception:
    sys.exit(0)
best, n = None, -1
for r in m.get("rules") or []:
    p = str(r.get("pattern", "")).rstrip("/")
    if not p:
        continue
    hit = cwd == p if r.get("match") == "exact" else (cwd == p or cwd.startswith(p + "/"))
    if hit and len(p) > n:
        best, n = r.get("agent"), len(p)
print(best or m.get("fallback") or "")
PY
}
