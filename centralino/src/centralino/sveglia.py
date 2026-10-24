"""La sveglia dentro il centralino (BR24-F4.1), modo attivo.

Un giro ogni 10 s (e subito dopo un invio):

1. scadenze: un'attesa aperta scade a ``deadline`` passata o dopo 72 h senza movimento,
   se non ha ritorni da gestire; chi aspetta riceve un avviso (riga 6: non sveglia);
2. la tabella di §6 (``Ombra.giro_sveglia``) decide, e qui si esegue: un testo per
   sessione con tutti i suoi messaggi del giro, scritto nel prompt via Onda o tmux;
3. il motivo di un rinvio finisce sul ritorno dell'attesa (``pending_wake``), così la
   band e ``bus_inbox`` lo mostrano;
4. una sessione viva senza canale da più di 5 minuti: un avviso all'agente hub, una volta
   per messaggio.

wake.py resta installato finché Mario non fa la prova: lo script di passaggio ferma il
suo launchd e accende questa.
"""

from __future__ import annotations

import json
import re
from datetime import timedelta

from . import canale
from .canale import Rinvio
from .files import msg_id
from .tempo import age_s, iso, iso_now, now, parse_iso

WAIT_IDLE_H = 72
NOCHANNEL_S = 300
GESTISCI = "gestisci con bus_close (chiuso, riarma o letto) o rispondi con bus_reply"

TIPO = {"esito": "return", "domanda": "question", "da_agganciare": "return", "risposta": "answer",
        "delega": "delegation", "urgente": "urgent"}


def _s(v, n: int) -> str:
    return re.sub(r"\s+", " ", str(v or "")).strip()[:n]


