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

from .hookops import HookOps
from .ombra import Ombra
from .ops import Centralino
from .pulizia import Pulizia
from .sveglia import Sveglia

MAX_REQ = 1024 * 1024
IMPORT_S = 5
SVEGLIA_S = 10
CONFRONTO_S = 600
RACCOLTA_S = 60
PULIZIA_S = 6 * 3600


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


def _ciclo_sveglia(stop: threading.Event, ora: threading.Event, fn, log) -> None:
    """Un giro ogni 10 s, o subito quando un invio lo chiede (sveglia_ora)."""
    while not stop.is_set():
        ora.wait(SVEGLIA_S)
        ora.clear()
        if stop.is_set():
            return
        try:
            fn()
        except Exception as exc:  # noqa: BLE001
            log("error", "giro_sveglia", error=repr(exc))


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
        os.umask(0o077)  # database, log e socket contengono i messaggi: solo l'utente
        for f in self.home.glob("centralino.db*"):
            os.chmod(f, 0o600)
        if self.sock_path.exists():
            if _socket_in_uso(self.sock_path):
                raise SystemExit(f"un centralino risponde già su {self.sock_path}")
            self.sock_path.unlink()
        self.central = Centralino(self.home, log_path=self.home / "logs" / "centralino.jsonl")
        self.ombra = Ombra(self.central)
        self.ombra.registra()
        HookOps(self.central, self.ombra).registra()
        modo = self.central.modo
        self.central.log("info", "avvio", home=str(self.home), socket=str(self.sock_path), pid=os.getpid(),
                         modo=modo.as_dict())
        self.ombra.importa(completo=True, solo_nuovi=modo.sqlite)
        self.server = Server(self.sock_path, self.central)
        os.chmod(self.sock_path, 0o600)
        if self.giri:
            log = self.central.log
            if modo.sqlite:
                giri = [(IMPORT_S, lambda: self.ombra.importa(solo_nuovi=True), "rete"),
                        (PULIZIA_S, Pulizia(self.central).giro, "pulizia")]
            else:
                giri = [(IMPORT_S, self.ombra.importa, "import"),
                        (CONFRONTO_S, self.ombra.confronta, "confronto")]
            if modo.attiva:
                sveglia = Sveglia(self.central, self.ombra)
                threading.Thread(target=_ciclo_sveglia, args=(self.stop, self.central.sveglia_ora, sveglia.giro, log),
                                 daemon=True).start()
            else:
                giri.append((SVEGLIA_S, self.ombra.giro_sveglia, "sveglia"))
            if modo.raccolta:
                giri.append((RACCOLTA_S, self.central.raccolta, "raccolta"))
            for every, fn, nome in giri:
                threading.Thread(target=_ciclo, args=(self.stop, every, fn, log, nome), daemon=True).start()
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
