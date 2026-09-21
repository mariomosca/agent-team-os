#!/bin/bash
# agent-team-os-lib.sh — shared functions for Agent Team OS (bus protocol v1)
# Source this from hooks and command scripts:
#   source "$HOME/.claude/scripts/agent-team-os-lib.sh"
#
# Functions: ab_detect_agent, ab_iso_now, ab_msg_id, ab_lock, ab_unlock,
#            ab_write_message, ab_list_inbox, ab_count_inbox, ab_resolve_msg_path,
#            ab_mark_read, ab_log_outbox, ab_thread_append, ab_update_registry,
#            ab_msg_project_path, ab_msg_workspace_match, ab_count_inbox_here,
#            ab_count_inbox_elsewhere,
#            ab_cursor_path, ab_cursor_get_last, ab_cursor_update,
#            ab_drain_fresh_count, ab_drain_fresh_list, ab_drain_for_stop

AB_HOME="${AB_HOME:-$HOME/.agent-team-os}"
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
  # Normalize symlinks (e.g. macOS /tmp -> /private/tmp) so prefix rules match
  # the canonical path Onda/Claude report. Fall back to raw cwd if realpath fails.
  local rcwd
  rcwd=$(realpath "$cwd" 2>/dev/null || echo "$cwd")
  # Match against both raw and resolved path (rule may be written either way).
  jq -r --arg cwd "$cwd" --arg rcwd "$rcwd" '
    .rules[] | select(.pattern as $p | ($cwd | startswith($p)) or ($rcwd | startswith($p))) | .agent
  ' "$AB_MAP" 2>/dev/null | head -1
}

# ---------- Session identity (v1.4) ----------
# One agent name can have several live sessions (e.g. two Kai: microsoft-mcp and
# noi-calendar). The bus addresses them as `agent` or `agent/slug`, where slug is the
# workspace basename. Messages sent to the bare name stay readable by every session.

ab_slug_for_cwd() {
  # Workspace slug for a cwd: basename, lowercased, non-alphanumerics folded to '-'.
  # Empty cwd or '/' yields "root" so callers always get a usable path segment.
  local cwd="${1:-$PWD}"
  local rcwd
  rcwd=$(realpath "$cwd" 2>/dev/null || echo "$cwd")
  local base
  base=$(basename "$rcwd")
  [[ -z "$base" || "$base" == "/" ]] && base="root"
  printf '%s' "$base" | tr '[:upper:]' '[:lower:]' | sed 's/[^a-z0-9._-]/-/g'
}

ab_parse_addr_agent() {
  # "kai/noi-calendar" -> "kai"   |   "kai" -> "kai"
  printf '%s' "${1%%/*}"
}

ab_parse_addr_slug() {
  # "kai/noi-calendar" -> "noi-calendar"   |   "kai" -> "" (broadcast)
  local addr="$1"
  [[ "$addr" == */* ]] || { printf ''; return 0; }
  printf '%s' "${addr#*/}"
}

ab_session_inbox_dir() {
  # Inbox directory for a specific session. Args: agent, slug
  printf '%s/inboxes/%s/@%s' "$AB_HOME" "$1" "$2"
}

ab_registry_dir() {
  printf '%s/registry/%s.d' "$AB_HOME" "$1"
}

ab_update_session_registry() {
  # Register one live session as its own entry, so two sessions of the same agent
  # no longer overwrite each other. Args: agent, active(true|false), workspace, [slug]
  local agent="$1" active="${2:-true}" workspace="${3:-$PWD}"
  local slug="${4:-$(ab_slug_for_cwd "$workspace")}"
  local dir f ts tmp prev_started
  dir=$(ab_registry_dir "$agent")
  mkdir -p "$dir" || return 0
  f="$dir/${slug}.json"
  ts=$(ab_iso_now)
  tmp="${f}.tmp"

  if [[ "$active" != "true" ]]; then
    rm -f "$f"
    return 0
  fi

  # Keep the original session_started across heartbeats of the same session.
  prev_started=""
  if [[ -s "$f" ]] && jq empty "$f" 2>/dev/null; then
    prev_started=$(jq -r '.session_started // ""' "$f" 2>/dev/null)
  fi
  [[ -z "$prev_started" || "$prev_started" == "null" ]] && prev_started="$ts"

  jq -n --arg name "$agent" --arg slug "$slug" --arg ts "$ts" \
        --arg ws "$workspace" --arg started "$prev_started" --arg pid "${PPID:-0}" \
    '{name:$name, slug:$slug, active:true, last_seen:$ts,
      workspace_path:$ws, session_started:$started, pid:($pid|tonumber)}' > "$tmp" \
    && ab_json_promote "$tmp" "$f"
}

