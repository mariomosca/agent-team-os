"""Trasporto verso il mini con l'ssh finto del repo (tests/fake-ssh/ssh): il mini è una cartella."""

import json
from pathlib import Path

import pytest

from centralino import remoto
from centralino.ops import Centralino

FAKE_SSH = Path(__file__).resolve().parents[2] / "tests" / "fake-ssh" / "ssh"


@pytest.fixture
def due(tmp_path, monkeypatch):
    laptop, mini = tmp_path / "laptop", tmp_path / "mini"
    ws = laptop / "ws" / "alita" / "s1"
    ws.mkdir(parents=True)
    laptop.mkdir(exist_ok=True)
    mini.mkdir()
    (laptop / "AGENT_MAP.json").write_text(json.dumps({
        "rules": [{"pattern": str(laptop / "ws" / "alita"), "agent": "alita"}],
        "agents": {"alita": {}, "kai": {}},
        "hosts": {"laptop": {"hub": True}, "mini": {"ssh": "mini", "bus_home": str(mini)}}}))
    down = tmp_path / "mini-down"
    monkeypatch.setattr(remoto, "SSH_BIN", str(FAKE_SSH))
    monkeypatch.setenv("FAKE_SSH_DOWN", str(down))
    monkeypatch.setenv("HOME", str(tmp_path))  # ControlPath sotto ~/.ssh finto
    c = Centralino(laptop)
    yield c, laptop, mini, down
    c.store.close()


def _send(c, laptop, to):
    r = c.handle_request({"v": 1, "id": "1", "op": "send", "caller": {"cwd": str(laptop / "ws" / "alita" / "s1")},
                          "args": {"to": to, "intent": "x", "summary": "ciao", "message_type": "brief",
                                   "kind": "comunicazione"}})
    assert r["ok"], r
    return r["result"]


def test_mini_giu_coda_poi_raccolta(due):
    c, laptop, mini, down = due
    down.touch()
    out = _send(c, laptop, "kai@mini/crewmoor")
    assert out["queued"] and out["host"] == "mini"
    assert list((laptop / "outbox" / "mini").rglob(f"{out['id']}.json"))
    assert c.store.read().execute("SELECT host FROM remote_queue WHERE msg_id=?", (out["id"],)).fetchone()
    # il mini ha lasciato un messaggio per noi: finché è giù niente si perde
    mini_out = mini / "outbox" / "laptop" / "alita"
    mini_out.mkdir(parents=True)
    rec = {"id": "msg-20261024T120000Z-damini01", "from": "kai", "to": "alita", "thread_id": "thread-x",
           "type": "response", "intent": "fatto", "priority": "normal", "payload": {"summary": "ok"},
           "ts": "2026-10-24T12:00:00Z", "context": {"host": "mini"}}
    (mini_out / f"{rec['id']}.json").write_text(json.dumps(rec))
    assert c.raccolta()[0]["status"] == "unreachable"
    assert list((laptop / "outbox" / "mini").rglob("msg-*.json")) and list(mini_out.glob("msg-*.json"))
    down.unlink()
    res = c.raccolta()[0]
    assert (res["status"], res["pushed"], res["pulled"], res["queued"]) == ("ok", 1, 1, 0)
    assert list((mini / "inboxes" / "kai" / "@crewmoor").glob(f"{out['id']}.json"))
    assert not list(mini_out.glob("msg-*.json")) and c.sveglia_ora.is_set()
    row = c.store.message(rec["id"])
    assert row["state"] == "pending" and row["origin"] == "mini" and row["to_agent"] == "alita"
    assert c.store.message(out["id"])["delivery"] == "remote"        # la coda si è svuotata
    assert not c.store.read().execute("SELECT 1 FROM remote_queue").fetchall()
    # lo stesso messaggio che ricompare sul mini (cancellazione fallita) non si duplica
    mini_out.joinpath(f"{rec['id']}.json").write_text(json.dumps(rec))
    assert c.raccolta()[0]["pulled"] == 0 and not list(mini_out.glob("msg-*.json"))
    assert c.store.read().execute("SELECT COUNT(*) FROM messages WHERE id=?", (rec["id"],)).fetchone()[0] == 1


def test_consegna_diretta_con_mini_su(due):
    c, laptop, mini, down = due
    out = _send(c, laptop, "kai@mini")
    assert not out["queued"] and out["path"] is None
    assert list((mini / "inboxes" / "kai").glob(f"{out['id']}.json"))
    assert not (laptop / "outbox" / "mini").exists() or not list((laptop / "outbox" / "mini").rglob("msg-*.json"))
