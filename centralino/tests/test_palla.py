"""Chi ha la palla su una delega (BR24-F6.4/F6.6/F6.7): i casi che erano in
mods/bus-band/hooks/waits.test.ts, ora calcolati dal centralino nella vista."""

from datetime import datetime, timezone

import pytest

from centralino import palla, tempo
from centralino.palla import info_orbit, rif_piano, stato_palla, titolo_breve


@pytest.fixture(autouse=True)
def ora():
    tempo.imposta(lambda: datetime(2026, 10, 17, 11, 0, tzinfo=timezone.utc))
    yield
    tempo.imposta(None)


def attesa(**over):
    return {"v": 1, "task_id": "t-20261017-abc123", "agent": "alita", "slug": "work-hub",
            "description": "spike orbit", "delegated_to": "kai/f3", "created_at": "2026-10-17T10:48:00Z",
            "deadline": None, "state": "expected", **over}


KAI_LAVORA = {"agent": "kai", "slug": "f3", "host": "laptop", "lavora": True}
KAI_FERMO = {**KAI_LAVORA, "lavora": False}
LETTA = "2026-10-17T10:50:00Z"


def sp(rec, vive=(), letto=LETTA, orbit=None, **kw):
    return stato_palla(rec, letto, list(vive), "laptop", orbit, **kw)


def test_chi_ha_la_palla_in_ordine():
    domanda = attesa(returns={"q": {"type": "question", "at": "2026-10-17T10:55:00Z", "state": "woken"}})
    esito = attesa(returns={"r": {"type": "response", "at": "2026-10-17T10:55:00Z", "state": "woken"}})
    riarmata = attesa(description="BR24-F6.4: x", returns={
        "r": {"type": "response", "at": "2026-10-17T10:55:00Z", "state": "handled", "handled": "riarma"}})
    orbit_mario = [{"code": "BR24", "phase": "F6", "phase_title": "Misura", "step_key": "F6.4",
                    "title": "Banda", "state": "doing", "assignee": "mario"}]
    orbit_kai = [{**orbit_mario[0], "assignee": "kai"}]
    assert sp(domanda)["stato_palla"] == "domanda"
    assert sp(esito)["stato_palla"] == "esito"
    assert sp(attesa(state="expired"), [KAI_LAVORA])["stato_palla"] == "scaduta"
    assert sp(riarmata, [KAI_FERMO], orbit=orbit_mario)["stato_palla"] == "mario"
    assert sp(riarmata, [KAI_FERMO], orbit=orbit_kai)["stato_palla"] == "consegnato"
    assert sp(attesa())["stato_palla"] == "chiusa"
    assert sp(attesa(), [KAI_LAVORA])["stato_palla"] == "lavora"
    assert sp(attesa(), [KAI_FERMO])["stato_palla"] == "fermo"


def test_in_coda_finche_non_la_legge():
    assert sp(attesa(), [KAI_LAVORA], letto=None)["stato_palla"] == "coda"
    riga = sp(attesa(), [KAI_LAVORA], letto=None)["riga"]
    assert (riga["glifo"], riga["parola"]) == ("⧗", "in coda: Kai occupato")
    assert sp(attesa(), [KAI_LAVORA])["riga"]["parola"] == "Kai ci lavora"
    assert sp(attesa(), [KAI_FERMO], letto=None)["riga"]["parola"] == "Kai fermo"
    # nessuno sa se è letta (attesa senza messaggio di delega): resta "ci lavora"
    assert sp(attesa(), [KAI_LAVORA], letto=None, letto_noto=False)["stato_palla"] == "lavora"
    # chi l'ha letta è l'esecutore finché un ritorno non ne nomina uno
    assert sp(attesa(delegated_to="kai"), [KAI_LAVORA], letto_da="kai/f3")["riga"]["esecutore"] == "kai/f3"


def test_non_aperto_qui_vivo_altrove():
    vive = [{"agent": "kai", "slug": "onda", "host": "laptop", "lavora": False},
            {"agent": "kai", "slug": "alita-mobile", "host": "laptop", "lavora": True},
            {"agent": "kai", "slug": "x", "host": "mini", "lavora": True},
            {"agent": "vera", "slug": "f3", "host": "laptop", "lavora": True}]
    rec = attesa(executor_session="kai/f3")
    assert sp(rec, vive)["riga"]["parola"] == "Kai non aperto qui · vivo in alita-mobile +1"
    assert sp(rec, vive[:1])["riga"]["parola"] == "Kai non aperto qui · vivo in onda"
    assert (sp(rec)["riga"]["glifo"], sp(rec)["riga"]["parola"]) == ("○", "Kai non aperto")


