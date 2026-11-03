"""TEO26-F1.4: ``centralino chiama``, la casella di un agente headless (Teo sul mini) senza MCP,
né Onda né tmux. Il runner apre la sessione con ``session.event start``, legge la casella almeno
ogni 60 s (ogni chiamata è un battito) e risponde sulle deleghe con lo stesso task_id."""

import io
import json
import tempfile
import threading
from pathlib import Path

import pytest

from centralino.cli import main
from centralino.client import call


@pytest.fixture
def bus():
    home = Path(tempfile.mkdtemp(prefix="cc-", dir="/tmp"))
    (home / "AGENT_MAP.json").write_text(json.dumps({
        "rules": [{"pattern": str(home / "ws" / "leo"), "agent": "leo"},
                  {"pattern": str(home / "ws" / "teo"), "agent": "teo"},
                  {"pattern": str(home / "ws" / "alita"), "agent": "alita"}],
        "agents": {"leo": {"domain": "brandart"}, "alita": {"domain": "global"},
                   "teo": {"domain": "brandart", "workspace_root": str(home / "ws" / "teo")}},
        "cc_awareness": {"hub": "alita"},
        "hosts": {"laptop": {"hub": True}}}))
    for a in ("leo", "teo", "alita"):
        (home / "ws" / a).mkdir(parents=True)
    from centralino.server import Daemon
    d = Daemon(home, giri=False).avvia()
    t = threading.Thread(target=d.esegui, daemon=True)
    t.start()
    assert call("session.event", {"event": "start"}, caller={"cwd": str(home / "ws" / "leo")},
                sock=home / "centralino.sock")["ok"]
    yield home
    d.ferma()
    t.join(5)


def chiama(home, capsys, monkeypatch, op, args=None, *extra, stdin=None):
    monkeypatch.setattr("sys.stdin", io.StringIO(stdin or ""))
    argv = ["--home", str(home), "chiama", op, "--agent", "teo", "--slug", "teo",
            "--cwd", str(home / "ws" / "teo"), *extra]
    if args is not None:
        argv += ["--args", json.dumps(args)]
    rc = main(argv)
    out = capsys.readouterr().out
    return rc, json.loads(out) if out.strip() else None


def test_runner_headless_riceve_e_risponde_su_delega(bus, capsys, monkeypatch):
    sock = bus / "centralino.sock"
    rc, r = chiama(bus, capsys, monkeypatch, "session.event", {"event": "start", "source": "runner"})
    assert rc == 0 and r["ok"], r

    # leo delega a teo nudo: nessuna sessione di teo ha un canale, resta nella casella condivisa
    leo = {"cwd": str(bus / "ws" / "leo")}
    s = call("send", {"to": "teo", "intent": "prova", "summary": "fai X", "message_type": "request",
                      "kind": "delega"}, caller=leo, sock=sock)
    assert s["ok"], s
    tid = s["result"]["task_id"]

    rc, r = chiama(bus, capsys, monkeypatch, "inbox")
    assert rc == 0 and r["result"]["count"] == 1
    mid = r["result"]["messages"][0]["id"]
    assert r["result"]["messages"][0]["task_id"] == tid

    # la chiamata vale come battito: teo/teo è viva (per heartbeat, senza canale)
    st = call("status", {"agent": "teo"}, caller=leo, sock=sock)["result"]["roster"][0]["live_sessions"]
    assert [(s["slug"], s["canale"]) for s in st] == [("teo", "nessuno")]

    # gli argomenti possono arrivare anche da stdin
    rc, r = chiama(bus, capsys, monkeypatch, "reply", None,
                   stdin=json.dumps({"message_id": mid, "summary": "fatto X"}))
    assert rc == 0 and r["ok"], r

    ritorni = call("inbox", {}, caller=leo, sock=sock)["result"]["messages"]
    assert [(m["task_id"], m["type"]) for m in ritorni] == [(tid, "response")]


def test_teo_delega_e_il_ritorno_torna_a_teo(bus, capsys, monkeypatch):
    sock = bus / "centralino.sock"
    chiama(bus, capsys, monkeypatch, "session.event", {"event": "start"})
    rc, r = chiama(bus, capsys, monkeypatch, "send", {"to": "leo", "intent": "d", "summary": "serve Y",
                                                      "message_type": "request", "kind": "delega"})
    assert rc == 0 and r["ok"], r
    tid = r["result"]["task_id"]
    leo = {"cwd": str(bus / "ws" / "leo")}
    m = call("inbox", {}, caller=leo, sock=sock)["result"]["messages"][0]
    assert call("reply", {"message_id": m["id"], "summary": "Y fatto"}, caller=leo, sock=sock)["ok"]
    rc, r = chiama(bus, capsys, monkeypatch, "inbox")
    msgs = r["result"]["messages"]
    assert [(x["task_id"], x["type"]) for x in msgs] == [(tid, "response")]


def test_niente_cc_al_hub_per_il_traffico_brandart(bus, capsys, monkeypatch):
    """Review di Alita del 29/10: dal dominio brandart al personale niente contenuto. Il bus v3
    non produce cc-notify: un messaggio teo → leo non lascia nulla nella casella di alita."""
    sock = bus / "centralino.sock"
    assert call("session.event", {"event": "start"}, caller={"cwd": str(bus / "ws" / "alita")}, sock=sock)["ok"]
    chiama(bus, capsys, monkeypatch, "session.event", {"event": "start"})
    chiama(bus, capsys, monkeypatch, "send", {"to": "leo", "intent": "ev", "summary": "importo 12.345 euro",
                                              "message_type": "event"})
    hub = call("inbox", {"all_sessions": True}, caller={"cwd": str(bus / "ws" / "alita")}, sock=sock)["result"]
    assert hub["count"] == 0


def test_errori(bus, capsys, monkeypatch):
    rc, r = chiama(bus, capsys, monkeypatch, "send", {"to": "leo", "intent": "x", "summary": "y",
                                                      "message_type": "request", "kind": "comunicazione"})
    assert rc == 1 and r["ok"] is False and r["error"]["code"]
    assert chiama(bus, capsys, monkeypatch, "inbox", None, "--args", "[1]")[0] == 2
    assert main(["--home", "/tmp/nessun-bus-qui", "chiama", "inbox", "--agent", "teo", "--slug", "teo",
                 "--args", "{}"]) == 2
