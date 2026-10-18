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

    def test_idle_session_woken_once(self):
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
        self.assertNotIn("\n", p["prompt"])
        st = self.wait_state()
        self.assertEqual(st["state"], "woken")
        self.assertEqual(st["woken_via"], "onda")
        # secondo esito dello stesso task: nessuna seconda sveglia
        self.deliver()
        self.assertEqual(wake.tick(), {})
        self.assertEqual(len(self.onda.calls), 1)

    def test_message_without_task_id_does_not_wake(self):
        self.sessions([self.alita()])
        self.expect()
        self.deliver(task=None)
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
        self.assertEqual(wake.tick(), {"woken_question": ["t-20261017-abc123"]})
        self.assertEqual(len(self.onda.calls), 1)
        prompt = self.onda.calls[0]["params"]["prompt"]
        self.assertIn(f"bus_read {qid}", prompt)
        self.assertIn("Domanda", prompt)
        self.assertNotIn("\n", prompt)
        st = self.wait_state()
        self.assertEqual(st["state"], "expected")
        self.assertEqual(st["questions"][qid]["state"], "woken")
        self.assertEqual(st["questions"][qid]["summary"], "uso il tema scuro?")
        # stessa domanda: nessuna seconda sveglia
        self.assertEqual(wake.tick(), {})
        self.assertEqual(len(self.onda.calls), 1)
        # poi l'esito sveglia comunque
        rid = self.deliver()
        self.assertEqual(wake.tick(), {"woken": ["t-20261017-abc123"]})
        self.assertEqual(len(self.onda.calls), 2)
        self.assertIn(f"bus_read {rid}", self.onda.calls[1]["params"]["prompt"])
        self.assertEqual(self.wait_state()["state"], "woken")

    def test_two_questions_two_wakes(self):
        self.sessions([self.alita()])
        self.expect()
        self.question()
        self.question()
        self.assertEqual(wake.tick(), {"woken_question": ["t-20261017-abc123"]})
        self.assertEqual(wake.tick(), {"woken_question": ["t-20261017-abc123"]})
        self.assertEqual(wake.tick(), {})
        self.assertEqual(len(self.onda.calls), 2)

    def test_question_busy_postponed_then_seen(self):
        self.sessions([self.alita(state="working")])
        self.expect()
        qid = self.question()
        self.assertEqual(wake.tick(), {"postponed": ["t-20261017-abc123"]})
        q = self.wait_state()["questions"][qid]
        self.assertEqual(q["state"], "pending")
        self.assertEqual(q["pending_wake"]["reason"], "onda:working")
        # la sessione l'ha letta da sola mentre lavorava: vista, niente sveglia
        src = self.home / "inboxes" / "alita" / "@work-hub" / f"{qid}.json"
        dst = src.parent / ".read" / src.name
        dst.parent.mkdir(exist_ok=True)
        src.rename(dst)
        self.sessions([self.alita()])
        wake.tick()
        self.assertEqual(self.onda.calls, [])
        q = self.wait_state()["questions"][qid]
        self.assertEqual(q["state"], "seen")
        self.assertNotIn("pending_wake", q)

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
        q = self.wait_state()["questions"][qid]
        self.assertEqual(q["state"], "seen")
        self.assertIn("answered_at", q)
        self.assertEqual(self.onda.calls, [])

    def test_question_without_task_id_does_not_wake(self):
        self.sessions([self.alita()])
        self.expect()
        self.deliver(task=None, type="question")
        self.assertEqual(wake.tick(), {})
        self.assertEqual(self.onda.calls, [])

    def test_question_from_chain_blocked(self):
        self.sessions([self.alita()])
        self.expect()
        qid = self.question(wake_chain={"task_id": "t-x", "at": iso(datetime.now(timezone.utc))})
        wake.tick()
        self.assertEqual(self.onda.calls, [])
        self.assertEqual(self.wait_state()["questions"][qid]["state"], "blocked")
        self.assertEqual(self.wait_state()["state"], "expected")

    def test_question_respects_hourly_cap(self):
        self.sessions([self.alita()])
        self.expect()
        d = self.home / "waits" / "alita" / "@work-hub"
        (d / ".wakes.log").write_text("\n".join([f"{time.time():.0f}"] * wake.MAX_PER_HOUR) + "\n")
        qid = self.question()
        self.assertEqual(wake.tick(), {"postponed": ["t-20261017-abc123"]})
        self.assertEqual(self.wait_state()["questions"][qid]["pending_wake"]["reason"], "tetto_orario")

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

    def test_chain_is_blocked(self):
        # A->B->A: l'esito arriva da una sessione appena svegliata (wake_chain).
        self.sessions([self.alita()])
        self.expect()
        self.deliver(wake_chain={"task_id": "t-x", "at": iso(datetime.now(timezone.utc)), "session": "kai/f3"})
        self.assertEqual(wake.tick(), {"blocked": ["t-20261017-abc123"]})
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
        self.assertIn("-t %3 -l Esito del task t-20261017-abc123", sent)
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


if __name__ == "__main__":
    unittest.main(verbosity=1)
