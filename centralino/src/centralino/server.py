"""Daemon: socket Unix + giri dell'ombra.

Una richiesta per connessione, JSON su una riga (contratto §3.1). Le connessioni sono
servite in thread separati, ma ogni scrittura passa dalla transazione unica di Store.
"""

from __future__ import annotations

import faulthandler
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


WATCHDOG_S = 30          # ogni quanto il watchdog controlla
WATCHDOG_PING_S = 10     # entro quanto il socket deve rispondere
WATCHDOG_FALLIMENTI = 2  # controlli falliti di fila prima di uscire (launchd riavvia)


def _ciclo_sveglia(stop: threading.Event, ora: threading.Event, fn, log, battiti: dict) -> None:
    """Un giro ogni 10 s, o subito quando un invio lo chiede (sveglia_ora)."""
    battiti["sveglia"] = (time.monotonic(), SVEGLIA_S)
    while not stop.is_set():
        ora.wait(SVEGLIA_S)
        ora.clear()
        if stop.is_set():
            return
        try:
            fn()
        except Exception as exc:  # noqa: BLE001
            log("error", "giro_sveglia", error=repr(exc))
        battiti["sveglia"] = (time.monotonic(), SVEGLIA_S)


def _ciclo(stop: threading.Event, every: float, fn, log, nome: str, battiti: dict) -> None:
    battiti[nome] = (time.monotonic(), every)
    while not stop.wait(every):
        try:
            fn()
        except Exception as exc:  # noqa: BLE001 - un giro fallito non ferma il daemon
            log("error", f"giro_{nome}", error=repr(exc))
        battiti[nome] = (time.monotonic(), every)


def _ping(path: Path, timeout: float) -> bool:
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect(str(path))
        s.sendall(b'{"v": 1, "id": "watchdog", "op": "admin.ping"}\n')
        buf = b""
        while not buf.endswith(b"\n"):
            chunk = s.recv(4096)
            if not chunk:
                return False
            buf += chunk
        return bool(json.loads(buf).get("ok"))
    except (OSError, ValueError):
        return False
    finally:
        s.close()


def _esci_per_watchdog(home: Path, motivo: str) -> None:
    """Scrive il perché e gli stack di tutti i thread nel log degli errori, poi esce: launchd riavvia."""
    f = home / "logs" / "centralino-errori.log"
    f.parent.mkdir(parents=True, exist_ok=True)
    with f.open("a", encoding="utf-8") as fh:
        fh.write(f"\n{time.strftime('%Y-%m-%dT%H:%M:%S%z')} watchdog: {motivo}; esco perché launchd riavvii\n")
        fh.flush()
        faulthandler.dump_traceback(file=fh, all_threads=True)
        fh.flush()
    os._exit(70)


def _watchdog(d: "Daemon", esci) -> None:
    """Il socket deve rispondere e i giri periodici devono avanzare. Due controlli falliti di fila:
    ``esci(motivo)``. Gira in un thread suo, quindi vede anche un main thread bloccato."""
    falliti = 0
    while not d.stop.wait(d.watchdog_s):
        problemi = []
        if not _ping(d.sock_path, d.watchdog_ping_s):
            problemi.append(f"il socket non risponde entro {d.watchdog_ping_s:g} s")
        adesso = time.monotonic()
        for nome, (ultimo, every) in list(d.battiti.items()):
            if adesso - ultimo > 3 * every + 120:
                problemi.append(f"il giro {nome} è fermo da {int(adesso - ultimo)} s")
        if not problemi:
            falliti = 0
            continue
        falliti += 1
        d.central.log("error", "watchdog", problemi=problemi, falliti=falliti)
        if falliti >= WATCHDOG_FALLIMENTI:
            esci("; ".join(problemi))
            return


class Daemon:
    """Socket in ascolto + giri periodici. ``avvia()`` prepara, ``esegui()`` serve fino a ``ferma()``.

    ``ferma()`` imposta solo un flag: si chiama da un gestore di segnale, che non deve mai
    prendere lock né avviare thread (24/10, 21:36: un gestore che aspettava un lock, interrotto
    da un secondo segnale, si è bloccato su sé stesso e il socket non ha più risposto)."""

    def __init__(self, home: Path, sock_path: Path | None = None, giri: bool = True,
                 watchdog: bool = False, watchdog_s: float = WATCHDOG_S, esci=None,
                 watchdog_ping_s: float = WATCHDOG_PING_S):
        self.home = Path(home)
        self.sock_path = Path(sock_path or self.home / "centralino.sock")
        self.giri = giri
        self.watchdog = watchdog
        self.watchdog_s = watchdog_s
        self.watchdog_ping_s = watchdog_ping_s
        self._esci = esci or (lambda motivo: _esci_per_watchdog(self.home, motivo))
        self.stop = threading.Event()
        self.battiti: dict[str, tuple[float, float]] = {}
        self._fine = False

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
        self.server.timeout = 0.5
        os.chmod(self.sock_path, 0o600)
        (self.home / "centralino.pid").write_text(f"{os.getpid()}\n")
        log = self.central.log
        if self.giri:
            if modo.sqlite:
                giri = [(IMPORT_S, lambda: self.ombra.importa(solo_nuovi=True), "rete"),
                        (PULIZIA_S, Pulizia(self.central).giro, "pulizia")]
            else:
                giri = [(IMPORT_S, self.ombra.importa, "import"),
                        (CONFRONTO_S, self.ombra.confronta, "confronto")]
            if modo.attiva:
                sveglia = Sveglia(self.central, self.ombra)
                threading.Thread(target=_ciclo_sveglia, name="giro-sveglia", daemon=True,
                                 args=(self.stop, self.central.sveglia_ora, sveglia.giro, log, self.battiti)).start()
            else:
                giri.append((SVEGLIA_S, self.ombra.giro_sveglia, "sveglia"))
            if modo.raccolta:
                giri.append((RACCOLTA_S, self.central.raccolta, "raccolta"))
            for every, fn, nome in giri:
                threading.Thread(target=_ciclo, name=f"giro-{nome}", daemon=True,
                                 args=(self.stop, every, fn, log, nome, self.battiti)).start()
        if self.watchdog:
            threading.Thread(target=_watchdog, name="watchdog", args=(self, self._esci), daemon=True).start()
        return self

    def esegui(self) -> None:
        try:
            while not self._fine:
                self.server.handle_request()  # torna al più dopo server.timeout
        finally:
            self.stop.set()
            self.server.server_close()
            for f in (self.sock_path, self.home / "centralino.pid"):
                try:
                    f.unlink()
                except OSError:
                    pass
            self.central.log("info", "fine", pid=os.getpid(), uptime_s=round(time.time() - self.central.started))
            self.central.store.close()

    def ferma(self) -> None:
        self._fine = True  # solo un flag: sicuro dentro un gestore di segnale


def serve(home: Path, sock_path: Path | None = None, giri: bool = True) -> None:
    home = Path(home)
    (home / "logs").mkdir(parents=True, exist_ok=True)
    # kill -USR1 <pid>: gli stack Python di tutti i thread nel log degli errori, senza root
    errori = (home / "logs" / "centralino-errori.log").open("a", encoding="utf-8")
    faulthandler.register(signal.SIGUSR1, file=errori, all_threads=True)
    d = Daemon(home, sock_path, giri, watchdog=giri).avvia()
    signal.signal(signal.SIGTERM, lambda s, f: d.ferma())
    signal.signal(signal.SIGINT, lambda s, f: d.ferma())
    d.esegui()
