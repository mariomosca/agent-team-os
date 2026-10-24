"""Test della sveglia (bus v2.6): python3 -I tests/test_wake.py

Un AB_HOME temporaneo, un Onda finto (socket unix che registra le chiamate
agent.prompt e risponde come gli si dice) e un tmux finto (tests/fake-bin/tmux).
Niente Onda vero, niente sessioni vere.
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
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
        wake.REPORT_HOUR = 99  # niente report del lunedi' nei giri dei test
        wake.NOCHANNEL_S = 300
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
        self.deliver(type="event")
        self.assertEqual(wake.tick(), {"postponed": ["t-20261017-abc123"]})
        self.assertEqual(self.wait_state()["pending_wake"]["reason"], "tetto_orario")
        # BR24-F2.1: l'esito (response) passa anche oltre il tetto orario della sessione
        self.deliver()
        self.assertEqual(wake.tick(), {"woken": ["t-20261017-abc123"]})

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
        self.expect(task="t-20261017-def456", to="nico/app-x")
        a = self.deliver()
        b = self.deliver(task="t-20261017-def456", **{"from": "nico", "from_session": "nico/app-x"})
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
        self.assertIn("nico/app-x", prompt)
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
        mid = self.deliver(type="event")
        self.assertEqual(wake.tick(), {"postponed": ["t-20261017-abc123"]})
        self.assertEqual(self.wait_state()["returns"][mid]["pending_wake"]["reason"], "tetto_task")
        self.assertEqual(self.wait_state()["pending_wake"]["reason"], "tetto_task")
        self.assertEqual(self.onda.calls, [])

    def test_pair_cap_does_not_stop_other_agents(self):
        self.sessions([self.alita()])
        self.expect()
        self.expect(task="t-20261017-def456", to="nico/app-x")
        wake.pair_log("alita", "kai").parent.mkdir(parents=True, exist_ok=True)
        wake.pair_log("alita", "kai").write_text(f"{time.time():.0f}\n" * wake.PAIR_MAX_PER_HOUR)
        k = self.deliver(type="event")
        n = self.deliver(task="t-20261017-def456", **{"from": "nico", "from_session": "nico/app-x"})
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
        # BR24-F2.1: gli esiti di kai hanno il loro tetto, le risposte di alita quello del task
        self.assertLessEqual(wakes, wake.TASK_MAX_PER_HOUR + wake.RESPONSE_MAX_PER_HOUR)
        self.assertGreater(wakes, 0)
        self.assertEqual(len(wake._stamps(wake.task_log("t-20261017-abc123"))), wakes)
        self.assertEqual(len(wake._stamps(wake.response_log("t-20261017-abc123"))), wake.RESPONSE_MAX_PER_HOUR)
        self.assertNotIn("woken", out)  # a regime: nessuna sveglia

    # -- rete di sicurezza fuori delega (BS21-F3.1)

    def urgent(self, prio="urgent", req=True, sender="nico", where="", **extra) -> str:
        return self.deliver(task=None, where=where, type="request", kind="comunicazione", priority=prio,
                            requires_response=req, **dict({"from": sender, "from_session": f"{sender}/app-x"}, **extra))

    def test_urgent_with_response_wakes_the_idle_recipient(self):
        self.sessions([self.alita()])
        mid = self.urgent()
        self.assertEqual(wake.tick(), {"urgent": [mid]})
        prompt = self.onda.calls[0]["params"]["prompt"]
        self.assertIn("URGENTE", prompt)
        self.assertIn(f"bus_read {mid}", prompt)
        self.assertEqual(wake.tick(), {})  # una volta per messaggio

    def test_urgent_needs_priority_and_requires_response(self):
        self.sessions([self.alita()])
        self.urgent(prio="normal")
        self.urgent(req=False)
        self.assertEqual(wake.tick(), {})
        self.assertEqual(self.onda.calls, [])

    def test_urgent_once_every_15_minutes_per_pair(self):
        self.sessions([self.alita()])
        a = self.urgent()
        self.assertEqual(wake.tick(), {"urgent": [a]})
        b = self.urgent(prio="high")
        self.assertEqual(wake.tick(), {"postponed": [b]})
        st = json.loads((self.home / "waits" / "alita" / "@work-hub" / ".urgent.json").read_text())
        self.assertEqual(st[b]["pending_wake"]["reason"], "tetto_urgenti")
        c = self.urgent(sender="vera")  # un'altra coppia passa
        out = wake.tick()
        self.assertEqual(out["urgent"], [c])
        # passati i 15 minuti, anche b
        f = wake.urgent_log("alita", "nico")
        f.write_text(f"{time.time() - wake.URGENT_PAIR_S - 1:.0f}\n")
        self.assertEqual(wake.tick().get("urgent"), [b])

    def test_urgent_to_the_bare_agent_needs_a_single_live_session(self):
        self.sessions([self.alita()])
        mid = self.urgent(where="..")  # inboxes/alita/, la casella condivisa
        self.assertEqual(wake.tick(), {"urgent": [mid]})
        self.sessions([self.alita(), dict(self.alita(term="pane-2"), slug="altra", sessionId="s2")])
        self.urgent(where="..", sender="vera")
        self.assertEqual(wake.tick(), {})

    def test_urgent_read_before_the_wake_does_not_wake(self):
        self.sessions([self.alita(state="working")])
        mid = self.urgent()
        self.assertEqual(wake.tick(), {"postponed": [mid]})
        src = next(self.home.rglob(f"{mid}.json"))
        (src.parent / ".read").mkdir(exist_ok=True)
        src.rename(src.parent / ".read" / src.name)
        self.sessions([self.alita()])
        self.assertEqual(wake.tick(), {})

    # -- misura settimanale (BS21-F3.2)

    def test_report_counts_returns_caps_and_unhandled(self):
        self.sessions([self.alita()])
        self.expect()
        a = self.deliver()
        wake.tick()  # a: con sveglia
        b = self.deliver(where=".done")  # b: letto prima
        wake.task_log("t-20261017-abc123").write_text(f"{time.time():.0f}\n" * wake.TASK_MAX_PER_HOUR)
        c = self.deliver(type="event")  # c: fermato dal tetto
        wake.tick()
        old = self.deliver(task=None, **{"from": "vera", "from_session": "vera/growth"})
        p = next(self.home.rglob(f"{old}.json"))
        os.utime(p, (time.time() - 7200, time.time() - 7200))
        r = wake.report()
        self.assertEqual(r["ritorni"]["totale"], 3)
        self.assertEqual((r["ritorni"]["con_sveglia"], r["ritorni"]["letti_prima"], r["ritorni"]["in_attesa"]), (1, 1, 1))
        self.assertAlmostEqual(r["serviti"], 0.667, places=3)
        self.assertFalse(r["ok"])
        self.assertEqual(r["tetti"]["per_motivo"], {"tetto_task": 1})
        self.assertEqual(r["non_gestiti_oltre_1h"], {"alita/work-hub": 1})
        self.assertEqual(r["deleghe"]["aperte"], 1)
        text = wake.report_text(r)
        self.assertIn("ritorni serviti 67% (obiettivo 95%) SOTTO OBIETTIVO", text)
        self.assertIn("alita/work-hub 1", text)

    def test_weekly_report_once_and_to_the_hub(self):
        (self.home / "AGENT_MAP.json").write_text(json.dumps(
            {"agents": {"alita": {}, "kai": {}}, "rules": [], "cc_awareness": {"hub": "alita"}}))
        f = wake.weekly_report("2026-W43")
        self.assertTrue(f.exists() and f.with_suffix(".txt").exists())
        [note] = list((self.home / "inboxes" / "alita").glob("msg-*.json"))
        msg = json.loads(note.read_text())
        self.assertEqual((msg["type"], msg["intent"]), ("event", "report-sveglia"))
        self.assertIn("Sveglia del bus", msg["payload"]["summary"])
        self.assertNotIn("task_id", msg)

    # -- canale della sveglia (BS21-F3.3b, il caso di Leo del 21/10)

    def test_session_retired_by_onda_for_silence_is_still_woken(self):
        # Onda scrive "exited" per una sessione ferma da oltre 30 min senza eventi
        # (SILENT_EXIT_MS): era Leo, vivo nel suo pane, con due esiti rimasti pending.
        retired = dict(self.alita(), state="exited", hasText=None)
        self.sessions([retired])
        self.expect(target={"onda_terminal_id": "pane-alita", "claude_session_id": "sess-alita"})
        self.deliver()
        self.assertEqual(wake.tick(), {"woken": ["t-20261017-abc123"]})
        [call] = self.onda.calls
        self.assertEqual(call["params"]["terminalId"], "pane-alita")
        self.assertTrue(call["params"]["onlyIfIdle"])  # decide la guardia di Onda

    def test_live_entry_wins_over_a_retired_one(self):
        old = dict(self.alita(term="pane-vecchio"), state="exited", sessionId="sess-vecchia")
        self.sessions([old, self.alita(term="pane-nuovo")])
        self.expect(target={})
        self.deliver()
        wake.tick()
        self.assertEqual(self.onda.calls[0]["params"]["terminalId"], "pane-nuovo")

    def test_really_ended_session_is_refused_by_onda_then_notified(self):
        wake.NOCHANNEL_S = 0
        (self.home / "AGENT_MAP.json").write_text(json.dumps(
            {"agents": {"alita": {}, "kai": {}, "leo": {}}, "rules": [], "cc_awareness": {"hub": "alita"}}))
        self.sessions([dict(self.alita(), state="exited", hasText=None)])
        self.onda.reply = {"success": False, "errorCode": "not_idle", "reason": "no_session", "completedSteps": []}
        self.expect()
        mid = self.deliver()
        out = wake.tick()
        self.assertEqual(out["postponed"], ["t-20261017-abc123"])
        self.assertEqual(out["nochannel"], ["alita/work-hub"])
        [note] = list((self.home / "inboxes" / "alita").glob("msg-*.json"))
        n = json.loads(note.read_text())
        self.assertEqual((n["intent"], n["priority"]), ("sveglia-senza-canale", "high"))
        self.assertIn(mid, n["payload"]["messages"])
        self.assertNotIn("task_id", n)
        self.assertEqual(self.wait_state()["returns"][mid]["nochannel_notice"]["msg_id"], n["id"])
        wake.tick()  # una volta sola
        self.assertEqual(len(list((self.home / "inboxes" / "alita").glob("msg-*.json"))), 1)

    def test_no_channel_at_all_waits_then_notifies_once(self):
        (self.home / "AGENT_MAP.json").write_text(json.dumps(
            {"agents": {"alita": {}, "kai": {}}, "rules": [], "cc_awareness": {"hub": "alita"}}))
        self.sessions([])  # nessuna sessione in Onda, nessun tmux
        self.expect(target={})
        self.deliver()
        wake.NOCHANNEL_S = 300
        self.assertNotIn("nochannel", wake.tick())  # appena arrivato: si aspetta un po'
        f = self.home / "waits" / "alita" / "@work-hub" / "t-20261017-abc123.json"
        rec = json.loads(f.read_text())
        for e in rec["returns"].values():
            e["pending_wake"]["since"] = iso(datetime.now(timezone.utc) - timedelta(minutes=6))
        f.write_text(json.dumps(rec))
        self.assertEqual(wake.tick()["nochannel"], ["alita/work-hub"])
        self.assertNotIn("nochannel", wake.tick())
        self.assertEqual(len(list((self.home / "inboxes" / "alita").glob("msg-*.json"))), 1)

    # -- una delega nuova sveglia chi la riceve se e' fermo (BS21-F3.3c)

    def delega_a_kai(self, where="", prio="normal", **extra) -> str:
        return self.to_kai("request", where=where, kind="delega", priority=prio,
                           payload={"summary": "rifai il parser"}, **extra)

    def test_new_delegation_wakes_the_idle_recipient(self):
        self.sessions([self.kai()])
        mid = self.delega_a_kai()
        self.assertEqual(wake.tick(), {"delegation": ["t-20261017-abc123"]})
        [call] = self.onda.calls
        self.assertEqual(call["params"]["terminalId"], "pane-kai")
        p = call["params"]["prompt"]
        self.assertIn("Nuova delega t-20261017-abc123 da alita/work-hub: rifai il parser", p)
        self.assertIn(f"bus_read {mid}", p)
        self.assertIn("message_type='response'", p)
        self.assertNotIn("\n", p)
        self.assertEqual(wake.tick(), {})  # una volta per delega

    def test_delegation_to_a_working_session_waits(self):
        self.sessions([self.kai(state="working")])
        self.delega_a_kai()
        self.assertEqual(wake.tick(), {"postponed": ["t-20261017-abc123"]})
        self.sessions([self.kai()])
        self.assertEqual(wake.tick(), {"delegation": ["t-20261017-abc123"]})

    def test_delegation_rules_low_priority_and_bare_agent(self):
        self.sessions([self.kai()])
        self.delega_a_kai(prio="low")
        self.assertEqual(wake.tick(), {})
        self.delega_a_kai(task="t-20261017-def456")
        self.assertEqual(wake.tick(), {"delegation": ["t-20261017-def456"]})
        # all'agente intero: si sveglia solo se ha una sola sessione viva
        two = [self.kai(), dict(self.kai(term="pane-k2"), slug="altra", sessionId="s-k2")]
        self.sessions(two)
        self.delega_a_kai(where="..", task="t-20261017-zzz111")
        self.assertNotIn("delegation", wake.tick())
        self.sessions([self.kai()])
        self.delega_a_kai(where="..", task="t-20261017-zzz999")
        out = wake.tick()
        self.assertIn("t-20261017-zzz999", out.get("delegation", []))

    def test_urgent_delegation_wakes_once_not_twice(self):
        self.sessions([self.kai()])
        self.delega_a_kai(prio="urgent", requires_response=True)
        out = wake.tick()
        self.assertEqual(out, {"delegation": ["t-20261017-abc123"]})
        self.assertEqual(len(self.onda.calls), 1)

    # -- un messaggio da un altro host sveglia chi lo riceve se e' fermo (AM18-F9.6)

    def from_cloud(self, where="..", **extra) -> str:
        n = len(list(self.home.rglob("msg-*.json")))
        mid = f"msg-20261024T122646Z-c{n:07d}"
        d = self.home / "inboxes" / "alita" / "@work-hub"
        if where:
            d = d / where
        d.mkdir(parents=True, exist_ok=True)
        msg = {"id": mid, "from": "alita", "from_session": "alita/cloud", "to": "alita", "type": "request",
               "intent": "da-alita-mobile", "priority": "normal", "requires_response": True,
               "ts": iso(datetime.now(timezone.utc)), "payload": {"summary": "guarda la mail di Massimo"},
               "context": {"host": "cloud"}}
        msg.update(extra)
        (d / f"{mid}.json").write_text(json.dumps(msg))
        return mid

    def test_message_from_another_host_wakes_the_idle_session(self):
        os.environ["AB_HOST"] = "laptop"
        self.sessions([self.alita()])
        mid = self.from_cloud()
        self.assertEqual(wake.tick(), {"remote": [mid]})
        [call] = self.onda.calls
        self.assertEqual(call["params"]["terminalId"], "pane-alita")
        p = call["params"]["prompt"]
        self.assertIn("Messaggio da alita/cloud@cloud (da-alita-mobile): guarda la mail di Massimo", p)
        self.assertIn(f"bus_read {mid}", p)
        self.assertNotIn("\n", p)
        self.assertEqual(wake.tick(), {})  # una volta per messaggio

    def test_remote_message_waits_while_the_session_works(self):
        os.environ["AB_HOST"] = "laptop"
        self.sessions([self.alita(state="working")])
        mid = self.from_cloud()
        self.assertEqual(wake.tick(), {"postponed": [mid]})
        self.sessions([self.alita()])
        self.assertEqual(wake.tick(), {"remote": [mid]})

    def test_remote_to_the_bare_agent_goes_to_the_home_session(self):
        os.environ["AB_HOST"] = "laptop"
        (self.home / "AGENT_MAP.json").write_text(json.dumps(
            {"agents": {"alita": {"workspace_root": "/w/work-hub"}, "kai": {}}, "rules": []}))
        other = dict(self.alita(term="pane-a2"), slug="reports", sessionId="s-a2", cwd="/w/work-hub/reports")
        self.sessions([self.alita(), other])
        mid = self.from_cloud()
        self.assertEqual(wake.tick(), {"remote": [mid]})
        [call] = self.onda.calls
        self.assertEqual(call["params"]["terminalId"], "pane-alita")
        # la sessione di casa ritirata da Onda per silenzio vince sull'altra viva (prova del 24/10)
        self.sessions([self.alita(state="exited"), other])
        self.from_cloud()
        self.assertIn("remote", wake.tick())
        self.assertEqual(self.onda.calls[-1]["params"]["terminalId"], "pane-alita")

    def test_remote_rules(self):
        os.environ["AB_HOST"] = "laptop"
        self.sessions([self.alita()])
        self.from_cloud(context={"host": "laptop"})  # stessa macchina
        self.from_cloud(priority="low")
        self.from_cloud(type="response")
        self.from_cloud(wake_chain={"task_id": "t-x"})
        self.assertEqual(wake.tick(), {})
        # un ritorno su un'attesa del destinatario lo sveglia collect_returns, una volta
        self.expect(to="alita@cloud")
        self.from_cloud(task_id="t-20261017-abc123")
        out = wake.tick()
        self.assertNotIn("remote", out)
        self.assertEqual(len(self.onda.calls), 1)
        os.environ.pop("AB_HOST")

    # -- aggancio (BS21-F1.3): i ritorni scritti come messaggi nuovi

    def msg(self, mid: str) -> dict:
        return json.loads(next(self.home.rglob(f"{mid}.json")).read_text())

    def test_new_message_from_executor_session_is_hooked(self):
        self.sessions([self.alita()])
        self.expect()
        mid = self.deliver(task=None)
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
        out = wake.tick()
        self.assertNotIn("linked", out)
        self.assertNotIn("da_agganciare", out)
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

    # -- BR24 passo 0: casi del 24 ottobre

    def test_br24_final_response_is_not_held_by_the_task_or_pair_cap(self):
        # caso t-20261024-irjocy: 3 domande e un ritorno intermedio nell'ultima ora,
        # poi l'esito finale alle 17:24Z rimasto dietro tetto_task
        self.sessions([self.alita()])
        self.expect()
        wake.task_log("t-20261017-abc123").parent.mkdir(parents=True, exist_ok=True)
        wake.task_log("t-20261017-abc123").write_text(f"{time.time():.0f}\n" * wake.TASK_MAX_PER_HOUR)
        wake.pair_log("alita", "kai").parent.mkdir(parents=True, exist_ok=True)
        wake.pair_log("alita", "kai").write_text(f"{time.time():.0f}\n" * wake.PAIR_MAX_PER_HOUR)
        q = self.question()
        mid = self.deliver()
        self.assertEqual(wake.tick(), {"postponed": ["t-20261017-abc123"], "woken": ["t-20261017-abc123"]})
        self.assertIn(mid, self.onda.calls[0]["params"]["prompt"])
        self.assertNotIn(q, self.onda.calls[0]["params"]["prompt"])
        st = self.wait_state()
        self.assertEqual(st["returns"][mid]["state"], "woken")
        self.assertEqual(st["returns"][q]["pending_wake"]["reason"], "tetto_task")

    def test_br24_responses_have_their_own_cap(self):
        self.sessions([self.alita()])
        self.expect()
        wake.response_log("t-20261017-abc123").parent.mkdir(parents=True, exist_ok=True)
        wake.response_log("t-20261017-abc123").write_text(f"{time.time():.0f}\n" * wake.RESPONSE_MAX_PER_HOUR)
        mid = self.deliver()
        self.assertEqual(wake.tick(), {"postponed": ["t-20261017-abc123"]})
        self.assertEqual(self.wait_state()["returns"][mid]["pending_wake"]["reason"], "tetto_esiti")

    def gestisci_during_wake(self, esito: str):
        """Durante la sveglia (il giro ha l'attesa in memoria) la sessione da' un esito."""
        real = wake.try_wake

        def racing(tgt, d, text, capped=True):
            mid = text.split("bus_read ")[1].split()[0].rstrip(".,;:)")
            p = next((self.home / "inboxes" / "alita").rglob(f"{mid}.json"))
            wake.gestisci("alita", "work-hub", p, esito)
            return "onda"
        wake.try_wake = racing
        try:
            return wake.tick()
        finally:
            wake.try_wake = real

    def test_br24_tick_does_not_undo_a_close_given_meanwhile(self):
        self.sessions([self.alita()])
        self.expect()
        mid = self.deliver()
        self.gestisci_during_wake("chiuso")
        st = self.wait_state()
        self.assertEqual(st["state"], "closed")
        self.assertEqual(st["returns"][mid]["state"], "handled")
        self.assertNotIn("pending_wake", st)

    def test_br24_tick_does_not_drop_rearmed_at(self):
        # il test rosso di test-wake-lib.sh: riarma mentre gira la sveglia
        self.sessions([self.alita()])
        self.expect()
        mid = self.deliver(type="event")
        self.gestisci_during_wake("riarma")
        st = self.wait_state()
        self.assertEqual(st["state"], "expected")
        self.assertIn("rearmed_at", st)
        self.assertEqual(st["returns"][mid]["handled"], "riarma")

    def test_br24_merge_keeps_handled_and_open_state(self):
        disk = {"state": "closed", "returns": {"m1": {"state": "handled"}}, "last_activity": "2026-10-24T17:00:00Z"}
        mine = {"state": "expired", "returns": {"m1": {"state": "woken"}, "m2": {"state": "pending",
                "pending_wake": {"reason": "tetto_task"}}}, "last_activity": "2026-10-24T17:05:00Z",
                "pending_wake": {"reason": "tetto_task"}}
        out = wake.merge_wait(disk, mine)
        self.assertEqual(out["state"], "closed")
        self.assertEqual(out["returns"]["m1"]["state"], "handled")
        self.assertEqual(out["returns"]["m2"]["state"], "pending")
        self.assertEqual(out["last_activity"], "2026-10-24T17:05:00Z")
        self.assertNotIn("pending_wake", out)  # attesa chiusa: niente rimando

    def test_br24_close_removes_pending_wake_from_the_wait(self):
        self.sessions([self.alita()])
        self.expect()
        wake.task_log("t-20261017-abc123").parent.mkdir(parents=True, exist_ok=True)
        wake.task_log("t-20261017-abc123").write_text(f"{time.time():.0f}\n" * wake.TASK_MAX_PER_HOUR)
        mid = self.deliver(type="event")
        wake.tick()
        self.assertEqual(self.wait_state()["pending_wake"]["reason"], "tetto_task")
        wake.gestisci("alita", "work-hub", next(self.home.rglob(f"{mid}.json")), "chiuso")
        st = self.wait_state()
        self.assertEqual(st["state"], "closed")
        self.assertNotIn("pending_wake", st)

    # caso iii: messaggi a "kai" agganciati a t-20261021-xn9lv0
    def to_bare_alita(self, **extra) -> str:
        n = len(list(self.home.rglob("msg-*.json")))
        mid = f"msg-20261024T163000Z-r{n:07d}"
        d = self.home / "inboxes" / "alita"
        d.mkdir(parents=True, exist_ok=True)
        msg = {"id": mid, "from": "kai", "from_session": "kai/f3", "to": "alita", "type": "brief",
               "intent": "x", "ts": iso(datetime.now(timezone.utc)), "payload": {"summary": "altro tema"}}
        msg.update(extra)
        (d / f"{mid}.json").write_text(json.dumps(msg))
        return mid

    def test_br24_comunicazione_is_never_hooked(self):
        self.sessions([self.alita()])
        self.expect()
        mid = self.deliver(task=None, type="brief", kind="comunicazione")
        out = wake.tick()
        self.assertNotIn("linked", out)
        self.assertNotIn("task_link", self.msg(mid))
        self.assertEqual(self.onda.calls, [])

    def test_br24_shared_inbox_message_is_hooked_only_as_a_reply(self):
        self.sessions([self.alita()])
        self.expect(delegation={"msg_id": "msg-deleg-1", "thread_id": "thread-d1"})
        cloud = self.to_bare_alita(type="event", **{"from_session": "kai/cloud"})
        same = self.to_bare_alita(type="response")  # stessa sessione esecutrice, ma all'agente intero
        reply = self.to_bare_alita(type="response", in_reply_to="msg-deleg-1")
        out = wake.tick()
        self.assertEqual(out["linked"], [f"{reply}>t-20261017-abc123"])
        self.assertNotIn("task_link", self.msg(cloud))
        self.assertNotIn("task_link", self.msg(same))

    def registry(self, agent: str, slug: str, age_s: float = 0) -> None:
        d = self.home / "registry" / f"{agent}.d"
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{slug}.json").write_text(json.dumps({"name": agent, "slug": slug, "last_seen": iso(
            datetime.now(timezone.utc) - timedelta(seconds=age_s))}))

    def test_br24_wait_belongs_to_its_session_while_it_lives(self):
        self.expect()
        self.registry("alita", "work-hub")
        self.assertIsNone(wake.own_wait("alita", "t-20261017-abc123", "altra"))
        self.assertIsNotNone(wake.own_wait("alita", "t-20261017-abc123", "work-hub"))
        mid = self.deliver(where="", type="event")
        p = next(self.home.rglob(f"{mid}.json"))
        with self.assertRaises(ValueError):
            wake.gestisci("alita", "altra", p, "riarma")
        # la sessione che ha delegato e' finita: l'attesa passa a chi c'e'
        self.registry("alita", "work-hub", age_s=wake.SESSION_TTL_S + 60)
        self.assertIsNotNone(wake.own_wait("alita", "t-20261017-abc123", "altra"))
        self.assertEqual(wake.gestisci("alita", "altra", p, "chiuso")["wait"]["state"], "closed")

    # caso iv: attese con i ritorni gia' archiviati, e t-20261021-xn9lv0
    def test_br24_close_a_wait_by_task_id(self):
        self.expect()
        old = self.deliver(where=".done")
        pending = self.deliver(type="event")
        out = wake.chiudi("alita", "work-hub", "t-20261017-abc123", "lavoro concluso")
        self.assertEqual((out["state"], out["was"]), ("closed", "expected"))
        self.assertEqual([f["id"] for f in out["filed"]], [pending])
        st = self.wait_state()
        self.assertEqual((st["state"], st["note"]), ("closed", "lavoro concluso"))
        self.assertEqual(st["returns"][pending]["handled"], "chiuso")
        self.assertFalse((self.home / "inboxes" / "alita" / "@work-hub" / f"{pending}.json").exists())
        self.assertTrue((self.home / "inboxes" / "alita" / "@work-hub" / ".done" / f"{pending}.json").exists())
        self.assertTrue(old)
        with self.assertRaises(ValueError):
            wake.chiudi("kai", "f3", "t-20261017-abc123")
        # una sessione diversa dello stesso agente puo' chiuderla (delega di una sessione precedente)
        self.expect(task="t-20261017-def456")
        self.assertEqual(wake.chiudi("alita", "nuova", "t-20261017-def456")["state"], "closed")
        # dalla riga di comando, come la usa la lib
        self.expect(task="t-20261017-ghi789")
        with contextlib.redirect_stdout(io.StringIO()) as buf:
            self.assertEqual(wake.main(["chiudi", "--agent", "alita", "--slug", "work-hub",
                                        "--task-id", "t-20261017-ghi789"]), 0)
        self.assertEqual(json.loads(buf.getvalue())["state"], "closed")
        self.assertEqual(self.wait_state("t-20261017-ghi789")["state"], "closed")

    def test_br24_deadline_expires_even_with_handled_returns(self):
        past = iso(datetime.now(timezone.utc) - timedelta(minutes=1))
        self.expect(deadline=past)
        f = self.home / "waits" / "alita" / "@work-hub" / "t-20261017-abc123.json"
        rec = json.loads(f.read_text())
        rec["returns"] = {"m-old": {"state": "handled", "type": "question", "at": past}}
        f.write_text(json.dumps(rec))
        self.assertEqual(wake.tick(), {"expired": ["t-20261017-abc123"]})
        self.assertEqual(self.wait_state()["note"], "deadline passata")

    def test_br24_old_unhandled_return_does_not_keep_a_wait_alive(self):
        self.expect()
        f = self.home / "waits" / "alita" / "@work-hub" / "t-20261017-abc123.json"
        rec = json.loads(f.read_text())
        old = iso(datetime.now(timezone.utc) - timedelta(hours=wake.WAIT_IDLE_H + 1))
        rec["created_at"] = rec["last_activity"] = old
        rec["returns"] = {"m-old": {"state": "pending", "type": "event", "at": old}}
        f.write_text(json.dumps(rec))
        self.assertEqual(wake.tick(), {"expired": ["t-20261017-abc123"]})
        # un ritorno recente da leggere invece la tiene aperta
        self.expect(task="t-20261017-def456")
        f2 = self.home / "waits" / "alita" / "@work-hub" / "t-20261017-def456.json"
        rec = json.loads(f2.read_text())
        rec["created_at"] = old
        rec["returns"] = {"m-new": {"state": "pending", "type": "event", "at": iso(datetime.now(timezone.utc))}}
        f2.write_text(json.dumps(rec))
        wake.tick()
        self.assertEqual(self.wait_state("t-20261017-def456")["state"], "expected")

    # caso ii: kai/orbit viva in Onda ma assente dal registro
    def test_br24_registry_follows_onda(self):
        self.sessions([self.kai(state="working"), dict(self.kai(), slug="vecchia", state="exited"),
                       dict(self.kai(), agent="sconosciuto")])
        wake.tick()
        reg = self.home / "registry" / "kai.d"
        self.assertEqual(sorted(p.stem for p in reg.glob("*.json")), ["f3"])
        rec = json.loads((reg / "f3.json").read_text())
        self.assertEqual((rec["source"], rec["workspace_path"]), ("onda", "/w/f3"))
        self.assertTrue(wake.session_live("kai", "f3"))
        # last_seen fermo durante un turno lungo: il giro lo rinfresca
        self.registry("kai", "f3", age_s=3600)
        wake.tick()
        self.assertLess(time.time() - wake.parse_iso(json.loads((reg / "f3.json").read_text())["last_seen"]), 30)
        # la voce aggiunta dal giro se ne va quando Onda non la da' piu' viva
        self.sessions([])
        (reg / "f3.json").write_text(json.dumps(dict(rec, source="onda")))
        wake.tick()
        self.assertFalse((reg / "f3.json").exists())
        # Onda spento: il registro non si tocca
        self.registry("kai", "f3", age_s=3600)
        self.sessions([self.kai()], pid=999999)
        wake.tick()
        self.assertTrue((reg / "f3.json").exists())


if __name__ == "__main__":
    unittest.main(verbosity=1)
