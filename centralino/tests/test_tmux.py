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
# Schermi veri del mini (capture-pane -e, Claude Code 29/10/2026): suggerimento e placeholder in
# SGR 2 (dim), testo scritto dall'utente senza attributi.
RIGA = "\x1b[38;5;244m" + "─" * 40 + "\n"
SUGGERIMENTO = ("● Fatto.\n\n" + RIGA + "\x1b[39m❯\u00a0\x1b[2mrebase su develop dopo il merge della #210\x1b[0m\n"
                + RIGA + "\x1b[39m  \x1b[38;5;211m⏵⏵ bypass permissions on\x1b[38;5;246m (shift+tab to cycle)\x1b[39m\n")
PLACEHOLDER = ("● Fatto.\n\n" + RIGA + "\x1b[39m❯ \x1b[2mTry \"edit <filepath> to...\"\x1b[0m\n" + RIGA)
SCRITTO = ("● Fatto.\n\n" + RIGA + "\x1b[39m❯ testo scritto a mano\n" + RIGA)
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


def test_testo_attenuato_non_conta():
    pieno, vivo = canale.testi_schermo(SUGGERIMENTO)
    assert "rebase su develop" in pieno and "rebase su develop" not in vivo
    assert pieno.count("\n") == vivo.count("\n")
    assert canale.screen_refusal(SUGGERIMENTO) is None
    assert canale.screen_refusal(PLACEHOLDER) is None
    assert canale.screen_refusal(SCRITTO) == "has_text"
    # colori estesi (38;5;n, 38;2;r;g;b) non vanno letti come dim
    assert canale.testi_schermo("\x1b[38;5;2mA\x1b[38;2;1;2;3mB\x1b[0m")[1] == "AB"
    assert canale.testi_schermo("\x1b[2mA\x1b[22mB")[1] == "B"


@pytest.mark.parametrize("schermo", [IDLE, SUGGERIMENTO, PLACEHOLDER])
def test_pannello_fermo_si_sveglia(tmux, schermo):
    (tmux / "screen").write_text(schermo)
    assert canale.via_tmux("%3", "Ritorno sul task t-x") == "tmux"
    sent = (tmux / "sent").read_text()
    assert "-t %3 -l Ritorno sul task t-x" in sent and "-t %3 Enter" in sent


@pytest.mark.parametrize("schermo,motivo", [(TESTO, "tmux:has_text"), (SCRITTO, "tmux:has_text"),
                                            (OCCUPATA, "tmux:busy")])
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


def test_rinvio_per_testo_si_riprova_e_torna_nel_log(tmux, tmp_path):
    """t-20261029-gsmn19: la delega rinviata per has_text si riprova a ogni giro; il rinvio identico
    torna in wake_decisions ogni RIPROVA_LOG_S, e la sveglia parte appena la casella è libera."""
    from datetime import datetime, timedelta, timezone

    from centralino import tempo
    from centralino.sveglia import RIPROVA_LOG_S

    t = [datetime(2026, 10, 29, 11, 19, tzinfo=timezone.utc)]
    tempo.imposta(lambda: t[0])
    home = tmp_path / "mini"
    ws = home / "ws"
    for a in ("nico", "leo"):
        (ws / a / "s1").mkdir(parents=True)
    (home / "AGENT_MAP.json").write_text(json.dumps({
        "rules": [{"pattern": str(ws / "nico"), "agent": "nico"}, {"pattern": str(ws / "leo"), "agent": "leo"}],
        "agents": {"nico": {}, "leo": {}}, "hosts": {"laptop": {"hub": True}, "mini": {}}}))
    (home / "HOST").write_text("mini\n")
    c = Centralino(home)
    HookOps(c).registra()
    try:
        nico = {"cwd": str(ws / "nico" / "s1"), "tmux_pane": "%38"}
        assert c.handle_request({"v": 1, "id": "1", "op": "hook.start", "caller": nico, "args": {}})["ok"]
        assert c.handle_request({"v": 1, "id": "1", "op": "hook.stop", "caller": nico, "args": {}})["ok"]
        r = c.handle_request({"v": 1, "id": "2", "op": "send", "caller": {"cwd": str(ws / "leo" / "s1")},
                              "args": {"to": "nico/s1", "intent": "x", "summary": "fai X",
                                       "message_type": "request", "kind": "delega"}})
        assert r["ok"], r
        log = lambda: c.store.read().execute(  # noqa: E731
            "SELECT decisione, motivo FROM wake_decisions WHERE agent='nico' ORDER BY id").fetchall()
        sv = Sveglia(c)

        def giro(dopo_s: float):
            t[0] += timedelta(seconds=dopo_s)
            c._write(lambda: c._conn().execute("UPDATE sessions SET last_heartbeat=? WHERE agent='nico'",
                                               (tempo.iso_now(),)))
            return [(d.decisione, d.motivo) for d in sv.giro_sveglia()]

        (tmux / "screen").write_text(SCRITTO)
        assert giro(0) == [("coda", "tmux:has_text")]
        assert giro(10) == [("coda", "tmux:has_text")]  # riprovata, non riscritta
        assert len(log()) == 1
        assert giro(RIPROVA_LOG_S) == [("coda", "tmux:has_text")]
        assert [tuple(x) for x in log()] == [("coda", "tmux:has_text")] * 2
        # solo il suggerimento grigio nella casella: la sveglia parte
        (tmux / "screen").write_text(SUGGERIMENTO)
        assert giro(10) == [("sveglia", None)]
        assert "-t %38 -l " in (tmux / "sent").read_text()
    finally:
        tempo.imposta(None)
        c.store.close()


