#!/usr/bin/env python3
"""agent-team-os-onda-prompt.py — PreToolUse su onda_agent_prompt (bus v2.8, BS21-F1.4).

Una delega passata scrivendo nel pane di un'altra sessione (onda_agent_prompt)
non passa dal bus, quindi nessuno registrava l'attesa. Questo hook chiede la
stessa scelta esplicita di bus_send:

  [delega] <testo>         registra l'attesa sulla sessione che scrive (task_id,
                           chi esegue = la sessione del pane di destinazione) e
                           aggiunge in coda la riga su come rispondere col task_id
  [comunicazione] <testo>  nessuna attesa: il marcatore si toglie e basta
  prompt gia' con task_id  passa (bus_expect fatto a mano, prompt_line in coda);
                           con [delega] davanti: niente seconda attesa ne' seconda riga
  prompt corto             passa (risposte a menu, "sì", /exit, ...)
  prompt lungo senza scelta: rifiutato con la spiegazione, come bus_send senza kind

Mai bloccare per un errore suo: qualunque eccezione = il prompt passa com'e'.
Gira con il python3 di sistema (3.9), solo stdlib.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import sys
from pathlib import Path

# Sotto questa lunghezza un prompt e' una risposta, non un incarico.
DECLARE_MIN_CHARS = int(os.environ.get("AB_ONDA_DECLARE_MIN", "200"))
MARK_RE = re.compile(r"^\s*\[(delega|comunicazione)\]\s*", re.I)
TASK_RE = re.compile(r"task_id\s*=\s*['\"]?([A-Za-z0-9][A-Za-z0-9._-]{2,63})")
SLUG_SAFE = re.compile(r"[^a-z0-9._-]")

DENY_REASON = (
    "Dichiara che cosa e' questo prompt, come per bus_send: inizia il testo con [delega] "
    "se vuoi l'esito (il bus registra l'attesa, aggiunge la riga col task_id e ti sveglia "
    "a ogni ritorno) oppure con [comunicazione] se non deve tornare niente. Poi richiama "
    "onda_agent_prompt."
)


def load_wake():
    here = Path(__file__).resolve().parent
    for cand in (here.parent / "scripts" / "agent-team-os-wake.py",
                 Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")
                 / "scripts" / "agent-team-os-wake.py"):
        if cand.is_file():
            spec = importlib.util.spec_from_file_location("ab_wake", cand)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            return mod
    return None


def detect_agent(wake, cwd: str) -> str | None:
    """Agente della cartella, regola piu' lunga (come agent-bus-mcp)."""
    if os.environ.get("AB_AGENT"):
        return os.environ["AB_AGENT"]
    amap = wake.read_json(wake.bus_home() / "AGENT_MAP.json") or {}
    try:
        resolved = str(Path(cwd).resolve())
    except OSError:
        resolved = cwd
    best = None
    for rule in amap.get("rules") or []:
        pat = rule.get("pattern") or ""
        if pat and ((cwd + "/").startswith(pat) or (resolved + "/").startswith(pat)):
            if best is None or len(pat) > best[0]:
                best = (len(pat), rule.get("agent") or "")
    return best[1] if best and best[1] else None


def slug_for(cwd: str) -> str:
    if os.environ.get("AB_SESSION_SLUG"):
        return os.environ["AB_SESSION_SLUG"]
    try:
        base = Path(cwd).resolve().name
    except OSError:
        base = Path(cwd).name
    return SLUG_SAFE.sub("-", (base or "root").lower())


def target_session(wake, terminal_id: str | None) -> str | None:
    """agente/slug della sessione nel pane di destinazione, da sessions.json di Onda."""
    if not terminal_id:
        return None
    doc = wake.read_json(wake.bus_home() / "onda" / "sessions.json") or {}
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
    if not isinstance(tool_input, dict):
        return
    prompt = tool_input.get("prompt")
    if not isinstance(prompt, str):
        return
    m = MARK_RE.match(prompt)
    if not m:
        if len(prompt.strip()) >= DECLARE_MIN_CHARS and not TASK_RE.search(prompt):
            answer("deny", DENY_REASON)
        return
    body = prompt[m.end():]
    if m.group(1).lower() == "comunicazione":
        answer("allow", "comunicazione: nessuna attesa", {**tool_input, "prompt": body})
        return

    wake = load_wake()
    if wake is None:
        return  # bus non installato: il prompt passa com'e', marcatore compreso
    cwd = event.get("cwd") or os.getcwd()
    agent = detect_agent(wake, cwd)
    if not agent:
        answer("deny", f"[delega] da una cartella che non e' di nessun agente ({cwd}): "
                       "nessuna sessione a cui registrare l'attesa. Usa [comunicazione].")
        return
    slug = slug_for(cwd)
    to = target_session(wake, tool_input.get("terminalId"))
    # BR24-F2.8: [delega] su un prompt che ha gia' la sua riga col task_id (bus_expect
    # fatto prima, prompt_line in coda): una seconda attesa e una seconda riga
    # dividevano i ritorni fra due task (24/10: c80xi9 e c6hmvh). Si tiene la sua; se
    # nessuno la aspetta ancora, l'attesa si registra con quel task_id.
    if (tm := TASK_RE.search(body)) is not None:
        tid = tm.group(1)
        if wake.find_wait(tid) is None:
            wake.expect(agent, slug, re.sub(r"\s+", " ", body).strip()[:120], to, task_id=tid,
                        target={"onda_terminal_id": os.environ.get("ONDA_TERMINAL_ID"),
                                "claude_session_id": os.environ.get("CLAUDE_CODE_SESSION_ID")
                                or event.get("session_id"),
                                "tmux_pane": os.environ.get("TMUX_PANE")})
            wake.log(f"onda delega {tid} {agent}/{slug} -> {to or tool_input.get('terminalId')} (task_id del prompt)")
        answer("allow", f"attesa {tid} gia' nel prompt", {**tool_input, "prompt": body})
        return
    desc = re.sub(r"\s+", " ", body).strip()[:120]
    target = {"onda_terminal_id": os.environ.get("ONDA_TERMINAL_ID"),
              "claude_session_id": os.environ.get("CLAUDE_CODE_SESSION_ID") or event.get("session_id"),
              "tmux_pane": os.environ.get("TMUX_PANE")}
    rec = wake.expect(agent, slug, desc, to, target=target)
    tid = rec["task_id"]
    host = wake.host_self()
    amap = wake.read_json(wake.bus_home() / "AGENT_MAP.json") or {}
    addr = f"{agent}@{host}/{slug}" if host and amap.get("hosts") else f"{agent}/{slug}"
    line = (f"Quando hai finito, manda l'esito con bus_send a {addr}, message_type='response', "
            f"task_id='{tid}'. Se ti serve una risposta prima, manda le domande allo stesso "
            f"indirizzo con message_type='question' e lo stesso task_id.")
    wake.log(f"onda delega {tid} {agent}/{slug} -> {to or tool_input.get('terminalId')}")
    answer("allow", f"attesa registrata {tid}", {**tool_input, "prompt": body.rstrip() + "\n\n" + line})


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
