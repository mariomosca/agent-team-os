"""BR24-F5.5e: un claude non interattivo dentro il pannello di un'altra sessione (mcp_sync --verify
lancia `claude mcp list` nel workspace di Vera) non registra una sessione, non si prende il pannello
altrui, e l'avviso "senza canale" vale solo per sessioni vive e messaggi recenti."""

import importlib.util
import json
import os
from datetime import timedelta
from pathlib import Path

import pytest

from centralino.hookops import HookOps
from centralino.ops import Centralino
from centralino.sveglia import Sveglia
from centralino.tempo import iso, iso_now, now

HOOK = Path(__file__).resolve().parents[2] / "hooks" / "agent-team-os-centralino.py"


def _hook_mod():
    spec = importlib.util.spec_from_file_location("hook_centralino", HOOK)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.parametrize("argv,atteso", [
    ("claude --model opus --dangerously-skip-permissions", False),
    ("claude", False),
    ("/opt/homebrew/bin/node /x/node_modules/@anthropic-ai/claude-code/cli.js --resume", False),
    ("claude mcp list", True),
    ("claude -p ciao --model haiku", True),
    ("claude --model haiku --print ciao", True),
    ("/opt/homebrew/bin/node /x/cli.js doctor", True),
    ("zsh -lc qualcosa", False),
])
def test_non_interattiva(argv, atteso):
    assert _hook_mod().non_interattiva(argv.split()) is atteso


@pytest.fixture
def c(tmp_path):
    ws = tmp_path / "ws"
    for a in ("kai", "vera"):
        (ws / a / "s1").mkdir(parents=True)
    (tmp_path / "AGENT_MAP.json").write_text(json.dumps({
        "rules": [{"pattern": str(ws / "kai"), "agent": "kai"}, {"pattern": str(ws / "vera"), "agent": "vera"}],
        "agents": {"kai": {}, "vera": {}}}))
    (tmp_path / "onda").mkdir()
    (tmp_path / "onda" / "sessions.json").write_text(json.dumps({
        "v": 1, "ondaPid": os.getpid(), "writtenAt": iso_now(),
        "sessions": [{"terminalId": "pane-kai", "agent": "kai", "slug": "s1", "state": "working",
                      "sessionId": "sess-kai", "hasText": False, "stateSince": iso_now()}]}))
    cen = Centralino(tmp_path)
    HookOps(cen).registra()
    cen.ws = ws
    yield cen
    cen.store.close()


def _start(c, agent, terminale):
    r = c.handle_request({"v": 1, "id": "1", "op": "hook.start", "args": {},
                          "caller": {"cwd": str(c.ws / agent / "s1"), "onda_terminal_id": terminale}})
    assert r["ok"], r
    return c.store.read().execute("SELECT onda_terminal_id FROM sessions WHERE agent=?", (agent,)).fetchone()[0]


def test_pannello_altrui_scartato(c):
    assert _start(c, "kai", "pane-kai") == "pane-kai"       # il suo
    assert _start(c, "vera", "pane-kai") is None            # ereditato dal pannello di kai
    assert _start(c, "vera", "pane-nuovo") == "pane-nuovo"  # sconosciuto a Onda: si tiene


def _messaggio_senza_canale(c, eta_giorni: int) -> str:
    _start(c, "vera", None)
    r = c.handle_request({"v": 1, "id": "2", "op": "send", "caller": {"cwd": str(c.ws / "kai" / "s1")},
                          "args": {"to": "vera/s1", "intent": "x", "summary": "brief", "message_type": "brief",
                                   "kind": "delega"}})
    mid = r["result"]["id"]
    eta = timedelta(days=eta_giorni) if eta_giorni else timedelta(minutes=11)
    c._write(lambda: c._conn().execute("UPDATE messages SET ts=? WHERE id=?", (iso(now() - eta), mid)))
    return mid


def _sveglia(c, notifiche):
    return Sveglia(c, notifica=lambda titolo, testo: notifiche.append(testo))


def test_avviso_senza_canale_anche_a_sessione_chiusa(c):
    # AM26-F2.2: un messaggio azionabile per una stanza chiusa è fermo lo stesso; nessun hub qui: notifica.
    # WT30-F4.1 R3: la delega non presa passa alla casella condivisa di vera alla chiusura della stanza
    mid = _messaggio_senza_canale(c, 0)
    c.handle_request({"v": 1, "id": "3", "op": "hook.end", "args": {}, "caller": {"cwd": str(c.ws / "vera" / "s1")}})
    assert c.store.message(mid)["to_slug"] is None
    notifiche = []
    assert len(_sveglia(c, notifiche).avvisi_senza_canale()) == 1 and "per vera da kai/s1" in notifiche[0]


def test_avviso_senza_canale_non_per_messaggi_vecchi(c):
    _messaggio_senza_canale(c, 40)
    assert _sveglia(c, []).avvisi_senza_canale() == []


def test_avviso_senza_canale_resta_per_il_caso_vero(c):
    _messaggio_senza_canale(c, 0)
    notifiche = []
    s = _sveglia(c, notifiche)
    assert len(s.avvisi_senza_canale()) == 1
    assert s.avvisi_senza_canale() == [] and len(notifiche) == 1   # una volta per gruppo
