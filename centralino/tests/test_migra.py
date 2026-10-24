"""Migrazione in SQLite e ritorno ai file: nessun messaggio perso nel giro completo."""

import json

from centralino import config, migra
from centralino.files import read_json
from centralino.ombra import Ombra
from centralino.ops import Centralino
from centralino.sim import Banco, _msg


def test_giro_completo_file_sqlite_file(tmp_path):
    b = Banco(tmp_path / "bus")                                     # modo ombra: si scrivono i file
    b.onda("kai/orbit", "idle")
    d = b.op("alita/work-hub", "send", to="kai/orbit", intent="x", summary="x", message_type="request", kind="delega")
    b.scrivi_come_lib("kai/orbit", _msg("msg-20261024T160000Z-dallalib", "leo/pm", "kai/orbit", "brief",
                                         "2026-10-24T16:00:00Z", kind="comunicazione"))
    (b.home / "threads").mkdir(exist_ok=True)
    (b.home / "threads" / "thread-vecchio.json").write_text(json.dumps(
        {"id": "thread-vecchio", "messages": [{"id": "msg-sparito", "from": "leo", "ts": "2026-06-01T00:00:00Z"}]}))
    b.chiudi()
    home = b.home
    config.scrivi(home, config.Modo("sqlite", "attiva", False))
    c = Centralino(home, kick_wake=lambda caller: None, pid_alive=lambda pid: True)
    out = migra.migra(c, Ombra(c))
    assert out["pending"] == 2 and out["attese_aperte"] == 1 and out["righe_thread_legacy"] == 1
    assert c.op_thread({}, {"thread_id": "thread-vecchio"}, [])["count"] == 1
    # lavoro in modo attivo: l'esecutore risponde, il delegante chiude, la comunicazione si legge
    who = lambda s: {"cwd": str(b.cwd(s))}  # noqa: E731
    e = c.op_reply(who("kai/orbit"), {"message_id": d["id"], "summary": "fatto"}, [])
    c.op_close(who("kai/orbit"), {"message_id": "msg-20261024T160000Z-dallalib", "esito": "letto"}, [])
    c.op_close(who("alita/work-hub"), {"message_id": e["sent"]["id"], "esito": "chiuso"}, [])
    assert not (home / "inboxes" / "alita" / "@work-hub").joinpath(f"{e['sent']['id']}.json").exists()
    res = migra.esporta(c)
    assert res["messaggi_scritti"] == 1 and res["messaggi_spostati"] == 2 and res["attese_scritte"] >= 1
    assert (home / "inboxes" / "alita" / "@work-hub" / ".done" / f"{e['sent']['id']}.json").exists()
    assert (home / "inboxes" / "kai" / "@orbit" / ".done" / f"{d['id']}.json").exists()
    assert (home / "inboxes" / "kai" / "@orbit" / ".read" / "msg-20261024T160000Z-dallalib.json").exists()
    assert not list((home / "inboxes" / "kai" / "@orbit").glob("msg-*.json"))
    assert read_json(home / "waits" / "alita" / "@work-hub" / f"{d['task_id']}.json")["state"] == "closed"
    assert migra.esporta(c) == {"messaggi_scritti": 0, "messaggi_spostati": 0, "attese_scritte": 0}
    c.store.close()
