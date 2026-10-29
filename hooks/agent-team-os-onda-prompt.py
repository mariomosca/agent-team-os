#!/usr/bin/env python3
"""agent-team-os-onda-prompt.py — PreToolUse su onda_agent_prompt, bus v3 (BR24-F4.2).

Stesse regole della v2.8 (BS21-F1.4, BR24-F2.8), ma l'attesa la registra il centralino:
  [delega] <testo>         attesa sulla sessione che scrive, riga col task_id in coda
  [comunicazione] <testo>  nessuna attesa: il marcatore si toglie e basta
  prompt gia' con task_id  passa; con [delega] davanti l'attesa si registra con quel task_id
  prompt corto             passa
  prompt lungo senza scelta: rifiutato con la spiegazione, come bus_send senza kind
Un [delega] che segue un bus_send(kind=delega) non apre una seconda attesa (t-20261029-lbgswf):
il centralino lo aggancia alla delega aperta se il testo ne cita il task_id o l'intent, o se e'
andata alla stessa sessione negli ultimi 10 minuti; la riga in coda porta il task_id di quella.
Centralino irraggiungibile su una [delega]: il prompt passa senza marcatore e senza attesa,
con il motivo nella risposta. Mai bloccare per un errore suo. Solo stdlib.
"""
from __future__ import annotations

import json
import os
import re
import socket
import sys
from pathlib import Path

DECLARE_MIN_CHARS = int(os.environ.get("AB_ONDA_DECLARE_MIN", "200"))
MARK_RE = re.compile(r"^\s*\[(delega|comunicazione)\]\s*", re.I)
TASK_RE = re.compile(r"task_id\s*=\s*['\"]?([A-Za-z0-9][A-Za-z0-9._-]{2,63})")
DENY_REASON = (
    "Dichiara che cosa e' questo prompt, come per bus_send: inizia il testo con [delega] "
    "se vuoi l'esito (il bus registra l'attesa, aggiunge la riga col task_id e ti sveglia "
    "a ogni ritorno) oppure con [comunicazione] se non deve tornare niente. Poi richiama "
    "onda_agent_prompt."
)


def home() -> Path:
    return Path(os.environ.get("AB_HOME") or Path.home() / ".agent-team-os")


def chiama(op: str, caller: dict, args: dict) -> dict:
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(5)
    try:
        s.connect(str(home() / "centralino.sock"))
        s.sendall((json.dumps({"v": 1, "id": f"onda-{os.getpid()}", "op": op, "caller": caller,
                               "args": args}, ensure_ascii=False) + "\n").encode("utf-8"))
        buf = b""
        while not buf.endswith(b"\n"):
            chunk = s.recv(65536)
            if not chunk:
                break
            buf += chunk
    finally:
        s.close()
    return json.loads(buf)


def target_session(terminal_id: str | None) -> str | None:
    if not terminal_id:
        return None
    try:
        doc = json.loads((home() / "onda" / "sessions.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    for s in doc.get("sessions") or []:
        if isinstance(s, dict) and s.get("terminalId") == terminal_id and s.get("agent"):
            return f"{s['agent']}/{s['slug']}" if s.get("slug") else s["agent"]
    return None


def answer(decision: str, reason: str | None = None, tool_input: dict | None = None) -> None:
    out: dict = {"hookEventName": "PreToolUse", "permissionDecision": decision}
    if reason:
        out["permissionDecisionReason"] = reason
    if tool_input is not None:
        out["updatedInput"] = tool_input
    print(json.dumps({"hookSpecificOutput": out}, ensure_ascii=False))


def handle(event: dict) -> None:
    tool_input = event.get("tool_input")
    if not isinstance(tool_input, dict) or not isinstance(tool_input.get("prompt"), str):
        return
    prompt = tool_input["prompt"]
    m = MARK_RE.match(prompt)
    if not m:
        if len(prompt.strip()) >= DECLARE_MIN_CHARS and not TASK_RE.search(prompt):
            answer("deny", DENY_REASON)
        return
    body = prompt[m.end():]
    if m.group(1).lower() == "comunicazione":
        answer("allow", "comunicazione: nessuna attesa", {**tool_input, "prompt": body})
        return
    caller = {"cwd": os.environ.get("CLAUDE_PROJECT_DIR") or event.get("cwd") or os.getcwd(),
              "agent": os.environ.get("AB_AGENT") or None, "slug": os.environ.get("AB_SESSION_SLUG") or None,
              "claude_session_id": event.get("session_id") or os.environ.get("CLAUDE_CODE_SESSION_ID"),
              "onda_terminal_id": os.environ.get("ONDA_TERMINAL_ID"), "tmux_pane": os.environ.get("TMUX_PANE")}
    to = target_session(tool_input.get("terminalId"))
    desc = re.sub(r"\s+", " ", body).strip()[:120]
    tm = TASK_RE.search(body)
    args = {"description": desc, "delegated_to": to, "onda_prompt": True, "testo": body[:4000]}
    if tm:
        args["task_id"] = tm.group(1)
    try:
        resp = chiama("expect", caller, args)
    except (OSError, ValueError) as exc:
        answer("allow", f"centralino irraggiungibile ({exc}): nessuna attesa registrata",
               {**tool_input, "prompt": body})
        return
    if tm:
        # la riga col task_id c'e' gia': se l'attesa esisteva, va bene lo stesso (conflict)
        answer("allow", f"attesa {tm.group(1)} gia' nel prompt", {**tool_input, "prompt": body})
        return
    if not resp.get("ok"):
        err = resp.get("error") or {}
        if err.get("code") == "no_identity":
            answer("deny", f"[delega] da una cartella che non e' di nessun agente: {err.get('message')} "
                           "Usa [comunicazione].")
        else:
            answer("allow", f"attesa non registrata: {err.get('message')}", {**tool_input, "prompt": body})
        return
    r = resp["result"]
    come = "agganciato alla delega aperta" if r.get("agganciata") else "attesa registrata"
    answer("allow", f"{come} {r['task_id']}",
           {**tool_input, "prompt": body.rstrip() + "\n\n" + r["prompt_line"]})


def main() -> int:
    try:
        event = json.load(sys.stdin)
        if isinstance(event, dict):
            handle(event)
    except Exception:  # mai bloccare un prompt per un errore dell'hook
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
