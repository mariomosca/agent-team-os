import json
import os
import socket
import tempfile
import threading
import time
from pathlib import Path

import pytest

from centralino.client import CentralinoGiu, call
from centralino.files import read_json


@pytest.fixture
def bus(monkeypatch):
    # path corto: i socket Unix su macOS stanno sotto i 104 caratteri
    home = Path(tempfile.mkdtemp(prefix="cb-", dir="/tmp"))
    (home / "AGENT_MAP.json").write_text(json.dumps({
        "rules": [{"pattern": str(home / "ws" / "kai"), "agent": "kai"},
                  {"pattern": str(home / "ws" / "alita"), "agent": "alita"}],
        "agents": {"kai": {}, "alita": {}}, "hosts": {"laptop": {"hub": True}}}))
    for a in ("kai", "alita"):
        (home / "ws" / a / "s1").mkdir(parents=True)
    monkeypatch.setenv("AB_WAKE_SCRIPT", "/nonexistent")
    from centralino.server import Daemon
    d = Daemon(home, giri=False).avvia()
    t = threading.Thread(target=d.esegui, daemon=True)
    t.start()
    yield home
    d.ferma()
    t.join(5)
    assert not (home / "centralino.sock").exists()


def _caller(home, a):
    return {"cwd": str(home / "ws" / a / "s1")}


def test_roundtrip_e_permessi(bus):
    sock = bus / "centralino.sock"
    assert oct(sock.stat().st_mode & 0o777) == "0o600"
    r = call("admin.ping", sock=sock)
    assert r["ok"] and r["result"]["schema"] == 1
    r = call("send", {"to": "alita/s1", "intent": "x", "summary": "ciao", "message_type": "request",
                      "kind": "delega"}, caller=_caller(bus, "kai"), sock=sock)
    assert r["ok"], r
    tid = r["result"]["task_id"]
    assert read_json(bus / "waits" / "kai" / "@s1" / f"{tid}.json")["state"] == "expected"
    inbox = call("inbox", {}, caller=_caller(bus, "alita"), sock=sock)["result"]
    assert inbox["count"] == 1 and inbox["messages"][0]["task_id"] == tid


def test_errori_chiari(bus):
    sock = bus / "centralino.sock"
    r = call("send", {"to": "kai", "intent": "x", "summary": "y", "message_type": "brief"},
             caller=_caller(bus, "alita"), sock=sock)
    assert not r["ok"] and r["error"]["code"] == "kind_required"
    r = call("boh", sock=sock)
    assert r["error"]["code"] == "bad_request"
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.connect(str(sock))
    s.sendall(b"{non json\n")
    assert json.loads(s.recv(4096))["error"]["code"] == "bad_request"
    s.close()


def test_scrittori_concorrenti(bus):
    sock = bus / "centralino.sock"
    errori = []

    def manda(i):
        r = call("send", {"to": "kai/s1", "intent": f"m{i}", "summary": "x", "message_type": "event"},
                 caller=_caller(bus, "alita"), sock=sock)
        if not r["ok"]:
            errori.append(r)

    ts = [threading.Thread(target=manda, args=(i,)) for i in range(40)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert not errori
    files = list((bus / "inboxes" / "kai" / "@s1").glob("msg-*.json"))
    assert len(files) == 40
    st = call("admin.stats", sock=sock)["result"]
    assert st["messaggi"] == 40 and st["pending"] == 40
    assert not list((bus / "inboxes").rglob("*.tmp"))


def test_centralino_giu(tmp_path):
    with pytest.raises(CentralinoGiu, match="non risponde"):
        call("admin.ping", sock=tmp_path / "nessuno.sock")
