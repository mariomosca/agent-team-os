"""DC29-F4: deleghe che si chiudono da sole. Onda e Orbit finti (callable, socket e HTTP
fittizi), AB_HOME temporaneo, nessun repo vero, nessun daemon live."""

import http.server
import json
import os
import socket
import sqlite3
import tempfile
import threading
from pathlib import Path

import pytest

from centralino import palla
from centralino.deleghe import Deleghe, EsternoGiu, OrbitMcp, _Spento
from centralino.sim import Banco

HOOK = Path(__file__).resolve().parents[2] / "hooks" / "agent-team-os-onda-spawn.py"


class FintaOnda:
    def __init__(self):
        self.chiamate: list[tuple[str, dict]] = []
        self.retire: list[dict] = []
        self.lista: list[dict] = []
        self.giu = False

    def __call__(self, method, params, timeout=None):
        if self.giu:
            raise OSError("socket assente")
        self.chiamate.append((method, params))
        if method == "agent.close":
            return {"closed": True}
        if method == "worktree.retire":
            assert "path" in params and "worktreePath" not in params, params  # methods.ts legge path
            if self.retire:
                return self.retire.pop(0)
            return {"retired": True, "integratedBy": "tree", "tag": "archivio/feat-x", "branchDeleted": True}
        if method == "worktree.list":
            return {"worktrees": self.lista}
        return {}

    def metodi(self, m):
        return [p for x, p in self.chiamate if x == m]


class FintoOrbit:
    def __init__(self):
        self.scritte: list[dict] = []
        self.giu = False

    def chiama(self, tool, args):
        if self.giu:
            raise EsternoGiu("Orbit non risponde")
        assert tool == "delega_set"
        assert args.get("closure_at") is None or isinstance(args["closure_at"], int), args  # epoch ms
        self.scritte.append(args)
        return {"ok": True}


@pytest.fixture
def b(tmp_path):
    banco = Banco(tmp_path / "bus")
    banco.fonda = FintaOnda()
    banco.forbit = FintoOrbit()
    banco.c.deleghe.onda_call = banco.fonda
    banco.c.deleghe.orbit = banco.forbit
    banco.c.deleghe.sync = True
    banco.c.orbit = palla.Orbit(None)  # mai l'orbit.db vero: i passi done li decide il test
    os.environ["AB_RETIRE_ATTESA_S"] = "0"
    yield banco
    banco.chiudi()


def _spawn_res(b, addr, repo="/finto/repo", labels=None, cwd=True, worktree=True):
    agent, _, slug = addr.partition("/")
    path = str(b.cwd(addr))
    r = {"terminalId": f"pane-{agent}-{slug}", "sessionId": f"sess-{agent}-{slug}", "workspaceId": f"ws-{slug}"}
    if cwd:
        r["cwd"] = path
    if worktree:
        r["worktree"] = {"path": path, "repoRoot": repo, "branch": f"feat/{slug}"}
    if labels:
        r["labels"] = labels
    return r


def _righe(b):
    return [dict(x) for x in b.c.store.read().execute("SELECT * FROM deleghe ORDER BY id")]


def _delega(b, frm, to, summary):
    return b.op(frm, "send", to=to, intent="lavoro", summary=summary, message_type="request",
                kind="delega")["task_id"]


def _start(b, addr, terminal):
    caller = {**b.caller(addr), "onda_terminal_id": terminal}
    resp = b.c.handle_request({"v": 1, "id": "x", "op": "hook.start", "caller": caller, "args": {}})
    assert resp["ok"], resp


# ---------- legame ----------

def test_spawn_poi_bus_send(b):
    b.onda("alita/work-hub")
    out = b.op("alita/work-hub", "delega.spawn", result=_spawn_res(b, "kai/dc29-f3"),
               input={"worktree": {"repoRoot": "/finto/repo", "branch": "feat/dc29-f3"}})
    assert out["registrato"] and out["task_id"] is None
    tid = _delega(b, "alita/work-hub", "kai/dc29-f3", "DC29-F3.1: fai la primitiva")
    rr = _righe(b)
    assert len(rr) == 1
    r = rr[0]
    assert (r["task_id"], r["step_ref"], r["terminal_id"], r["worktree_path"]) == (
        tid, "DC29-F3.1", "pane-kai-dc29-f3", str(b.cwd("kai/dc29-f3")))
    assert r["delegator"] == "alita/work-hub" and r["spawn_by"] == "alita/work-hub" and r["linked_at"]
    s = b.forbit.scritte[-1]
    assert s["step_ref"] == "DC29-F3.1" and s["bus_task_id"] == tid and s["terminal_id"] == "pane-kai-dc29-f3"
    assert s["closure_state"] == "open" and s["branch"] == "feat/dc29-f3" and r["orbit_dirty"] == 0


