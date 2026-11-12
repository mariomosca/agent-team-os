"""CM02-F3.3c: le deleghe di DC29 senza centralino. Onda e Orbit finti, agent-map e sessions.json di Onda
dal Banco (AB_HOME temporaneo), SQLite delle deleghe temporaneo, nessun daemon, nessun repo vero."""

import io
import json
import os

import pytest

from centralino import deleghe_crewmoor as dc
from centralino.deleghe_crewmoor import Centrale, DelegheCrewmoor, Store, gestisci
from centralino.deleghe import EsternoGiu
from centralino.sim import Banco

from test_deleghe import FintaOnda

SEND = "mcp__plugin_crewmoor_crewmoor__crewmoor_send"
CLOSE = "mcp__plugin_crewmoor_crewmoor__crewmoor_close"


class FintoOrbit:
    def __init__(self):
        self.scritte: list[dict] = []
        self.done: set[str] = set()
        self.giu = False

    def chiama(self, tool, args):
        if self.giu:
            raise EsternoGiu("Orbit non risponde")
        if tool == "plan_get":
            code = args["plan_id"]
            steps = [{"key": k.split("-", 1)[1], "state": "done"} for k in self.done if k.startswith(code + "-")]
            return {"code": code, "phases": [{"key": "F1", "steps": steps}]}
        assert tool == "delega_set", tool
        self.scritte.append(args)
        return {"ok": True}


@pytest.fixture
def k(tmp_path, monkeypatch):
    banco = Banco(tmp_path / "bus")
    monkeypatch.setenv("AB_RETIRE_ATTESA_S", "0")
    for v in ("CLAUDE_PROJECT_DIR", "CREWMOOR_AGENT", "AB_AGENT", "CREWMOOR_SESSION_SLUG", "AB_SESSION_SLUG",
              "ONDA_TERMINAL_ID"):
        monkeypatch.delenv(v, raising=False)
    central = Centrale(home=banco.home, store=Store(tmp_path / "dc" / "deleghe.db"))
    central.log_path = tmp_path / "dc" / "deleghe.log"
    onda, orbit = FintaOnda(), FintoOrbit()
    banco.fonda, banco.forbit = onda, orbit
    banco.central = central

    def nuovo():
        return DelegheCrewmoor(central, onda_call=onda, orbit=orbit)
    banco.dc = nuovo
    yield banco
    banco.chiudi()


def _ev(k, addr, **campi):
    return {"cwd": str(k.cwd(addr)), "session_id": f"claude-{addr.replace('/', '-')}", **campi}


def _spawn(k, frm, addr, repo="/finto/repo"):
    agent, _, slug = addr.partition("/")
    path = str(k.cwd(addr))
    res = {"terminalId": f"pane-{agent}-{slug}", "sessionId": f"sess-{agent}-{slug}", "workspaceId": f"ws-{slug}",
           "cwd": path, "worktree": {"path": path, "repoRoot": repo, "branch": f"feat/{slug}"}}
    ev = _ev(k, frm, tool_name="mcp__onda__onda_agent_spawn", tool_input={"workspaceRootPath": path},
             tool_response={"content": [{"type": "text", "text": json.dumps(res)}]})
    return gestisci("spawn", ev, k.dc())


def _delega(k, frm, to, task_id, step_ref=None, mode="delegation"):
    inp = {"to": to, "kind": "brief", "mode": mode, "title": "fai il lavoro", "text": "testo"}
    if step_ref:
        inp["stepRef"] = step_ref
    res = {"messageId": "msg_1", "taskId": task_id if mode == "delegation" else None,
           "to": {"agentId": "agt_1", "name": to.lstrip("@").split("/")[0].split("@")[0]}, "route": "session", "created": True}
    ev = _ev(k, frm, tool_name=SEND, tool_input=inp, tool_response={"structuredContent": res})
    return gestisci("delega", ev, k.dc())


def _chiudi(k, frm, task_id, stato="closed"):
    ev = _ev(k, frm, tool_name=CLOSE, tool_input={"taskId": task_id, "action": "closed"},
             tool_response={"structuredContent": {"taskId": task_id, "waitState": stato, "changed": True}})
    return gestisci("chiusa", ev, k.dc())


def _righe(k):
    return [dict(r) for r in k.central.store.read().execute("SELECT * FROM deleghe ORDER BY id")]


def test_spawn_poi_delega_si_legano_e_orbit_sa(k):
    k.onda("kai/cm02-x", "idle")
    out = _spawn(k, "alita/work-hub", "kai/cm02-x")
    assert out["registrato"]
    out = _delega(k, "alita/work-hub", "@kai/cm02-x", "t-20261111-aaaaaa", step_ref="CM02-F3.3c")
    assert out["registrato"] and out["legate"] == ["t-20261111-aaaaaa"]
    [r] = _righe(k)
    assert (r["task_id"], r["terminal_id"], r["delegator"]) == ("t-20261111-aaaaaa", "pane-kai-cm02-x",
                                                                "alita/work-hub")
    assert k.forbit.scritte[-1]["bus_task_id"] == "t-20261111-aaaaaa"
    assert k.forbit.scritte[-1]["step_ref"] == "CM02-F3.3c"


