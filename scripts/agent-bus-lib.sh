#!/bin/bash
# agent-bus-lib.sh — shared functions for Agent Bus v1
# Source this from hooks and command scripts:
#   source "$HOME/.claude/scripts/agent-bus-lib.sh"
#
# Functions: ab_detect_agent, ab_iso_now, ab_msg_id, ab_lock, ab_unlock,
#            ab_write_message, ab_list_inbox, ab_count_inbox, ab_resolve_msg_path,
#            ab_mark_read, ab_log_outbox, ab_thread_append, ab_update_registry

AB_HOME="${AB_HOME:-$HOME/.agent-bus}"
AB_MAP="$AB_HOME/AGENT_MAP.json"

# ---------- Time & IDs ----------

ab_iso_now() {
  # ISO-8601 with timezone, BSD date (macOS)
  date -u +"%Y-%m-%dT%H:%M:%SZ"
}

ab_iso_local() {
  date +"%Y-%m-%dT%H:%M:%S%z" | sed 's/\(..\)$/:\1/'
}

ab_msg_id() {
  # msg-<iso-compact>-<rand8>
  local ts rand
  ts=$(date -u +"%Y%m%dT%H%M%SZ")
  rand=$(LC_ALL=C tr -dc 'a-z0-9' </dev/urandom | head -c 8)
  echo "msg-${ts}-${rand}"
}

ab_thread_id() {
  local ts rand
  ts=$(date -u +"%Y%m%d")
  rand=$(LC_ALL=C tr -dc 'a-z0-9' </dev/urandom | head -c 6)
  echo "thread-${ts}-${rand}"
}

# ---------- Agent detection ----------

ab_detect_agent() {
  # Determine agent from cwd via AGENT_MAP.json
  # Prints agent name to stdout, or empty if no match.
  local cwd="${1:-$PWD}"
  if [[ ! -f "$AB_MAP" ]]; then
    return 0
  fi
  if ! command -v jq >/dev/null 2>&1; then
    # jq is required for agent detection. Install via:
    #   brew install jq    (macOS)
    #   apt install jq     (Debian/Ubuntu)
    echo "WARN: jq not found. Install jq to enable agent detection." >&2
    return 0
  fi
  jq -r --arg cwd "$cwd" '
    .rules[] | select(.pattern as $p | $cwd | startswith($p)) | .agent
  ' "$AB_MAP" 2>/dev/null | head -1
}

ab_agent_exists() {
  local agent="$1"
  [[ -n "$agent" ]] && [[ -d "$AB_HOME/inboxes/$agent" ]]
}

ab_agent_display() {
  local agent="$1"
  jq -r --arg a "$agent" '.agents[$a].display_name // $a' "$AB_MAP" 2>/dev/null || echo "$agent"
}

ab_agent_role() {
  local agent="$1"
  jq -r --arg a "$agent" '.agents[$a].role // ""' "$AB_MAP" 2>/dev/null
}

ab_agent_persona_file() {
  # Returns absolute path to persona file if defined and existing, else empty.
  local agent="$1"
  local rel
  rel=$(jq -r --arg a "$agent" '.agents[$a].persona_file // ""' "$AB_MAP" 2>/dev/null)
  [[ -z "$rel" || "$rel" == "null" ]] && return 0
  local abs="$AB_HOME/$rel"
  [[ -f "$abs" ]] && echo "$abs"
}

# ---------- Locks ----------

ab_lock() {
  # Atomic lock via mkdir. Returns 0 on success, 1 on busy.
  local agent="$1"
  local lockdir="$AB_HOME/locks/${agent}.lock"
  local tries=0
  while ! mkdir "$lockdir" 2>/dev/null; do
    tries=$((tries+1))
    if (( tries > 20 )); then
      return 1
    fi
    sleep 0.05
  done
  return 0
}

ab_unlock() {
  local agent="$1"
  rmdir "$AB_HOME/locks/${agent}.lock" 2>/dev/null || true
}

# ---------- Inbox queries ----------

ab_list_inbox() {
  # List pending message files for agent (excluding .read/).
  # Prints absolute paths, one per line.
  local agent="$1"
  local dir="$AB_HOME/inboxes/$agent"
  [[ -d "$dir" ]] || return 0
  find "$dir" -maxdepth 1 -name "msg-*.json" -type f 2>/dev/null | sort
}