def test_bus_send_poi_spawn(b):
    tid = _delega(b, "alita/work-hub", "kai/dc29-f2", "DC29-F2.1 fai la tabella")
    assert _righe(b)[0]["spawned_at"] is None and b.forbit.scritte == []  # niente spawn: nessuna riga su Orbit
    out = b.op("alita/work-hub", "delega.spawn", result=_spawn_res(b, "kai/dc29-f2"), input={})
    assert out["task_id"] == tid and out["legate"] == [tid]
    assert len(_righe(b)) == 1 and b.forbit.scritte[-1]["worktree_path"] == str(b.cwd("kai/dc29-f2"))


def test_legame_a_sessionstart(b):
    """Spawn senza cwd né worktree: chi è la sessione lo dice il suo SessionStart nel terminale."""
    b.op("alita/work-hub", "delega.spawn", result=_spawn_res(b, "kai/sx", cwd=False, worktree=False), input={})
    tid = _delega(b, "alita/work-hub", "kai/sx", "fai X")
    assert len(_righe(b)) == 2  # non si sa ancora che sono la stessa cosa
    _start(b, "kai/sx", "pane-kai-sx")
    rr = _righe(b)
    assert len(rr) == 1 and rr[0]["task_id"] == tid and rr[0]["terminal_id"] == "pane-kai-sx"


def test_legame_per_labels_ref(b):
    b.op("alita/work-hub", "delega.spawn",
         result=_spawn_res(b, "kai/altro", cwd=False, worktree=False, labels={"ref": "dc29-f3.2"}), input={})
    tid = _delega(b, "alita/work-hub", "kai/qualunque", "DC29-F3.2 lavoro")
    rr = _righe(b)
    assert len(rr) == 1 and rr[0]["task_id"] == tid and rr[0]["terminal_id"] == "pane-kai-altro"


def test_leo_delega_a_nico(b):
    amap = json.loads((b.home / "AGENT_MAP.json").read_text())
    amap["rules"].append({"pattern": str(b.root / "Projects" / "06-Brandart"), "match": "prefix", "agent": "nico"})
    amap["agents"]["nico"] = {"role": "tech", "domain": "brandart"}
    (b.home / "AGENT_MAP.json").write_text(json.dumps(amap))
    st = (b.home / "AGENT_MAP.json").stat()
    os.utime(b.home / "AGENT_MAP.json", ns=(st.st_atime_ns, st.st_mtime_ns + 10))
    nico = b.root / "Projects" / "06-Brandart" / "piattaforma-feat-x"
    nico.mkdir(parents=True)
    leo = {"cwd": str(b.root / "Projects" / "00-Brandart-PM-OS"), "pid": 1}
    (b.root / "Projects" / "00-Brandart-PM-OS").mkdir(parents=True, exist_ok=True)
    res = {"terminalId": "pane-nico", "cwd": str(nico),
           "worktree": {"path": str(nico), "repoRoot": "/finto/piattaforma", "branch": "feat/x"}}
    assert b.c.handle_request({"v": 1, "op": "delega.spawn", "caller": leo,
                               "args": {"result": res, "input": {}}})["ok"]
    r = b.c.handle_request({"v": 1, "op": "send", "caller": leo, "args": {
        "to": "nico/piattaforma-feat-x", "intent": "BA12-F1.1", "summary": "fai", "message_type": "request",
        "kind": "delega"}})
    assert r["ok"], r
    rr = _righe(b)
    assert len(rr) == 1 and rr[0]["delegator"] == "leo/00-brandart-pm-os" and rr[0]["sess_agent"] == "nico"
    assert rr[0]["step_ref"] == "BA12-F1.1" and rr[0]["terminal_id"] == "pane-nico"


