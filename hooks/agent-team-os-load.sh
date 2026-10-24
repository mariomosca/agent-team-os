#!/usr/bin/env bash
# agent-team-os-load.sh — bus v3 (BR24-F4.2): l'hook passa al centralino. Vedi agent-team-os-centralino.py.
exec /usr/bin/python3 -I "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/scripts/agent-team-os-centralino.py" start
