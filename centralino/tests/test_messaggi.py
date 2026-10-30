"""WT30-F4.1: ciclo di vita dei messaggi (R1-R4) con il Daemon vero sul socket.

Gli stessi quattro casi del banco (``caso_wt30_*``), senza Onda: le sessioni sono vive per
battito e senza canale. La grazia di R3 si regola con ``AB_NON_LETTO_S``.
"""

import json
import tempfile
import threading
from datetime import timedelta
from pathlib import Path

import pytest

from centralino.client import call
from centralino.sveglia import Sveglia
from centralino.tempo import iso, now


@pytest.fixture
def bus(monkeypatch):
    monkeypatch.setenv("AB_NON_LETTO_S", "0")
    home = Path(tempfile.mkdtemp(prefix="cm-", dir="/tmp"))  # socket Unix sotto i 104 caratteri
    (home / "AGENT_MAP.json").write_text(json.dumps({
        "rules": [{"pattern": str(home / "ws" / "kai"), "agent": "kai"},
                  {"pattern": str(home / "ws" / "alita"), "agent": "alita"}],
        "agents": {"kai": {}, "alita": {}}, "hosts": {"laptop": {"hub": True}},
        "cc_awareness": {"hub": "alita"}}))
    for a, s in (("kai", "s1"), ("kai", "s2"), ("alita", "s1")):
        (home / "ws" / a / s).mkdir(parents=True)
    from centralino.server import Daemon
    d = Daemon(home, giri=False).avvia()
    t = threading.Thread(target=d.esegui, daemon=True)
    t.start()
    d.home_ = home
    for addr in ("kai/s1", "kai/s2", "alita/s1"):
        assert _op(d, addr, "session.event", event="start")["ok"]
    yield d
    d.ferma()
    t.join(5)


def _caller(d, addr):
    return {"cwd": str(d.home_ / "ws" / addr)}


def _op(d, addr, op, **args):
    return call(op, args, caller=_caller(d, addr) if addr else None, sock=d.home_ / "centralino.sock")


def _ok(r):
    assert r["ok"], r
    return r["result"]


def _msg(d, mid):
    return d.central.store.message(mid)


def _giro(d, notifiche=None):
    return Sveglia(d.central, notifica=lambda titolo, testo: (notifiche if notifiche is not None else []).append(testo)).giro()


def _inbox(d, addr, intent=None):
    ms = _ok(_op(d, addr, "inbox"))["messages"]
    return [m for m in ms if intent is None or m["intent"] == intent]


def test_r1_request_comunicazione_rifiutata(bus):
    r = _op(bus, "alita/s1", "send", to="kai", intent="mcp-config", summary="x", message_type="request",
            kind="comunicazione")
    assert not r["ok"] and r["error"]["code"] == "kind_required" and "kind='delega'" in r["error"]["message"]
    for typ in ("brief", "handoff"):
        assert _op(bus, "alita/s1", "send", to="kai/s1", intent="info", summary="x", message_type=typ,
                   kind="comunicazione")["ok"]
    assert _ok(_op(bus, "alita/s1", "send", to="kai/s1", intent="w", summary="x", message_type="request",
                   kind="delega"))["task_id"]


def test_r2_esito_e_domanda_consumano_la_copia_condivisa(bus):
    d = _ok(_op(bus, "alita/s1", "send", to="kai", intent="lavoro", summary="x", message_type="request",
                kind="delega"))
    assert _msg(bus, d["id"])["to_slug"] is None  # due sessioni di kai senza canale: casella condivisa
    # l'esito mandato con bus_send (non bus_reply) da una sessione qualunque di kai
    _ok(_op(bus, "kai/s2", "send", to="alita/s1", intent="fatto", summary="ok", message_type="response",
            task_id=d["task_id"]))
    row = _msg(bus, d["id"])
    h = json.loads(row["raw"])["handled"]
    assert row["state"] == "done" and h["esito"] == "gestito" and h["by"] == "kai/s2" and h["via"] == "ritorno"
    assert d["id"] not in {m["id"] for m in _inbox(bus, "kai/s1")}

    # una domanda consuma solo la copia condivisa
    d2 = _ok(_op(bus, "alita/s1", "send", to="kai", intent="altro", summary="x", message_type="request",
                 kind="delega"))
    _ok(_op(bus, "kai/s1", "send", to="alita/s1", intent="q", summary="?", message_type="question",
            task_id=d2["task_id"]))
    assert _msg(bus, d2["id"])["state"] == "done"


