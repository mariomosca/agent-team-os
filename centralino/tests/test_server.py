import json
import os
import socket
import tempfile
import threading
import time
from pathlib import Path

import pytest

from centralino.client import CentralinoGiu, call


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
    waits = call("waits", {}, caller=_caller(bus, "kai"), sock=sock)["result"]["waits"]
    assert [(w["task_id"], w["state"]) for w in waits] == [(tid, "expected")]
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
    st = call("admin.stats", sock=sock)["result"]
    assert st["messaggi"] == 40 and st["pending"] == 40
    assert not list((bus / "inboxes").rglob("msg-*")) if (bus / "inboxes").is_dir() else True  # niente file di casella


def test_centralino_giu(tmp_path):
    with pytest.raises(CentralinoGiu, match="non risponde"):
        call("admin.ping", sock=tmp_path / "nessuno.sock")


def test_vista_globale(bus):
    sock = bus / "centralino.sock"
    call("send", {"to": "alita/s1", "intent": "x", "summary": "ciao", "message_type": "request", "kind": "delega"},
         caller=_caller(bus, "kai"), sock=sock)
    v = call("vista", sock=sock)["result"]
    assert "modo" not in v
    assert [m["raw"]["intent"] for m in v["messaggi"]] == ["x"]
    assert v["messaggi"][0]["agent"] == "alita" and v["messaggi"][0]["slug"] == "s1"
    assert len(v["attese"]) == 1 and v["attese"][0]["raw"]["state"] == "expected"
    # BR24-F6.7: chi ha la palla lo dice il centralino, con i dati della riga
    a = v["attese"][0]
    assert a["stato_palla"] in ("coda", "fermo", "lavora", "chiusa")
    assert a["riga"]["parola"] and a["riga"]["glifo"] and a["riga"]["leggi"] == f"/read {a['raw']['delegation']['msg_id']}"
    assert {x["agent"] for x in v["agenti"]} == {"kai", "alita"}
    assert {s["slug"] for s in v["sessioni"]} == {"s1"}  # vive per heartbeat
    assert v["onda"] == {"valido": False, "writtenAt": None, "sessioni": []}


def test_domanda_dell_esecutore_lascia_la_delega_aperta(bus):
    # BR24-F5.5b (a): prova del 24/10, task t-20261024-hcpl2x
    sock = bus / "centralino.sock"
    kai, alita = _caller(bus, "kai"), _caller(bus, "alita")
    r = call("send", {"to": "kai/s1", "intent": "x", "summary": "fai X", "message_type": "request",
                      "kind": "delega"}, caller=alita, sock=sock)["result"]
    mid, tid = r["id"], r["task_id"]
    lettura = call("read", {"message_id": mid}, caller=kai, sock=sock)["result"]
    assert "bus_gestisci" not in lettura["_task_hint"] and "bus_reply" in lettura["_task_hint"]
    q = call("reply", {"message_id": mid, "summary": "A o B?", "message_type": "question"},
             caller=kai, sock=sock)
    assert q["ok"], q
    inbox = call("inbox", {}, caller=kai, sock=sock)["result"]
    assert [m["id"] for m in inbox["messages"]] == [mid]
    domanda = call("inbox", {}, caller=alita, sock=sock)["result"]["messages"]
    assert [(m["type"], m["task_id"]) for m in domanda] == [("question", tid)]
    hint = call("read", {"message_id": domanda[0]["id"]}, caller=alita, sock=sock)["result"]["_task_hint"]
    assert "bus_close" in hint and "bus_gestisci" not in hint and "risposto" not in hint
    # l'esito sulla delega originale la consuma
    e = call("reply", {"message_id": mid, "summary": "fatto"}, caller=kai, sock=sock)
    assert e["ok"] and e["result"]["remaining"] == 0


def test_stop_non_blocca_l_esecutore_in_attesa_della_risposta(bus):
    # BR24-F5.5d: prova del 24/10, Alita bloccata 3 volte su 3 dopo la sua domanda
    sock = bus / "centralino.sock"
    kai, alita = _caller(bus, "kai"), _caller(bus, "alita")
    stop = lambda: call("hook.stop", {}, caller=kai, sock=sock)["result"]["block"]  # noqa: E731
    mid = call("send", {"to": "kai/s1", "intent": "x", "summary": "fai X", "message_type": "request",
                        "kind": "delega"}, caller=alita, sock=sock)["result"]["id"]
    assert stop() is True
    call("reply", {"message_id": mid, "summary": "A o B?", "message_type": "question"}, caller=kai, sock=sock)
    assert stop() is False and stop() is False
    q = call("inbox", {}, caller=alita, sock=sock)["result"]["messages"][0]["id"]
    risposta = call("reply", {"message_id": q, "summary": "A"}, caller=alita, sock=sock)["result"]["sent"]["id"]
    assert stop() is True                       # la risposta blocca da sola
    call("close", {"message_id": risposta, "esito": "letto"}, caller=kai, sock=sock)
    assert stop() is True                       # e la delega torna azionabile
    call("reply", {"message_id": mid, "summary": "fatto con A"}, caller=kai, sock=sock)
    assert stop() is False


