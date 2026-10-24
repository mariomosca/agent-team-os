"""Hook v3 (centralino/hooks) lanciati come li lancia Claude Code, contro un daemon vero."""

import json
import os
import subprocess
import tempfile
import threading
import time
from pathlib import Path

import pytest

from centralino import config
from centralino.client import call

HOOKS = Path(__file__).resolve().parents[1] / "hooks"


@pytest.fixture
def bus(monkeypatch):
    home = Path(tempfile.mkdtemp(prefix="ch-", dir="/tmp"))
    ws = home / "ws"
    (home / "AGENT_MAP.json").write_text(json.dumps({
        "rules": [{"pattern": str(ws / "kai"), "agent": "kai"}, {"pattern": str(ws / "alita"), "agent": "alita"}],
        "agents": {"kai": {"display_name": "Kai", "role": "tech", "persona_file": "personas/kai.md"}, "alita": {}},
        "hosts": {"laptop": {"hub": True}}}))
    (home / "personas").mkdir()
    (home / "personas" / "kai.md").write_text("Sono Kai.\n")
    for a in ("kai", "alita"):
        (ws / a / "s1").mkdir(parents=True)
    config.scrivi(home, config.Modo("sqlite", "attiva", False))
    cfg = home / "cfg"
    (cfg / "scripts").mkdir(parents=True)
    os.symlink(HOOKS / "agent-team-os-centralino.py", cfg / "scripts" / "agent-team-os-centralino.py")
    from centralino.server import Daemon
    d = Daemon(home, giri=False).avvia()
    t = threading.Thread(target=d.esegui, daemon=True)
    t.start()
    yield home, cfg
    d.ferma()
    t.join(5)


def hook(home, cfg, nome, who, stdin=None):
    env = {k: v for k, v in os.environ.items() if not k.startswith(("AB_", "ONDA_", "CLAUDE_", "TMUX"))}
    env.update(AB_HOME=str(home), CLAUDE_CONFIG_DIR=str(cfg), CLAUDE_PROJECT_DIR=str(home / "ws" / who / "s1"),
               ONDA_TERMINAL_ID=f"pane-{who}")
    r = subprocess.run([str(HOOKS / nome)], input=json.dumps(stdin or {"session_id": f"sess-{who}"}),
                       capture_output=True, text=True, env=env, timeout=15)
    assert r.returncode == 0, r.stderr
    return r.stdout


def test_ciclo_di_vita_degli_hook(bus):
    home, cfg = bus
    out = hook(home, cfg, "agent-team-os-load.sh", "kai")
    assert "You are Kai (kai@laptop/s1)" in out and "Inbox: empty." in out and "Sono Kai." in out
    reg = json.loads((home / "registry" / "kai.d" / "s1.json").read_text())
    assert reg["active"] and reg["pid"] > 1
    assert hook(home, cfg, "agent-team-os-stop.sh", "kai") == ""          # niente da fare: nessun blocco
    call("send", {"to": "kai/s1", "intent": "lavoro", "summary": "fai", "message_type": "request", "kind": "delega",
                  "priority": "high"}, caller={"cwd": str(home / "ws" / "alita" / "s1")}, sock=home / "centralino.sock")
    ctx = json.loads(hook(home, cfg, "agent-team-os-urgent.sh", "kai"))
    assert "1 urgent/high" in ctx["hookSpecificOutput"]["additionalContext"]
    blocco = json.loads(hook(home, cfg, "agent-team-os-stop.sh", "kai"))
    assert blocco["decision"] == "block" and "block 1/3" in blocco["reason"]
    hook(home, cfg, "agent-team-os-end.sh", "kai")
    assert not (home / "registry" / "kai.d" / "s1.json").exists()


def test_centralino_giu_gli_hook_tacciono(tmp_path):
    env = dict(os.environ, AB_HOME=str(tmp_path), CLAUDE_CONFIG_DIR=str(HOOKS.parent))
    for nome in ("agent-team-os-load.sh", "agent-team-os-stop.sh", "agent-team-os-urgent.sh", "agent-team-os-end.sh"):
        r = subprocess.run(["/usr/bin/python3", "-I", str(HOOKS / "agent-team-os-centralino.py"),
                            nome.split("-")[-1][:-3].replace("load", "start").replace("urgent", "prompt")],
                           input="{}", capture_output=True, text=True, env=env, timeout=15)
        assert r.returncode == 0 and r.stdout == ""


def test_onda_prompt_delega(bus):
    home, cfg = bus
    env = {k: v for k, v in os.environ.items() if not k.startswith(("AB_", "ONDA_", "CLAUDE_", "TMUX"))}
    env.update(AB_HOME=str(home), CLAUDE_PROJECT_DIR=str(home / "ws" / "alita" / "s1"))
    ev = {"session_id": "sess-alita", "tool_input": {"terminalId": "pane-x", "prompt": "[delega] fai il lavoro X"}}
    r = subprocess.run(["/usr/bin/python3", "-I", str(HOOKS / "agent-team-os-onda-prompt.py")], input=json.dumps(ev),
                       capture_output=True, text=True, env=env, timeout=15)
    out = json.loads(r.stdout)["hookSpecificOutput"]
    assert out["permissionDecision"] == "allow" and "task_id='t-" in out["updatedInput"]["prompt"]
    assert not out["updatedInput"]["prompt"].startswith("[delega]")
    lungo = {"tool_input": {"prompt": "x" * 300}}
    r = subprocess.run(["/usr/bin/python3", "-I", str(HOOKS / "agent-team-os-onda-prompt.py")], input=json.dumps(lungo),
                       capture_output=True, text=True, env=env, timeout=15)
    assert json.loads(r.stdout)["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_concorrenza_modo_attivo(bus):
    """Richieste in parallelo mentre il giro della sveglia scrive: nessuna transazione condivisa."""
    home, cfg = bus
    from centralino.server import Daemon  # noqa: F401
    sock = home / "centralino.sock"
    errori = []
    fermo = threading.Event()

    def giri():
        while not fermo.is_set():
            r = call("hook.prompt", caller={"cwd": str(home / "ws" / "kai" / "s1")}, sock=sock)
            if not r["ok"]:
                errori.append(r)

    def manda(i):
        r = call("send", {"to": "kai/s1", "intent": f"m{i}", "summary": "x", "message_type": "request",
                          "kind": "delega"}, caller={"cwd": str(home / "ws" / "alita" / "s1")}, sock=sock)
        if not r["ok"]:
            errori.append(r)
    t = threading.Thread(target=giri)
    t.start()
    ts = [threading.Thread(target=manda, args=(i,)) for i in range(30)]
    [x.start() for x in ts]
    [x.join() for x in ts]
    fermo.set()
    t.join()
    assert not errori, errori[:2]
    st = call("admin.stats", sock=sock)["result"]
    assert st["messaggi"] == 30 and st["attese_aperte"] == 30
