"""Attese doppie (t-20261029-lbgswf): bus_send(kind=delega) e poi onda_agent_prompt "[delega] ..."
aprivano due attese per lo stesso lavoro. Le sequenze sono quelle vere del 29/10 nel centralino
del laptop (Leo verso due sessioni di Nico)."""

import json
from datetime import datetime, timedelta, timezone

import pytest

from centralino import palla, tempo
from centralino.hookops import HookOps
from centralino.ops import Centralino
from centralino.sveglia import Sveglia

FER = "brandart-agentic-platform-fix-fer26-feedback-29-10"
RIS = "brandart-governance-feat-ris26-job-description"


@pytest.fixture
def bus(tmp_path):
    t = [datetime(2026, 10, 29, 13, 51, 11, tzinfo=timezone.utc)]
    tempo.imposta(lambda: t[0])
    ws = tmp_path / "ws"
    for a, s in (("leo", "00-brandart-pm-os"), ("nico", FER), ("nico", RIS)):
        (ws / a / s).mkdir(parents=True)
    (tmp_path / "AGENT_MAP.json").write_text(json.dumps({
        "rules": [{"pattern": str(ws / a), "agent": a} for a in ("leo", "nico")],
        "agents": {"leo": {}, "nico": {}}}))
    c = Centralino(tmp_path)
    HookOps(c).registra()

    def call(op, args, who=("leo", "00-brandart-pm-os")):
        r = c.handle_request({"v": 1, "id": "1", "op": op, "caller": {"cwd": str(ws / who[0] / who[1])},
                              "args": args})
        assert r["ok"], r
        return r["result"]

    def passa(s):
        t[0] += timedelta(seconds=s)

    def aperte():
        return sorted(w["task_id"] for w in c.bus.list_waits("leo", "00-brandart-pm-os") if w["state"] == "expected")

    yield c, call, passa, aperte
    tempo.imposta(None)
    c.store.close()


def delega(call, slug, intent, summary):
    return call("send", {"to": f"nico/{slug}", "intent": intent, "summary": summary,
                         "message_type": "request", "kind": "delega"})["task_id"]


def prompt(call, slug, testo):
    """Quello che manda l'hook onda-prompt per un "[delega] <testo>"."""
    return call("expect", {"description": testo[:120], "delegated_to": f"nico/{slug}",
                           "onda_prompt": True, "testo": testo})


def test_intent_citato_13_51(bus):
    """er8hk8 + orcr7l: il prompt cita l'intent, 3 s dopo."""
    c, call, passa, aperte = bus
    a = delega(call, FER, "fer26-capo-funzione-da-file", "Mario (14:50) lo considera un bug")
    passa(3)
    r = prompt(call, FER, "Leo: delega urgente sul bus, intent fer26-capo-funzione-da-file. Correggi "
                          "isFunctionHead perché il capo funzione si ricava dal file")
    assert r["agganciata"] and r["task_id"] == a
    assert a in r["prompt_line"]
    assert aperte() == [a]


def test_task_id_citato_12_09(bus):
    """wsjuhc + 4h6t9w: il prompt cita il task_id della delega."""
    c, call, passa, aperte = bus
    a = delega(call, RIS, "ris26-job-description-fonte-hr", "RIS26-F6.6, cambio di fonte per il ruolo")
    passa(5)
    r = prompt(call, RIS, f"Leo: nuova delega sul bus {a} (msg-x). Il ruolo si prende dal file HR")
    assert r["agganciata"] and r["task_id"] == a and aperte() == [a]


def test_niente_citato_ma_appena_mandata_17_58(bus):
    """myhqk5 + 1cyniw: nessuna citazione, 2 s dopo, stessa sessione: si aggancia."""
    c, call, passa, aperte = bus
    a = delega(call, FER, "acs29-sql-regole-katia", "ACS29, due script SQL")
    passa(2)
    r = prompt(call, FER, "Leo: due script SQL di Agents Crew da eseguire dal laptop")
    assert r["agganciata"] and r["task_id"] == a and aperte() == [a]
    # un secondo [delega] alla stessa sessione è un lavoro nuovo: la delega è già agganciata
    passa(60)
    r2 = prompt(call, FER, "Leo: e poi controlla anche i log di ieri")
    assert not r2["agganciata"] and r2["task_id"] != a and len(aperte()) == 2


