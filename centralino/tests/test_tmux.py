"""Canale tmux della sveglia (il mini, sessioni fuori da Onda, BR24-F5.7), col tmux finto del
repo (tests/fake-bin/tmux): capture-pane legge $FAKE_TMUX_DIR/screen, send-keys scrive in sent."""

import json
from pathlib import Path

import pytest

from centralino import canale
from centralino.canale import Rinvio
from centralino.hookops import HookOps
from centralino.ops import Centralino
from centralino.sveglia import Sveglia

FAKE_TMUX = Path(__file__).resolve().parents[2] / "tests" / "fake-bin" / "tmux"
IDLE = "● Fatto.\n\n" + "─" * 40 + "\n❯ \n" + "─" * 40 + "\n  ? for shortcuts\n"
TESTO = "● Fatto.\n\n" + "─" * 40 + "\n❯ sto scrivendo una cosa\n" + "─" * 40 + "\n"
OCCUPATA = "✻ Working… (12s · esc to interrupt)\n\n" + "─" * 40 + "\n❯ \n" + "─" * 40 + "\n"


@pytest.fixture
def tmux(tmp_path, monkeypatch):
    d = tmp_path / "tmux"
    d.mkdir()
    monkeypatch.setenv("AB_TMUX_BIN", str(FAKE_TMUX))
    monkeypatch.setenv("FAKE_TMUX_DIR", str(d))
    monkeypatch.setattr(canale.time, "sleep", lambda s: None)
    return d


def test_casella_del_prompt():
    assert canale.read_prompt_box(IDLE) == ""
    assert canale.read_prompt_box(TESTO) == "sto scrivendo una cosa"
    assert canale.read_prompt_box("$ ls\nfoo\n") is None


def test_pannello_fermo_si_sveglia(tmux):
    (tmux / "screen").write_text(IDLE)
    assert canale.via_tmux("%3", "Ritorno sul task t-x") == "tmux"
    sent = (tmux / "sent").read_text()
    assert "-t %3 -l Ritorno sul task t-x" in sent and "-t %3 Enter" in sent


@pytest.mark.parametrize("schermo,motivo", [(TESTO, "tmux:has_text"), (OCCUPATA, "tmux:busy")])
def test_testo_o_lavoro_rinviano(tmux, schermo, motivo):
    (tmux / "screen").write_text(schermo)
    with pytest.raises(Rinvio, match=motivo):
        canale.via_tmux("%3", "x")
    assert not (tmux / "sent").exists()


def test_pannello_assente(tmux):
    with pytest.raises(Rinvio, match="tmux:pane_assente"):
        canale.via_tmux("%9", "x")


def test_sul_mini_una_delega_sveglia_il_pannello_tmux(tmux, tmp_path):
    home = tmp_path / "mini"
    ws = home / "ws"
    for a in ("kai", "alita"):
        (ws / a / "s1").mkdir(parents=True)
    (home / "AGENT_MAP.json").write_text(json.dumps({
        "rules": [{"pattern": str(ws / "kai"), "agent": "kai"}, {"pattern": str(ws / "alita"), "agent": "alita"}],
        "agents": {"kai": {}, "alita": {}}, "hosts": {"laptop": {"hub": True}, "mini": {}}}))
    (home / "HOST").write_text("mini\n")
    c = Centralino(home)
    HookOps(c).registra()
    try:
        kai = {"cwd": str(ws / "kai" / "s1"), "tmux_pane": "%3"}
        assert c.handle_request({"v": 1, "id": "1", "op": "hook.start", "caller": kai, "args": {}})["ok"]
        assert c.vivo.stato("kai", "s1").canale == "tmux"
        assert c.handle_request({"v": 1, "id": "1", "op": "hook.stop", "caller": kai, "args": {}})["ok"]  # turno finito
        r = c.handle_request({"v": 1, "id": "2", "op": "send", "caller": {"cwd": str(ws / "alita" / "s1")},
                              "args": {"to": "kai/s1", "intent": "x", "summary": "fai X",
                                       "message_type": "request", "kind": "delega"}})
        assert r["ok"], r
        (tmux / "screen").write_text(IDLE)
        decisioni = Sveglia(c).giro_sveglia()
        assert [(d.decisione, d.canale) for d in decisioni] == [("sveglia", "tmux")]
        assert "-t %3 -l " in (tmux / "sent").read_text()
        # BR24-F6.6: la sveglia consegnata vale come lettura della delega
        m = c.store.message(r["result"]["id"])
        assert m["letto_at"] and m["letto_da"] == "kai/s1"
    finally:
        c.store.close()
