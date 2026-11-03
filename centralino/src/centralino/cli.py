"""centralino serve | status | sim | consegna | pendenti | chiama"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .client import CentralinoGiu, call


def _home(a) -> Path:
    return Path(a.home or os.environ.get("AB_HOME") or Path.home() / ".agent-team-os")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="centralino", description="Centralino del bus di Agent Team OS")
    p.add_argument("--home", help="radice del bus (default AB_HOME o ~/.agent-team-os)")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("serve", help="avvia il daemon (in primo piano)")
    s.add_argument("--socket")
    s.add_argument("--senza-giri", action="store_true", help="niente sveglia/raccolta/pulizia periodiche")
    st = sub.add_parser("status", help="ping e contatori del daemon in esecuzione")
    st.add_argument("--timeout", type=float, default=10, help="secondi di attesa del socket (default 10)")
    sub.add_parser("consegna", help="un messaggio da un altro host (JSON {agent, slug, record} su stdin)")
    sub.add_parser("pendenti", help="messaggi in casella di questo host, una riga inboxes/<agente>[/@slug]/<id>.json")
    ch = sub.add_parser("chiama", help="un'operazione del socket per un agente senza MCP (runner headless): "
                        "args JSON da --args o da stdin, risposta JSON su stdout")
    ch.add_argument("op", help="send, reply, close, inbox, read, thread, waits, status, ...")
    ch.add_argument("--agent", default=os.environ.get("AB_AGENT"), help="agente (default AB_AGENT, altrimenti dalla cartella)")
    ch.add_argument("--slug", default=os.environ.get("AB_SESSION_SLUG"),
                    help="sessione (default AB_SESSION_SLUG, altrimenti il nome della cartella)")
    ch.add_argument("--cwd", default=None, help="cartella del chiamante (default la cartella corrente)")
    ch.add_argument("--args", dest="op_args", default=None, help="argomenti JSON (default: stdin se non è un terminale)")
    ch.add_argument("--senza-battito", action="store_true",
                    help="non segnare la sessione viva (come i client di servizio); di default la chiamata vale "
                         "come battito: un runner che legge la casella almeno ogni 60 s resta una sessione viva")
    ch.add_argument("--timeout", type=float, default=30)
    sm = sub.add_parser("sim", help="banco di prova a simulazione (AB_HOME temporaneo)")
    sm.add_argument("-v", "--verbose", action="store_true")
    a = p.parse_args(argv)
    home = _home(a)
    sock = home / "centralino.sock"

    if a.cmd == "serve":
        from .server import serve
        serve(home, Path(a.socket) if a.socket else None, giri=not a.senza_giri)
        return 0
    if a.cmd == "sim":
        from .sim import main as sim_main
        return sim_main(verbose=a.verbose)
    if a.cmd == "pendenti":
        # lettura diretta di SQLite (WAL): la usa l'host che raccoglie, via ssh, anche a daemon fermo
        import sqlite3
        from .indirizzi import AgentMap
        conn = sqlite3.connect(f"file:{home / 'centralino.db'}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        for r in conn.execute("SELECT id, to_agent, to_slug FROM messages WHERE state='pending' AND to_host=? "
                              "ORDER BY ts", (AgentMap(home).host_self() or "",)):
            print(f"inboxes/{r['to_agent']}" + (f"/@{r['to_slug']}" if r["to_slug"] else "") + f"/{r['id']}.json")
        return 0
    if a.cmd == "chiama":
        raw = a.op_args if a.op_args is not None else ("" if sys.stdin.isatty() else sys.stdin.read())
        try:
            op_args = json.loads(raw) if raw.strip() else {}
        except ValueError as exc:
            print(f"argomenti non JSON: {exc}", file=sys.stderr)
            return 2
        if not isinstance(op_args, dict):
            print("gli argomenti devono essere un oggetto JSON", file=sys.stderr)
            return 2
        caller = {"cwd": a.cwd or os.getcwd(), "agent": a.agent or None, "slug": a.slug or None,
                  "pid": os.getppid(), "heartbeat": not a.senza_battito}
        try:
            r = call(a.op, op_args, caller=caller, sock=sock, timeout=a.timeout)
        except CentralinoGiu as exc:
            print(str(exc), file=sys.stderr)
            return 2
        print(json.dumps(r, ensure_ascii=False))
        return 0 if r.get("ok") else 1
    try:
        if a.cmd == "consegna":
            d = json.load(sys.stdin)
            r = call("consegna", {"agent": d.get("agent"), "slug": d.get("slug"), "record": d.get("record"),
                                  "origine": d.get("origine") or "host"}, sock=sock)
            print(json.dumps(r, ensure_ascii=False))
            return 0 if r.get("ok") else 1
        out = {"ping": call("admin.ping", sock=sock, timeout=a.timeout),
               "stats": call("admin.stats", sock=sock, timeout=a.timeout)}
    except CentralinoGiu as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