ab_count_inbox() {
  local agent="$1"
  ab_list_inbox "$agent" | wc -l | tr -d ' '
}

ab_count_inbox_priority() {
  # Count messages with priority in {urgent, high}
  local agent="$1"
  local count=0
  while IFS= read -r f; do
    [[ -z "$f" ]] && continue
    local prio
    prio=$(jq -r '.priority // "normal"' "$f" 2>/dev/null)
    if [[ "$prio" == "urgent" || "$prio" == "high" ]]; then
      count=$((count+1))
    fi
  done < <(ab_list_inbox "$agent")
  echo "$count"
}

ab_resolve_msg_path() {
  # Given a msg-id (possibly partial), find file in inbox or .read/.
  local agent="$1"
  local msg_id="$2"
  local f
  for base in "$AB_HOME/inboxes/$agent" "$AB_HOME/inboxes/$agent/.read"; do
    [[ -d "$base" ]] || continue
    f=$(find "$base" -maxdepth 1 -name "${msg_id}*" -type f 2>/dev/null | head -1)
    [[ -n "$f" ]] && { echo "$f"; return 0; }
  done
  return 1
}

ab_mark_read() {
  # Move message file from inbox to .read/
  local agent="$1"
  local msg_path="$2"
  local fname
  fname=$(basename "$msg_path")
  mkdir -p "$AB_HOME/inboxes/$agent/.read"
  mv "$msg_path" "$AB_HOME/inboxes/$agent/.read/$fname"
}

# ---------- Write message ----------

ab_write_message() {
  # Write a JSON message to recipient inbox.
  # Args (via env vars for clarity, all required unless noted):
  #   AB_FROM, AB_TO, AB_TYPE, AB_INTENT
  #   AB_PAYLOAD_JSON (JSON object as string, default {})
  #   AB_PRIORITY (default normal)
  #   AB_THREAD_ID (optional, auto-generated if empty)
  #   AB_IN_REPLY_TO (optional)
  #   AB_CONTEXT_REFS_JSON (JSON array, default [])
  #   AB_REQUIRES_RESPONSE (true|false, default false)
  #   AB_RESPONSE_BY (iso, optional)
  # Echoes the new msg-id on success.
  local from="${AB_FROM:?AB_FROM required}"
  local to="${AB_TO:?AB_TO required}"
  local type="${AB_TYPE:?AB_TYPE required}"
  local intent="${AB_INTENT:?AB_INTENT required}"
  local payload="${AB_PAYLOAD_JSON:-{\}}"
  local priority="${AB_PRIORITY:-normal}"
  local thread_id="${AB_THREAD_ID:-$(ab_thread_id)}"
  local in_reply_to="${AB_IN_REPLY_TO:-}"
  local refs="${AB_CONTEXT_REFS_JSON:-[]}"
  local req_resp="${AB_REQUIRES_RESPONSE:-false}"
  local resp_by="${AB_RESPONSE_BY:-}"

  if ! ab_agent_exists "$to"; then
    echo "ERROR: unknown agent '$to'" >&2
    return 1
  fi

  # Routing rules: check deny list in AGENT_MAP
  local denied
  denied=$(jq -r --arg from "$from" --arg to "$to" '
    .routing_rules[$from].deny // [] | index($to) // "ok"
  ' "$AB_MAP" 2>/dev/null)
  if [[ "$denied" != "ok" && -n "$denied" && "$denied" != "null" ]]; then
    local reason
    reason=$(jq -r --arg from "$from" '.routing_rules[$from].reason // "isolation rule"' "$AB_MAP")
    echo "ERROR: routing denied $from→$to ($reason). Route via a hub agent if configured." >&2
    return 2
  fi

  local msg_id
  msg_id=$(ab_msg_id)
  local ts
  ts=$(ab_iso_now)

  ab_lock "$to" || { echo "ERROR: could not lock $to inbox" >&2; return 3; }

  local target="$AB_HOME/inboxes/$to/${msg_id}.json"
  local tmp="${target}.tmp"

  # Build JSON via jq for safety
  jq -n \
    --arg id "$msg_id" \
    --arg from "$from" \
    --arg to "$to" \
    --arg thread "$thread_id" \
    --arg in_reply "$in_reply_to" \
    --arg type "$type" \
    --arg intent "$intent" \
    --arg prio "$priority" \
    --argjson payload "$payload" \
    --argjson refs "$refs" \
    --argjson req "$req_resp" \
    --arg resp_by "$resp_by" \
    --arg ts "$ts" \
    '{
      id: $id,
      version: "1.0",
      from: $from,
      to: $to,
      thread_id: $thread,
      in_reply_to: (if $in_reply == "" then null else $in_reply end),
      type: $type,
      intent: $intent,
      priority: $prio,
      payload: $payload,
      context_refs: $refs,
      requires_response: $req,
      response_by: (if $resp_by == "" then null else $resp_by end),
      ts: $ts
    }' > "$tmp"

  if ! jq empty "$tmp" 2>/dev/null; then
    rm -f "$tmp"
    ab_unlock "$to"
    echo "ERROR: built invalid JSON" >&2
    return 4
  fi

  mv "$tmp" "$target"
  ab_unlock "$to"

  # Audit log
  ab_log_outbox "$target"
  ab_thread_append "$thread_id" "$msg_id" "$from" "$to" "$type" "$intent"

  echo "$msg_id"
}

