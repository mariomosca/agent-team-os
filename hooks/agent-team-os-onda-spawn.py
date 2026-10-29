#!/usr/bin/env python3
"""agent-team-os-onda-spawn.py — hook su onda_agent_spawn, deleghe che si chiudono da sole (DC29-F4).

    agent-team-os-onda-spawn.py pre    PreToolUse: spawn con worktree → rete sugli orfani del repo
                                       (delega.orfani), prima che Onda conti il tetto di worktree
    agent-team-os-onda-spawn.py post   PostToolUse: la risposta dello spawn va al centralino
                                       (delega.spawn), che la lega alla delega del bus

Mai bloccante: non stampa decisioni, timeout brevi, ogni errore si ignora. Solo stdlib, gira con
/usr/bin/python3 -I.
"""
from __future__ import annotations

import json
import os
import socket
import sys
from pathlib import Path

TIMEOUT_S = {"pre": 4.0, "post": 3.0}


def home() -> Path:
    return Path(os.environ.get("AB_HOME") or Path.home() / ".agent-team-os")


def chiama(op: str, caller: dict, args: dict, timeout: float) -> dict | None:
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect(str(home() / "centralino.sock"))
        s.sendall((json.dumps({"v": 1, "id": f"spawn-{os.getpid()}", "op": op, "caller": caller,
                               "args": args}, ensure_ascii=False) + "\n").encode("utf-8"))
        buf = b""
        while not buf.endswith(b"\n"):
            chunk = s.recv(65536)
            if not chunk:
                break
            buf += chunk
        return json.loads(buf) if buf else None
    except (OSError, ValueError):
        return None
    finally:
        s.close()


def risultato(resp) -> dict | None:
    """La risposta di uno strumento MCP come la passa Claude Code: un oggetto, una lista di blocchi
    ``{type: text, text}``, ``{content: [...]}`` o ``structuredContent``. Il primo JSON oggetto vince."""
    if isinstance(resp, dict):
        if isinstance(resp.get("structuredContent"), dict):
            return resp["structuredContent"]
        if "terminalId" in resp or "errorCode" in resp:
            return resp
        if isinstance(resp.get("content"), list):
            return risultato(resp["content"])
        if isinstance(resp.get("text"), str):
            return risultato(resp["text"])
        return None
    if isinstance(resp, list):
        for x in resp:
            out = risultato(x)
            if out is not None:
                return out
        return None
    if isinstance(resp, str):
        try:
            return risultato(json.loads(resp))
        except ValueError:
            return None
    return None


def caller_di(event: dict) -> dict:
    return {"cwd": os.environ.get("CLAUDE_PROJECT_DIR") or event.get("cwd") or os.getcwd(),
            "agent": os.environ.get("AB_AGENT") or None, "slug": os.environ.get("AB_SESSION_SLUG") or None,
            "claude_session_id": event.get("session_id") or os.environ.get("CLAUDE_CODE_SESSION_ID"),
            "onda_terminal_id": os.environ.get("ONDA_TERMINAL_ID"), "heartbeat": False}


def handle(fase: str, event: dict) -> None:
    inp = event.get("tool_input") if isinstance(event.get("tool_input"), dict) else {}
    if fase == "pre":
        wt = inp.get("worktree") if isinstance(inp.get("worktree"), dict) else {}
        if wt.get("repoRoot"):
            chiama("delega.orfani", caller_di(event), {"repo_root": wt["repoRoot"], "attendi_s": 2.5},
                   TIMEOUT_S["pre"])
        return
    res = risultato(event.get("tool_response"))
    if res is None:
        return
    chiama("delega.spawn", caller_di(event), {"result": res, "input": inp}, TIMEOUT_S["post"])


def main(argv: list[str]) -> int:
    fase = argv[1] if len(argv) > 1 else ""
    if fase not in TIMEOUT_S:
        return 0
    try:
        event = json.load(sys.stdin)
        if isinstance(event, dict):
            handle(fase, event)
    except Exception:  # mai fermare uno spawn per un errore dell'hook
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
