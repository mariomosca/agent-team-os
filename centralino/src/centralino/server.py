"""Daemon: socket Unix + giri dell'ombra.

Una richiesta per connessione, JSON su una riga (contratto §3.1). Le connessioni sono
servite in thread separati, ma ogni scrittura passa dalla transazione unica di Store.
"""

from __future__ import annotations

import json
import os
import signal
import socket
import socketserver
import struct
import sys
import threading
import time
from pathlib import Path

from .ombra import Ombra
from .ops import Centralino

MAX_REQ = 1024 * 1024
IMPORT_S = 5
SVEGLIA_S = 10
CONFRONTO_S = 600


def _peer_uid(sock: socket.socket) -> int | None:
    """uid di chi si collega (macOS: LOCAL_PEERCRED → struct xucred). None se non ricavabile."""
    if sys.platform != "darwin":
        return None
    try:
        raw = sock.getsockopt(0, 0x001, 76)  # SOL_LOCAL, LOCAL_PEERCRED, sizeof(xucred)
        _version, uid = struct.unpack_from("<II", raw)
        return uid
    except OSError:
        return None


class _Handler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        srv: "Server" = self.server  # type: ignore[assignment]
        uid = _peer_uid(self.request)
        if uid is not None and uid != os.getuid():
            self._reply({"v": 1, "ok": False, "error": {"code": "bad_request", "message": "utente diverso"}})
            return
        line = self.rfile.readline(MAX_REQ + 1)
        if len(line) > MAX_REQ:
            self._reply({"v": 1, "ok": False, "error": {"code": "bad_request", "message": "richiesta oltre 1 MB"}})
            return
        try:
            req = json.loads(line)
            if not isinstance(req, dict):
                raise ValueError("non è un oggetto")
        except ValueError as exc:
            self._reply({"v": 1, "ok": False, "error": {"code": "bad_request", "message": f"JSON non valido: {exc}"}})
            return
        self._reply(srv.central.handle_request(req))

    def _reply(self, obj: dict) -> None:
        try:
            self.wfile.write((json.dumps(obj, ensure_ascii=False, default=str) + "\n").encode("utf-8"))
        except OSError:
            pass


class Server(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True
    allow_reuse_address = True
    request_queue_size = 128  # il default (5) su macOS rifiuta le connessioni appena arrivano insieme

    def __init__(self, path: Path, central: Centralino):
        self.central = central
        super().__init__(str(path), _Handler)


def _socket_in_uso(path: Path) -> bool:
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(1)
    try:
        s.connect(str(path))
        return True
    except OSError:
        return False
    finally:
        s.close()


def _ciclo(stop: threading.Event, every: float, fn, log, nome: str) -> None:
    while not stop.wait(every):
        try:
            fn()
        except Exception as exc:  # noqa: BLE001 - un giro fallito non ferma il daemon
            log("error", f"giro_{nome}", error=repr(exc))


class Daemon:
    """Socket in ascolto + giri dell'ombra. ``avvia()`` prepara, ``esegui()`` serve fino a ``ferma()``."""

    def __init__(self, home: Path, sock_path: Path | None = None, giri: bool = True):
        self.home = Path(home)
        self.sock_path = Path(sock_path or self.home / "centralino.sock")
        self.giri = giri
        self.stop = threading.Event()

    def avvia(self) -> "Daemon":
        if self.sock_path.exists():
            if _socket_in_uso(self.sock_path):
                raise SystemExit(f"un centralino risponde già su {self.sock_path}")
            self.sock_path.unlink()
        self.central = Centralino(self.home, log_path=self.home / "logs" / "centralino.jsonl")
        self.ombra = Ombra(self.central)
        self.ombra.registra()
        self.central.log("info", "avvio", home=str(self.home), socket=str(self.sock_path), pid=os.getpid())
        self.ombra.importa(completo=True)
        old_umask = os.umask(0o177)
        try:
            self.server = Server(self.sock_path, self.central)
        finally:
            os.umask(old_umask)
        os.chmod(self.sock_path, 0o600)
        if self.giri:
            for every, fn, nome in ((IMPORT_S, self.ombra.importa, "import"),
                                    (SVEGLIA_S, self.ombra.giro_sveglia, "sveglia"),
                                    (CONFRONTO_S, self.ombra.confronta, "confronto")):
                threading.Thread(target=_ciclo, args=(self.stop, every, fn, self.central.log, nome),
                                 daemon=True).start()
        return self

    def esegui(self) -> None:
        try:
            self.server.serve_forever(poll_interval=0.2)
        finally:
            self.stop.set()
            self.server.server_close()
            try:
                self.sock_path.unlink()
            except OSError:
                pass
            self.central.log("info", "fine", pid=os.getpid(), uptime_s=round(time.time() - self.central.started))
            self.central.store.close()

    def ferma(self) -> None:
        self.stop.set()
        threading.Thread(target=self.server.shutdown, daemon=True).start()


def serve(home: Path, sock_path: Path | None = None, giri: bool = True) -> None:
    d = Daemon(home, sock_path, giri).avvia()
    signal.signal(signal.SIGTERM, lambda s, f: d.ferma())
    signal.signal(signal.SIGINT, lambda s, f: d.ferma())
    d.esegui()
