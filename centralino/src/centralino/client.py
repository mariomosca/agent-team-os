"""Client minimo del socket (CLI e test). Il MCP ha la sua copia, senza dipendere da questo pacchetto."""

from __future__ import annotations

import json
import os
import socket
import time
from pathlib import Path


class CentralinoGiu(RuntimeError):
    pass


def default_socket() -> Path:
    return Path(os.environ.get("AB_HOME", str(Path.home() / ".agent-team-os"))) / "centralino.sock"


def call(op: str, args: dict | None = None, caller: dict | None = None, sock: Path | None = None,
         timeout: float = 30) -> dict:
    path = Path(sock or default_socket())
    req = {"v": 1, "id": f"r-{os.getpid()}-{id(args)}", "op": op, "args": args or {}}
    if caller is not None:
        req["caller"] = caller
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        for tentativo in range(5):
            try:
                s.connect(str(path))
                break
            except (ConnectionRefusedError, BlockingIOError):
                # coda d'ascolto piena o daemon che riparte: la richiesta non è partita, riprovare è sicuro
                if tentativo == 4 or not path.exists():
                    raise
                time.sleep(0.05 * (tentativo + 1))
                s.close()
                s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                s.settimeout(timeout)
        s.sendall((json.dumps(req, ensure_ascii=False) + "\n").encode("utf-8"))
        buf = b""
        while not buf.endswith(b"\n"):
            chunk = s.recv(65536)
            if not chunk:
                break
            buf += chunk
    except OSError as exc:
        raise CentralinoGiu(f"il centralino del bus non risponde su {path}: {exc}") from None
    finally:
        s.close()
    if not buf:
        raise CentralinoGiu(f"il centralino del bus ha chiuso la connessione su {path} senza risposta")
    return json.loads(buf)
