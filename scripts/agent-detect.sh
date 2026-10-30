#!/usr/bin/env bash
# ab_detect_agent <cwd>: agente della cartella secondo ~/.agent-team-os/AGENT_MAP.json
# (vince il prefisso piu' lungo, poi il fallback della mappa). Stessa regola di
# hooks/voice-notify/identity.mjs e del centralino (indirizzi.agent_for_cwd).
# Nato il 30/10/2026 (DC29-F5.5): install.sh di agent-team-os (BR24-F5.7) toglie agent-team-os-lib.sh,
# che statusline.sh e mvd-nudge.sh usavano tramite il link agent-bus-lib.sh.
ab_detect_agent() {
  /usr/bin/python3 -I - "${1:-$PWD}" "$HOME/.agent-team-os/AGENT_MAP.json" <<'PY' 2>/dev/null
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