class Sveglia:
    def __init__(self, central, ombra, onda_call=None):
        self.c = central
        self.ombra = ombra
        self.onda_call = onda_call or canale.onda_call

    # ---------- testo ----------

    def _item(self, d) -> dict | None:
        row = self.c.store.message(d.msg_id)
        if row is None:
            return None
        m = json.loads(row["raw"])
        pl = m.get("payload") if isinstance(m.get("payload"), dict) else {}
        who = m.get("from_session") or m.get("from") or "?"
        host = (m.get("context") or {}).get("host") if isinstance(m.get("context"), dict) else None
        if host and host != self.c.amap.host_self():
            who = f"{who}@{host}"
        w = self.c.store.wait(m["task_id"]) if isinstance(m.get("task_id"), str) else None
        return {"mid": d.msg_id, "kind": TIPO.get(d.regola, "return"), "task_id": m.get("task_id"),
                "sender": who, "summary": _s(pl.get("summary"), 120), "priority": m.get("priority"),
                "desc": _s(w["description"], 80) if w is not None and w["description"] else ""}

    @staticmethod
    def _riga(it: dict) -> str:
        mid, tid, who, k = it["mid"], it["task_id"], it["sender"], it["kind"]
        desc = f" ({it['desc']})" if it.get("desc") else ""
        if k == "question":
            return f"domanda sul task {tid}{desc} da {who}, che aspetta te (bus_read {mid})"
        if k == "answer":
            return f"risposta sul task {tid} da {who} (bus_read {mid})"
        if k == "delegation":
            what = f": {it['summary']}" if it.get("summary") else ""
            return f"nuova delega {tid} da {who}{what} (bus_read {mid})"
        if k == "urgent":
            return f"messaggio URGENTE da {who} (bus_read {mid})"
        return f"ritorno sul task {tid}{desc} da {who} (bus_read {mid})"

    def testo(self, decisioni) -> str:
        items = [i for i in (self._item(d) for d in decisioni) if i]
        grp = {k: [i for i in items if i["kind"] == k] for k in ("return", "question", "answer", "delegation", "urgent")}
        ups = grp["return"] + grp["question"]
        parts = []
        cap = lambda t: t[0].upper() + t[1:]  # noqa: E731
        if len(ups) == 1:
            parts.append(cap(self._riga(ups[0])) + f": leggilo e {GESTISCI}.")
        elif ups:
            parts.append(f"{len(ups)} messaggi sulle tue deleghe: " + "; ".join(map(self._riga, ups))
                         + f". Leggili e {GESTISCI}.")
        if grp["answer"]:
            a = grp["answer"]
            lead = self._riga(a[0]) if len(a) == 1 else f"{len(a)} risposte sui task che esegui: " + "; ".join(map(self._riga, a))
            parts.append(cap(lead) + ": leggi e prosegui il lavoro.")
        if grp["delegation"]:
            a = grp["delegation"]
            lead = self._riga(a[0]) if len(a) == 1 else f"{len(a)} deleghe nuove: " + "; ".join(map(self._riga, a))
            parts.append(cap(lead) + ": leggi, fai il lavoro e manda l'esito con bus_reply sulla delega.")
        if grp["urgent"]:
            a = grp["urgent"]
            lead = self._riga(a[0]) if len(a) == 1 else f"{len(a)} messaggi urgenti: " + "; ".join(map(self._riga, a))
            parts.append(cap(lead) + ": leggi e gestisci.")
        return " ".join(parts)

    # ---------- esecuzione ----------

    def esegui(self, agent: str, slug: str, stato, decisioni) -> str:
        host = self.c.amap.host_self() or ""
        row = self.c.store.read().execute("SELECT * FROM sessions WHERE agent=? AND slug=? AND host=?",
                                          (agent, slug, host)).fetchone()
        sess = self.c.onda.find(agent, slug, host, row["claude_session_id"] if row else None)
        text = self.testo(decisioni)
        if not text:
            raise Rinvio("testo_vuoto")
        if sess is not None:
            esito = canale.via_onda(sess, text, call=self.onda_call)
        elif row is not None and row["tmux_pane"]:
            esito = canale.via_tmux(row["tmux_pane"], text)
        else:
            raise Rinvio("nessun_canale")
        self.c.log("info", "sveglia", sessione=f"{agent}/{slug}", canale=esito,
                   messaggi=[d.msg_id for d in decisioni])
        return esito

    # ---------- scadenze ----------

    def scadenze(self) -> list[str]:
        host = self.c.amap.host_self()
        scadute = []
        for r in self.c.store.read().execute("SELECT raw FROM waits WHERE state='expected'").fetchall():
            rec = json.loads(r["raw"])
            if host and rec.get("host") and rec.get("host") != host:
                continue
            pending = any(isinstance(e, dict) and e.get("state") == "pending"
                          for e in (rec.get("returns") or {}).values())
            if pending:
                continue
            dl = parse_iso(rec.get("deadline"))
            idle = age_s(rec.get("last_activity") or rec.get("created_at"))
            motivo = None
            if dl is not None and now() > dl:
                motivo = "deadline"
            elif idle is not None and idle > WAIT_IDLE_H * 3600:
                motivo = "inattiva"
            if motivo:
                self._scade(rec, motivo)
                scadute.append(rec["task_id"])
        return scadute

    def _scade(self, rec: dict, motivo: str) -> None:
        from .db import Store
        ts = iso_now()
        rec.update(state="expired", expired_at=ts, closed_by=f"scadenza:{motivo}")
        rec.pop("pending_wake", None)
        who = rec.get("delegated_to") or "chi hai delegato"
        summary = (f"Attesa del task {rec['task_id']} ({rec.get('description') or ''}) su {who} chiusa: "
                   f"nessun movimento da {WAIT_IDLE_H} ore. Se il lavoro è ancora aperto, delega di nuovo."
                   if motivo == "inattiva" else
                   f"Nessun esito per il task {rec['task_id']} ({rec.get('description') or ''}) da {who} "
                   f"entro {rec.get('deadline')}. Controlla la sessione delegata.")
        avviso = self._evento(rec["agent"], f"{rec['agent']}/{rec['slug']}", "task-scaduto", "normal",
                              summary, {"expired_task_id": rec["task_id"]})

        def scrivi(c):
            Store.put_wait(c, rec, origin="centralino")
        self.c.files._tx(scrivi)
        self.c.files.accogli(avviso, rec["agent"], rec["slug"], origin="centralino")
        self.c.log("info", "attesa_scaduta", task_id=rec["task_id"], motivo=motivo)

    def _evento(self, to: str, frm: str, intent: str, priority: str, summary: str, extra: dict) -> dict:
        ts = iso_now()
        rec = {"id": msg_id(), "version": "1.0", "from": frm.partition("/")[0], "from_session": frm, "to": to,
               "thread_id": f"thread-{ts[:10].replace('-', '')}-sveglia", "in_reply_to": None, "type": "event",
               "intent": intent, "priority": priority, "payload": {"summary": summary, **extra},
               "context_refs": [], "requires_response": False, "ts": ts}
        if h := self.c.amap.host_self():
            rec["context"] = {"host": h}
        return rec

    # ---------- motivi e avvisi ----------

    def _pending_wake(self, nuove) -> None:
        """Il motivo di un rinvio sul ritorno dell'attesa (per bus_inbox e la band)."""
        from .db import Store
        per_task: dict[str, list] = {}
        for a, s, d in nuove:
            if d.regola in ("esito", "domanda") and d.decisione in ("coda", "sveglia"):
                row = self.c.store.message(d.msg_id)
                if row is not None and row["task_id"]:
                    per_task.setdefault(row["task_id"], []).append(d)
        for tid, decs in per_task.items():
            def scrivi(c, tid=tid, decs=decs):
                r = c.execute("SELECT raw FROM waits WHERE task_id=?", (tid,)).fetchone()
                if r is None:
                    return
                rec = json.loads(r["raw"])
                ts = iso_now()
                for d in decs:
                    e = (rec.get("returns") or {}).get(d.msg_id)
                    if not isinstance(e, dict):
                        continue
                    if d.decisione == "sveglia":
                        # la sveglia non consuma il ritorno: resta pending finché non si gestisce
                        e.pop("pending_wake", None)
                        e.update(woken_at=ts, woken_via=d.canale)
                    else:
                        prev = e.get("pending_wake") or {}
                        e["pending_wake"] = {"reason": d.motivo, "since": prev.get("since") or ts, "checked_at": ts}
                from .files import with_pending_wake
                Store.put_wait(c, with_pending_wake(rec), origin="centralino")
            self.c.files._tx(scrivi)

    def avvisi_senza_canale(self) -> list[str]:
        r = self.c.store.read()
        limite = iso(now() - timedelta(seconds=NOCHANNEL_S))
        hub = (self.c.amap.data().get("cc_awareness") or {}).get("hub")
        inviati = []
        righe = r.execute(
            "SELECT w.agent, w.slug, w.msg_id, MIN(w.at) AS dal FROM wake_decisions w "
            "JOIN messages m ON m.id=w.msg_id AND m.state='pending' "
            "WHERE w.modo='attiva' AND w.motivo='nessun_canale' GROUP BY w.agent, w.slug, w.msg_id "
            "HAVING MIN(w.at) <= ?", (limite,)).fetchall()
        for x in righe:
            key = f"nochannel:{x['agent']}/{x['slug']}:{x['msg_id']}"
            if r.execute("SELECT 1 FROM meta WHERE key=?", (key,)).fetchone():
                continue
            # ancora senza canale adesso?
            if self.c.vivo.stato(x["agent"], x["slug"]).canale != "nessuno":
                continue
            dest = hub if hub and self.c.amap.agent_exists(hub) else x["agent"]
            ev = self._evento(dest, f"{dest}/sveglia", "sveglia-senza-canale", "high",
                              f"Sveglia senza canale: {x['agent']}/{x['slug']} ha un messaggio da svegliare "
                              f"({x['msg_id']}) ma dal {x['dal']} non ha un pannello di Onda né tmux. "
                              "Va svegliata a mano o riaperta in Onda.",
                              {"session": f"{x['agent']}/{x['slug']}", "messages": [x["msg_id"]]})
            self.c.files.accogli(ev, dest, None, origin="centralino")
            self.c._write(lambda key=key: self.c._conn().execute(
                "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (key, iso_now())))
            inviati.append(ev["id"])
        return inviati

    # ---------- giro ----------

    def giro(self) -> dict:
        scadute = self.scadenze()
        decs = self.ombra.giro_sveglia(esegui=self.esegui)
        self._pending_wake(getattr(self.ombra, "ultimo_giro", []))
        avvisi = self.avvisi_senza_canale()
        return {"scadute": scadute, "decisioni": len(decs), "avvisi": avvisi}