def test_delega_poi_spawn_si_legano(k):
    k.onda("kai/cm02-y", "idle")
    _delega(k, "alita/work-hub", "@kai@laptop/cm02-y", "t-20261111-bbbbbb")
    out = _spawn(k, "alita/work-hub", "kai/cm02-y")
    assert out["task_id"] == "t-20261111-bbbbbb"


def test_chiusura_dell_attesa_chiude_sessione_e_worktree(k):
    k.onda("kai/cm02-x", "idle")
    _spawn(k, "alita/work-hub", "kai/cm02-x")
    _delega(k, "alita/work-hub", "@kai/cm02-x", "t-20261111-cccccc", step_ref="CM02-F3.3c")
    k.avanza(10)
    out = _chiudi(k, "alita/work-hub", "t-20261111-cccccc")
    assert out["chiusa"]
    assert [p["terminalId"] for p in k.fonda.metodi("agent.close")] == ["pane-kai-cm02-x"]
    assert len(k.fonda.metodi("worktree.retire")) == 1
    [r] = _righe(k)
    assert (r["closure_state"], r["closure_reason"]) == ("closed", "retired")
    assert k.forbit.scritte[-1]["closure_state"] == "closed"
    _chiudi(k, "alita/work-hub", "t-20261111-cccccc")  # idempotente
    assert len(k.fonda.metodi("worktree.retire")) == 1


def test_comunicazione_e_riarma_non_toccano_niente(k):
    assert not _delega(k, "alita/work-hub", "@kai/cm02-x", "t-x", mode="notice")["registrato"]
    assert _righe(k) == []
    k.onda("kai/cm02-x", "idle")
    _spawn(k, "alita/work-hub", "kai/cm02-x")
    _delega(k, "alita/work-hub", "@kai/cm02-x", "t-20261111-dddddd")
    assert not _chiudi(k, "alita/work-hub", "t-20261111-dddddd", stato="open")["chiusa"]
    assert k.fonda.metodi("agent.close") == []


def test_altri_strumenti_si_saltano(k):
    ev = _ev(k, "alita/work-hub", tool_name="mcp__agent-bus__bus_send", tool_input={}, tool_response={})
    assert "saltato" in gestisci("delega", ev, k.dc())


def test_fine_sessione_chiude_le_finite_per_passo_done(k):
    k.onda("kai/cm02-z", "idle")
    _spawn(k, "alita/work-hub", "kai/cm02-z")
    _delega(k, "alita/work-hub", "@kai/cm02-z", "t-20261111-eeeeee", step_ref="CM02-F2.13")
    k.forbit.done.add("CM02-F2.13")
    k.avanza(10)
    out = gestisci("fine", _ev(k, "kai/cm02-z"), k.dc())
    assert out["sessione"] == "kai/cm02-z"
    [r] = _righe(k)
    assert r["closure_state"] == "closed" and r["session_closed"] == 1


def test_orbit_giu_non_ferma_niente(k):
    k.forbit.giu = True
    k.onda("kai/cm02-x", "idle")
    _spawn(k, "alita/work-hub", "kai/cm02-x")
    out = _delega(k, "alita/work-hub", "@kai/cm02-x", "t-20261111-ffffff", step_ref="CM02-F3.3c")
    assert out["registrato"]
    [r] = _righe(k)
    assert r["orbit_dirty"] == 1  # si riscrive al prossimo evento
    k.forbit.giu = False
    gestisci("giro", {}, k.dc())
    assert _righe(k)[0]["orbit_dirty"] == 0


def test_orbit_url_dal_mcp_json(tmp_path, monkeypatch):
    monkeypatch.delenv("AB_ORBIT_MCP_URL", raising=False)
    (tmp_path / "ws" / "sotto").mkdir(parents=True)
    (tmp_path / "ws" / ".mcp.json").write_text(json.dumps(
        {"mcpServers": {"orbit": {"type": "http", "url": "https://orbit.esempio/mcp"}}}))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "vuota"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    assert dc.orbit_url(str(tmp_path / "ws" / "sotto")) == "https://orbit.esempio/mcp"
    monkeypatch.setenv("AB_ORBIT_MCP_URL", "https://altro/mcp")
    assert dc.orbit_url(None) == "https://altro/mcp"


def test_main_non_fallisce_mai(tmp_path, monkeypatch):
    monkeypatch.setenv("DELEGHE_CREWMOOR_DB", str(tmp_path / "d.db"))
    monkeypatch.setenv("AB_HOME", str(tmp_path / "bus-vuoto"))
    monkeypatch.setattr("sys.stdin", io.StringIO("non è json"))
    assert dc.main(["deleghe-crewmoor", "delega"]) == 0
    log = (tmp_path / "deleghe.log").read_text()
    assert "deleghe_errore" in log
    monkeypatch.setattr("sys.stdin", io.StringIO("{}"))
    assert dc.main(["deleghe-crewmoor", "evento-strano"]) == 0