def test_fuori_finestra_non_lega(b):
    b.op("alita/work-hub", "delega.spawn", result=_spawn_res(b, "kai/tardi"), input={})
    b.avanza(31 * 60)
    _delega(b, "alita/work-hub", "kai/tardi", "fai")
    assert len(_righe(b)) == 2


def test_seconda_delega_eredita_worktree(b):
    b.op("alita/work-hub", "delega.spawn", result=_spawn_res(b, "kai/due"), input={})
    t1 = _delega(b, "alita/work-hub", "kai/due", "DC29-F3.1 uno")
    b.avanza(40 * 60)  # oltre la finestra: eredita lo stesso, è la stessa sessione già legata
    t2 = _delega(b, "alita/work-hub", "kai/due", "DC29-F3.3 due")
    rr = {r["task_id"]: r for r in _righe(b)}
    assert rr[t2]["terminal_id"] == rr[t1]["terminal_id"] and rr[t2]["worktree_path"] == rr[t1]["worktree_path"]


# ---------- chiusura ----------

def _pronta(b, addr="kai/dc29-f3", summary="DC29-F3.1 fai"):
    b.onda("alita/work-hub")
    b.onda(addr, "idle")
    b.op("alita/work-hub", "delega.spawn", result=_spawn_res(b, addr), input={})
    tid = _delega(b, "alita/work-hub", addr, summary)
    b.op(addr, "send", to="alita/work-hub", intent="re", summary="fatto", message_type="response", task_id=tid)
    return tid


def test_bus_close_chiude_sessione_e_worktree_idempotente(b):
    tid = _pronta(b)
    b.op("alita/work-hub", "close", task_id=tid)
    assert [p["terminalId"] for p in b.fonda.metodi("agent.close")] == ["pane-kai-dc29-f3"]
    assert b.fonda.metodi("agent.close")[0]["killPty"] is True
    ret = b.fonda.metodi("worktree.retire")
    assert len(ret) == 1 and ret[0]["archiveTagPrefix"] == "archivio/" and ret[0]["deleteBranch"] is True
    r = _righe(b)[0]
    assert (r["closure_state"], r["closure_reason"]) == ("closed", "retired")
    assert "squash riconosciuto" in r["closure_detail"] and "archivio/feat-x" in r["closure_detail"]
    assert b.forbit.scritte[-1]["closure_state"] == "closed"
    out = b.op(None, "delega.chiudi", task_id=tid, reason="step_done", attendi=True)
    assert out["esiti"][0]["gia"] and len(b.fonda.metodi("worktree.retire")) == 1  # idempotente


def test_bus_close_da_messaggio_esito_chiuso(b):
    tid = _pronta(b)
    ritorno = [m for m in b.op("alita/work-hub", "inbox")["messages"] if m["task_id"] == tid][0]
    b.op("alita/work-hub", "close", message_id=ritorno["id"], esito="chiuso")
    assert _righe(b)[0]["closure_state"] == "closed"


def test_sessione_al_lavoro_resta_pending_poi_riprova(b):
    tid = _pronta(b)
    b.onda("kai/dc29-f3", "working")
    b.op("alita/work-hub", "close", task_id=tid)
    r = _righe(b)[0]
    assert (r["closure_state"], r["closure_reason"]) == ("pending", "session_al_lavoro")
    assert b.fonda.metodi("agent.close") == [] and b.fonda.metodi("worktree.retire") == []
    b.avanza(90)
    b.onda("kai/dc29-f3", "idle")
    b.onda("kai/dc29-f3", "idle")
    b.avanza(10)
    b.op("alita/work-hub", "inbox")  # un evento qualunque non basta: servono gli eventi delle deleghe
    _delega(b, "alita/work-hub", "kai/altra", "altra")  # bus_send di una delega: riprova le pending
    assert _righe(b)[0]["closure_state"] == "closed"