def test_non_aggancia_fuori_finestra_o_altra_sessione(bus):
    c, call, passa, aperte = bus
    a = delega(call, FER, "acs29-sql-regole-katia", "ACS29, due script SQL")
    passa(2)
    altra = prompt(call, RIS, "Leo: lavoro diverso per un'altra sessione")
    assert not altra["agganciata"]
    passa(11 * 60)
    tardi = prompt(call, FER, "Leo: lavoro nuovo a voce")
    assert not tardi["agganciata"]
    assert len(aperte()) == 3 and a in aperte()


def test_senza_onda_prompt_expect_resta_com_era(bus):
    c, call, passa, aperte = bus
    a = delega(call, FER, "acs29-sql-regole-katia", "ACS29")
    r = call("expect", {"description": "bus_expect a mano", "delegated_to": f"nico/{FER}"})
    assert not r["agganciata"] and r["task_id"] != a


def test_gemella_orfana_si_chiude_da_sola_16_36(bus):
    """tha7oh (bus_send) + l2f2i5 (hook, vecchio): Nico risponde su una, l'altra senza ritorni si chiude."""
    c, call, passa, aperte = bus
    a = delega(call, FER, "fer26-riprendi-feedback-pia", "Mario (17:35): riprendi le richieste di Pia")
    passa(2)
    b = call("expect", {"description": "Leo: Mario chiede di riprendere le richieste di Pia",
                        "delegated_to": f"nico/{FER}"})["task_id"]  # come faceva l'hook vecchio
    assert sorted([a, b]) == aperte()
    sv = Sveglia(c)
    assert sv.giro()["gemelle"] == []  # nessun esito ancora: nessuna delle due si tocca
    passa(90)
    call("send", {"to": "leo/00-brandart-pm-os", "intent": "re", "summary": "PR #220 pronta",
                  "message_type": "response", "task_id": b}, who=("nico", FER))
    assert sv.giro()["gemelle"] == [a]
    w = c.store.wait(a)
    assert w["state"] == "closed" and w["closed_by"] == f"gemella:{b}"
    assert aperte() == [b]


def test_due_bus_send_vicini_sono_due_lavori(bus):
    """2b6afx e 5z6joc (13:46, 7 s): due deleghe vere, una risposta non chiude l'altra."""
    c, call, passa, aperte = bus
    a = delega(call, FER, "fer26-invia-aggiornamenti-dettaglio", "dettaglio 1")
    passa(7)
    b = delega(call, FER, "fer26-invia-aggiornamenti-tornato-indietro", "dettaglio 2")
    call("send", {"to": "leo/00-brandart-pm-os", "intent": "re", "summary": "fatto 1",
                  "message_type": "response", "task_id": a}, who=("nico", FER))
    assert Sveglia(c).giro()["gemelle"] == []
    assert b in aperte()


def test_attesa_vecchia_senza_ritorni_da_verificare(bus):
    c, call, passa, aperte = bus
    a = delega(call, FER, "acs29-sql-regole-katia", "ACS29")
    rec = c.bus.list_waits("leo", "00-brandart-pm-os")[0]
    assert not palla.da_verificare(rec)
    passa(palla.DA_VERIFICARE_H * 3600 + 60)
    s = palla.stato_palla(rec, None, [], "laptop")
    assert s["stato_palla"] == "da_verificare" and s["riga"]["parola"].startswith("da verificare")
    banner = c.handle_request({"v": 1, "id": "1", "op": "hook.start", "caller": {
        "cwd": str(c.home / "ws" / "leo" / "00-brandart-pm-os")}, "args": {}})["result"]["banner"]
    assert f"Da verificare (nessun ritorno da oltre {palla.DA_VERIFICARE_H} h" in banner and a in banner
    assert "Deleghe aperte:" not in banner
