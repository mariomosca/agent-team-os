"""Tests for hooks/agent-team-os-mobile.py (MM31).

Run: /usr/bin/python3 -m unittest tests/test_mobile.py -v   (from the repo root)
A fake caffeinate (tests/fake-bin/caffeinate) stands in for the real one.
"""

import datetime as dt
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "hooks" / "agent-team-os-mobile.py"
FAKE_CAF = REPO / "tests" / "fake-bin" / "caffeinate"

spec = importlib.util.spec_from_file_location("mobile", SCRIPT)
mobile = importlib.util.module_from_spec(spec)


def ts(y, mo, d, h, mi=0):
    return dt.datetime(y, mo, d, h, mi).timestamp()


SAT_0930 = ts(2026, 10, 31, 9, 30)   # Saturday
TUE_1000 = ts(2026, 11, 3, 10, 0)    # Tuesday


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.caf_log = self.home / "caf.log"
        self.env_backup = dict(os.environ)
        os.environ.update({
            "AB_HOME": str(self.home),
            "AB_MOBILE_CAFFEINATE": str(FAKE_CAF),
            "AB_MOBILE_CAF_LOG": str(self.caf_log),
            "AB_MOBILE_DISPLAYS": "internal",
            "AB_MOBILE_NOW": str(SAT_0930),
            "ONDA_TERMINAL_ID": "pane-test",
        })
        os.environ.pop("TMUX_PANE", None)
        os.environ.pop("CLAUDE_PROJECT_DIR", None)
        spec.loader.exec_module(mobile)
        self.transcript = self.home / "t.jsonl"
        self.transcript.write_text('{"type":"user","message":{"content":"ciao"}}\n')

    def tearDown(self):
        caf = mobile.read_json(mobile._caf_path())
        if caf and caf.get("pid"):
            try:
                os.kill(int(caf["pid"]), 15)
            except Exception:
                pass
        os.environ.clear()
        os.environ.update(self.env_backup)
        self.tmp.cleanup()

    def rc(self, url):
        att = {"type": "remote_session_change", "url": url, "sendUserFileHint": bool(url)}
        with self.transcript.open("a") as f:
            f.write(json.dumps({"type": "attachment", "attachment": att}, separators=(",", ":")) + "\n")
            f.write('{"type":"assistant","message":{"content":"ok"}}\n')

    def prompt(self, text, sid="s1", event="UserPromptSubmit", **extra):
        hook = {"hook_event_name": event, "session_id": sid, "cwd": "/x/work-hub",
                "transcript_path": str(self.transcript), "prompt": text, **extra}
        return mobile.handle(hook)

    def state(self, sid="s1"):
        return mobile.read_json(mobile.state_path(sid))

    def caf(self):
        return mobile.read_json(mobile._caf_path())


class ParseSwitch(unittest.TestCase):
    def test_phrases(self):
        p = lambda s: (spec.loader.exec_module(mobile), mobile.parse_switch(s))[1]
        self.assertEqual(p("mobile on"), ("on", None))
        self.assertEqual(p("/mobile on 4h"), ("on", 4.0))
        self.assertEqual(p("Modalità mobile, sono fuori fino a stasera"), ("on", None))
        self.assertEqual(p("sono al telefono"), ("on", None))
        self.assertEqual(p("Sono fuori."), ("on", None))
        self.assertEqual(p("mobile on per 3 ore"), ("on", 3.0))
        self.assertEqual(p("/mobile off"), ("off", None))
        self.assertEqual(p("mobile off"), ("off", None))
        self.assertEqual(p("Sono tornato!"), ("off", None))
        self.assertEqual(p("sono al mac"), ("off", None))
        self.assertEqual(p("/mobile status"), ("status", None))
        self.assertEqual(p("fai il deploy, sono fuori"), (None, None))
        self.assertEqual(p("sono fuori strada con questa idea, ripensiamo tutta l'architettura del modulo"), (None, None))
        self.assertEqual(p("il mobile online va rifatto"), (None, None))
        self.assertEqual(p(""), (None, None))


class Deadline(Base):
    def test_weekend_until_monday_8(self):
        self.assertEqual(mobile.default_until(SAT_0930), ts(2026, 11, 2, 8))
        self.assertEqual(mobile.default_until(ts(2026, 10, 30, 15)), ts(2026, 11, 2, 8))  # Friday afternoon
        self.assertEqual(mobile.default_until(ts(2026, 11, 1, 23)), ts(2026, 11, 2, 8))   # Sunday night

    def test_weekday_plus_8h(self):
        self.assertEqual(mobile.default_until(TUE_1000), TUE_1000 + 8 * 3600)