def test_tenuta_avvisa_una_volta_per_motivo(b):
    tid = _pronta(b)
    b.fonda.retire = [{"retired": False, "reason": "dirty", "detail": "3 file: a, b, c"}] * 2 + \
        [{"retired": False, "reason": "not_integrated"}]
    b.op("alita/work-hub", "close", task_id=tid)
    r = _righe(b)[0]
    assert (r["closure_state"], r["closure_reason"]) == ("kept", "dirty")
    avvisi = lambda: [m for m in b.op("alita/work-hub", "inbox")["messages"] if m["intent"] == "delega-tenuta"]  # noqa: E731
    assert len(avvisi()) == 1 and avvisi()[0]["kind"] == "comunicazione" and "dirty" in avvisi()[0]["summary"]
    assert avvisi()[0]["task_id"] is None  # non è un ritorno sull'attesa: non sveglia
    assert b.forbit.scritte[-1]["closure_state"] == "kept" and b.forbit.scritte[-1]["closure_reason"] == "dirty"
    b.op(None, "delega.chiudi", step_ref="DC29-F3.1", reason="step_done", attendi=True)
    assert len(avvisi()) == 1  # stesso motivo: niente secondo avviso
    b.op(None, "delega.chiudi", step_ref="dc29-f3.1", reason="step_done", attendi=True)
    assert len(avvisi()) == 2 and any("not_integrated" in m["summary"] for m in avvisi())


def test_altre_deleghe_tengono_la_sessione(b):
    t1 = _pronta(b, summary="DC29-F3.1 uno")
    t2 = _delega(b, "alita/work-hub", "kai/dc29-f3", "DC29-F3.2 due")
    b.op("alita/work-hub", "close", task_id=t1)
    rr = {r["task_id"]: r for r in _righe(b)}
    assert (rr[t1]["closure_state"], rr[t1]["closure_reason"]) == ("closed", "altre_deleghe")
    assert b.fonda.metodi("agent.close") == [] and b.fonda.metodi("worktree.retire") == []
    b.op("alita/work-hub", "close", task_id=t2)
    rr = {r["task_id"]: r for r in _righe(b)}
    assert rr[t2]["closure_state"] == "closed" and len(b.fonda.metodi("agent.close")) == 1
    assert len(b.fonda.metodi("worktree.retire")) == 1


def test_onda_giu_e_orbit_giu_poi_recupero(b):
    tid = _pronta(b)
    b.fonda.giu = True
    b.forbit.giu = True
    b.op("alita/work-hub", "close", task_id=tid)
    r = _righe(b)[0]
    assert r["closure_state"] == "pending" and r["orbit_dirty"] == 1
    b.fonda.giu = False
    b.forbit.giu = False
    b.avanza(61)
    b.onda("kai/dc29-f3", "idle")
    b.op(None, "delega.chiudi", task_id=tid, reason="riprova", attendi=True)
    r = _righe(b)[0]
    assert r["closure_state"] == "closed" and r["orbit_dirty"] == 0
    assert b.forbit.scritte[-1]["closure_state"] == "closed"


def test_fetch_failed_e_pending(b):
    tid = _pronta(b)
    b.fonda.retire = [{"retired": False, "reason": "fetch_failed"}]
    b.op("alita/work-hub", "close", task_id=tid)
    assert _righe(b)[0]["closure_state"] == "pending"
    assert [m for m in b.op("alita/work-hub", "inbox")["messages"] if m["intent"] == "delega-tenuta"] == []


def test_delega_senza_worktree_chiude_solo_la_sessione(b):
    b.onda("kai/solo", "idle")
    b.op("alita/work-hub", "delega.spawn", result=_spawn_res(b, "kai/solo", worktree=False), input={})
    tid = _delega(b, "alita/work-hub", "kai/solo", "fai")
    b.op("alita/work-hub", "close", task_id=tid)
    r = _righe(b)[0]
    assert (r["closure_state"], r["closure_reason"]) == ("closed", "senza_worktree")
    assert len(b.fonda.metodi("agent.close")) == 1 and b.fonda.metodi("worktree.retire") == []


def test_sessione_non_nata_per_la_delega_non_si_chiude(b):
    b.onda("kai/onda-electron", "idle")
    tid = _delega(b, "alita/work-hub", "kai/onda-electron", "fai")
    b.op("alita/work-hub", "close", task_id=tid)
    assert b.fonda.chiamate == [] and _righe(b)[0]["closure_state"] == "closed"


