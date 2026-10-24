#!/usr/bin/env python3
"""agent-team-os-centralino.py — hook sottili del bus v3 (BR24-F4.2, CENTRALINO.md §13.2).

    agent-team-os-centralino.py start|prompt|stop|end   (stdin: JSON dell'hook di Claude Code)

Il registro delle sessioni, il banner, gli avvisi e la regola dello Stop stanno nel
centralino: qui si passa solo chi chiama (cartella, pid della sessione, sessione Claude,
pannello di Onda) e si stampa la risposta nel formato che Claude Code si aspetta.
Gli .sh degli hook fanno exec di questo script, quindi il pid del padre è Claude.

Centralino irraggiungibile: nessun blocco, nessun testo nel prompt, uscita 0.
Solo stdlib, gira con /usr/bin/python3 -I.
"""
from __future__ import annotations

import json
import os
import socket
import sys
from pathlib import Path

TIMEOUT_S = {"start": 2.5, "prompt": 3.0, "stop": 4.0, "end": 2.5}  # sotto i timeout di settings.json


def home() -> Path:
    return Path(os.environ.get("AB_HOME") or Path.home() / ".agent-team-os")


def chiama(op: str, caller: dict, timeout: float) -> dict | None:
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect(str(home() / "centralino.sock"))
        req = {"v": 1, "id": f"hook-{os.getpid()}", "op": op, "caller": caller, "args": {}}
        s.sendall((json.dumps(req, ensure_ascii=False) + "\n").encode("utf-8"))
        buf = b""
        while not buf.endswith(b"\n"):
            chunk = s.recv(65536)
            if not chunk:
                break
            buf += chunk
        resp = json.loads(buf) if buf else None
    except (OSError, ValueError):
        return None
    finally:
        s.close()
    return resp.get("result") if isinstance(resp, dict) and resp.get("ok") else None


def main(argv: list[str]) -> int:
    ev = argv[1] if len(argv) > 1 else ""
    if ev not in TIMEOUT_S:
        return 0
    try:
        data = json.load(sys.stdin)
        data = data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        data = {}
    cwd = os.environ.get("CLAUDE_PROJECT_DIR") or data.get("cwd") or os.getcwd()
    caller = {
        "cwd": cwd,
        "agent": os.environ.get("AB_AGENT") or None,
        "slug": os.environ.get("AB_SESSION_SLUG") or None,
        "pid": os.getppid(),
        "claude_session_id": data.get("session_id") or os.environ.get("CLAUDE_CODE_SESSION_ID"),
        "onda_terminal_id": os.environ.get("ONDA_TERMINAL_ID"),
        "tmux_pane": os.environ.get("TMUX_PANE"),
    }
    out = chiama(f"hook.{ev}", caller, TIMEOUT_S[ev])
    if not out:
        return 0
    if ev == "start":
        print(out.get("banner") or "")
        if out.get("persona"):
            print(f"\n=== Persona: {out.get('display')} ===\n{out['persona'].rstrip()}\n=== End Persona ===")
    elif ev == "prompt" and out.get("additionalContext"):
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "UserPromptSubmit",
                                                 "additionalContext": out["additionalContext"]}},
                         ensure_ascii=False))
    elif ev == "stop" and out.get("block"):
        print(json.dumps({"decision": "block", "reason": out.get("reason")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv))
    except Exception:  # un hook non deve mai rompere la sessione
        sys.exit(0)
