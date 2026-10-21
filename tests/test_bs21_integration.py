"""BS21 end to end: agent-bus-mcp (Bus) e la sveglia sugli stessi file.

    AB_BUS_MCP_SRC=~/Projects/07-Tooling/mcp/agent-bus-mcp/src python3 -I tests/test_bs21_integration.py

Due sessioni finte (alita/work-hub delega, kai/f3 esegue) su un AB_HOME temporaneo
e un Onda finto: delega dichiarata, ritorno scritto come messaggio nuovo, sveglia,
risposta che sveglia l'esecutore, botta e risposta a vuoto fermato dal tetto.
Senza il sorgente di agent-bus-mcp il test si salta.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = Path(os.environ.get("AB_BUS_MCP_SRC") or Path.home() / "Projects/07-Tooling/mcp/agent-bus-mcp/src").expanduser()


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


wake = _load("wake", ROOT / "scripts" / "agent-team-os-wake.py")
FakeOnda = _load("test_wake_helpers", ROOT / "tests" / "test_wake.py").FakeOnda

try:
    sys.path.insert(0, str(SRC))
    from agent_bus_mcp.bus import Bus  # type: ignore
except Exception:  # agent-bus-mcp non c'e' su questa macchina
    Bus = None


def iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@unittest.skipIf(Bus is None, f"agent-bus-mcp non trovato in {SRC}")
class Bs21Integration(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="abbs21.")
        self.home = Path(self.tmp.name)
        os.environ["AB_HOME"] = str(self.home)
        os.environ.pop("AB_HOST", None)
        (self.home / "AGENT_MAP.json").write_text(json.dumps({"agents": {"alita": {}, "kai": {}, "nico": {}},
                                                               "rules": []}))
        self.sock = f"/tmp/abi-{os.getpid()}.sock"
        if os.path.exists(self.sock):
            os.unlink(self.sock)
        os.environ["ONDA_SOCKET"] = self.sock
        self.onda = FakeOnda(self.sock)
        wake.SETTLE_S = 0
        wake.REPORT_HOUR = 99  # niente report del lunedi' nei giri dei test
        (self.home / "onda").mkdir()
        sess = lambda agent, slug: {"sessionId": f"s-{agent}", "terminalId": f"pane-{agent}", "agent": agent,
                                    "slug": slug, "state": "idle", "hasText": False, "stateSince": "2026-01-01T00:00:00Z"}
        (self.home / "onda" / "sessions.json").write_text(json.dumps(
            {"v": 1, "writtenAt": iso(), "ondaPid": os.getpid(),
             "sessions": [sess("alita", "work-hub"), sess("kai", "f3"), sess("nico", "app-x")]}))
        self.bus = Bus(home=self.home)

    def tearDown(self) -> None:
        self.onda.close()
        if os.path.exists(self.sock):
            os.unlink(self.sock)
        self.tmp.cleanup()

    def woke(self) -> list[str]:
        return [c["params"]["terminalId"] for c in self.onda.calls]

    def test_delegation_return_answer_and_close(self):
        d = self.bus.send(sender="alita", to="kai/f3", msg_type="request", intent="spike",
                          payload={"summary": "fai lo spike"}, sender_session="work-hub", kind="delega",
                          target={"onda_terminal_id": "pane-alita"})
        tid = d["task_id"]
        self.assertEqual(wake.tick(), {"delegation": [tid]})  # la delega sveglia kai, fermo
        self.assertEqual(self.woke(), ["pane-kai"])
        self.bus.handle("kai", "f3", d["id"], "letto")

        # kai risponde con un messaggio nuovo, senza task_id ne' in_reply_to: si aggancia
        q = self.bus.send(sender="kai", to="alita/work-hub", msg_type="question", intent="domanda",
                          payload={"summary": "branch nuovo?"}, sender_session="f3")
        out = wake.tick()
        self.assertEqual(out["linked"], [f"{q['id']}>{tid}"])
        self.assertEqual(self.woke(), ["pane-kai", "pane-alita"])

        # alita risponde con bus_gestisci risposto: sveglia kai, l'attesa resta aperta
        h = self.bus.handle("alita", "work-hub", q["id"], "risposto", reply="sì, feat/x")
        self.assertEqual(h["wait"]["state"], "expected")
        self.assertEqual(wake.tick(), {"woken": [tid]})
        self.assertEqual(self.woke(), ["pane-kai", "pane-alita", "pane-kai"])
        kai_msg = self.bus.read_message("kai", h["sent"]["id"])
        self.bus.handle("kai", "f3", kai_msg["id"], "letto")

        # un aggiornamento intermedio sveglia, alita riarma: nessun messaggio a kai
        u = self.bus.send(sender="kai", to="alita/work-hub", msg_type="event", intent="avanzamento",
                          payload={"summary": "a meta'"}, sender_session="f3", task_id=tid)
        wake.tick()
        self.assertEqual(self.woke()[-1], "pane-alita")
        n_kai = len(list((self.home / "inboxes" / "kai").rglob("msg-*.json")))
        self.bus.handle("alita", "work-hub", u["id"], "riarma")
        self.assertEqual(len(list((self.home / "inboxes" / "kai").rglob("msg-*.json"))), n_kai)

        # l'esito: sveglia, alita chiude; un ritorno dopo la chiusura non sveglia
        r = self.bus.send(sender="kai", to="alita/work-hub", msg_type="response", intent="esito",
                          payload={"summary": "fatto"}, sender_session="f3", in_reply_to=d["id"])
        self.assertEqual(r["task_id"], tid)
        wake.tick()
        calls = len(self.onda.calls)
        self.bus.handle("alita", "work-hub", r["id"], "chiuso")
        self.bus.send(sender="kai", to="alita/work-hub", msg_type="event", intent="ps",
                      payload={"summary": "ps"}, sender_session="f3", task_id=tid)
        self.assertEqual(wake.tick(), {})
        self.assertEqual(len(self.onda.calls), calls)

    def test_comunicazione_wakes_nobody(self):
        self.bus.send(sender="nico", to="alita/work-hub", msg_type="brief", intent="nota",
                      payload={"summary": "per conoscenza"}, sender_session="app-x", kind="comunicazione")
        self.assertEqual(wake.tick(), {})
        self.assertEqual(self.onda.calls, [])

    def test_ping_pong_on_empty_is_stopped_by_the_cap(self):
        d = self.bus.send(sender="alita", to="kai/f3", msg_type="request", intent="x",
                          payload={"summary": "x"}, sender_session="work-hub", kind="delega",
                          target={"onda_terminal_id": "pane-alita"})
        tid = d["task_id"]
        for i in range(15):
            m = self.bus.send(sender="kai", to="alita/work-hub", msg_type="question", intent="q",
                              payload={"summary": f"ok {i}?"}, sender_session="f3", task_id=tid)
            wake.tick()
            self.bus.handle("alita", "work-hub", m["id"], "risposto", reply="ok")
            wake.tick()
            for f in (self.home / "inboxes" / "kai" / "@f3").glob("msg-*.json"):
                if json.loads(f.read_text()).get("type") == "response":
                    self.bus.handle("kai", "f3", f.stem, "letto")
        self.assertEqual(len(self.onda.calls), wake.TASK_MAX_PER_HOUR)  # 15 giri, 4 sveglie
        w = json.loads((self.home / "waits" / "alita" / "@work-hub" / f"{tid}.json").read_text())
        self.assertEqual(w["state"], "expected")
        held = [e for e in w["returns"].values() if (e.get("pending_wake") or {}).get("reason") == "tetto_task"]
        self.assertEqual(held, [])  # gestite a mano: nessuna resta appesa
        self.assertEqual(len(wake._stamps(wake.task_log(tid))), len(self.onda.calls))


if __name__ == "__main__":
    unittest.main(verbosity=1)