def test_session_end_chiude_solo_le_finite_poi_orfani(b):
    b.onda("kai/fine", "idle")
    b.op("alita/work-hub", "delega.spawn", result=_spawn_res(b, "kai/fine", repo="/finto/r"), input={})
    tid = _delega(b, "alita/work-hub", "kai/fine", "fai")
    b.op("kai/fine", "hook.end")
    assert _righe(b)[0]["closure_state"] == "open" and b.fonda.metodi("worktree.retire") == []
    assert b.fonda.metodi("worktree.list")[0]["repoRoot"] == "/finto/r"  # rete orfani del suo repo
    b.op("kai/fine", "send", to="alita/work-hub", intent="re", summary="fatto", message_type="response",
         task_id=tid)
    b.c.store.read()
    with b.c.store.tx() as c:  # attesa chiusa senza passare dal bus (es. da un'altra sessione)
        c.execute("UPDATE waits SET state='closed' WHERE task_id=?", (tid,))
    b.op("kai/fine", "hook.start")
    b.op("kai/fine", "hook.end")
    r = _righe(b)[0]
    assert r["closure_state"] == "closed" and b.fonda.metodi("agent.close") == []  # sessione già finita


# ---------- rete sugli orfani ----------

def _orbit_db(path: Path, done: list[str], aperti: list[str]):
    c = sqlite3.connect(path)
    c.executescript("CREATE TABLE plans (id INTEGER PRIMARY KEY, code TEXT, status TEXT);"
                    "CREATE TABLE plan_phases (id INTEGER PRIMARY KEY, plan_id INT, key TEXT, title TEXT);"
                    "CREATE TABLE tasks (id INTEGER PRIMARY KEY, phase_id INT, step_key TEXT, title TEXT,"
                    " state TEXT, assignee TEXT);"
                    "INSERT INTO plans VALUES (1, 'DC29', 'active'); INSERT INTO plan_phases VALUES (1, 1, 'F3', 'x');")
    for k in done:
        c.execute("INSERT INTO tasks (phase_id, step_key, state) VALUES (1, ?, 'done')", (k,))
    for k in aperti:
        c.execute("INSERT INTO tasks (phase_id, step_key, state) VALUES (1, ?, 'todo')", (k,))
    c.commit()
    c.close()


def test_rete_orfani(b, tmp_path):
    _orbit_db(tmp_path / "orbit.db", done=["F3.5"], aperti=["F3.6"])
    b.c.orbit = palla.Orbit(tmp_path / "orbit.db")
    repo = "/finto/repo"
    # una delega aperta (attesa aperta, passo non done) e una con l'attesa chiusa
    b.op("alita/work-hub", "delega.spawn", result=_spawn_res(b, "kai/aperta", repo=repo), input={})
    _delega(b, "alita/work-hub", "kai/aperta", "DC29-F3.6 aperta")
    b.op("alita/work-hub", "delega.spawn", result=_spawn_res(b, "kai/finita", repo=repo), input={})
    t2 = _delega(b, "alita/work-hub", "kai/finita", "DC29-F3.7 finita")
    with b.c.store.tx() as c:
        c.execute("UPDATE waits SET state='closed' WHERE task_id=?", (t2,))
    p = lambda s: str(b.cwd(f"kai/{s}"))  # noqa: E731
    b.fonda.lista = [
        {"path": repo, "branch": "main", "clean": True, "live": False},
        {"path": p("viva"), "branch": "feat/viva", "live": True, "liveReason": "pty"},
        {"path": p("aperta"), "branch": "feat/aperta", "live": False},
        {"path": p("finita"), "branch": "feat/finita", "live": False},
        {"path": "/finto/repo-feat-ref-done", "branch": "feat/a", "live": False, "labels": {"ref": "DC29-F3.5"}},
        {"path": "/finto/repo-feat-ref-aperto", "branch": "feat/b", "live": False, "labels": {"ref": "DC29-F3.6"}},
        {"path": "/finto/repo-feat-integrato", "branch": "feat/c", "live": False,
         "integratedInto": {"base": "main", "by": "tree"}},
        {"path": "/finto/repo-feat-nessuno", "branch": "feat/d", "live": False, "integratedInto": None},
    ]
    out = b.op("alita/work-hub", "delega.orfani", repo_root=repo, attendi_s=0)
    ritirati = [x["path"] for x in b.fonda.metodi("worktree.retire")]
    assert ritirati == [p("finita"), "/finto/repo-feat-ref-done", "/finto/repo-feat-integrato"]
    assert {x["path"] for x in out["tenuti"]} == {p("viva"), p("aperta"), "/finto/repo-feat-ref-aperto"}
    assert [x["path"] for x in out["segnalati"]] == ["/finto/repo-feat-nessuno"]
    assert b.fonda.metodi("agent.close") == []  # la rete non chiude sessioni: lì non ce n'è nessuna viva


