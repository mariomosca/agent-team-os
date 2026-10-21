---
description: Delegate a task to another agent and wait for its outcome. Args - "<to> <intent>". Same as /send with kind=delega; works between any two agents.
---

Delegate a task to `$1` with intent `$2`, waiting for the outcome. `$1` is a bus address:
`kai`, `kai/slug`, or on another machine `kai@mini/slug` (see `/send`). No roles: any agent
can delegate to any other (Nico→Kai, Kai→Vera, Leo→Alita, ...); routing rules still apply.

What a delegation does (bus v2.8, BS21):
- the wait is registered on THIS session when the message is sent, with a `task_id` that
  travels on the message (`attesa registrata t-...`);
- whatever the recipient sends back on it — the result, a question, an update, even a new
  message without `in_reply_to` — wakes this session when it is idle; while it works, the
  returns queue up and arrive together at the next wake;
- each return is consumed explicitly with `bus_gestisci` (chiuso, riarma, risposto, letto).

Steps:

1. **Detect sender** and **check routing** as in `/send` steps 2-3.

2. **Compose the payload** as in `/send` step 4: what to do, where (`project_path`), the
   acceptance criteria, `context_refs`. Ask for a deadline (`response_by`, optional): past
   it with no outcome the wait expires with a notice in your inbox.

3. **Preview**, then send only after explicit confirmation:

   ```
   Delegation:
     <FROM> → <TO>   intent: <INTENT>   priority: <PRIO>
     summary: ...
     deadline: ...
   Confirm? (y/n)
   ```

4. **Send** — MCP first, the helper as fallback:

   - MCP: `bus_send(to="<to>", intent="<intent>", summary="...", message_type="request",
     kind="delega", response_by="<iso>")` → the result has `task_id` and `note`.
   - Helper:

     ```bash
     LIB="${CLAUDE_PLUGIN_ROOT:-${CLAUDE_CONFIG_DIR:-$HOME/.claude}}/scripts/agent-team-os-lib.sh"
     [[ -f "$LIB" ]] || LIB="${CLAUDE_CONFIG_DIR:-$HOME/.claude}/scripts/agent-team-os-lib.sh"
     source "$LIB"
     AB_FROM="$(ab_detect_agent "$PWD")" AB_TO="<to>" AB_TYPE=request AB_KIND=delega \
     AB_INTENT="<intent>" AB_PAYLOAD_JSON='{...}' AB_RESPONSE_BY="<iso>" \
     MSG_ID=$(ab_write_message)   # stderr: attesa registrata t-...
     ```

5. **Confirm** with msg-id and task_id. Tell the user the session can now go idle: the
   outcome wakes it.

Delegating through an Onda pane instead of the bus (`onda_agent_prompt`): call `bus_expect`
and append its `prompt_line` to the prompt, or start the prompt with `[delega]` and the
bus hook does it for you.
