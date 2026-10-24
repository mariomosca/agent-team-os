"""Ciclo di vita del daemon: segnali e watchdog (regressione del blocco del 24/10 alle 21:36)."""

import json
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

from centralino.client import call
from centralino.server import Daemon

BIN = Path(sys.executable).parent / "centralino"


def _home(n_msg: int = 0) -> Path:
    home = Path(tempfile.mkdtemp(prefix="cv-", dir="/tmp"))
    (home / "AGENT_MAP.json").write_text(json.dumps({
        "rules": [{"pattern": str(home / "ws" / "kai"), "agent": "kai"}],
        "agents": {"kai": {}}, "hosts": {"laptop": {"hub": True}}}))
    (home / "ws" / "kai" / "s1").mkdir(parents=True)
    box = home / "inboxes" / "kai" / "@s1"
    box.mkdir(parents=True)
    for i in range(n_msg):  # un po' di lavoro per import e confronto
        (box / f"msg-20261024T190000Z-{i:08d}.json").write_text(json.dumps(
            {"id": f"msg-20261024T190000Z-{i:08d}", "from": "kai", "to": "kai", "type": "event", "intent": "x",
             "thread_id": "t", "ts": "2026-10-24T19:00:00Z", "payload": {"summary": "x"}}))
    return home


def test_il_gestore_di_segnale_non_prende_lock():
    """ferma() gira dentro un gestore di segnale: niente lock, niente thread."""
    home = _home()
    d = Daemon(home, giri=False).avvia()
    chiamate = []

    def prof(frame, evento, arg):
        if evento == "c_call":
            chiamate.append(getattr(arg, "__qualname__", str(arg)))
        elif evento == "call":
            chiamate.append(f"{frame.f_code.co_filename}:{frame.f_code.co_name}")
    sys.setprofile(prof)
    try:
        d.ferma()
    finally:
        sys.setprofile(None)
    chiamate = [c for c in chiamate if "setprofile" not in c]
    sospette = [c for c in chiamate if "acquire" in c or "threading" in c or "start" in c or "set" in c.split(".")[-1]]
    assert not sospette, sospette
    d.esegui()  # esce subito: il flag è già alzato
    assert not d.sock_path.exists()


def test_segnali_a_raffica_sotto_carico():
    """Daemon vero con i giri attivi, richieste in corso e SIGTERM a raffica: esce sempre, entro 5 s."""
    for prova in range(5):
        home = _home(n_msg=300)
        p = subprocess.Popen([str(BIN), "--home", str(home), "serve"], env={**os.environ, "AB_HOME": str(home)},
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        sock = home / "centralino.sock"
        for _ in range(100):
            if sock.exists():
                break
            time.sleep(0.05)
        fermo = threading.Event()

        def martella():
            while not fermo.is_set():
                try:
                    call("vista", sock=sock, timeout=5)
                    call("status", caller={"cwd": str(home / "ws" / "kai" / "s1")}, sock=sock, timeout=5)
                except Exception:  # noqa: BLE001 - il daemon sta uscendo
                    pass
        ts = [threading.Thread(target=martella, daemon=True) for _ in range(4)]
        [t.start() for t in ts]
        time.sleep(0.3)
        for _ in range(4):
            p.send_signal(signal.SIGTERM)
            time.sleep(0.002 * prova)
        try:
            p.wait(5)
        except subprocess.TimeoutExpired:
            p.kill()
            raise AssertionError(f"prova {prova}: il daemon non è uscito dopo SIGTERM ripetuti")
        finally:
            fermo.set()
        assert not sock.exists(), "socket lasciato indietro"


def test_watchdog_socket_che_non_risponde():
    home = _home()
    motivi = []
    d = Daemon(home, giri=False, watchdog=True, watchdog_s=0.2, watchdog_ping_s=0.3, esci=motivi.append).avvia()
    # esegui() non parte: il socket accetta in coda ma nessuno risponde, come il 24/10
    for _ in range(50):
        if motivi:
            break
        time.sleep(0.1)
    d.stop.set()
    assert motivi and "il socket non risponde" in motivi[0]


def test_watchdog_giro_fermo():
    home = _home()
    motivi = []
    d = Daemon(home, giri=False, watchdog=True, watchdog_s=0.2, watchdog_ping_s=2, esci=motivi.append).avvia()
    t = threading.Thread(target=d.esegui, daemon=True)
    t.start()
    d.battiti["confronto"] = (time.monotonic() - 5000, 600)  # un confronto partito e mai finito
    for _ in range(50):
        if motivi:
            break
        time.sleep(0.1)
    d.ferma()
    t.join(5)
    assert motivi and "il giro confronto è fermo" in motivi[0]


def test_watchdog_tutto_bene_non_esce():
    home = _home()
    motivi = []
    d = Daemon(home, giri=False, watchdog=True, watchdog_s=0.1, watchdog_ping_s=2, esci=motivi.append).avvia()
    t = threading.Thread(target=d.esegui, daemon=True)
    t.start()
    time.sleep(1.0)
    d.ferma()
    t.join(5)
    assert motivi == []
