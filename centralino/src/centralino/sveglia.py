"""La sveglia dentro il centralino (BR24-F4.1), modo attivo.

Un giro ogni 10 s (e subito dopo un invio):

1. scadenze: un'attesa aperta scade a ``deadline`` passata o dopo 72 h senza movimento,
   se non ha ritorni da gestire; chi aspetta riceve un avviso (riga 6: non sveglia);
2. la tabella di §6 (``regole.py``) decide e si esegue: un testo per sessione con tutti i
   suoi messaggi del giro, scritto nel prompt via Onda o tmux;
3. il motivo di un rinvio finisce sul ritorno dell'attesa (``pending_wake``), così la
   band e ``bus_inbox`` lo mostrano;
4. AM26-F2.2: messaggi che chiedono un'azione fermi da più di 10 minuti senza una sessione
   con canale che li possa ricevere: un messaggio azionabile all'hub, uno per gruppo
   (destinatario e mittente); se l'hub stesso non ha canale, una notifica di sistema.

Le sessioni vive senza canale non ricevono i messaggi della casella condivisa: restano vive
per il battito, ma una consegna lì resterebbe muta (kai/lighthouse, 26/10, quattro richieste
di Mario ferme quattro ore).
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from datetime import timedelta

from . import canale, regole
from .canale import Rinvio
from .db import Store
from .dbbus import msg_id, with_pending_wake
from .tempo import age_s, iso, iso_now, now, parse_iso

WAIT_IDLE_H = 72
FERMO_S = 600           # un messaggio azionabile fermo da tanto, senza canale, fa l'avviso all'hub
INTENT_FERMI = "messaggi-fermi-senza-canale"
VECCHIO_GG = 30          # un messaggio pending più vecchio non genera l'avviso senza canale
TESTO_AVVISO_S = 1800   # rinvio per testo nella casella da tanto: avviso a chi ha delegato
INTENT_TESTO = "sveglia-rinviata-testo"
RIPROVA_LOG_S = 600     # un rinvio del canale che si ripete identico torna nel log ogni tanto
GESTISCI = "gestisci con bus_close (chiuso, riarma o letto) o rispondi con bus_reply"

TIPO = {"esito": "return", "domanda": "question", "da_agganciare": "return", "risposta": "answer",
        "delega": "delegation", "urgente": "urgent"}


def _s(v, n: int) -> str:
    return re.sub(r"\s+", " ", str(v or "")).strip()[:n]


def attese_di(store) -> dict[str, regole.Attesa]:
    """Le attese per la tabella di sveglia, per task_id."""
    rows = store.read().execute(
        "SELECT task_id, owner_agent, owner_slug, executor_session, delegated_to, state FROM waits").fetchall()
    return {r["task_id"]: regole.Attesa(r["task_id"], r["owner_agent"], r["owner_slug"],
                                        r["executor_session"], r["delegated_to"], r["state"])
            for r in rows}


class Sveglia:
    def __init__(self, central, onda_call=None, notifica=None):
        self.c = central
        self.onda_call = onda_call or canale.onda_call
        self.notifica = notifica or canale.notifica_sistema
        self.ultimo_giro: list = []

    # ---------- la tabella: chi riceve cosa ----------

    def _attese(self) -> dict[str, regole.Attesa]:
        return attese_di(self.c.store)

    def destinazioni(self, attese=None) -> dict[tuple[str, str], list[tuple[str, str, str | None]]]:
        """(agente, slug) -> [(msg_id, regola, mittente)] per i messaggi pending di questa macchina."""
        attese = attese if attese is not None else self._attese()
        host = self.c.amap.host_self() or ""
        vive_cache: dict[str, list[str]] = {}
        out: dict[tuple[str, str], list] = defaultdict(list)
        for r in self.c.store.read().execute(
                "SELECT id, to_agent, to_slug, raw FROM messages WHERE state='pending' AND to_host=?", (host,)):
            rec = json.loads(r["raw"])
            agent = r["to_agent"]
            if agent not in vive_cache:
                vive_cache[agent] = self.c.vivo.slug_con_canale(agent)
            for slug in regole.destinatari(rec, agent, r["to_slug"], attese, vive_cache[agent]):
                out[(agent, slug)].append((r["id"], regole.classifica(rec, agent, slug, attese),
                                           rec.get("from_session") or rec.get("from")))
        return out

    def blocca_stop(self, agent: str, slug: str) -> list[str]:
        """Messaggi che, secondo la tabella, bloccano lo Stop di agent/slug."""
        dest = self.destinazioni()
        return [m for m, regola, _ in dest.get((agent, slug), []) if regole.blocca_stop(regola)]

    def giro_sveglia(self) -> list[regole.Decisione]:
        """Un giro della tabella: decide per ogni messaggio pending e fa le sveglie. Un ``Rinvio``
        le trasforma in coda col suo motivo."""
        giro = int(now().timestamp() * 1000)
        attese = self._attese()
        r = self.c.store.read()
        gia = {(x["msg_id"], x["agent"], x["slug"]) for x in r.execute(
            "SELECT msg_id, agent, slug FROM wake_decisions WHERE decisione='sveglia'")}
        ultime: dict[tuple, tuple] = {}
        quando: dict[tuple, str] = {}
        for x in r.execute("SELECT msg_id, agent, slug, decisione, motivo, at FROM wake_decisions ORDER BY id"):
            ultime[(x["msg_id"], x["agent"], x["slug"])] = (x["decisione"], x["motivo"])
            quando[(x["msg_id"], x["agent"], x["slug"])] = x["at"]
        ora_fa = iso(now() - timedelta(hours=1))
        quarto_fa = iso(now() - timedelta(seconds=regole.URGENTI_COPPIA_S))
        tutte: list[tuple[str, str, regole.Decisione]] = []
        for (agent, slug), items in self.destinazioni(attese).items():
            items = [i for i in items if (i[0], agent, slug) not in gia]
            if not items:
                continue
            stato = self.c.vivo.stato(agent, slug)
            n_ora = r.execute(
                "SELECT COUNT(DISTINCT giro) FROM wake_decisions WHERE agent=? AND slug=? "
                "AND decisione='sveglia' AND regola!='esito' AND at>=?", (agent, slug, ora_fa)).fetchone()[0]
            urg = {x[0] for x in r.execute(
                "SELECT m.from_agent || '/' || COALESCE(m.from_slug, '') FROM wake_decisions w "
                "JOIN messages m ON m.id=w.msg_id WHERE w.agent=? AND w.slug=? AND w.regola='urgente' "
                "AND w.decisione='sveglia' AND w.at>=?", (agent, slug, quarto_fa))}
            decs = regole.decidi(stato, items, n_ora, urg)
            sveglie = [d for d in decs if d.decisione == "sveglia"]
            if sveglie:
                try:
                    c = self.esegui(agent, slug, stato, sveglie)
                    decs = [regole.Decisione(d.msg_id, d.regola, d.decisione, d.motivo, c)
                            if d.decisione == "sveglia" else d for d in decs]
                except Rinvio as exc:
                    decs = [regole.Decisione(d.msg_id, d.regola, "coda", str(exc), None)
                            if d.decisione == "sveglia" else d for d in decs]
            for d in decs:
                tutte.append((agent, slug, d))
        def da_scrivere(a: str, s: str, d) -> bool:
            k = (d.msg_id, a, s)
            if ultime.get(k) != (d.decisione, d.motivo):
                return True
            # il rinvio di un canale (tmux:has_text, onda:busy, ...) si riprova a ogni giro: ogni
            # RIPROVA_LOG_S lo si riscrive, così il log mostra che la sveglia è ancora in prova
            age = age_s(quando.get(k))
            return (d.decisione == "coda" and (d.motivo or "").startswith(("tmux:", "onda:"))
                    and age is not None and age >= RIPROVA_LOG_S)
        nuove = [(a, s, d) for a, s, d in tutte if da_scrivere(a, s, d)]
        if nuove:
            task_of = {}
            for a, s, d in nuove:
                row = self.c.store.message(d.msg_id)
                task_of[d.msg_id] = row["task_id"] if row else None

            def scrivi():
                conn = self.c._conn()
                at = iso_now()
                for a, s, d in nuove:
                    if d.decisione == "sveglia":  # consegnata nella casella: il messaggio è arrivato
                        conn.execute("UPDATE messages SET letto_at=?, letto_da=? WHERE id=? "
                                     "AND letto_at IS NULL AND (to_slug IS NULL OR to_slug=?)",
                                     (at, f"{a}/{s}", d.msg_id, s))
                    conn.execute("INSERT INTO wake_decisions (at, giro, agent, slug, msg_id, task_id, regola, "
                                 "decisione, motivo, canale) VALUES (?,?,?,?,?,?,?,?,?,?)",
                                 (at, giro, a, s, d.msg_id, task_of.get(d.msg_id), d.regola, d.decisione,
                                  d.motivo, d.canale))
            self.c._write(scrivi)
        self.ultimo_giro = nuove
        return [d for _, _, d in tutte]

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
        self.c.bus._tx(scrivi)
        self.c.bus.accogli(avviso, rec["agent"], rec["slug"], origin="centralino")
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
                Store.put_wait(c, with_pending_wake(rec), origin="centralino")
            self.c.bus._tx(scrivi)

    def _bloccati(self, attese) -> dict[tuple[str, str | None, str], list[dict]]:
        """Messaggi azionabili di questa macchina fermi da più di FERMO_S senza una sessione
        con canale che li riceva, per (agente, slug o None per la casella condivisa, mittente)."""
        host = self.c.amap.host_self() or ""
        canali: dict[str, list[str]] = {}
        gruppi: dict[tuple[str, str | None, str], list[dict]] = defaultdict(list)
        for r in self.c.store.read().execute(
                "SELECT id, ts, to_agent, to_slug, raw FROM messages WHERE state='pending' AND to_host=? "
                "AND ts >= ? AND ts <= ? ORDER BY id",
                (host, iso(now() - timedelta(days=VECCHIO_GG)), iso(now() - timedelta(seconds=FERMO_S)))):
            rec = json.loads(r["raw"])
            if rec.get("intent") == INTENT_FERMI:
                continue
            agent = r["to_agent"]
            if agent not in canali:
                canali[agent] = self.c.vivo.slug_con_canale(agent)
            dest = regole.destinatari(rec, agent, r["to_slug"], attese, canali[agent])
            if any(s in canali[agent] for s in dest):
                continue
            regola = regole.classifica(rec, agent, dest[0] if dest else "", attese)
            if regola not in regole.SVEGLIANO:
                continue
            slug = r["to_slug"] or (dest[0] if dest else None)
            mittente = rec.get("from_session") or rec.get("from") or "?"
            host_m = (rec.get("context") or {}).get("host") if isinstance(rec.get("context"), dict) else None
            if host_m and host_m != host:
                mittente = f"{mittente}@{host_m}"
            gruppi[(agent, slug, mittente)].append({"id": r["id"], "ts": r["ts"], "intent": rec.get("intent") or ""})
        return gruppi

    def avvisi_senza_canale(self) -> list[str]:
        """AM26-F2.2: al posto di un evento per messaggio, un messaggio azionabile (urgente, sveglia
        l'hub) per gruppo fermo, una volta finché il gruppo non si svuota."""
        r = self.c.store.read()
        hub = (self.c.amap.data().get("cc_awareness") or {}).get("hub")
        hub = hub if hub and self.c.amap.agent_exists(hub) else None
        inviati = []
        for (agent, slug, mittente), msgs in self._bloccati(self._attese()).items():
            key = f"fermi:{agent}/{slug or '*'}:{mittente}:{msgs[0]['id']}"
            if r.execute("SELECT 1 FROM meta WHERE key=?", (key,)).fetchone():
                continue
            minuti = int((age_s(msgs[0]["ts"]) or 0) // 60)
            da_quando = f"{minuti} min" if minuti < 120 else f"{minuti // 60} ore"
            vive = [s.slug for s in self.c.vivo.vive(agent) if s.canale == "nessuno"]
            dove = (f"{agent}/{slug} non ha una sessione con canale" if slug else
                    f"nessuna sessione di {agent} con canale")
            if vive:
                dove += f" (vive senza canale: {', '.join(vive)})"
            intents = ", ".join(sorted({m["intent"] for m in msgs if m["intent"]}))
            n = len(msgs)
            testo = (f"{n} {'messaggio' if n == 1 else 'messaggi'} per {agent}{'/' + slug if slug else ''} "
                     f"da {mittente} {'fermo' if n == 1 else 'fermi'} da {da_quando}: {dove}"
                     + (f". Intent: {intents}" if intents else "") + ". Apri una sessione in Onda o reinstrada "
                     f"({', '.join(m['id'] for m in msgs)}).")
            casa = self.c.amap.home_slug(hub) if hub else None
            canali_hub = self.c.vivo.slug_con_canale(hub) if hub and hub != agent else []
            if canali_hub:
                hub_slug = casa if casa in canali_hub else canali_hub[0]
                ev = self._evento(hub, f"{hub}/sveglia", INTENT_FERMI, "urgent", testo,
                                  {"agent": agent, "slug": slug, "mittente": mittente,
                                   "messages": [m["id"] for m in msgs]})
                self.c.bus.accogli(ev, hub, hub_slug, origin="centralino")
                inviati.append(ev["id"])
            else:
                try:
                    self.notifica("Bus: messaggi fermi", testo)
                except Exception as exc:  # noqa: BLE001 — una notifica mancata non ferma il giro
                    self.c.log("warn", "notifica_fallita", errore=str(exc))
                inviati.append(f"notifica:{key}")
            self.c.log("info", "messaggi_fermi", gruppo=key, n=n)
            self.c._write(lambda key=key: self.c._conn().execute(
                "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (key, iso_now())))
        return inviati

    def avvisi_testo(self) -> list[str]:
        """Una sveglia rinviata per testo nella casella (``tmux:has_text``, ``onda:has_text``) da più
        di TESTO_AVVISO_S: un evento a chi ha delegato (``from_session`` del messaggio), una volta per
        attesa. Evento: non sveglia e non blocca lo Stop (riga 6); non va ad Alita."""
        r = self.c.store.read()
        righe: dict[tuple, list] = defaultdict(list)
        for x in r.execute("SELECT w.msg_id, w.agent, w.slug, w.decisione, w.motivo, w.at FROM wake_decisions w "
                           "JOIN messages m ON m.id=w.msg_id WHERE m.state='pending' AND w.msg_id IN "
                           "(SELECT msg_id FROM wake_decisions WHERE motivo LIKE '%:has_text') ORDER BY w.id"):
            righe[(x["msg_id"], x["agent"], x["slug"])].append(x)
        inviati = []
        for (mid, agent, slug), rows in righe.items():
            inizio = None
            for x in reversed(rows):  # la serie finale di rinvii per testo
                if x["decisione"] != "coda" or not (x["motivo"] or "").endswith(":has_text"):
                    break
                inizio = x["at"]
            eta = age_s(inizio)
            if eta is None or eta < TESTO_AVVISO_S:
                continue
            row = self.c.store.message(mid)
            if row is None or not row["task_id"]:
                continue
            m = json.loads(row["raw"])
            tid = row["task_id"]
            key = f"testo:{tid}"
            if r.execute("SELECT 1 FROM meta WHERE key=?", (key,)).fetchone():
                continue
            frm = m.get("from_session") or ""
            f_agent, _, f_slug = frm.partition("/")
            if not f_agent or not f_slug or (f_agent, f_slug) == (agent, slug):
                continue
            f_host = (m.get("context") or {}).get("host") if isinstance(m.get("context"), dict) else None
            me = self.c.amap.host_self()
            to = f"{f_agent}@{f_host}/{f_slug}" if f_host and f_host != me else f"{f_agent}/{f_slug}"
            dove = f"{agent}@{me}/{slug}" if me and f_host and f_host != me else f"{agent}/{slug}"
            motivo = rows[-1]["motivo"]
            minuti = int(eta // 60)
            testo = (f"La sveglia per il task {tid} su {dove} è ferma da {minuti} minuti: nella casella di "
                     f"quella sessione c'è testo scritto e non ancora inviato ({motivo}), quindi il messaggio "
                     f"{mid} non è stato scritto nel prompt. Il centralino riprova da solo; se serve, sblocca "
                     "la sessione a mano. Avviso unico per questo task.")
            try:
                out = self.c.bus.send(agent, to, "event", INTENT_TESTO,
                                      {"summary": testo, "msg_id": mid, "sessione": dove, "motivo": motivo},
                                      sender_session=slug, task_id=tid)
                inviati.append(out["id"])
                self.c.log("info", "avviso_testo", task_id=tid, a=to, minuti=minuti)
            except Exception as exc:  # noqa: BLE001 — un avviso mancato non ferma il giro
                self.c.log("warn", "avviso_testo_fallito", task_id=tid, errore=str(exc))
            self.c._write(lambda key=key: self.c._conn().execute(
                "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (key, iso_now())))
        return inviati

    # ---------- giro ----------

    def giro(self) -> dict:
        gemelle = self.c.bus.chiudi_gemelle()
        for tid in gemelle:
            self.c.log("info", "attesa_gemella_chiusa", task_id=tid)
        scadute = self.scadenze()
        decs = self.giro_sveglia()
        self._pending_wake(self.ultimo_giro)
        avvisi = self.avvisi_senza_canale()
        avvisi += self.avvisi_testo()
        return {"scadute": scadute, "decisioni": len(decs), "avvisi": avvisi, "gemelle": gemelle}