ab_list_sessions() {
  # Live sessions for an agent, one slug per line, most recently seen first.
  # A session is considered dead after AB_SESSION_TTL_MIN minutes without heartbeat.
  local agent="$1"
  local ttl="${AB_SESSION_TTL_MIN:-240}"
  local dir
  dir=$(ab_registry_dir "$agent")
  [[ -d "$dir" ]] || return 0
  local now_epoch
  now_epoch=$(date -u +%s)
  local f
  for f in "$dir"/*.json; do
    [[ -e "$f" ]] || continue
    jq empty "$f" 2>/dev/null || continue
    # Declare with an explicit value: under zsh a bare `local a b c` followed by
    # assignments echoes "name=value" to stdout, which corrupts this function's
    # output when the lib is sourced from a zsh shell instead of a bash hook.
    local slug=""
    local seen=""
    local seen_epoch=0
    slug=$(jq -r '.slug // ""' "$f" 2>/dev/null)
    seen=$(jq -r '.last_seen // ""' "$f" 2>/dev/null)
    [[ -z "$slug" ]] && continue
    # BSD date (macOS) first, GNU as fallback. BSD `date -j -f` prints a partial
    # parse to STDOUT on failure, which would leak into this function's output, so
    # both branches are redirected and the result is validated as digits.
    local ts_clean=""
    ts_clean="${seen%%+*}"; ts_clean="${ts_clean%Z}"
    seen_epoch=$(date -j -f "%Y-%m-%dT%H:%M:%S" "$ts_clean" +%s 2>/dev/null) \
      || seen_epoch=$(date -u -d "$seen" +%s 2>/dev/null) \
      || seen_epoch=0
    [[ "$seen_epoch" =~ ^[0-9]+$ ]] || seen_epoch=0
    if [[ "$seen_epoch" -gt 0 ]] && (( (now_epoch - seen_epoch) / 60 > ttl )); then
      continue
    fi
    printf '%s\t%s\n' "$seen" "$slug"
  done | sort -r | cut -f2
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
  # List pending message files for agent (excluding .read/ and .done/).
  # Prints absolute paths, one per line.
  #
  # Session scoping (v1.4): messages addressed to `agent/slug` land in the
  # per-session dir `inboxes/<agent>/@<slug>/`. A session sees its own dir PLUS the
  # shared root (messages sent to the bare agent name stay visible to every session).
  # Pass a slug as $2, or set AB_SESSION_SLUG, to scope; with neither, every
  # session dir is listed, which keeps old callers and `/inbox --all` working.
  local agent="$1"
  local slug="${2:-${AB_SESSION_SLUG:-}}"
  local dir="$AB_HOME/inboxes/$agent"
  [[ -d "$dir" ]] || return 0
  {
    # Shared root: always visible.
    find "$dir" -maxdepth 1 -name "msg-*.json" -type f 2>/dev/null
    if [[ -n "$slug" ]]; then
      local sdir
      sdir=$(ab_session_inbox_dir "$agent" "$slug")
      [[ -d "$sdir" ]] && find "$sdir" -maxdepth 1 -name "msg-*.json" -type f 2>/dev/null
    else
      # No slug known → show every session dir, so nothing stays invisible.
      find "$dir" -mindepth 2 -maxdepth 2 -name "msg-*.json" -type f \
           -not -path "*/.read/*" -not -path "*/.done/*" 2>/dev/null
    fi
  } | sort
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
  # Given a msg-id (possibly partial), find the file anywhere under the agent's
  # inbox: shared root, per-session dirs (v1.4), and their .read/ archives.
  local agent="$1"
  local msg_id="$2"
  local root="$AB_HOME/inboxes/$agent"
  [[ -d "$root" ]] || return 1
  local f
  # Pending first (root, then session dirs), so a live message wins over an archived one.
  f=$(find "$root" -maxdepth 1 -name "${msg_id}*" -type f 2>/dev/null | head -1)
  [[ -n "$f" ]] && { echo "$f"; return 0; }
  f=$(find "$root" -mindepth 2 -maxdepth 2 -name "${msg_id}*" -type f \
        -not -path "*/.read/*" -not -path "*/.done/*" 2>/dev/null | head -1)
  [[ -n "$f" ]] && { echo "$f"; return 0; }
  f=$(find "$root" -name "${msg_id}*" -type f \
        \( -path "*/.read/*" -o -path "*/.done/*" \) 2>/dev/null | head -1)
  [[ -n "$f" ]] && { echo "$f"; return 0; }
  return 1
}

ab_mark_read() {
  # Move a message to .read/ NEXT TO where it lives, so a session message stays in
  # its own session dir instead of being hoisted into the shared root.
  local agent="$1"
  local msg_path="$2"
  local fname dest
  fname=$(basename "$msg_path")
  dest="$(dirname "$msg_path")/.read"
  mkdir -p "$dest"
  mv "$msg_path" "$dest/$fname"
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

  # v1.4: `to` may be "agent" or "agent/slug". The slug picks one live session;
  # the message JSON keeps the bare agent name so readers and threads are unchanged.
  local to_addr="$to"
  local to_slug
  to_slug=$(ab_parse_addr_slug "$to_addr")
  to=$(ab_parse_addr_agent "$to_addr")

  if ! ab_agent_exists "$to"; then
    echo "ERROR: unknown agent '$to'" >&2
    return 1
  fi

  # Writing to a bare name while the recipient has several sessions open is how a
  # message ends up in the shared queue and blocks whichever session gets there
  # first — usually the wrong one. Name the sessions so the sender can qualify.
  if [[ -z "$to_slug" ]]; then
    local live_n live_list
    live_list=$(ab_list_sessions "$to")
    live_n=$(printf '%s\n' "$live_list" | grep -c . 2>/dev/null)
    if [[ "${live_n:-0}" -gt 1 ]]; then
      echo "NOTE: '$to' has ${live_n} live sessions ($(printf '%s ' $live_list))." >&2
      echo "      Delivered to the shared queue, which all of them see. To reach one:" >&2
      echo "      AB_TO=$to/<slug>" >&2
    fi
  fi

  # A slug that matches no live session is a typo, and silently dropping the message
  # into a directory nobody reads is the failure mode we are fixing. Say so instead.
  if [[ -n "$to_slug" ]]; then
    local live
    live=$(ab_list_sessions "$to")
    if [[ -n "$live" ]] && ! printf '%s\n' "$live" | grep -qx "$to_slug"; then
      echo "WARN: no live session '$to/$to_slug'. Live: $(printf '%s ' $live)" >&2
      echo "      Delivering anyway; it will be read when that session starts." >&2
    fi
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

  local target_dir="$AB_HOME/inboxes/$to"
  if [[ -n "$to_slug" ]]; then
    target_dir=$(ab_session_inbox_dir "$to" "$to_slug")
    # Create the archive dirs alongside the inbox: the recipient archives into
    # .done/ right after reading, and a fresh session dir would otherwise make the
    # very first targeted message land somewhere with nowhere to file it.
    mkdir -p "$target_dir/.read" "$target_dir/.done"
  fi
  local target="$target_dir/${msg_id}.json"
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

  # Cross-domain cc-awareness (full-mesh graph mode): drop a non-blocking
  # notify in the hub inbox so the hub keeps visibility without gatekeeping.
  ab_maybe_cc_hub "$from" "$to" "$msg_id" "$thread_id" "$type" "$intent" "$priority"

  echo "$msg_id"
}

# ---------- Cross-domain cc-awareness ----------

ab_agent_domain() {
  # Echo the domain of an agent (falls back to the agent name itself if unset,
  # so domain-less agents like lifeos are treated as their own isolated domain).
  local agent="$1"
  jq -r --arg a "$agent" '.agents[$a].domain // $a' "$AB_MAP" 2>/dev/null
}

ab_maybe_cc_hub() {
  # Non-blocking: if cc_awareness is enabled and this message crosses domains
  # without involving the hub, write a lightweight cc-notify to the hub inbox.
  local from="$1" to="$2" orig_msg="$3" thread="$4" type="$5" intent="$6" prio="$7"

  local enabled hub
  enabled=$(jq -r '.cc_awareness.enabled // false' "$AB_MAP" 2>/dev/null)
  [[ "$enabled" != "true" ]] && return 0
  hub=$(jq -r '.cc_awareness.hub // "alita"' "$AB_MAP" 2>/dev/null)
  [[ -z "$hub" || "$hub" == "null" ]] && return 0

  # Skip if the hub is already a party to the message.
  [[ "$from" == "$hub" || "$to" == "$hub" ]] && return 0

  local dom_from dom_to
  dom_from=$(ab_agent_domain "$from")
  dom_to=$(ab_agent_domain "$to")
  # Only cc on cross-domain traffic.
  [[ "$dom_from" == "$dom_to" ]] && return 0

  # Build the cc-notify payload (references the original message, no response needed).
  local cc_id cc_ts cc_target cc_tmp
  cc_id=$(ab_msg_id)
  cc_ts=$(ab_iso_now)
  ab_lock "$hub" || return 0   # best-effort: never fail the original send
  cc_target="$AB_HOME/inboxes/$hub/${cc_id}.json"
  cc_tmp="${cc_target}.tmp"

  jq -n \
    --arg id "$cc_id" \
    --arg from "$from" \
    --arg hub "$hub" \
    --arg thread "$thread" \
    --arg orig "$orig_msg" \
    --arg dom_from "$dom_from" \
    --arg dom_to "$dom_to" \
    --arg to "$to" \
    --arg otype "$type" \
    --arg ointent "$intent" \
    --arg oprio "$prio" \
    --arg ts "$cc_ts" \
    '{
      id: $id,
      version: "1.0",
      from: $from,
      to: $hub,
      thread_id: $thread,
      in_reply_to: null,
      type: "cc-notify",
      intent: "cross-domain-awareness",
      priority: "low",
      payload: {
        summary: ("Cross-domain " + $dom_from + "→" + $dom_to + ": " + $from + "→" + $to + " (" + $otype + "/" + $ointent + ", prio " + $oprio + ")"),
        original_msg_id: $orig,
        original_from: $from,
        original_to: $to,
        original_type: $otype,
        original_intent: $ointent,
        original_priority: $oprio,
        note: "Auto-cc for hub awareness (full-mesh mode). Not actionable; original parties handle it."
      },
      context_refs: [],
      requires_response: false,
      response_by: null,
      ts: $ts
    }' > "$cc_tmp" 2>/dev/null

  if jq empty "$cc_tmp" 2>/dev/null; then
    mv "$cc_tmp" "$cc_target"
    ab_log_outbox "$cc_target"
  else
    rm -f "$cc_tmp"
  fi
  ab_unlock "$hub"
  return 0
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
     ' "$f" > "$tmp"
  ab_json_promote "$tmp" "$f"
}

# ---------- Safe JSON write ----------

ab_json_promote() {
  # Promote a candidate temp file over its target ONLY if it is non-empty valid JSON.
  # Guards the `jq ... > "$tmp" && mv "$tmp" "$f"` idiom: jq exits 0 on empty input and
  # produces no output, so a plain mv silently promotes emptiness. Once a state file is
  # empty every later run re-confirms it empty (absorbing state) - the failure mode that
  # left 4 of 6 registry files at 0 bytes.
  # Args: tmp, target.  Returns 0 on promotion, 1 on rejection (target left untouched).
  local tmp="$1" target="$2"
  if [[ -s "$tmp" ]] && jq empty "$tmp" 2>/dev/null; then
    mv "$tmp" "$target"
    return 0
  fi
  rm -f "$tmp"
  echo "WARN: refusing to write invalid/empty JSON to $target" >&2
  return 1
}

# ---------- Registry ----------

ab_update_registry() {
  # Update active state + last_seen for given agent.
  # Args: agent, active(true|false), workspace_path
  local agent="$1"
  local active="${2:-true}"
  local workspace="${3:-$PWD}"
  # v1.4: each live session gets its own registry entry. The flat file below is kept
  # in sync for older readers (Onda plugin, /bus), but it can only describe one
  # session — ab_list_sessions is the accurate source.
  ab_update_session_registry "$agent" "$active" "$workspace"

  local f="$AB_HOME/registry/${agent}.json"
  [[ -f "$f" ]] || return 0
  local ts
  ts=$(ab_iso_now)
  local tmp="${f}.tmp"

  # Input guard: an existing-but-empty or corrupt file must be rebuilt, not fed to jq.
  # `[[ -f ]]` above is satisfied by a 0-byte file, and jq on empty input exits 0 with no
  # output, so without this the entry can never recover once emptied.
  if ! [[ -s "$f" ]] || ! jq empty "$f" 2>/dev/null; then
    jq -n --arg name "$agent" \
          --argjson active "$active" \
          --arg ts "$ts" \
          --arg ws "$workspace" \
      '{
        name: $name,
        active: $active,
        last_seen: (if $active then $ts else null end),
        workspace_path: (if $active then $ws else null end),
        session_started: (if $active then $ts else null end)
      }' > "$tmp"
    ab_json_promote "$tmp" "$f"
    return $?
  fi

  jq --argjson active "$active" \
     --arg ts "$ts" \
     --arg ws "$workspace" \
     '
     .active = $active
     | .last_seen = $ts
     | .workspace_path = $ws
     | (if .session_started == null and $active then .session_started = $ts else . end)
     | (if $active == false then .session_started = null else . end)
     ' "$f" > "$tmp"
  ab_json_promote "$tmp" "$f"
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

# ---------- Workspace filtering (v1.2) ----------

ab_msg_project_path() {
  # Extract context.project_path from a message file. Echoes empty if absent.
  local msg_path="$1"
  [[ -f "$msg_path" ]] || return 0
  local p
  p=$(jq -r '.context.project_path // ""' "$msg_path" 2>/dev/null)
  [[ "$p" == "null" ]] && p=""
  echo "$p"
}

ab_msg_workspace_match() {
  # Decide if a message is "in scope" for the current workspace.
  # Echoes one of: "here" | "elsewhere" | "global"
  local msg_path="$1"
  local cwd="${2:-$PWD}"
  local mp
  mp=$(ab_msg_project_path "$msg_path")
  if [[ -z "$mp" ]]; then
    echo "global"
    return 0
  fi
  cwd="${cwd%/}"
  mp="${mp%/}"
  if [[ "$cwd" == "$mp" ]]; then
    echo "here"
    return 0
  fi
  if [[ "$cwd" == "$mp"/* ]]; then
    echo "here"
    return 0
  fi
  if [[ "$mp" == "$cwd"/* ]]; then
    echo "here"
    return 0
  fi
  echo "elsewhere"
}

ab_count_inbox_here() {
  # Count inbox messages that match current workspace (here + global).
  local agent="$1"
  local cwd="${2:-$PWD}"
  local count=0
  while IFS= read -r f; do
    [[ -z "$f" ]] && continue
    local m
    m=$(ab_msg_workspace_match "$f" "$cwd")
    if [[ "$m" == "here" || "$m" == "global" ]]; then
      count=$((count+1))
    fi
  done < <(ab_list_inbox "$agent")
  echo "$count"
}

ab_count_inbox_elsewhere() {
  # Count inbox messages NOT in current workspace scope.
  local agent="$1"
  local cwd="${2:-$PWD}"
  local count=0
  while IFS= read -r f; do
    [[ -z "$f" ]] && continue
    local m
    m=$(ab_msg_workspace_match "$f" "$cwd")
    if [[ "$m" == "elsewhere" ]]; then
      count=$((count+1))
    fi
  done < <(ab_list_inbox "$agent")
  echo "$count"
}

# ---------- Drain-on-Stop (v2.0 §4) ----------

ab_cursor_path() {
  # Return path to cursor.json for the current session of this agent.
  # v1.4: the loop guard is per session — two sessions of the same agent must not
  # share a block counter, or one relents because the other already blocked.
  local agent="$1"
  local slug="${2:-${AB_SESSION_SLUG:-}}"
  if [[ -n "$slug" ]]; then
    echo "$AB_HOME/agents/${agent}/cursor@${slug}.json"
  else
    echo "$AB_HOME/agents/${agent}/cursor.json"
  fi
}

ab_cursor_get_last() {
  # Return lastProcessed msg-id from cursor.json (empty string if cursor absent).
  local agent="$1"
  local f
  f=$(ab_cursor_path "$agent")
  [[ -f "$f" ]] || { echo ""; return 0; }
  jq -r '.lastProcessed // ""' "$f" 2>/dev/null
}

ab_cursor_update() {
  # Write cursor: lastProcessed + drainBlockCount.
  # Args: agent, lastProcessed, drainBlockCount
  local agent="$1"
  local last="$2"
  local count="${3:-0}"
  local f
  f=$(ab_cursor_path "$agent")
  mkdir -p "$(dirname "$f")"
  printf '{"lastProcessed":"%s","drainBlockCount":%d}\n' "$last" "$count" > "$f"
}

ab_drain_fresh_count() {
  # Count inbox messages with id > lastProcessed (string sort; ISO timestamp IDs are sortable).
  # Args: agent, lastProcessed (empty string = all messages are fresh)
  local agent="$1"
  local last="${2:-}"
  local count=0
  while IFS= read -r f; do
    [[ -z "$f" ]] && continue
    local mid
    mid=$(basename "$f" .json)
    if [[ -z "$last" || "$mid" > "$last" ]]; then
      count=$((count+1))
    fi
  done < <(ab_list_inbox "$agent")
  echo "$count"
}

ab_drain_fresh_list() {
  # Return up to 5 fresh message summaries (id | from | intent) for block reason.
  # Args: agent, lastProcessed
  local agent="$1"
  local last="${2:-}"
  local n=0
  while IFS= read -r f; do
    [[ -z "$f" ]] && continue
    local mid
    mid=$(basename "$f" .json)
    if [[ -z "$last" || "$mid" > "$last" ]]; then
      local from intent
      from=$(jq -r '.from // "?"' "$f" 2>/dev/null)
      intent=$(jq -r '.intent // .type // "?"' "$f" 2>/dev/null)
      echo "  ${mid}  from=${from}  intent=${intent}"
      n=$((n+1))
      [[ "$n" -ge 5 ]] && break
    fi
  done < <(ab_list_inbox "$agent" | sort)
}

ab_drain_for_stop() {
  # Main drain logic for Stop hook.
  # Exits 0 (allow stop) or prints block JSON to stdout and exits 1 (block stop).
  # Args: agent, blockCap (default 3)
  #
  # "Unhandled" = messages physically present in inbox/ (NOT yet moved to .done/
  # by the agent). We block until the inbox is actually drained, so the cursor
  # does NOT advance on a mere block — the agent must move handled messages to
  # .done/. drainBlockCount is the loop guard: if the agent keeps stopping
  # without handling the inbox, after `cap` consecutive blocks we relent and
  # allow stop, to avoid trapping it forever.
  local agent="$1"
  local cap="${2:-3}"
  local slug="${AB_SESSION_SLUG:-}"

  # Count messages still in inbox (ab_list_inbox excludes .done/).
  # Scoped to this session: a message addressed to a SIBLING session must not
  # hold this one hostage. Shared-root messages still count for everyone —
  # they were sent to the agent, so whoever is around should deal with them.
  local pending
  pending=$(ab_list_inbox "$agent" "$slug" | grep -c . 2>/dev/null)
  [[ -z "$pending" ]] && pending=0

  # Inbox empty → nothing to drain. Reset loop guard and allow stop.
  if [[ "$pending" -eq 0 ]]; then
    local cursor_file
    cursor_file=$(ab_cursor_path "$agent")
    [[ -f "$cursor_file" ]] && ab_cursor_update "$agent" "" 0
    exit 0
  fi

  # Read loop-guard counter
  local cursor_file block_count
  cursor_file=$(ab_cursor_path "$agent")
  block_count=0
  if [[ -f "$cursor_file" ]]; then
    block_count=$(jq -r '.drainBlockCount // 0' "$cursor_file" 2>/dev/null)
    [[ "$block_count" == "null" || -z "$block_count" ]] && block_count=0
  fi

  # Loop guard: blocked cap times already and inbox still not drained → relent.
  if [[ "$block_count" -ge "$cap" ]]; then
    ab_cursor_update "$agent" "" 0
    exit 0
  fi

  block_count=$((block_count + 1))
  ab_cursor_update "$agent" "" "$block_count"

  # Build block reason from pending messages (up to 5)
  local msg_lines
  msg_lines=$(AB_SESSION_SLUG="$slug" ab_drain_fresh_list "$agent" "")
  local reason
  reason="${pending} unread message(s) in inbox (block ${block_count}/${cap}). Open the files in inbox/, act on each, then move handled ones to inbox/.done/ before stopping:
${msg_lines}"

  # Output Claude Code Stop hook decision JSON
  printf '{"decision":"block","reason":"%s"}\n' \
    "$(echo "$reason" | sed 's/"/\\"/g; s/$/\\n/g' | tr -d '\n' | sed 's/\\n$//')"

  return 1
}

# --- v2.0: load TaskProvider module if present (same dir as this lib) ---
_ATO_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ -f "$_ATO_LIB_DIR/agent-team-os-taskprovider.sh" ]]; then
  # shellcheck source=/dev/null
  source "$_ATO_LIB_DIR/agent-team-os-taskprovider.sh"
fi
