"""Laptop e mini, due centralini (BR24-F5.7). Il mini è un daemon vero su una seconda radice;
l'ssh finto del repo (tests/fake-ssh/ssh) esegue in locale il comando che il laptop gli manda."""

import json
import sqlite3
import sys
import tempfile
import threading
from pathlib import Path

import pytest

from centralino import remoto
from centralino.client import call
from centralino.ops import Centralino
from centralino.server import Daemon

FAKE_SSH = Path(__file__).resolve().parents[2] / "tests" / "fake-ssh" / "ssh"
SRC = Path(__file__).resolve().parents[1] / "src"


@pytest.fixture
def due(monkeypatch):
    # path corto: i socket Unix su macOS stanno sotto i 104 caratteri
    tmp = Path(tempfile.mkdtemp(prefix="cr-", dir="/tmp"))
    laptop, mini = tmp / "laptop", tmp / "mini"
    (laptop / "ws" / "alita" / "s1").mkdir(parents=True)
    (mini / "ws" / "kai" / "crewmoor").mkdir(parents=True)
    cmd = f"PYTHONPATH={SRC} {sys.executable} -m centralino.cli --home {mini}"
    hosts = {"laptop": {"hub": True}, "mini": {"ssh": "mini", "bus_home": str(mini), "centralino": cmd}}
    (laptop / "AGENT_MAP.json").write_text(json.dumps({
        "rules": [{"pattern": str(laptop / "ws" / "alita"), "agent": "alita"}],
        "agents": {"alita": {}, "kai": {}}, "hosts": hosts}))
    (laptop / "HOST").write_text("laptop\n")
    (mini / "AGENT_MAP.json").write_text(json.dumps({
        "rules": [{"pattern": str(mini / "ws" / "kai"), "agent": "kai"}],
        "agents": {"alita": {}, "kai": {}}, "cc_awareness": {"hub": "alita"},
        "hosts": {"laptop": {"hub": True}, "mini": {}}}))
    (mini / "HOST").write_text("mini\n")
    down = tmp / "mini-down"
    monkeypatch.setattr(remoto, "SSH_BIN", str(FAKE_SSH))
    monkeypatch.setenv("FAKE_SSH_DOWN", str(down))
    monkeypatch.setenv("HOME", str(tmp))  # ControlPath sotto ~/.ssh finto
    d = Daemon(mini, giri=False).avvia()
    t = threading.Thread(target=d.esegui, daemon=True)
    t.start()
    c = Centralino(laptop)
    yield c, laptop, mini, down
    c.store.close()
    d.ferma()
    t.join(5)


def _send(c, laptop, to):
    r = c.handle_request({"v": 1, "id": "1", "op": "send", "caller": {"cwd": str(laptop / "ws" / "alita" / "s1")},
                          "args": {"to": to, "intent": "x", "summary": "ciao", "message_type": "brief",
                                   "kind": "comunicazione"}})
    assert r["ok"], r
    return r["result"]


def _sul_mini(mini, mid):
    conn = sqlite3.connect(f"file:{mini / 'centralino.db'}?mode=ro", uri=True)
    return conn.execute("SELECT to_agent, to_slug, state FROM messages WHERE id=?", (mid,)).fetchone()


def test_consegna_diretta_al_centralino_del_mini(due):
    c, laptop, mini, down = due
    out = _send(c, laptop, "kai@mini/crewmoor")
    assert not out["queued"] and out["host"] == "mini"
    assert _sul_mini(mini, out["id"]) == ("kai", "crewmoor", "pending")
    assert not list((laptop / "outbox").rglob("msg-*.json")) if (laptop / "outbox").exists() else True
    # il laptop vede la casella del mini attraverso il suo centralino
    snap = c.remoto.remote_status("mini", force=True)
    assert snap["ok"] and snap["pending"] == [f"inboxes/kai/@crewmoor/{out['id']}.json"]


def test_mini_giu_coda_poi_raccolta_nei_due_sensi(due):
    c, laptop, mini, down = due
    down.touch()
    out = _send(c, laptop, "kai@mini/crewmoor")
    assert out["queued"] and list((laptop / "outbox" / "mini").rglob(f"{out['id']}.json"))
    assert c.store.read().execute("SELECT host FROM remote_queue WHERE msg_id=?", (out["id"],)).fetchone()
    # kai sul mini risponde ad alita: il laptop non è raggiungibile dal mini, va nella sua coda
    r = call("send", {"to": "alita/s1", "intent": "fatto", "summary": "ok", "message_type": "brief",
                      "kind": "comunicazione"}, caller={"cwd": str(mini / "ws" / "kai" / "crewmoor")},
             sock=mini / "centralino.sock")["result"]
    assert r["queued"] and list((mini / "outbox" / "laptop").rglob(f"{r['id']}.json"))
    assert c.raccolta()[0]["status"] == "unreachable"
    assert _sul_mini(mini, out["id"]) is None
    down.unlink()
    res = c.raccolta()[0]
    assert (res["status"], res["pushed"], res["pulled"], res["queued"]) == ("ok", 1, 1, 0)
    assert _sul_mini(mini, out["id"]) == ("kai", "crewmoor", "pending")
    row = c.store.message(r["id"])
    assert row["state"] == "pending" and row["origin"] == "mini" and row["to_agent"] == "alita"
    assert c.store.message(out["id"])["delivery"] == "remote"        # la coda si è svuotata
    assert not c.store.read().execute("SELECT 1 FROM remote_queue").fetchall()
    assert not list((mini / "outbox" / "laptop").rglob("msg-*.json")) and c.sveglia_ora.is_set()
    # la stessa consegna ripetuta (ack perso) non si duplica sul mini
    assert c.remoto.consegna("mini", "kai", "crewmoor",
                             json.dumps(json.loads(c.store.message(out["id"])["raw"])).encode())
    conn = sqlite3.connect(f"file:{mini / 'centralino.db'}?mode=ro", uri=True)
    assert conn.execute("SELECT COUNT(*) FROM messages WHERE id=?", (out["id"],)).fetchone()[0] == 1


def test_host_senza_centralino_resta_in_coda(due):
    c, laptop, mini, down = due
    amap = json.loads((laptop / "AGENT_MAP.json").read_text())
    del amap["hosts"]["mini"]["centralino"]
    (laptop / "AGENT_MAP.json").write_text(json.dumps(amap))
    out = _send(c, laptop, "kai@mini")
    assert out["queued"] and _sul_mini(mini, out["id"]) is None
