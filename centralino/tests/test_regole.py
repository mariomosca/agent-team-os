from types import SimpleNamespace as S

from centralino.regole import Attesa, blocca_stop, classifica, decidi, destinatari

W = {"t-1": Attesa("t-1", "alita", "work-hub", "kai/orbit", "kai/orbit", "expected"),
     "t-2": Attesa("t-2", "alita", "work-hub", None, "kai/orbit", "closed")}


def test_classifica():
    assert classifica({"type": "response", "task_id": "t-1"}, "alita", "work-hub", W) == "esito"
    assert classifica({"type": "question", "task_id": "t-1"}, "alita", "work-hub", W) == "domanda"
    assert classifica({"type": "response", "task_id": "t-1"}, "kai", "orbit", W) == "risposta"
    assert classifica({"type": "response", "task_id": "t-2"}, "alita", "work-hub", W) == "nessuna"  # attesa chiusa
    assert classifica({"type": "response"}, "alita", "work-hub", W) == "nessuna"                     # senza task_id
    assert classifica({"type": "brief", "kind": "delega"}, "kai", "x", W) == "delega"
    assert classifica({"type": "brief", "kind": "comunicazione", "priority": "high"}, "kai", "x", W) == "nessuna"
    assert classifica({"type": "event", "priority": "urgent"}, "kai", "x", W) == "urgente"
    # come ab_msg_actionable dopo il passo 0
    assert classifica({"type": "response", "task_id": "t-9"}, "kai", "x", W) == "risposta"   # nessuna attesa qui
    assert classifica({"type": "question"}, "kai", "x", W) == "domanda_libera"
    assert classifica({"type": "request", "requires_response": True}, "kai", "x", W) == "delega"  # vecchio, senza kind
    assert classifica({"type": "event", "task_link": {"by": "da_agganciare"}}, "alita", "work-hub", W) == "da_agganciare"
    assert classifica({"type": "response", "payload": {"task_id": "t-1"}}, "alita", "work-hub", W) == "esito"


def test_destinatari():
    assert destinatari({"type": "response", "task_id": "t-1"}, "alita", None, W, ["a", "work-hub"]) == ["work-hub"]
    assert destinatari({"type": "brief"}, "kai", None, W, ["a", "b"]) == ["a", "b"]
    assert destinatari({"type": "brief"}, "kai", "z", W, ["a"]) == ["z"]


def test_blocca_stop():
    assert blocca_stop("esito") and blocca_stop("domanda_libera") and not blocca_stop("nessuna")


def test_domanda_libera_non_sveglia():
    d = decidi(_stato(), [("q", "domanda_libera", "leo/pm")], 0, set())
    assert d[0].decisione == "niente"


def _stato(**kw):
    base = dict(viva=True, canale="onda", ferma=True, onda_state="idle")
    base.update(kw)
    return S(**base)


def test_decidi_tabella():
    it = [("m1", "esito", "kai/orbit"), ("m2", "nessuna", None)]
    d = {x.msg_id: (x.decisione, x.motivo) for x in decidi(_stato(), it, 0, set())}
    assert d == {"m1": ("sveglia", None), "m2": ("niente", None)}
    d = decidi(_stato(ferma=False, onda_state="working"), it[:1], 0, set())
    assert (d[0].decisione, d[0].motivo) == ("coda", "al_lavoro:working")
    d = decidi(_stato(viva=False), it[:1], 0, set())
    assert (d[0].decisione, d[0].motivo) == ("niente", "non_viva")
    d = decidi(_stato(canale="nessuno"), it[:1], 0, set())
    assert (d[0].decisione, d[0].motivo) == ("coda", "nessun_canale")


def test_tetto_ed_esito_esente():
    d = decidi(_stato(), [("q", "domanda", "kai/orbit")], 10, set())
    assert (d[0].decisione, d[0].motivo) == ("coda", "tetto_sessione")
    d = {x.msg_id: x for x in decidi(_stato(), [("q", "domanda", "kai/orbit"), ("e", "esito", "kai/orbit")], 10, set())}
    assert d["e"].decisione == "sveglia" and d["e"].motivo is None
    assert d["q"].decisione == "sveglia" and d["q"].motivo == "accorpato:tetto_sessione"


def test_urgenti_per_coppia():
    d = decidi(_stato(), [("u", "urgente", "leo/pm")], 0, {"leo/pm"})
    assert (d[0].decisione, d[0].motivo) == ("coda", "tetto_urgenti")
    d = decidi(_stato(), [("u", "urgente", "nico/x")], 0, {"leo/pm"})
    assert d[0].decisione == "sveglia"
