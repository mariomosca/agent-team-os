"""WT30-F5.4: il socket di Onda per sessione (schema v7). Chi ha due Onda (l'app e ``npm run dev``)
viene svegliato e chiuso dall'Onda che ospita la sua sessione, non da quella dell'ambiente del daemon."""

import os
import sqlite3

import pytest

from centralino import canale, palla
from centralino.db import SCHEMA_VERSION, Store
from centralino.sim import Banco

DEV = "/tmp/onda-dev.sock"


def test_v6_diventa_v7_con_backup(tmp_path):
    p = tmp_path / "centralino.db"
    Store(p).close()
    c = sqlite3.connect(p)
    c.execute("ALTER TABLE sessions DROP COLUMN onda_socket")
    c.execute("INSERT OR REPLACE INTO meta VALUES ('schema_version', '6')")
    c.execute("INSERT INTO sessions (agent, slug, host) VALUES ('kai', 'x', 'laptop')")
    c.commit()
    c.close()
    s = Store(p)
    r = s.read()
    assert SCHEMA_VERSION == 7
    assert r.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0] == "7"
    assert "onda_socket" in {x["name"] for x in r.execute("PRAGMA table_info(sessions)")}
    assert r.execute("SELECT agent FROM sessions").fetchone()["agent"] == "kai"
    s.close()
    bak = list(tmp_path.glob("centralino.db.bak-v6-*"))
    assert len(bak) == 1
    assert "onda_socket" not in {x[1] for x in sqlite3.connect(bak[0]).execute("PRAGMA table_info(sessions)")}


def test_con_socket():
    visti = []

    def nuova(method, params, timeout=None, sock=None):
        visti.append(sock)
        return {}

    def vecchia(method, params):
        return {"vecchia": True}
    canale.con_socket(nuova, DEV)("m", {})
    canale.con_socket(nuova, None)("m", {})
    assert visti == [DEV, None]
    assert canale.con_socket(vecchia, DEV) is vecchia
    assert canale.onda_socket_path(DEV) == DEV
    assert canale.onda_socket_path(None) == (os.environ.get("ONDA_SOCKET")
                                             or os.path.expanduser("~/.config/onda/onda.sock"))


class Onda:
    def __init__(self):
        self.chiamate: list[tuple[str, dict, str | None]] = []

    def __call__(self, method, params, timeout=None, sock=None):
        self.chiamate.append((method, params, sock))
        if method == "agent.prompt":
            return {"submitted": True}
        if method == "agent.close":
            return {"closed": True}
        if method == "worktree.retire":
            return {"retired": True, "integratedBy": "tree"}
        return {}


@pytest.fixture
def b(tmp_path):
    banco = Banco(tmp_path / "bus")
    banco.fonda = Onda()
    yield banco
    banco.chiudi()


def _start(b, addr, sock, terminal=None):
    caller = {**b.caller(addr), "onda_socket": sock}
    if terminal:
        caller["onda_terminal_id"] = terminal
    r = b.c.handle_request({"v": 1, "id": "x", "op": "hook.start", "caller": caller, "args": {}})
    assert r["ok"], r


def _socket_di(b, addr):
    agent, _, slug = addr.partition("/")
    return b.c.store.read().execute("SELECT onda_socket FROM sessions WHERE agent=? AND slug=?",
                                    (agent, slug)).fetchone()["onda_socket"]


def test_registro_tiene_il_socket_della_sessione(b):
    _start(b, "kai/x", DEV)
    b.op("kai/x", "inbox")  # un battito senza socket (il MCP) non lo cancella
    assert _socket_di(b, "kai/x") == DEV
    _start(b, "kai/x", None)  # riaperta fuori da quell'Onda: il socket vecchio non vale più
    assert _socket_di(b, "kai/x") is None


def test_sveglia_usa_il_socket_della_sessione(b):
    b.sveglia.onda_call = b.fonda
    b.onda("alita/work-hub", "idle")
    b.onda("kai/x", "idle")
    b.onda("kai/y", "idle")
    _start(b, "kai/x", DEV)
    _start(b, "kai/y", None)
    b.op("alita/work-hub", "send", to="kai/x", intent="a", summary="a", message_type="request", kind="delega")
    b.op("alita/work-hub", "send", to="kai/y", intent="b", summary="b", message_type="request", kind="delega")
    b.giro()
    per_term = {p["terminalId"]: s for m, p, s in b.fonda.chiamate if m == "agent.prompt"}
    assert per_term == {"pane-kai-x": DEV, "pane-kai-y": None}


def test_chiudi_delega_usa_il_socket_e_chiude_la_casella(b):
    b.c.deleghe.onda_call = b.fonda
    b.c.deleghe.orbit = type("O", (), {"chiama": lambda self, tool, args: {}})()
    b.c.deleghe.sync = True
    b.c.orbit = palla.Orbit(None)
    os.environ["AB_RETIRE_ATTESA_S"] = "0"
    addr = "kai/wt30-s4"
    b.onda("alita/work-hub")
    b.onda(addr, "idle")
    path = str(b.cwd(addr))
    b.op("alita/work-hub", "delega.spawn", input={}, result={
        "terminalId": "pane-kai-wt30-s4", "sessionId": "sess-kai-wt30-s4", "workspaceId": "ws-s4", "cwd": path,
        "worktree": {"path": path, "repoRoot": "/finto/repo", "branch": "feat/s4"}})
    _start(b, addr, DEV, terminal="pane-kai-wt30-s4")
    tid = b.op("alita/work-hub", "send", to=addr, intent="WT30-F5.4", summary="fai", message_type="request",
               kind="delega")["task_id"]
    info = b.op("alita/work-hub", "send", to=addr, intent="info", summary="x", message_type="brief",
                kind="comunicazione")
    b.op(addr, "send", to="alita/work-hub", intent="re", summary="fatto", message_type="response", task_id=tid)
    b.op("alita/work-hub", "close", task_id=tid)
    usati = {m: s for m, p, s in b.fonda.chiamate}
    assert usati.get("agent.close") == DEV and usati.get("worktree.retire") == DEV
    # R3: la sessione chiusa dal centralino ha la fine registrata, i suoi messaggi aspettano la grazia
    row = b.c.store.read().execute("SELECT ended_at FROM sessions WHERE agent='kai' AND slug='wt30-s4'").fetchone()
    assert row["ended_at"] and b.c.store.message(info["id"])["state"] == "pending"