def test_orfani_onda_giu(b):
    b.fonda.giu = True
    out = b.op("alita/work-hub", "delega.orfani", repo_root="/finto/repo")
    assert "errore" in out and out["chiusi"] == []


def test_spawn_rifiutato_non_si_registra(b):
    out = b.op("alita/work-hub", "delega.spawn", result={"errorCode": "worktree_cap", "worktrees": []}, input={})
    assert out == {"registrato": False, "motivo": "worktree_cap"} and _righe(b) == []


def test_spawn_riusato_non_duplica(b):
    for _ in range(2):
        b.op("alita/work-hub", "delega.spawn", result=_spawn_res(b, "kai/riuso"), input={})
    assert len(_righe(b)) == 1


# ---------- AB_HOME di prova: Onda e Orbit veri mai ----------

def test_home_di_prova_non_tocca_onda_ne_orbit(tmp_path, monkeypatch):
    monkeypatch.delenv("AB_DELEGHE_ESTERNI", raising=False)
    banco = Banco(tmp_path / "bus")
    try:
        d = Deleghe(banco.c)
        assert isinstance(d.onda_call, _Spento) and isinstance(d.orbit, _Spento)
        with pytest.raises(EsternoGiu):
            d.orbit.chiama("delega_set", {})
    finally:
        banco.chiudi()


# ---------- Onda finta su socket Unix ----------

def test_onda_su_socket_fittizio(b, monkeypatch):
    from centralino import canale
    sock = Path(tempfile.mkdtemp(prefix="od-", dir="/tmp")) / "onda.sock"
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(str(sock))
    srv.listen(8)
    visti = []

    def servi():
        while True:
            try:
                conn, _ = srv.accept()
            except OSError:
                return
            with conn:
                req = json.loads(conn.makefile().readline())
                visti.append(req["method"])
                res = {"closed": True} if req["method"] == "agent.close" else {
                    "retired": True, "integratedBy": "ancestor", "tag": "archivio/feat-s"}
                conn.sendall((json.dumps({"jsonrpc": "2.0", "id": req["id"], "result": res}) + "\n").encode())
    threading.Thread(target=servi, daemon=True).start()
    monkeypatch.setenv("ONDA_SOCKET", str(sock))
    b.c.deleghe.onda_call = canale.onda_call
    tid = _pronta(b, "kai/sock")
    b.op("alita/work-hub", "close", task_id=tid)
    srv.close()
    assert visti == ["agent.close", "worktree.retire"]
    assert _righe(b)[0]["closure_detail"].endswith("merge riconosciuto, tag archivio/feat-s")


# ---------- Orbit finto su HTTP (streamable HTTP con sessione e SSE) ----------

