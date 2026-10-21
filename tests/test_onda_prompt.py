"""Test dell'hook PreToolUse su onda_agent_prompt (BS21-F1.4): python3 -I tests/test_onda_prompt.py

AB_HOME temporaneo, l'hook lanciato come lo lancia Claude Code (JSON su stdin).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOOK = ROOT / "hooks" / "agent-team-os-onda-prompt.py"
LONG = "Kai, " + "rifai il parser del bus e aggiungi i test. " * 8


class OndaPromptTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="abonda.")
        self.home = Path(self.tmp.name)
        (self.home / "AGENT_MAP.json").write_text(json.dumps({
            "agents": {"alita": {}, "kai": {}, "nico": {}},
            "rules": [{"pattern": "/w/work-hub", "agent": "alita"},
                      {"pattern": "/w/Brandart/", "agent": "nico"}],
            "hosts": {"laptop": {"hub": True}}}))
        (self.home / "onda").mkdir()
        (self.home / "onda" / "sessions.json").write_text(json.dumps({"v": 1, "sessions": [
            {"terminalId": "pane-kai", "agent": "kai", "slug": "agent-team-os"}]}))

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def run_hook(self, prompt: str, cwd: str = "/w/work-hub", terminal: str = "pane-kai") -> dict | None:
        env = {k: v for k, v in os.environ.items() if not k.startswith(("AB_", "ONDA_", "TMUX"))}
        env.update(AB_HOME=str(self.home), ONDA_TERMINAL_ID="pane-alita", AB_HOST="laptop")
        ev = {"hook_event_name": "PreToolUse", "tool_name": "mcp__onda__onda_agent_prompt", "cwd": cwd,
              "session_id": "sess-1", "tool_input": {"terminalId": terminal, "prompt": prompt}}
        r = subprocess.run([sys.executable, "-I", str(HOOK)], input=json.dumps(ev), capture_output=True,
                           text=True, env=env, timeout=20)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)["hookSpecificOutput"] if r.stdout.strip() else None

    def waits(self) -> list[dict]:
        return [json.loads(p.read_text()) for p in self.home.glob("waits/*/@*/t-*.json")]

    def test_delega_registers_the_wait_and_appends_the_line(self):
        out = self.run_hook("[delega] " + LONG)
        self.assertEqual(out["permissionDecision"], "allow")
        [w] = self.waits()
        self.assertEqual((w["agent"], w["slug"], w["delegated_to"]), ("alita", "work-hub", "kai/agent-team-os"))
        self.assertEqual(w["target"]["onda_terminal_id"], "pane-alita")
        p = out["updatedInput"]["prompt"]
        self.assertTrue(p.startswith("Kai, rifai"))
        self.assertIn(f"task_id='{w['task_id']}'", p)
        self.assertIn("bus_send a alita@laptop/work-hub", p)
        self.assertEqual(out["updatedInput"]["terminalId"], "pane-kai")
        self.assertIn(w["task_id"], out["permissionDecisionReason"])

    def test_delega_works_between_any_two_agents(self):
        out = self.run_hook("[DELEGA]  guarda il diff", cwd="/w/Brandart/hr-bot")
        [w] = self.waits()
        self.assertEqual((w["agent"], w["slug"]), ("nico", "hr-bot"))
        self.assertIn("nico@laptop/hr-bot", out["updatedInput"]["prompt"])

    def test_comunicazione_strips_the_mark_and_registers_nothing(self):
        out = self.run_hook("[comunicazione] " + LONG)
        self.assertEqual(out["permissionDecision"], "allow")
        self.assertEqual(out["updatedInput"]["prompt"], LONG)
        self.assertEqual(self.waits(), [])

    def test_long_prompt_without_a_choice_is_refused(self):
        out = self.run_hook(LONG)
        self.assertEqual(out["permissionDecision"], "deny")
        self.assertIn("[delega]", out["permissionDecisionReason"])
        self.assertEqual(self.waits(), [])

    def test_short_prompts_and_manual_task_ids_pass(self):
        self.assertIsNone(self.run_hook("1"))
        self.assertIsNone(self.run_hook(LONG + " ... task_id='t-20261021-abc123'."))
        self.assertEqual(self.waits(), [])

    def test_unmapped_folder_cannot_delegate(self):
        out = self.run_hook("[delega] fai", cwd="/altrove")
        self.assertEqual(out["permissionDecision"], "deny")
        self.assertEqual(self.waits(), [])

    def test_broken_input_never_blocks(self):
        env = dict(os.environ, AB_HOME=str(self.home))
        r = subprocess.run([sys.executable, "-I", str(HOOK)], input="non json", capture_output=True,
                           text=True, env=env, timeout=20)
        self.assertEqual((r.returncode, r.stdout), (0, ""))


if __name__ == "__main__":
    unittest.main(verbosity=1)