class Manual(Base):
    def test_on_then_off(self):
        out = self.prompt("mobile on")
        self.assertIn("ATTIVATA", out)
        self.assertIn("Niente riga `# VOICE`", out)
        self.assertIn("coperchio", out)  # internal display only
        st = self.state()
        self.assertTrue(st["on"])
        self.assertEqual(st["source"], "manual")
        self.assertEqual(st["until"], ts(2026, 11, 2, 8))
        self.assertTrue(self.caf()["pid"])
        time.sleep(0.3)
        self.assertIn("-i -m -s -t", self.caf_log.read_text())

        # every later prompt carries the rules, without the activation notice
        out = self.prompt("controlla i log")
        self.assertIn("modalità mobile ON", out)
        self.assertNotIn("ATTIVATA", out)

        out = self.prompt("mobile off")
        self.assertIn("SPENTA", out)
        self.assertNotIn("modalità mobile ON", out)
        self.assertFalse(self.state()["on"])
        self.assertIsNone(self.caf())
        self.assertIsNone(self.prompt("controlla i log"))

    def test_hours(self):
        self.prompt("mobile on 3h")
        self.assertEqual(self.state()["until"], SAT_0930 + 3 * 3600)

    def test_no_lid_warning_with_external_display(self):
        os.environ["AB_MOBILE_DISPLAYS"] = "internal,external"
        self.assertNotIn("coperchio", self.prompt("mobile on").split("[modalità")[0])

    def test_expiry(self):
        self.prompt("mobile on 1h")
        os.environ["AB_MOBILE_NOW"] = str(SAT_0930 + 2 * 3600)
        out = self.prompt("ci sei?")
        self.assertIn("scadenza", out)
        self.assertFalse(self.state()["on"])
        self.assertIsNone(self.caf())

    def test_per_session(self):
        self.prompt("mobile on", sid="a")
        self.assertIsNone(self.prompt("ciao", sid="b"))
        self.assertTrue(mobile.is_active(self.state("a")))
        self.assertIsNone(self.state("b"))

    def test_caffeinate_shared_until_last_session_off(self):
        self.prompt("mobile on 2h", sid="a")
        self.prompt("mobile on 5h", sid="b")
        self.assertEqual(self.caf()["until"], SAT_0930 + 5 * 3600)
        pid_b = self.caf()["pid"]
        self.prompt("mobile off", sid="b")
        self.assertEqual(self.caf()["until"], SAT_0930 + 2 * 3600)
        self.assertNotEqual(self.caf()["pid"], pid_b)
        self.prompt("mobile off", sid="a")
        self.assertIsNone(self.caf())

    def test_session_end_forgets(self):
        self.prompt("mobile on")
        mobile.handle({"hook_event_name": "SessionEnd", "session_id": "s1", "reason": "logout"})
        self.assertIsNone(self.state())
        self.assertIsNone(self.caf())

    def test_clear_hands_state_to_the_new_session(self):
        self.prompt("mobile on")
        mobile.handle({"hook_event_name": "SessionEnd", "session_id": "s1", "reason": "clear"})
        self.assertIsNotNone(self.caf())
        out = mobile.handle({"hook_event_name": "SessionStart", "session_id": "s2", "source": "clear"})
        self.assertIn("modalità mobile ON", out)
        self.assertTrue(self.state("s2")["on"])
        self.assertIsNone(self.state("s1"))

    def test_stop_says_nothing(self):
        self.prompt("mobile on")
        self.assertIsNone(self.prompt("", event="Stop"))


class Auto(Base):
    def test_no_remote_control_no_switch(self):
        self.assertIsNone(self.prompt("ciao"))
        self.assertIsNone(self.state())

    def test_connected_turns_on_disconnected_turns_off(self):
        self.rc(None)
        self.assertIsNone(self.prompt("ciao"))
        self.rc("https://claude.ai/code/session_A")
        out = self.prompt("ciao dal telefono")
        self.assertIn("in automatico", out)
        self.assertEqual(self.state()["source"], "auto")
        self.rc(None)
        out = self.prompt("sono a casa")
        self.assertIn("remote control si è staccato", out)
        self.assertFalse(self.state()["on"])

    def test_stop_detects_too(self):
        self.rc("https://claude.ai/code/session_A")
        self.assertIsNone(self.prompt("", event="Stop"))
        self.assertTrue(self.state()["on"])

    def test_manual_off_holds_for_this_connection(self):
        self.rc("https://claude.ai/code/session_A")
        self.prompt("ciao")
        self.prompt("mobile off")
        self.assertIsNone(self.prompt("altro"))
        self.assertFalse(self.state()["on"])
        # a new connection turns it on again
        self.rc(None)
        self.rc("https://claude.ai/code/session_B")
        self.assertIn("in automatico", self.prompt("di nuovo dal telefono"))

    def test_manual_on_survives_disconnect(self):
        self.prompt("mobile on")
        self.rc("https://claude.ai/code/session_A")
        self.prompt("x")
        self.rc(None)
        self.assertIn("modalità mobile ON", self.prompt("y"))


class HookProcess(Base):
    def test_stdin_stdout_contract(self):
        env = dict(os.environ)
        hook = {"hook_event_name": "UserPromptSubmit", "session_id": "p1", "prompt": "mobile on 1h",
                "transcript_path": str(self.transcript), "cwd": "/x"}
        r = subprocess.run(["/usr/bin/python3", "-I", str(SCRIPT)], input=json.dumps(hook), capture_output=True,
                           text=True, env=env, timeout=10)
        self.assertEqual(r.returncode, 0)
        out = json.loads(r.stdout)
        self.assertEqual(out["hookSpecificOutput"]["hookEventName"], "UserPromptSubmit")
        self.assertIn("ATTIVATA", out["hookSpecificOutput"]["additionalContext"])
        r = subprocess.run(["/usr/bin/python3", "-I", str(SCRIPT)], input="not json", capture_output=True,
                           text=True, env=env, timeout=10)
        self.assertEqual((r.returncode, r.stdout), (0, ""))
        r = subprocess.run(["/usr/bin/python3", "-I", str(SCRIPT), "off", "--session", "p1"], capture_output=True,
                           text=True, env=env, timeout=10)
        self.assertEqual(r.returncode, 0)
        self.assertIn("SPENTA", r.stdout)


if __name__ == "__main__":
    unittest.main()