def test_orbit_mcp_http_fittizio():
    visti: list[dict] = []

    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_DELETE(self):
            visti.append({"delete": self.headers.get("mcp-session-id")})
            self.send_response(200)
            self.end_headers()

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            visti.append({"method": body.get("method"), "auth": self.headers.get("Authorization"),
                          "sid": self.headers.get("mcp-session-id")})
            if body.get("method") == "notifications/initialized":
                self.send_response(202)
                self.end_headers()
                return
            if body["method"] == "initialize":
                res = {"protocolVersion": "2025-03-26", "capabilities": {}, "serverInfo": {"name": "orbit"}}
            else:
                args = body["params"]["arguments"]
                res = {"content": [{"type": "text", "text": json.dumps({"id": 7, **args})}]}
            data = f"event: message\ndata: {json.dumps({'jsonrpc': '2.0', 'id': body['id'], 'result': res})}\n\n"
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            if body["method"] == "initialize":
                self.send_header("mcp-session-id", "s-1")
            self.end_headers()
            self.wfile.write(data.encode())

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        o = OrbitMcp(url=f"http://127.0.0.1:{srv.server_port}/mcp",
                     headers=lambda: {"Authorization": "Bearer finto"})
        out = o.chiama("delega_set", {"step_ref": "DC29-F4.1", "closure_state": "closed"})
        assert out == {"id": 7, "step_ref": "DC29-F4.1", "closure_state": "closed"}
        assert [v.get("method") for v in visti[:3]] == ["initialize", "notifications/initialized", "tools/call"]
        assert all(v.get("auth") == "Bearer finto" for v in visti[:3]) and visti[2]["sid"] == "s-1"
        assert visti[3] == {"delete": "s-1"}
    finally:
        srv.shutdown()
    with pytest.raises(EsternoGiu):
        OrbitMcp(url=f"http://127.0.0.1:{srv.server_port}/mcp", headers=lambda: {}, timeout=0.5).chiama("x", {})


def test_header_orbit_mai_in_errore(monkeypatch):
    from centralino import deleghe
    monkeypatch.setenv("AB_ORBIT_HEADERS_CMD", "/bin/sh -c 'echo SEGRETO >&2; exit 3'")
    with pytest.raises(EsternoGiu) as e:
        deleghe.headers_orbit()
    assert "SEGRETO" not in str(e.value)


# ---------- hook ----------

def test_hook_risultato_formati():
    import importlib.util
    spec = importlib.util.spec_from_file_location("spawn_hook", HOOK)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    r = {"terminalId": "t1", "worktree": {"path": "/x"}}
    assert m.risultato(r) == r
    assert m.risultato([{"type": "text", "text": json.dumps(r)}]) == r
    assert m.risultato({"content": [{"type": "text", "text": "log"}, {"type": "text", "text": json.dumps(r)}]}) == r
    assert m.risultato({"structuredContent": r, "content": []}) == r
    assert m.risultato("non json") is None


def test_hook_mai_bloccante_senza_centralino(tmp_path):
    import subprocess
    env = {**os.environ, "AB_HOME": str(tmp_path)}  # nessun socket: il centralino è giù
    for fase, ev in (("pre", {"tool_input": {"worktree": {"repoRoot": "/r", "branch": "b"}}}),
                     ("post", {"tool_input": {}, "tool_response": [{"type": "text", "text": "{\"terminalId\": \"t\"}"}]})):
        p = subprocess.run(["/usr/bin/python3", "-I", str(HOOK), fase], input=json.dumps(ev), text=True,
                           capture_output=True, env=env, timeout=10)
        assert p.returncode == 0 and p.stdout == ""


# ---------- nomi veri dei contratti (Onda develop 3c7c404, Orbit main 4dff82d) ----------

def test_retire_manda_path_anche_senza_workspace_id(b):
    b.onda("kai/senzaws", "idle")
    res = _spawn_res(b, "kai/senzaws")
    del res["workspaceId"]
    b.op("alita/work-hub", "delega.spawn", result=res, input={})
    tid = _delega(b, "alita/work-hub", "kai/senzaws", "fai")
    b.op("alita/work-hub", "close", task_id=tid)
    p = b.fonda.metodi("worktree.retire")[0]
    assert p["path"] == str(b.cwd("kai/senzaws")) and "workspaceId" not in p and "worktreePath" not in p
    assert _righe(b)[0]["closure_state"] == "closed"


def test_closure_at_in_epoch_ms(b):
    from centralino import tempo
    tid = _pronta(b)
    b.op("alita/work-hub", "close", task_id=tid)
    s = b.forbit.scritte[-1]
    assert s["closure_state"] == "closed" and s["closure_at"] == int(b.t.timestamp() * 1000)
    assert _righe(b)[0]["closure_at"] == tempo.iso(b.t)  # nel centralino resta ISO
    assert "closure_at" not in b.forbit.scritte[0]  # delega aperta: niente closure_at