def test_r2_il_delegante_non_consuma_e_chiudere_consuma(bus):
    # kai a kai: la risposta del delegante sul task non consuma la delega dell'esecutore
    d = _ok(_op(bus, "kai/s1", "send", to="kai/s2", intent="lavoro", summary="x", message_type="request",
                kind="delega"))
    _ok(_op(bus, "kai/s1", "send", to="kai/s2", intent="re", summary="precisazione", message_type="response",
            task_id=d["task_id"]))
    assert _msg(bus, d["id"])["state"] == "pending"
    # il delegante chiude l'attesa: la copia non chiede più niente
    _ok(_op(bus, "kai/s1", "close", task_id=d["task_id"]))
    row = _msg(bus, d["id"])
    assert row["state"] == "done" and row["esito"] == "attesa_chiusa"


def test_r3_sessione_chiusa_riassegna_e_rimbalza(bus):
    d = _ok(_op(bus, "alita/s1", "send", to="kai/s1", intent="lavoro", summary="x", message_type="request",
                kind="delega"))
    presa = _ok(_op(bus, "alita/s1", "send", to="kai/s1", intent="presa", summary="x", message_type="request",
                    kind="delega"))
    _ok(_op(bus, "kai/s1", "send", to="alita/s1", intent="q", summary="?", message_type="question",
            task_id=presa["task_id"]))
    c = _ok(_op(bus, "alita/s1", "send", to="kai/s1", intent="info", summary="leggi", message_type="brief",
                kind="comunicazione"))
    mia = _ok(_op(bus, "kai/s1", "send", to="alita/s1", intent="chiedo", summary="x", message_type="request",
                  kind="delega"))
    esito = _ok(_op(bus, "alita/s1", "send", to="kai/s1", intent="fatto", summary="ok", message_type="response",
                    task_id=mia["task_id"]))
    _ok(_op(bus, "kai/s1", "hook.end"))
    assert _msg(bus, d["id"])["to_slug"] is None          # delega non presa
    assert _msg(bus, esito["id"])["to_slug"] is None      # esito per un'attesa di kai/s1
    assert _msg(bus, presa["id"])["to_slug"] == "s1"      # presa (ha fatto una domanda): resta sua
    assert _msg(bus, c["id"])["to_slug"] == "s1"          # il resto aspetta la grazia
    _giro(bus)
    row = _msg(bus, c["id"])
    assert row["state"] == "done" and row["esito"] == "non_letto"
    ev = _inbox(bus, "alita/s1", "non-letto")
    assert len(ev) == 1 and c["id"] in ev[0]["summary"] and ev[0]["priority"] == "low" and ev[0]["type"] == "event"
    # un rimbalzo non rimbalza e non si ripete
    _ok(_op(bus, "alita/s1", "hook.end"))
    _giro(bus)
    assert len(_inbox(bus, "kai/s2", "non-letto")) == 0


def test_r3_grazia_e_ripresa(bus, monkeypatch):
    monkeypatch.setenv("AB_NON_LETTO_S", "600")
    c = _ok(_op(bus, "alita/s1", "send", to="kai/s1", intent="info", summary="x", message_type="brief",
                kind="comunicazione"))
    _ok(_op(bus, "kai/s1", "hook.end"))
    _giro(bus)
    assert _msg(bus, c["id"])["state"] == "pending"  # dentro la grazia
    _ok(_op(bus, "kai/s1", "hook.start"))             # claude --resume nella stessa cartella
    monkeypatch.setenv("AB_NON_LETTO_S", "0")
    _giro(bus)
    assert _msg(bus, c["id"])["state"] == "pending"  # tornata viva: resta sua


def test_r4_richiesta_ferma_un_giorno(bus):
    m = _ok(_op(bus, "kai/s2", "send", to="kai/s1", intent="mcp-config", summary="x", message_type="brief",
                kind="comunicazione"))
    notifiche = []
    _giro(bus, notifiche)
    assert not notifiche
    bus.central._write(lambda: bus.central._conn().execute(
        "UPDATE messages SET ts=? WHERE id=?", (iso(now() - timedelta(hours=25)), m["id"])))
    _giro(bus, notifiche)
    # l'hub (alita) non ha canale: notifica di sistema; il mittente ha l'evento
    assert len(notifiche) == 1 and "per kai/s1 da kai/s2" in notifiche[0] and m["id"] in notifiche[0]
    ev = _inbox(bus, "kai/s2", "richieste-ferme-da-un-giorno")
    assert len(ev) == 1 and ev[0]["type"] == "event"
    _giro(bus, notifiche)
    assert len(notifiche) == 1 and len(_inbox(bus, "kai/s2", "richieste-ferme-da-un-giorno")) == 1