# ---------- Outbox audit ----------

ab_log_outbox() {
  local msg_path="$1"
  local day
  day=$(date -u +"%Y-%m-%d")
  local logfile="$AB_HOME/outbox/${day}.jsonl"
  # Compact JSON one-liner
  jq -c '.' "$msg_path" >> "$logfile"
}

# ---------- Thread management ----------

ab_thread_append() {
  local thread_id="$1"
  local msg_id="$2"
  local from="$3"
  local to="$4"
  local type="$5"
  local intent="$6"
  local f="$AB_HOME/threads/${thread_id}.json"
  local ts
  ts=$(ab_iso_now)

  if [[ ! -f "$f" ]]; then
    jq -n \
      --arg id "$thread_id" \
      --arg from "$from" \
      --arg to "$to" \
      --arg intent "$intent" \
      --arg ts "$ts" \
      '{
        id: $id,
        subject: $intent,
        participants: [$from, $to] | unique,
        messages: [],
        status: "open",
        opened: $ts,
        closed: null
      }' > "$f"
  fi
  # Append msg + update participants
  local tmp="${f}.tmp"
  jq --arg mid "$msg_id" \
     --arg from "$from" \
     --arg to "$to" \
     --arg type "$type" \
     --arg ts "$ts" \
     '
     .messages += [{id: $mid, from: $from, to: $to, type: $type, ts: $ts}]
     | .participants = (.participants + [$from, $to] | unique)
     | (if $type == "confirm" or $type == "response" then .status = "completed" | .closed = $ts else . end)
     ' "$f" > "$tmp" && mv "$tmp" "$f"
}

# ---------- Registry ----------

ab_update_registry() {
  # Update active state + last_seen for given agent.
  # Args: agent, active(true|false), workspace_path
  local agent="$1"
  local active="${2:-true}"
  local workspace="${3:-$PWD}"
  local f="$AB_HOME/registry/${agent}.json"
  [[ -f "$f" ]] || return 0
  local ts
  ts=$(ab_iso_now)
  local tmp="${f}.tmp"
  jq --argjson active "$active" \
     --arg ts "$ts" \
     --arg ws "$workspace" \
     '
     .active = $active
     | .last_seen = $ts
     | .workspace_path = $ws
     | (if .session_started == null and $active then .session_started = $ts else . end)
     | (if $active == false then .session_started = null else . end)
     ' "$f" > "$tmp" && mv "$tmp" "$f"
}

# ---------- Pretty print ----------

ab_msg_summary() {
  # Compact one-line summary for inbox listing.
  local msg_path="$1"
  jq -r '
    "  - " + .id + "  [" + .priority + "] " + .type + "/" + .intent +
    " from " + .from +
    (if .payload.summary then "  | " + (.payload.summary | .[0:60]) else "" end) +
    (if .response_by then "  (by " + .response_by + ")" else "" end)
  ' "$msg_path" 2>/dev/null
}
