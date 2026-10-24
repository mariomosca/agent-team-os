"""centralino serve | status | sim"""

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
    try:
        out = {"ping": call("admin.ping", sock=sock, timeout=a.timeout),
               "stats": call("admin.stats", sock=sock, timeout=a.timeout)}
    except CentralinoGiu as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
