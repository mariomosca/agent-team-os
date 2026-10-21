"""Test della sveglia (bus v2.6): python3 -I tests/test_wake.py

Un AB_HOME temporaneo, un Onda finto (socket unix che registra le chiamate
agent.prompt e risponde come gli si dice) e un tmux finto (tests/fake-bin/tmux).
Niente Onda vero, niente sessioni vere.
"""

from __future__ import annotations

import importlib.util
import json
import os
import socket
import sys
import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("wake", ROOT / "scripts" / "agent-team-os-wake.py")
wake = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wake)


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class FakeOnda:
    """Socket JSON-RPC: registra le richieste, risponde con self.reply."""

    def __init__(self, path: str) -> None:
        self.path = path
        self.calls: list[dict] = []
        self.reply: dict = {"success": True, "submitted": True}
        self.srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.srv.bind(path)
        self.srv.listen(4)
        self.alive = True
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self) -> None:
        while self.alive:
            try:
                conn, _ = self.srv.accept()
            except OSError:
                return
            with conn:
                buf = b""
                while b"\n" not in buf:
                    chunk = conn.recv(65536)
                    if not chunk:
                        break
                    buf += chunk
                if not buf:
                    continue
                req = json.loads(buf.split(b"\n")[0])
                self.calls.append(req)
                conn.sendall((json.dumps({"jsonrpc": "2.0", "id": req["id"], "result": self.reply}) + "\n").encode())

    def close(self) -> None:
        self.alive = False
        self.srv.close()


class WakeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="abwake.")
        self.home = Path(self.tmp.name)
        os.environ["AB_HOME"] = str(self.home)
        os.environ.pop("AB_HOST", None)
        (self.home / "AGENT_MAP.json").write_text(json.dumps({"agents": {"alita": {}, "kai": {}}, "rules": []}))
        # Socket corto: il path dei socket unix ha un limite di ~104 byte.
        self.sock = f"/tmp/abw-{os.getpid()}-{id(self) % 10000}.sock"
        if os.path.exists(self.sock):
            os.unlink(self.sock)
        os.environ["ONDA_SOCKET"] = self.sock
        self.onda = FakeOnda(self.sock)
        os.environ["AB_TMUX_BIN"] = str(ROOT / "tests" / "fake-bin" / "tmux")
        os.environ["FAKE_TMUX_DIR"] = str(self.home / "tmux")
        (self.home / "tmux").mkdir()
        wake.SETTLE_S = 0
        self.sessions([])

    def tearDown(self) -> None:
        self.onda.close()
        if os.path.exists(self.sock):
            os.unlink(self.sock)
        self.tmp.cleanup()

    # -- helpers

    def sessions(self, sessions: list[dict], pid: int | None = None) -> None:
        d = self.home / "onda"
        d.mkdir(exist_ok=True)
        doc = {"v": 1, "writtenAt": iso(datetime.now(timezone.utc)), "ondaPid": pid or os.getpid(),
               "sessions": sessions}
        (d / "sessions.json").write_text(json.dumps(doc))

    def alita(self, state="idle", has_text=False, guard=True, since_s=60, term="pane-alita") -> dict:
        s = {"sessionId": "sess-alita", "terminalId": term, "workspaceId": "ws", "agent": "alita",
             "slug": "work-hub", "cwd": "/w/work-hub", "state": state,
             "stateSince": iso(datetime.now(timezone.utc) - timedelta(seconds=since_s)),
             "mounted": True, "host": "laptop"}
        if guard:
            s["hasText"] = has_text
        return s

    def expect(self, task="t-20261017-abc123", **kw) -> dict:
        target = kw.pop("target", {"onda_terminal_id": "pane-alita"})
        return wake.expect("alita", "work-hub", kw.pop("description", "spike orbit"),
                           kw.pop("to", "kai/f3"), task_id=task, target=target, **kw)

    def deliver(self, task: str | None = "t-20261017-abc123", where="", **extra) -> str:
        n = len(list(self.home.rglob("msg-*.json")))
        mid = f"msg-20261017T104400Z-x{n:07d}"
        d = self.home / "inboxes" / "alita" / "@work-hub"
        if where:
            d = d / where
        d.mkdir(parents=True, exist_ok=True)
        msg = {"id": mid, "from": "kai", "from_session": "kai/f3", "to": "alita", "type": "response",
               "intent": "esito", "ts": iso(datetime.now(timezone.utc)), "payload": {"summary": "fatto"}}
        if task:
            msg["task_id"] = task
        msg.update(extra)
        (d / f"{mid}.json").write_text(json.dumps(msg))
        return mid

    def wait_state(self, task="t-20261017-abc123") -> dict:
        return json.loads((self.home / "waits" / "alita" / "@work-hub" / f"{task}.json").read_text())

    # -- registro

    def test_expect_one_waiter_per_task(self):
        self.expect()
        with self.assertRaises(ValueError):
            wake.expect("kai", "f3", "stesso task", task_id="t-20261017-abc123")

    def test_expect_generates_id(self):
        rec = wake.expect("alita", "work-hub", "x")
        self.assertRegex(rec["task_id"], r"^t-\d{8}-[a-z0-9]{6}$")

    # -- sveglia via Onda

    def test_every_return_wakes_and_the_wait_stays_open(self):
        self.sessions([self.alita()])
        self.expect()
        mid = self.deliver()
        self.assertEqual(wake.tick(), {"woken": ["t-20261017-abc123"]})
        self.assertEqual(len(self.onda.calls), 1)
        p = self.onda.calls[0]["params"]
        self.assertEqual(self.onda.calls[0]["method"], "agent.prompt")
        self.assertTrue(p["onlyIfIdle"])
        self.assertEqual(p["terminalId"], "pane-alita")
        self.assertIn("t-20261017-abc123", p["prompt"])
        self.assertIn(f"bus_read {mid}", p["prompt"])
        self.assertIn("kai/f3", p["prompt"])
        self.assertIn("bus_gestisci", p["prompt"])
        self.assertNotIn("\n", p["prompt"])
        st = self.wait_state()
        self.assertEqual(st["state"], "expected")
        self.assertEqual(st["returns"][mid]["state"], "woken")
        self.assertEqual(st["returns"][mid]["woken_via"], "onda")
        self.assertEqual(st["executor_session"], "kai/f3")
        # lo stesso messaggio non sveglia due volte
        self.assertEqual(wake.tick(), {})
        # un secondo ritorno sullo stesso task sveglia di nuovo (IK18: 7 aggiornamenti, 0 sveglie)
        mid2 = self.deliver()
        self.assertEqual(wake.tick(), {"woken": ["t-20261017-abc123"]})
        self.assertEqual(len(self.onda.calls), 2)
        self.assertIn(f"bus_read {mid2}", self.onda.calls[1]["params"]["prompt"])

    def test_message_from_someone_else_does_not_wake(self):
        self.sessions([self.alita()])
        self.expect()
        self.deliver(task=None, **{"from": "vera", "from_session": "vera/growth"})
        self.deliver(task="t-altro-task")
        self.assertEqual(wake.tick(), {})
        self.assertEqual(self.onda.calls, [])

    def test_no_wait_no_wake(self):
        self.sessions([self.alita()])
        self.deliver()
        self.assertEqual(wake.tick(), {})
        self.assertEqual(self.onda.calls, [])

    # -- domande nel thread della delega (bus v2.7, DR18-F1.7)

    def question(self, **extra) -> str:
        return self.deliver(type="question", intent="domanda", thread_id="thread-20261017-q1",
                            payload={"summary": "uso il tema scuro?"}, **extra)

    def test_question_wakes_without_closing_wait(self):
        self.sessions([self.alita()])
        self.expect()
        qid = self.question()
        self.assertEqual(wake.tick(), {"woken": ["t-20261017-abc123"]})
        self.assertEqual(len(self.onda.calls), 1)
        prompt = self.onda.calls[0]["params"]["prompt"]
        self.assertIn(f"bus_read {qid}", prompt)
        self.assertIn("omanda", prompt)
        self.assertNotIn("\n", prompt)
        st = self.wait_state()
        self.assertEqual(st["state"], "expected")
        self.assertEqual(st["returns"][qid]["state"], "woken")
        self.assertEqual(st["returns"][qid]["type"], "question")
        self.assertEqual(st["returns"][qid]["summary"], "uso il tema scuro?")
        self.assertEqual(wake.tick(), {})
        rid = self.deliver()
        self.assertEqual(wake.tick(), {"woken": ["t-20261017-abc123"]})
        self.assertEqual(len(self.onda.calls), 2)
        self.assertIn(f"bus_read {rid}", self.onda.calls[1]["params"]["prompt"])

    def test_two_messages_one_wake(self):
        self.sessions([self.alita()])
        self.expect()
        q1, q2 = self.question(), self.question()
        self.assertEqual(wake.tick(), {"woken": ["t-20261017-abc123"]})
        self.assertEqual(len(self.onda.calls), 1)
        prompt = self.onda.calls[0]["params"]["prompt"]
        self.assertIn("2 messaggi", prompt)
        self.assertIn(q1, prompt)
        self.assertIn(q2, prompt)
        self.assertEqual(wake.tick(), {})

    def test_question_busy_postponed_then_seen(self):
        self.sessions([self.alita(state="working")])
        self.expect()
        qid = self.question()
        self.assertEqual(wake.tick(), {"postponed": ["t-20261017-abc123"]})
        q = self.wait_state()["returns"][qid]
        self.assertEqual(q["state"], "pending")
        self.assertEqual(q["pending_wake"]["reason"], "onda:working")
        # la sessione l'ha letta da sola mentre lavorava: vista, niente sveglia
        src = self.home / "inboxes" / "alita" / "@work-hub" / f"{qid}.json"
        dst = src.parent / ".read" / src.name
        dst.parent.mkdir(exist_ok=True)
        src.rename(dst)
        self.sessions([self.alita()])
        self.assertEqual(wake.tick(), {"seen": ["t-20261017-abc123"]})
        self.assertEqual(self.onda.calls, [])
        q = self.wait_state()["returns"][qid]
        self.assertEqual(q["state"], "seen")
        self.assertNotIn("pending_wake", q)
        self.assertNotIn("pending_wake", self.wait_state())

    def test_question_answered_from_thread(self):
        self.sessions([self.alita(state="working")])
        self.expect()
        qid = self.question()
        wake.tick()
        later = iso(datetime.now(timezone.utc) + timedelta(seconds=5))
        t = self.home / "threads"
        t.mkdir()
        (t / "thread-20261017-q1.jsonl").write_text(
            json.dumps({"id": "msg-r", "from": "alita", "to": "kai", "type": "response", "ts": later}) + "\n")
        self.sessions([self.alita()])
        wake.tick()
        q = self.wait_state()["returns"][qid]
        self.assertEqual(q["state"], "seen")
        self.assertIn("answered_at", q)
        self.assertEqual(self.onda.calls, [])

    def test_question_without_task_id_is_hooked_and_wakes(self):
        self.sessions([self.alita()])
        self.expect()
        qid = self.deliver(task=None, type="question")
        out = wake.tick()
        self.assertEqual(out["woken"], ["t-20261017-abc123"])
        self.assertEqual(out["linked"], [f"{qid}>t-20261017-abc123"])

    def test_wake_chain_no_longer_blocks_a_return(self):
        # v2.8: la risposta di una sessione appena svegliata deve svegliare (F2.3);
        # il botta e risposta a vuoto lo fermano i tetti (F2.4).
        self.sessions([self.alita()])
        self.expect()
        self.question(wake_chain={"task_id": "t-x", "at": iso(datetime.now(timezone.utc))})
        self.assertEqual(wake.tick(), {"woken": ["t-20261017-abc123"]})

    def test_question_respects_hourly_cap(self):
        self.sessions([self.alita()])
        self.expect()
        d = self.home / "waits" / "alita" / "@work-hub"
        (d / ".wakes.log").write_text("\n".join([f"{time.time():.0f}"] * wake.MAX_PER_HOUR) + "\n")
        qid = self.question()
        self.assertEqual(wake.tick(), {"postponed": ["t-20261017-abc123"]})
        self.assertEqual(self.wait_state()["returns"][qid]["pending_wake"]["reason"], "tetto_orario")

    # -- pulizia del registro nel giro (OR18-F3.3)

    def test_tick_prunes_registry_every_interval(self):
        d = self.home / "registry" / "kai.d"
        d.mkdir(parents=True)
        dead = {"name": "kai", "slug": "ripasso", "active": True, "last_seen": "2026-10-18T10:00:00Z", "pid": 999999}
        live = dict(dead, slug="vivo", pid=os.getpid())
        (d / "ripasso.json").write_text(json.dumps(dead))
        (d / "vivo.json").write_text(json.dumps(live))
        (self.home / "registry" / "kai.json").write_text(json.dumps({"name": "kai", "active": True}))
        out = wake.tick()
        self.assertEqual(len(out.get("registro", [])), 1)
        self.assertIn("kai/ripasso (pid 999999 morto)", out["registro"][0])
        self.assertFalse((d / "ripasso.json").exists())
        self.assertTrue((d / "vivo.json").exists())
        stale = list((self.home / "registry").glob(".stale-*/kai.d_ripasso.json"))
        self.assertEqual(len(stale), 1)
        # entro l'intervallo non riparte
        (d / "altro.json").write_text(json.dumps(dict(dead, slug="altro")))
        self.assertNotIn("registro", wake.tick())
        self.assertTrue((d / "altro.json").exists())

    def test_busy_postponed_then_woken(self):
        self.sessions([self.alita(state="working")])
        self.expect()
        self.deliver()
        self.assertEqual(wake.tick(), {"postponed": ["t-20261017-abc123"]})
        self.assertEqual(self.onda.calls, [])
        self.assertEqual(self.wait_state()["pending_wake"]["reason"], "onda:working")
        self.sessions([self.alita(state="idle")])
        self.assertEqual(wake.tick(), {"woken": ["t-20261017-abc123"]})
        self.assertEqual(len(self.onda.calls), 1)
        self.assertNotIn("pending_wake", self.wait_state())

    def test_text_in_box_postponed(self):
        self.sessions([self.alita(has_text=True)])
        self.expect()
        self.deliver()
        self.assertEqual(wake.tick(), {"postponed": ["t-20261017-abc123"]})
        self.assertEqual(self.onda.calls, [])

    def test_pending_input_and_asking_postponed(self):
        self.expect()
        self.deliver()
        for state in ("pending_input", "waiting_permission", "waiting_answer"):
            self.sessions([self.alita(state=state)])
            self.assertEqual(wake.tick(), {"postponed": ["t-20261017-abc123"]})
        self.assertEqual(self.onda.calls, [])

    def test_onda_without_guard_does_not_write(self):
        self.sessions([self.alita(guard=False)])
        self.expect()
        self.deliver()
        self.assertEqual(wake.tick(), {"postponed": ["t-20261017-abc123"]})
        self.assertEqual(self.wait_state()["pending_wake"]["reason"], "onda_senza_guardia")
        self.assertEqual(self.onda.calls, [])

    def test_onda_refusal_postpones(self):
        # Mario comincia a scrivere fra sessions.json e la chiamata: Onda rifiuta.
        self.sessions([self.alita()])
        self.onda.reply = {"success": False, "errorCode": "not_idle", "reason": "has_text", "completedSteps": []}
        self.expect()
        self.deliver()
        self.assertEqual(wake.tick(), {"postponed": ["t-20261017-abc123"]})
        self.assertEqual(self.wait_state()["pending_wake"]["reason"], "onda:has_text")
        self.assertEqual(self.wait_state()["state"], "expected")
        self.onda.reply = {"success": True, "submitted": True}
        self.assertEqual(wake.tick(), {"woken": ["t-20261017-abc123"]})

    def test_settling_session_postponed(self):
        wake.SETTLE_S = 5
        self.sessions([self.alita(since_s=1)])
        self.expect()
        self.deliver()
        self.assertEqual(wake.tick(), {"postponed": ["t-20261017-abc123"]})

    def test_dead_onda_ignored(self):
        self.sessions([self.alita()], pid=999999)
        self.expect()
        self.deliver()
        self.assertEqual(wake.tick(), {"postponed": ["t-20261017-abc123"]})
        self.assertEqual(self.wait_state()["pending_wake"]["reason"], "nessun_canale")

    def test_session_found_by_agent_slug_without_target(self):
        self.sessions([self.alita(term="pane-x")])
        self.expect(target={})
        self.deliver()
        self.assertEqual(wake.tick(), {"woken": ["t-20261017-abc123"]})
        self.assertEqual(self.onda.calls[0]["params"]["terminalId"], "pane-x")

    # -- regole

    def test_already_read_is_seen_not_woken(self):
        self.sessions([self.alita()])
        self.expect()
        self.deliver(where=".done")
        self.assertEqual(wake.tick(), {"seen": ["t-20261017-abc123"]})
        self.assertEqual(self.onda.calls, [])

    def test_own_message_ignored(self):
        self.sessions([self.alita()])
        self.expect()
        self.deliver(from_session="alita/work-hub")
        self.assertEqual(wake.tick(), {})

    def test_hourly_cap(self):
        self.sessions([self.alita()])
        d = self.home / "waits" / "alita" / "@work-hub"
        d.mkdir(parents=True)
        (d / ".wakes.log").write_text("\n".join(str(int(time.time()) - i) for i in range(wake.MAX_PER_HOUR)))
        self.expect()
        self.deliver()
        self.assertEqual(wake.tick(), {"postponed": ["t-20261017-abc123"]})
        self.assertEqual(self.wait_state()["pending_wake"]["reason"], "tetto_orario")

    def test_wake_marks_session_for_chain(self):
        self.sessions([self.alita()])
        self.expect()
        self.deliver()
        wake.tick()
        woken = json.loads((self.home / "waits" / "alita" / "@work-hub" / ".woken.json").read_text())
        self.assertEqual(woken["task_id"], "t-20261017-abc123")
        self.assertEqual([w["task_id"] for w in wake.list_waits("alita", include_closed=True)], ["t-20261017-abc123"])

    def test_deadline_expired_notifies_once(self):
        past = iso(datetime.now(timezone.utc) - timedelta(minutes=1))
        self.expect(deadline=past)
        self.assertEqual(wake.tick(), {"expired": ["t-20261017-abc123"]})
        notes = list((self.home / "inboxes" / "alita" / "@work-hub").glob("msg-*.json"))
        self.assertEqual(len(notes), 1)
        note = json.loads(notes[0].read_text())
        self.assertEqual(note["intent"], "task-scaduto")
        self.assertNotIn("task_id", note)
        self.assertEqual(wake.tick(), {})
        self.assertEqual(len(list((self.home / "inboxes" / "alita" / "@work-hub").glob("msg-*.json"))), 1)

    def test_other_host_wait_skipped(self):
        self.sessions([self.alita()])
        rec = self.expect()
        p = Path(self.home / "waits" / "alita" / "@work-hub" / f"{rec['task_id']}.json")
        r = json.loads(p.read_text()); r["host"] = "mini"; p.write_text(json.dumps(r))
        os.environ["AB_HOST"] = "laptop"
        self.deliver()
        self.assertEqual(wake.tick(), {})
        os.environ.pop("AB_HOST")

    # -- tmux (Mac mini, sessioni fuori da Onda)

    IDLE_SCREEN = ("● Fatto.\n\n" + "─" * 40 + "\n❯ \n" + "─" * 40 + "\n  ? for shortcuts\n")
    TEXT_SCREEN = ("● Fatto.\n\n" + "─" * 40 + "\n❯ sto scrivendo una cosa\n" + "─" * 40 + "\n")
    BUSY_SCREEN = ("✻ Working… (12s · esc to interrupt)\n\n" + "─" * 40 + "\n❯ \n" + "─" * 40 + "\n")

    def tmux_screen(self, text: str) -> None:
        (self.home / "tmux" / "screen").write_text(text)

    def test_tmux_idle_woken(self):
        self.tmux_screen(self.IDLE_SCREEN)
        self.expect(target={"tmux_pane": "%3"})
        self.deliver()
        self.assertEqual(wake.tick(), {"woken": ["t-20261017-abc123"]})
        sent = (self.home / "tmux" / "sent").read_text()
        self.assertIn("-t %3 -l Ritorno sul task t-20261017-abc123", sent)
        self.assertIn("-t %3 Enter", sent)

    def test_tmux_text_or_busy_postponed(self):
        self.expect(target={"tmux_pane": "%3"})
        self.deliver()
        for screen, why in ((self.TEXT_SCREEN, "tmux:has_text"), (self.BUSY_SCREEN, "tmux:busy")):
            self.tmux_screen(screen)
            self.assertEqual(wake.tick(), {"postponed": ["t-20261017-abc123"]})
            self.assertEqual(self.wait_state()["pending_wake"]["reason"], why)
        self.assertFalse((self.home / "tmux" / "sent").exists())

    def test_prompt_box_reader(self):
        self.assertEqual(wake.read_prompt_box(self.IDLE_SCREEN), "")
        self.assertEqual(wake.read_prompt_box(self.TEXT_SCREEN), "sto scrivendo una cosa")
        self.assertIsNone(wake.read_prompt_box("$ ls\nfoo\n"))
        self.assertEqual(wake.screen_refusal(self.BUSY_SCREEN), "busy")
        self.assertEqual(wake.screen_refusal("─" * 20 + "\n❯ 1. Yes\n  2. No\n Do you want to proceed?\n"), "asking")

    # -- v2.8: sveglia a ogni ritorno, accodata per sessione (BS21-F2.1)

    def test_working_session_gets_all_queued_returns_in_one_wake(self):
        self.sessions([self.alita(state="working")])
        self.expect()
        self.expect(task="t-20261017-def456", to="nico/hr-bot")
        a = self.deliver()
        b = self.deliver(task="t-20261017-def456", **{"from": "nico", "from_session": "nico/hr-bot"})
        c = self.deliver()
        self.assertEqual(wake.tick(), {"postponed": ["t-20261017-abc123", "t-20261017-def456"]})
        self.assertEqual(self.onda.calls, [])
        self.sessions([self.alita()])
        self.assertEqual(wake.tick(), {"woken": ["t-20261017-abc123", "t-20261017-def456"]})
        self.assertEqual(len(self.onda.calls), 1)
        prompt = self.onda.calls[0]["params"]["prompt"]
        self.assertIn("3 messaggi", prompt)
        for mid in (a, b, c):
            self.assertIn(mid, prompt)
        self.assertIn("nico/hr-bot", prompt)
        self.assertNotIn("\n", prompt)

    def test_closed_wait_does_not_wake(self):
        self.sessions([self.alita()])
        self.expect()
        f = self.home / "waits" / "alita" / "@work-hub" / "t-20261017-abc123.json"
        rec = json.loads(f.read_text()); rec["state"] = "closed"; f.write_text(json.dumps(rec))
        self.deliver()
        self.assertEqual(wake.tick(), {})

    def test_v26_woken_wait_stays_closed(self):
        self.sessions([self.alita()])
        self.expect()
        f = self.home / "waits" / "alita" / "@work-hub" / "t-20261017-abc123.json"
        rec = json.loads(f.read_text()); rec["state"] = "woken"; f.write_text(json.dumps(rec))
        self.deliver()
        self.assertEqual(wake.tick(), {})
        self.assertEqual(self.onda.calls, [])

    def test_v27_questions_already_handled_are_not_woken_again(self):
        self.sessions([self.alita()])
        self.expect()
        qid = self.question()
        f = self.home / "waits" / "alita" / "@work-hub" / "t-20261017-abc123.json"
        rec = json.loads(f.read_text()); rec["questions"] = {qid: {"state": "woken"}}; f.write_text(json.dumps(rec))
        self.assertEqual(wake.tick(), {})
        self.assertEqual(self.wait_state()["returns"][qid]["state"], "woken")

    def test_idle_wait_expires_with_a_notice(self):
        self.expect()
        f = self.home / "waits" / "alita" / "@work-hub" / "t-20261017-abc123.json"
        rec = json.loads(f.read_text())
        rec["created_at"] = iso(datetime.now(timezone.utc) - timedelta(hours=wake.WAIT_IDLE_H + 1))
        f.write_text(json.dumps(rec))
        self.assertEqual(wake.tick(), {"expired": ["t-20261017-abc123"]})
        st = self.wait_state()
        self.assertEqual((st["state"], st["note"]), ("expired", f"inattiva da {wake.WAIT_IDLE_H:.0f} ore"))
        [note] = list((self.home / "inboxes" / "alita" / "@work-hub").glob("msg-*.json"))
        self.assertIn("nessun movimento", json.loads(note.read_text())["payload"]["summary"])

    # -- v2.8: la risposta del delegante sveglia l'esecutore fermo (BS21-F2.3)

    def kai(self, state="idle", term="pane-kai") -> dict:
        return dict(self.alita(state=state, term=term), sessionId="sess-kai", agent="kai", slug="f3",
                    cwd="/w/f3")

    def to_kai(self, mtype="response", task="t-20261017-abc123", where="", **extra) -> str:
        n = len(list(self.home.rglob("msg-*.json")))
        mid = f"msg-20261017T104500Z-k{n:07d}"
        d = self.home / "inboxes" / "kai" / "@f3"
        if where:
            d = d / where
        d.mkdir(parents=True, exist_ok=True)
        msg = {"id": mid, "from": "alita", "from_session": "alita/work-hub", "to": "kai", "type": mtype,
               "intent": "x", "ts": iso(datetime.now(timezone.utc)), "payload": {"summary": "sì, tema scuro"},
               "task_id": task}
        msg.update(extra)
        (d / f"{mid}.json").write_text(json.dumps(msg))
        return mid

    def test_answer_wakes_the_idle_executor(self):
        self.sessions([self.alita(), self.kai()])
        self.expect()
        self.to_kai("request", kind="delega", where=".read")  # la delega che kai ha ricevuto
        aid = self.to_kai()
        self.assertEqual(wake.tick(), {"woken": ["t-20261017-abc123"]})
        [call] = self.onda.calls
        self.assertEqual(call["params"]["terminalId"], "pane-kai")
        self.assertIn(f"bus_read {aid}", call["params"]["prompt"])
        self.assertIn("Risposta sul task", call["params"]["prompt"])
        st = json.loads((self.home / "waits" / "kai" / "@f3" / ".answers.json").read_text())
        self.assertEqual(st[aid]["state"], "woken")
        self.assertEqual(wake.tick(), {})

    def test_answer_after_a_question_wakes_without_the_delegation_in_the_inbox(self):
        # sul mini l'esecutore ha la delega altrove: basta la sua domanda (waiting_agent)
        self.sessions([self.kai()])
        d = self.home / "registry" / "kai.d"
        d.mkdir(parents=True)
        (d / "f3.json").write_text(json.dumps({"name": "kai", "slug": "f3", "pid": os.getpid(),
                                               "waiting_agent": {"agent": "alita", "task_id": "t-20261017-zzz111"}}))
        self.to_kai(task="t-20261017-zzz111")
        self.assertEqual(wake.tick(), {"woken": ["t-20261017-zzz111"]})

    def test_response_for_a_task_the_session_does_not_execute_is_ignored(self):
        self.sessions([self.kai()])
        aid = self.to_kai(task="t-20261017-nonmio")
        self.assertEqual(wake.tick(), {})
        st = json.loads((self.home / "waits" / "kai" / "@f3" / ".answers.json").read_text())
        self.assertEqual(st[aid]["state"], "ignored")

    def test_busy_executor_gets_the_answer_later(self):
        self.sessions([self.kai(state="working")])
        self.to_kai("brief", kind="delega", where=".done")
        self.to_kai()
        self.assertEqual(wake.tick(), {"postponed": ["t-20261017-abc123"]})
        self.sessions([self.kai()])
        self.assertEqual(wake.tick(), {"woken": ["t-20261017-abc123"]})

    # -- anti-catena (BS21-F2.4)

    def test_task_cap(self):
        self.sessions([self.alita()])
        self.expect()
        wake.task_log("t-20261017-abc123").parent.mkdir(parents=True, exist_ok=True)
        wake.task_log("t-20261017-abc123").write_text(f"{time.time():.0f}\n" * wake.TASK_MAX_PER_HOUR)
        mid = self.deliver()
        self.assertEqual(wake.tick(), {"postponed": ["t-20261017-abc123"]})
        self.assertEqual(self.wait_state()["returns"][mid]["pending_wake"]["reason"], "tetto_task")
        self.assertEqual(self.wait_state()["pending_wake"]["reason"], "tetto_task")
        self.assertEqual(self.onda.calls, [])

    def test_pair_cap_does_not_stop_other_agents(self):
        self.sessions([self.alita()])
        self.expect()
        self.expect(task="t-20261017-def456", to="nico/hr-bot")
        wake.pair_log("alita", "kai").parent.mkdir(parents=True, exist_ok=True)
        wake.pair_log("alita", "kai").write_text(f"{time.time():.0f}\n" * wake.PAIR_MAX_PER_HOUR)
        k = self.deliver()
        n = self.deliver(task="t-20261017-def456", **{"from": "nico", "from_session": "nico/hr-bot"})
        self.assertEqual(wake.tick(), {"postponed": ["t-20261017-abc123"], "woken": ["t-20261017-def456"]})
        self.assertEqual(self.wait_state()["returns"][k]["pending_wake"]["reason"], "tetto_coppia")
        self.assertIn(n, self.onda.calls[0]["params"]["prompt"])
        self.assertNotIn(k, self.onda.calls[0]["params"]["prompt"])

    def test_two_sessions_answering_each_other_stop_at_the_cap(self):
        # Due sessioni finte che si rispondono a vuoto: alita aspetta kai, kai esegue.
        # Ogni giro: kai manda un ritorno, alita "risponde"; entrambe sempre ferme.
        self.sessions([self.alita(), self.kai()])
        self.expect()
        self.to_kai("request", kind="delega", where=".read")
        wakes = 0
        for _ in range(20):
            self.deliver()
            self.to_kai()
            out = wake.tick()
            wakes = len(self.onda.calls)
            # chi e' svegliato "legge": archivia tutto quello che ha in casella
            for box in ("alita", "kai"):
                for f in (self.home / "inboxes" / box).rglob("msg-*.json"):
                    if f.parent.name.startswith("@"):
                        (f.parent / ".done").mkdir(exist_ok=True)
                        f.rename(f.parent / ".done" / f.name)
        self.assertLessEqual(wakes, wake.TASK_MAX_PER_HOUR)
        self.assertGreater(wakes, 0)
        self.assertEqual(len(wake._stamps(wake.task_log("t-20261017-abc123"))), wakes)

    # -- aggancio (BS21-F1.3): i ritorni scritti come messaggi nuovi

    def msg(self, mid: str) -> dict:
        return json.loads(next(self.home.rglob(f"{mid}.json")).read_text())

    def test_new_message_from_executor_session_is_hooked(self):
        self.sessions([self.alita()])
        self.expect()
        mid = self.deliver(task=None, type="brief", kind="comunicazione")
        out = wake.tick()
        self.assertEqual(out["linked"], [f"{mid}>t-20261017-abc123"])
        self.assertEqual(out["woken"], ["t-20261017-abc123"])
        m = self.msg(mid)
        self.assertEqual(m["task_id"], "t-20261017-abc123")
        self.assertEqual(m["task_link"]["by"], "session")

    def test_hook_by_reply_to_the_delegation(self):
        self.expect(delegation={"msg_id": "msg-deleg-1", "thread_id": "thread-d1"})
        self.expect(task="t-20261017-def456", delegation={"msg_id": "msg-deleg-2", "thread_id": "thread-d2"})
        mid = self.deliver(task=None, in_reply_to="msg-deleg-2")
        wake.tick()
        self.assertEqual(self.msg(mid)["task_id"], "t-20261017-def456")
        self.assertEqual(self.msg(mid)["task_link"]["by"], "reply")

    def test_hook_by_thread_when_the_session_has_two_tasks(self):
        self.expect(delegation={"msg_id": "msg-deleg-1", "thread_id": "thread-d1"})
        self.expect(task="t-20261017-def456", delegation={"msg_id": "msg-deleg-2", "thread_id": "thread-d2"})
        mid = self.deliver(task=None, thread_id="thread-d2")
        wake.tick()
        self.assertEqual(self.msg(mid)["task_id"], "t-20261017-def456")
        self.assertEqual(self.msg(mid)["task_link"]["by"], "thread")

    def test_hook_by_agent_when_the_wait_is_unique(self):
        self.expect(to="kai")  # delegata a kai senza sessione
        mid = self.deliver(task=None, **{"from_session": "kai/altra"})
        wake.tick()
        self.assertEqual(self.msg(mid)["task_link"]["by"], "agent")

    def test_ambiguous_message_is_left_to_the_receiver(self):
        self.sessions([self.alita()])
        self.expect(to="kai")
        self.expect(task="t-20261017-def456", to="kai")
        mid = self.deliver(task=None, **{"from_session": "kai/terza"})
        out = wake.tick()
        self.assertEqual(out["da_agganciare"], [mid])
        m = self.msg(mid)
        self.assertNotIn("task_id", m)
        self.assertEqual(m["task_link"]["by"], "da_agganciare")
        self.assertEqual(sorted(m["task_link"]["candidates"]), ["t-20261017-abc123", "t-20261017-def456"])
        # chi riceve viene svegliato una volta per decidere
        self.assertEqual(len(self.onda.calls), 1)
        self.assertIn("da agganciare", self.onda.calls[0]["params"]["prompt"])
        self.assertIn("woken_at", self.msg(mid)["task_link"])
        self.assertEqual(wake.tick(), {})  # deciso una volta, non a ogni giro
        self.assertEqual(len(self.onda.calls), 1)

    def test_learned_executor_session_hooks_later_messages(self):
        self.sessions([self.alita("working")])
        self.expect(to="kai")
        self.expect(task="t-20261017-def456", to="kai/altra")
        self.deliver()  # primo ritorno con task_id da kai/f3: impara l'esecutore
        wake.tick()
        self.assertEqual(self.wait_state()["executor_session"], "kai/f3")
        mid = self.deliver(task=None)
        wake.tick()
        self.assertEqual(self.msg(mid)["task_link"]["by"], "session")
        self.assertEqual(self.msg(mid)["task_id"], "t-20261017-abc123")

    def test_never_hooks_a_delegation_or_a_cc(self):
        self.expect()
        d = self.deliver(task=None, type="request", kind="delega")
        c = self.deliver(task=None, type="cc-notify")
        self.assertEqual(wake.tick(), {})
        self.assertNotIn("task_link", self.msg(d))
        self.assertNotIn("task_link", self.msg(c))

    def test_message_older_than_the_wait_is_not_hooked(self):
        self.expect()
        old = iso(datetime.now(timezone.utc) - timedelta(hours=1))
        mid = self.deliver(task=None, ts=old)
        wake.tick()
        self.assertNotIn("task_link", self.msg(mid))

    def test_session_scoped_message_only_hooks_its_session_waits(self):
        wake.expect("alita", "altra-sessione", "x", "kai/f3", task_id="t-20261017-zzz999")
        mid = self.deliver(task=None)  # in @work-hub, che non aspetta nulla
        self.assertEqual(wake.tick(), {})
        self.assertNotIn("task_link", self.msg(mid))

    def test_wait_of_another_machine_is_not_used(self):
        os.environ["AB_HOST"] = "laptop"
        try:
            self.expect()
            f = self.home / "waits" / "alita" / "@work-hub" / "t-20261017-abc123.json"
            rec = json.loads(f.read_text()); rec["host"] = "mini"; f.write_text(json.dumps(rec))
            mid = self.deliver(task=None)
            wake.tick()
            self.assertNotIn("task_link", self.msg(mid))
        finally:
            os.environ.pop("AB_HOST", None)

    def test_archived_message_is_not_resurrected(self):
        self.expect()
        mid = self.deliver(task=None)
        p = next(self.home.rglob(f"{mid}.json"))
        msg = json.loads(p.read_text())
        arch = p.parent / ".read" / p.name
        arch.parent.mkdir(exist_ok=True)
        p.rename(arch)
        self.assertFalse(wake._rewrite_message(p, msg))
        self.assertFalse(p.exists())


if __name__ == "__main__":
    unittest.main(verbosity=1)
