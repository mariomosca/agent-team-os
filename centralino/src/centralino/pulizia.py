"""Pulizia automatica (BR24-F5.4), modo attivo, un giro ogni 6 ore.

- messaggi archiviati (read/done) e righe di thread vecchie oltre 30 giorni: via;
- attese chiuse, scadute o annullate da oltre 30 giorni: via, con i loro ritorni;
- decisioni di sveglia oltre 30 giorni: via;
- caselle di sessioni chiuse da più di 24 ore: i messaggi che chiedono un'azione passano
  all'indirizzo dell'agente nudo (unica sessione viva, poi casa, poi casella condivisa),
  gli altri si archiviano come letti; la sessione esce dal registro.

I file di oggi non si toccano: restano come archivio in sola lettura.
"""

from __future__ import annotations

import json
from datetime import timedelta

from . import regole
from .sveglia import attese_di
from .tempo import iso, iso_now, now

GIORNI = 30
SESSIONE_CHIUSA_H = 24


class Pulizia:
    def __init__(self, central):
        self.c = central

    def giro(self) -> dict:
        da = iso(now() - timedelta(days=GIORNI))
        chiuse_da = iso(now() - timedelta(hours=SESSIONE_CHIUSA_H))
        host = self.c.amap.host_self() or ""
        out = {"messaggi": 0, "attese": 0, "spostati": 0, "archiviati": 0, "sessioni": 0}
        attese = attese_di(self.c.store)

        sessioni = self.c.store.read().execute(
            "SELECT agent, slug FROM sessions WHERE host=? AND ended_at IS NOT NULL AND ended_at<=?",
            (host, chiuse_da)).fetchall()
        # anche le caselle di sessioni sparite senza un evento di fine (heartbeat fermo da 24 h)
        sessioni += self.c.store.read().execute(
            "SELECT agent, slug FROM sessions WHERE host=? AND ended_at IS NULL AND last_heartbeat<=?",
            (host, chiuse_da)).fetchall()
        morte = [(r["agent"], r["slug"]) for r in sessioni if not self.c.vivo.stato(r["agent"], r["slug"]).viva]

        def scrivi():
            c = self.c._conn()
            out["messaggi"] = c.execute("DELETE FROM messages WHERE state!='pending' AND ts<?", (da,)).rowcount
            out["messaggi"] += c.execute("DELETE FROM thread_legacy WHERE json_extract(entry, '$.ts')<?",
                                         (da,)).rowcount
            vecchie = [r[0] for r in c.execute(
                "SELECT task_id FROM waits WHERE state NOT IN ('expected') AND COALESCE(closed_at, created_at)<?",
                (da,))]
            for tid in vecchie:
                c.execute("DELETE FROM wait_returns WHERE task_id=?", (tid,))
                c.execute("DELETE FROM waits WHERE task_id=?", (tid,))
            out["attese"] = len(vecchie)
            c.execute("DELETE FROM wake_decisions WHERE at<?", (da,))
            for agent, slug in morte:
                dest = self.c.bus.slug_nudo(agent)
                for r in c.execute("SELECT id, raw FROM messages WHERE to_agent=? AND to_slug=? AND state='pending' "
                                   "AND to_host=?", (agent, slug, host)).fetchall():
                    m = json.loads(r["raw"])
                    if regole.blocca_stop(regole.classifica(m, agent, slug, attese)) and dest != slug:
                        c.execute("UPDATE messages SET to_slug=? WHERE id=?", (dest, r["id"]))
                        out["spostati"] += 1
                    else:
                        m["handled"] = {"esito": "letto", "at": iso_now(), "by": "centralino/pulizia"}
                        c.execute("UPDATE messages SET state='read', esito='letto', handled_by=?, handled_at=?, raw=? "
                                  "WHERE id=?", ("centralino/pulizia", iso_now(),
                                                 json.dumps(m, ensure_ascii=False, sort_keys=True), r["id"]))
                        out["archiviati"] += 1
                c.execute("DELETE FROM sessions WHERE agent=? AND slug=? AND host=?", (agent, slug, host))
                out["sessioni"] += 1
        self.c._write(scrivi)
        for agent, slug in morte:
            (self.c.home / "registry" / f"{agent}.d" / f"{slug}.json").unlink(missing_ok=True)
        self.c.log("info", "pulizia", **out)
        return out