def test_rinvio_per_testo_da_30_minuti_avvisa_chi_ha_delegato(tmux, tmp_path):
    """Dopo TESTO_AVVISO_S di has_text un evento (non sveglia, non blocca lo Stop) va a chi ha
    delegato, una volta per attesa; Alita (hub) non riceve niente."""
    from datetime import datetime, timedelta, timezone

    from centralino import regole, tempo
    from centralino.sveglia import INTENT_TESTO, TESTO_AVVISO_S

    t = [datetime(2026, 10, 29, 11, 19, tzinfo=timezone.utc)]
    tempo.imposta(lambda: t[0])
    home = tmp_path / "mini"
    ws = home / "ws"
    for a in ("nico", "leo", "alita"):
        (ws / a / "s1").mkdir(parents=True)
    (home / "AGENT_MAP.json").write_text(json.dumps({
        "rules": [{"pattern": str(ws / a), "agent": a} for a in ("nico", "leo", "alita")],
        "agents": {"nico": {}, "leo": {}, "alita": {}}, "cc_awareness": {"hub": "alita"},
        "hosts": {"laptop": {"hub": True}, "mini": {}}}))
    (home / "HOST").write_text("mini\n")
    c = Centralino(home)
    HookOps(c).registra()
    try:
        nico = {"cwd": str(ws / "nico" / "s1"), "tmux_pane": "%38"}
        assert c.handle_request({"v": 1, "id": "1", "op": "hook.start", "caller": nico, "args": {}})["ok"]
        assert c.handle_request({"v": 1, "id": "1", "op": "hook.stop", "caller": nico, "args": {}})["ok"]
        r = c.handle_request({"v": 1, "id": "2", "op": "send", "caller": {"cwd": str(ws / "leo" / "s1")},
                              "args": {"to": "nico/s1", "intent": "x", "summary": "fai X",
                                       "message_type": "request", "kind": "delega"}})
        assert r["ok"], r
        tid = r["result"]["task_id"]
        sv = Sveglia(c)
        (tmux / "screen").write_text(SCRITTO)

        def giro(dopo_s: float):
            t[0] += timedelta(seconds=dopo_s)
            c._write(lambda: c._conn().execute("UPDATE sessions SET last_heartbeat=? WHERE agent='nico'",
                                               (tempo.iso_now(),)))
            return sv.giro()["avvisi"]

        avvisi = lambda: c.store.read().execute(  # noqa: E731
            "SELECT to_agent, to_slug, raw FROM messages WHERE intent=?", (INTENT_TESTO,)).fetchall()
        assert giro(0) == []
        for _ in range(int(TESTO_AVVISO_S // 300) - 1):
            assert giro(300) == []
        assert avvisi() == []
        assert len(giro(301)) == 1
        rows = avvisi()
        assert [(x["to_agent"], x["to_slug"]) for x in rows] == [("leo", "s1")]
        rec = json.loads(rows[0]["raw"])
        assert rec["type"] == "event" and rec["task_id"] == tid and "nico/s1" in rec["payload"]["summary"]
        assert regole.classifica(rec, "leo", "s1", {}) == "nessuna"  # non sveglia, non blocca lo Stop
        assert giro(600) == [] and len(avvisi()) == 1  # una volta sola per attesa
        assert not c.store.read().execute("SELECT 1 FROM messages WHERE to_agent='alita'").fetchone()
    finally:
        tempo.imposta(None)
        c.store.close()