def test_l_esecutore_stesso_agente_slug_e_macchina():
    vive = [{"agent": "kai", "slug": "alita-mobile", "host": "laptop", "lavora": False},
            {"agent": "kai", "slug": "f3", "host": "mini", "lavora": True}]
    assert palla.esecutore_vivo("kai/alita-mobile", vive, "laptop")["slug"] == "alita-mobile"
    assert palla.esecutore_vivo("kai/f3", vive, "laptop") is None
    assert palla.esecutore_vivo("kai@mini/f3", vive, "laptop")["lavora"] is True
    assert palla.esecutore_vivo("kai/onda-electron", vive, "laptop") is None


def test_parole_e_cosa_apre_un_clic():
    rec = attesa(delegation={"msg_id": "msg-d"}, executor_session="kai/f3")
    r = sp(rec)["riga"]
    assert (r["parola"], r["glifo"], r["esecutore"], r["da"], r["leggi"]) == (
        "Kai non aperto", "○", "kai/f3", "12 min", "/read msg-d")
    back = attesa(delegation={"msg_id": "msg-d"},
                  returns={"msg-r": {"type": "response", "at": "2026-10-17T10:55:00Z", "state": "woken"}})
    r = sp(back)["riga"]
    assert (r["parola"], r["glifo"], r["leggi"], r["allerta"]) == ("esito da leggere", "✉", "/read msg-r", True)


def test_domande_solo_aperte():
    rec = attesa(questions={"q1": {"from": "kai", "at": "a", "answered_at": "b"}},
                 returns={"q2": {"type": "question", "at": "c", "state": "handled"},
                          "q3": {"type": "question", "at": "d", "state": "pending"}})
    assert sp(rec)["riga"]["leggi"] == "/read q3"


def test_tetto_sulla_sveglia_in_parola():
    r = sp(attesa(pending_wake={"reason": "tetto_task"}))["riga"]
    assert r["nota"] == "tetto di sveglie per questo task" and r["allerta"]


@pytest.mark.parametrize("testo,codice", [
    ("on24-f3-hairline: Piano ON24, fase F3", "ON24-F3"),
    ("am18-f15-analisi", "AM18-F15"),
    ("br24-f6.4-banda: BR24-F6.4: x", "BR24-F6.4"),
    ("wh17-f8.3b fix", "WH17-F8.3b"),
    ("nessun codice qui", None),
])
def test_codice_del_piano(testo, codice):
    r = rif_piano(testo)
    assert (r.codice if r else None) == codice


def test_titolo_breve():
    assert titolo_breve("on24-f3-hairline: Piano ON24, fase F3, nuova: Hairline.\n\nCos'è: x", None) == "Hairline."
    assert titolo_breve("br24-f6.6: BR24-F6.6: la banda distingue. Altro.", None) == "la banda distingue."
    assert titolo_breve("x", "Banda del bus ridisegnata (Mario 25/10): una riga") == "Banda del bus ridisegnata"


def test_orbit_titolo_e_mario():
    righe = [{"code": "ON24", "phase": "F3", "phase_title": "Hairline", "step_key": "F3.4",
              "title": "Prova di Mario", "state": "todo", "assignee": "mario"},
             {"code": "AM18", "phase": "F15", "phase_title": "Analisi", "step_key": "F15.1",
              "title": "Verifica", "state": "todo", "assignee": "kai"}]
    assert info_orbit(righe, rif_piano("ON24-F3")) == {"titolo": "Hairline", "mario": True}
    assert info_orbit(righe, rif_piano("AM18-F15.1")) == {"titolo": "Verifica", "mario": False}
    assert info_orbit(righe, rif_piano("XX11-F1")) is None


def test_orbit_in_sola_lettura(tmp_path):
    import sqlite3
    db = tmp_path / "orbit.db"
    c = sqlite3.connect(db)
    c.executescript("""
      CREATE TABLE plans (id TEXT, code TEXT, status TEXT);
      CREATE TABLE plan_phases (id TEXT, plan_id TEXT, key TEXT, title TEXT);
      CREATE TABLE tasks (id TEXT, phase_id TEXT, step_key TEXT, title TEXT, state TEXT, assignee TEXT);
      INSERT INTO plans VALUES ('p', 'BR24', 'active');
      INSERT INTO plan_phases VALUES ('f', 'p', 'F6', 'Misura');
      INSERT INTO tasks VALUES ('t', 'f', 'F6.6', 'Banda: in coda o ci lavora', 'doing', 'kai');""")
    c.commit()
    c.close()
    o = palla.Orbit(db)
    righe = o.righe([rif_piano("BR24-F6.6")])
    assert [(r["step_key"], r["title"]) for r in righe] == [("F6.6", "Banda: in coda o ci lavora")]
    assert palla.Orbit(tmp_path / "manca.db").righe([rif_piano("BR24-F6.6")]) == []
