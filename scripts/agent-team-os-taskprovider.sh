#!/usr/bin/env bash
# Agent Team OS — TaskProvider layer (v2.0)
#
# Maps each GOD domain to its task manager (the "provider") and exposes a
# uniform contract. This is SHELL GLUE: it does not call MCP tools directly
# (Claude Code does that), it tells the agent WHICH provider + mapping to use.
#
# Contract: PLAN-AGENT-TEAM-OS.md §v2.0 §6.
# Providers are reference implementations, not the only path — a conformant
# tool just needs to declare its mapping (assignee, status, priority, dependsOn,
# meetingIngestion) and the agent drives it.
#
# Functions: ab_task_provider, ab_task_domain, ab_task_mapping, ab_task_contract
#
# shellcheck disable=SC2155

AB_HOME="${AB_HOME:-$HOME/.agent-team-os}"
AB_MAP="${AB_MAP:-$AB_HOME/AGENT_MAP.json}"

# ab_task_domain <agent> → "side" | "brandart" | "global" | "lifeos" | "" (unknown)
# Domain is read from AGENT_MAP agents[].domain, with a sensible fallback by god.
ab_task_domain() {
  local agent="$1"
  [[ -z "$agent" ]] && return 0
  local dom
  dom=$(jq -r --arg a "$agent" '.agents[$a].domain // ""' "$AB_MAP" 2>/dev/null)
  echo "$dom"
}

# ab_task_provider <agent> → "orbit" | "" (none)
# Resolves the task provider for an agent's domain.
#   side    → orbit
#   brandart→ (none) PMOHub dismesso il 2026-10-01: Leo lavora con agenda.md + PM Status
ab_task_provider() {
  local agent="$1"
  [[ -z "$agent" ]] && return 0
  # explicit override on the agent wins
  local explicit
  explicit=$(jq -r --arg a "$agent" '.agents[$a].task_provider // ""' "$AB_MAP" 2>/dev/null)
  if [[ -n "$explicit" ]]; then echo "$explicit"; return 0; fi
  case "$(ab_task_domain "$agent")" in
    side)     echo "orbit" ;;
    brandart) echo "" ;;   # PMOHub dismesso 2026-10-01: nessun provider
    *)        echo "" ;;
  esac
}

# ab_task_mapping <provider> → JSON mapping (status/assignee/priority/dependsOn/meetingIngestion)
# The DECLARED contract mapping per §6. Adapters implement this 1:1.
ab_task_mapping() {
  local provider="$1"
  case "$provider" in
    orbit)
      cat <<'JSON'
{
  "provider": "orbit",
  "mcp_prefix": "mcp__orbit__",
  "status":   { "todo": "todo", "doing": "doing", "done": "done" },
  "assignee": { "kai": "kai", "vera": "vera", "nico": "nico", "alita": "alita", "leo": "leo" },
  "priority": { "p0": "high", "p1": "high", "p2": "normal", "p3": "low" },
  "dependsOn": "native",
  "meetingIngestion": "emulated",
  "notes": "task-level deps (task_dependencies, v18). blocked derived = has blocker task not done."
}
JSON
      ;;
    pmohub)
      # DEPRECATED 2026-10-01: PMOHub dismesso. Mapping tenuto solo come riferimento storico,
      # nessun agente lo risolve piu' (ab_task_provider non restituisce mai "pmohub").
      cat <<'JSON'
{
  "provider": "pmohub",
  "mcp_prefix": "mcp__pmohub__",
  "status":   { "todo": "todo", "doing": "in_progress", "done": "done" },
  "status_reverse_excludes": ["cancelled"],
  "assignee": { "kai": "kai", "vera": "vera", "nico": "nico", "alita": "alita", "leo": "leo" },
  "priority": { "p0": "P0", "p1": "P1", "p2": "P2", "p3": "P2" },
  "dependsOn": "native",
  "meetingIngestion": "native",
  "notes": "owner = AgentId in agentic write, free-text for human tasks. meeting_id native. cancelled excluded from listTasks."
}
JSON
      ;;
    *)
      echo '{}' ;;
  esac
}

# ab_task_contract <agent> → human-readable routing line for the GOD prompt.
# Used by the GOD persona to know which provider to drive and how.
ab_task_contract() {
  local agent="$1"
  local dom prov
  dom=$(ab_task_domain "$agent")
  prov=$(ab_task_provider "$agent")
  if [[ -z "$prov" ]]; then
    echo "No task provider for agent '$agent' (domain '${dom:-unknown}'). Tasks tracked via bus messages only."
    return 0
  fi
  echo "TaskProvider for '$agent': domain=$dom → provider=$prov (MCP $(ab_task_mapping "$prov" | jq -r .mcp_prefix 2>/dev/null)*). Canonical status todo/doing/done; blocked = DERIVED (dependsOn not all done). assignee = AgentId. See PLAN-AGENT-TEAM-OS.md §6 for full mapping."
}