def test_letto_at_alla_prima_lettura_dell_esecutore(bus):
    # BR24-F6.6: la banda distingue "in coda" (non letta) da "ci lavora" (letta)
    sock = bus / "centralino.sock"
    kai, alita = _caller(bus, "kai"), _caller(bus, "alita")
    (bus / "ws" / "kai" / "s2").mkdir()
    kai2 = {"cwd": str(bus / "ws" / "kai" / "s2")}
    mid = call("send", {"to": "kai/s1", "intent": "x", "summary": "fai X", "message_type": "request",
                        "kind": "delega"}, caller=alita, sock=sock)["result"]["id"]
    attesa = lambda: call("vista", sock=sock)["result"]["attese"][0]  # noqa: E731
    assert attesa()["letto_at"] is None
    # il delegante non la trova nella sua casella; un'altra sessione di kai la legge ma non conta
    assert not call("read", {"message_id": mid}, caller=alita, sock=sock)["ok"]
    assert call("read", {"message_id": mid}, caller=kai2, sock=sock)["ok"]
    assert attesa()["letto_at"] is None
    assert call("read", {"message_id": mid}, caller=kai, sock=sock)["ok"]
    prima = attesa()
    assert prima["letto_at"] and prima["letto_da"] == "kai/s1"
    # solo la prima lettura
    time.sleep(1.1)
    call("read", {"message_id": mid}, caller=kai, sock=sock)
    assert attesa()["letto_at"] == prima["letto_at"]
    # una comunicazione letta non segna nulla
    m2 = call("send", {"to": "kai/s1", "intent": "y", "summary": "info", "message_type": "brief",
                       "kind": "comunicazione"}, caller=alita, sock=sock)["result"]["id"]
    call("read", {"message_id": m2}, caller=kai, sock=sock)
    from centralino.db import Store
    st = Store(bus / "centralino.db")
    assert st.message(m2)["letto_at"] is None
    st.close()


def test_letta_al_primo_ritorno_dell_esecutore(bus):
    # BR24-F6.6 (correzione): chi lavora dalla sveglia può non chiamare mai bus_read
    sock = bus / "centralino.sock"
    kai, alita = _caller(bus, "kai"), _caller(bus, "alita")
    mid = call("send", {"to": "kai/s1", "intent": "x", "summary": "fai X", "message_type": "request",
                        "kind": "delega"}, caller=alita, sock=sock)["result"]["id"]
    assert call("vista", sock=sock)["result"]["attese"][0]["letto_at"] is None
    assert call("reply", {"message_id": mid, "summary": "A o B?", "message_type": "question"},
                caller=kai, sock=sock)["ok"]
    a = call("vista", sock=sock)["result"]["attese"][0]
    assert a["letto_at"] and a["letto_da"] == "kai/s1"


def test_migrazione_v4_con_backup(tmp_path):
    import sqlite3
    from centralino.db import Store
    db = tmp_path / "centralino.db"
    Store(db).close()
    c = sqlite3.connect(db)
    c.execute("UPDATE meta SET value='3' WHERE key='schema_version'")
    c.execute("INSERT INTO messages (id, ts, from_agent, to_agent, to_host, type, kind, thread_id, state, "
              "delivery, origin, raw) VALUES ('m-vecchia', 't', 'alita', 'kai', 'laptop', 'request', 'delega', "
              "'th', 'pending', 'local', 'x', '{}')")
    c.execute("ALTER TABLE messages DROP COLUMN letto_da")
    c.execute("ALTER TABLE messages DROP COLUMN letto_at")
    c.commit()
    c.close()
    st = Store(db)
    cols = {r["name"] for r in st.read().execute("PRAGMA table_info(messages)")}
    assert {"letto_at", "letto_da"} <= cols
    # una delega di prima vale come letta: niente "in coda" falso
    vecchia = st.message("m-vecchia")
    assert vecchia["letto_at"] and vecchia["letto_da"] is None
    st.close()
    baks = list(tmp_path.glob("centralino.db.bak-v3-*"))
    assert len(baks) == 1
    vecchio = sqlite3.connect(baks[0])
    assert vecchio.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0] == "3"
    vecchio.close()
    Store(db).close()  # già v4: nessun altro backup
    assert len(list(tmp_path.glob("centralino.db.bak-*"))) == 1
