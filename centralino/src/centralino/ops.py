"""Le operazioni del centralino (contratto §3 e §13).

Ogni operazione è una chiamata sul socket; ogni scrittura passa da ``Store.tx()`` e da ``DbBus``,
che tiene lo stato solo in SQLite.
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any, Callable

from . import __version__, palla
from .db import Store
from .deleghe import Deleghe
from .dbbus import BusError, DbBus
from .indirizzi import AgentMap, ErroreIndirizzo
from .log import Log
from .remoto import read_json
from .tempo import iso_now
from .vivo import OndaView, Vivacita

PRIORITA = {"urgent": 0, "high": 1, "normal": 2, "low": 3}


class Centralino:
    def __init__(self, home: Path, db_path: Path | None = None, log_path: Path | None = None, pid_alive=None):
        # La transazione aperta è del thread che l'ha aperta: un altro thread (giro della
        # sveglia, raccolta, un'altra richiesta) non deve mai vederla né scriverci dentro.
        self._tl = threading.local()
        self.home = Path(home)
        self.amap = AgentMap(self.home)
        self.store = Store(db_path or self.home / "centralino.db")
        self._tx_conn = None
        self.bus = DbBus(self.home, self.amap, self)
        self.remoto = self.bus.remoto
        self.onda = OndaView(self.home, pid_alive) if pid_alive else OndaView(self.home)
        self.vivo = Vivacita(self.onda, self.store, self.amap.host_self())
        self.bus.session_live = lambda agent, slug: self.vivo.stato(agent, slug).viva
        self.log = Log(log_path)
        # chi invia sveglia subito: il giro della sveglia non aspetta i suoi 10 s
        self.sveglia_ora = threading.Event()
        self.orbit = palla.Orbit(Path(os.environ["AB_ORBIT_DB"]) if os.environ.get("AB_ORBIT_DB") else palla.ORBIT_DB)
        self.started = time.time()
        # DC29-F4: registro spawn/deleghe e chiusura automatica (Onda e Orbit veri solo da ~/.agent-team-os)
        self.deleghe = Deleghe(self)
        self.deleghe.registra()

    # ---------- transazione corrente (per thread) ----------

    @property
    def _tx_conn(self):
        return getattr(self._tl, "conn", None)

    @_tx_conn.setter
    def _tx_conn(self, conn) -> None:
        self._tl.conn = conn

    def _conn(self):
        if self._tx_conn is None:
            raise RuntimeError("scrittura fuori transazione")
        return self._tx_conn

    def _write(self, fn: Callable[[], Any]) -> Any:
        with self.store.tx() as conn:
            prev, self._tx_conn = self._tx_conn, conn
            try:
                return fn()
            finally:
                self._tx_conn = prev

    # ---------- dispatch ----------

    def handle_request(self, req: dict) -> dict:
        rid = req.get("id")
        op = req.get("op") or ""
        t0 = time.monotonic()
        warnings: list[str] = []
        try:
            if req.get("v") != 1:
                raise BusError("versione del protocollo non supportata (serve v=1)")
            fn = self.OPS.get(op)
            if fn is None:
                raise BusError(f"operazione sconosciuta {op!r}")
            result = fn(self, req.get("caller") or {}, req.get("args") or {}, warnings)
            resp = {"v": 1, "id": rid, "ok": True, "result": result}
            if warnings:
                resp["warnings"] = warnings
            self.log("info", "op", op=op, ms=round((time.monotonic() - t0) * 1000, 1),
                     caller=(req.get("caller") or {}).get("cwd"))
            return resp
        except (BusError, ErroreIndirizzo) as exc:
            self.log("warn", "op_rifiutata", op=op, code=exc.code, message=str(exc))
            return {"v": 1, "id": rid, "ok": False, "error": {"code": exc.code, "message": str(exc)}}
        except Exception as exc:  # noqa: BLE001 - l'agente deve ricevere un errore, non una connessione chiusa
            self.log("error", "op_errore", op=op, error=repr(exc))
            return {"v": 1, "id": rid, "ok": False,
                    "error": {"code": "internal", "message": f"errore interno del centralino: {exc!r}"}}

    # ---------- identità ----------

    def _who(self, caller: dict):
        ident = self.amap.identify(caller)
        if caller.get("heartbeat", True):
            self._write(lambda: Store.heartbeat(self._conn(), ident.agent, ident.slug, ident.host or "",
                                                iso_now(), workspace=caller.get("cwd"), caller=caller))
        return ident

    def _live(self, agent: str) -> list[str]:
        return self.vivo.slug_vivi(agent)

    # ---------- operazioni degli agenti ----------

    def op_send(self, caller: dict, a: dict, warnings: list) -> dict:
        me = self._who(caller)
        kind = a.get("kind")
        if a.get("expect_result"):
            if kind == "comunicazione":
                raise BusError("expect_result=true and kind='comunicazione' contradict each other")
            kind = "delega"
        payload: dict[str, Any] = {"summary": a.get("summary") or ""}
        if a.get("details"):
            payload.update(a["details"])
        out = self._write(lambda: self.bus.send(
            sender=me.agent, to=a.get("to") or "", msg_type=a.get("message_type") or "request",
            intent=a.get("intent") or "", payload=payload, priority=a.get("priority") or "normal",
            thread=a.get("thread_id"), in_reply_to=a.get("in_reply_to"),
            context_refs=a.get("context_refs"), requires_response=bool(a.get("requires_response")),
            response_by=a.get("response_by"), sender_session=me.slug, task_id=a.get("task_id"),
            kind=kind, target=_target(caller), live_slugs=self._live))
        if kind != "delega":
            self.sveglia_ora.set()
        else:
            self._su_deleghe("su_delega", me, a, out)
        return out

    def op_reply(self, caller: dict, a: dict, warnings: list) -> dict:
        """v3 bus_reply: risponde a un messaggio e lo archivia con esito risposto. Se il messaggio
        è già archiviato, la risposta parte lo stesso (stesso thread e task_id). Una domanda
        dell'esecutore su una delega non la consuma: la delega resta in casella per l'esito."""
        me = self._who(caller)
        mid = a.get("message_id") or a.get("in_reply_to") or ""
        typ = a.get("message_type") or "response"
        if typ not in ("response", "question"):
            raise BusError("bus_reply: message_type 'response' (default) o 'question'")
        summary = (a.get("summary") or "").strip()
        if not summary:
            raise BusError("bus_reply: summary vuoto")
        orig = self.bus.read_message(me.agent, mid)
        tid = orig.get("task_id")
        domanda_esecutore = typ == "question" and not (tid and self.bus.own_wait(me.agent, tid, me.slug))
        if orig["_location"]["state"] == "pending" and not domanda_esecutore:
            out = self._write(lambda: self.bus.handle(me.agent, me.slug, orig["id"], "risposto", reply=summary,
                                                        live_slugs=self._live, reply_type=typ,
                                                        details=a.get("details")))
        else:
            payload: dict[str, Any] = {"summary": summary, **(a.get("details") or {})}
            sent = self._write(lambda: self.bus.send(
                sender=me.agent, to=self.bus.reply_address(orig), msg_type=typ,
                intent=f"re-{orig.get('intent') or 'risposta'}", payload=payload,
                thread=orig.get("thread_id"), in_reply_to=orig["id"], sender_session=me.slug,
                task_id=orig.get("task_id"), live_slugs=self._live))
            out = {"message": orig["id"], "esito": None, "task_id": orig.get("task_id"),
                   "sent": {"id": sent["id"], "to": sent["delivered_to"], "queued": sent.get("queued"),
                            "warning": sent.get("warning")}}
        self.sveglia_ora.set()
        out["remaining"] = self.bus.count_inbox(me.agent, me.slug)
        return out

    def op_expect(self, caller: dict, a: dict, warnings: list) -> dict:
        me = self._who(caller)
        rec = None
        if a.get("onda_prompt"):
            # t-20261029-lbgswf: il prompt di sveglia via Onda si aggancia alla delega già aperta
            rec = self._write(lambda: self.bus.aggancia(me.agent, me.slug, a.get("delegated_to"),
                                                        a.get("testo") or a.get("description") or "",
                                                        task_id=a.get("task_id")))
        agganciata = rec is not None
        rec = rec or self._write(lambda: self.bus.expect(
            me.agent, me.slug, a.get("description") or "", delegated_to=a.get("delegated_to"),
            deadline=a.get("deadline"), task_id=a.get("task_id"), target=_target(caller)))
        addr = f"{me.agent}/{me.slug}"
        if me.host and self.amap.hosts():
            addr = f"{me.agent}@{me.host}/{me.slug}"
        return {"task_id": rec["task_id"], "deadline": rec.get("deadline"), "agganciata": agganciata,
                "prompt_line": (f"Quando hai finito, manda l'esito con bus_send a {addr}, "
                                f"message_type='response', task_id='{rec['task_id']}'. Se ti serve "
                                f"una risposta prima, manda le domande allo stesso indirizzo con "
                                f"message_type='question' e lo stesso task_id.")}

    def op_handle(self, caller: dict, a: dict, warnings: list) -> dict:
        me = self._who(caller)
        if not a.get("message_id"):
            # passo 0 (BR24-F2.3): senza message_id, task_id + chiuso chiudono l'attesa
            if not a.get("task_id") or a.get("esito") != "chiuso":
                raise BusError("without message_id: task_id and esito='chiuso' close a delegation's wait")
            out = self._write(lambda: self.bus.close_wait(me.agent, me.slug, a["task_id"]))
            self._attesa_chiusa(out)
            out["remaining"] = self.bus.count_inbox(me.agent, me.slug)
            return out
        out = self._write(lambda: self.bus.handle(me.agent, me.slug, a.get("message_id") or "",
                                                    a.get("esito") or "", task_id=a.get("task_id"),
                                                    reply=a.get("reply"), live_slugs=self._live))
        if out.get("sent") and not out["sent"].get("queued"):
            self.sveglia_ora.set()
        self._attesa_chiusa(out)
        out["remaining"] = self.bus.count_inbox(me.agent, me.slug)
        return out

    def op_close(self, caller: dict, a: dict, warnings: list) -> dict:
        """v3 bus_close: consuma un messaggio (chiuso, riarma, letto) o, con solo task_id,
        chiude l'attesa di una delega."""
        me = self._who(caller)
        esito = a.get("esito") or "chiuso"
        if a.get("message_id"):
            if esito not in ("chiuso", "riarma", "letto"):
                raise BusError("bus_close: esito chiuso, riarma o letto (per rispondere usa bus_reply)")
            out = self._write(lambda: self.bus.handle(me.agent, me.slug, a["message_id"], esito,
                                                        task_id=a.get("task_id"), live_slugs=self._live))
        elif a.get("task_id"):
            if esito != "chiuso":
                raise BusError("bus_close con solo task_id: esito chiuso")
            out = self._write(lambda: self.bus.close_wait(me.agent, me.slug, a["task_id"], a.get("note")))
        else:
            raise BusError("bus_close: serve message_id oppure task_id")
        self._attesa_chiusa(out)
        out["remaining"] = self.bus.count_inbox(me.agent, me.slug)
        return out

    def op_archive(self, caller: dict, a: dict, warnings: list) -> dict:
        me = self._who(caller)
        state = a.get("state") or "done"
        dest = self._write(lambda: self.bus.archive_message(me.agent, a.get("message_id") or "", state))
        return {"archived": a.get("message_id"), "state": state, "path": str(dest),
                "remaining": self.bus.count_inbox(me.agent, me.slug)}

    def op_inbox(self, caller: dict, a: dict, warnings: list) -> dict:
        me = self._who(caller)
        self._collect()
        items = []
        for mid, scope, data in self.bus.inbox_items(me.agent, me.slug, all_sessions=bool(a.get("all_sessions"))):
            row = self._summarize(mid, scope, data, me.slug)
            if a.get("unread_only") and not row["requires_response"]:
                continue
            items.append(row)
        items.sort(key=lambda r: (PRIORITA.get(r["priority"], 2), r["ts"] or ""))
        out: dict[str, Any] = {"agent": me.agent, "slug": me.slug, "count": len(items), "messages": items}
        mie = []
        for w in self.bus.list_waits(me.agent, me.slug):
            if w.get("state") != "expected":
                continue
            rets = [{"msg_id": k, "type": e.get("type"), "from": e.get("from"), "summary": e.get("summary"),
                     "pending_wake": (e.get("pending_wake") or {}).get("reason")}
                    for k, e in (w.get("returns") or {}).items()
                    if isinstance(e, dict) and e.get("state") not in ("handled", "seen")]
            mie.append({"task_id": w["task_id"], "delegated_to": w.get("delegated_to"),
                        "description": w.get("description"), "created_at": w.get("created_at"),
                        "deadline": w.get("deadline"), "da_gestire": rets})
        if mie:
            out["mie_attese"] = mie
        return out

    def op_read(self, caller: dict, a: dict, warnings: list) -> dict:
        if a.get("thread_id") and not a.get("message_id"):
            return self.op_thread(caller, a, warnings)
        me = self._who(caller)
        data = self.bus.read_message(me.agent, a.get("message_id") or "")
        self.bus.segna_lettura(me.agent, me.slug, data["_location"]["path"].partition(":")[2])
        data["_reply_to"] = self.bus.reply_address(data)
        link = data.get("task_link") or {}
        tid = data.get("task_id")
        gest = (f"bus_close(message_id='{data['id']}', esito=...): chiuso (finito, l'attesa si chiude), "
                "riarma (non ha finito, aspetti ancora), letto; per rispondere all'esecutore "
                f"bus_reply(message_id='{data['id']}'), che lo sveglia con lo stesso task_id")
        if link.get("by") == "da_agganciare":
            data["_task_hint"] = (
                f"Da agganciare: {data.get('from')} ha piu' deleghe aperte da te "
                f"({', '.join(link.get('candidates') or [])}) e questo messaggio non dice a quale "
                f"appartiene. Decidi tu: {gest}, con task_id='<quello giusto>'.")
        elif tid and self.bus.own_wait(me.agent, tid, me.slug) is not None:
            what = "Domanda" if data.get("type") == "question" else "Ritorno"
            data["_task_hint"] = (f"{what} sul task {tid} che hai delegato a {data['_reply_to']}. "
                                  f"Gestiscilo con {gest}.")
        elif tid and (other := self.bus.wait_owner(tid)) is not None and other[0] == me.agent:
            # BR24-F2.6: delegato da un'altra sessione viva di questo agente, non da questa
            data["_task_hint"] = (f"Messaggio sul task {tid}, delegato da un'altra sessione ({me.agent}/"
                                  f"{other[1]}), non da te: non chiuderlo per lei. Se non ti "
                                  f"riguarda, bus_close(message_id='{data['id']}', esito='letto').")
        elif tid and data.get("type") == "response":
            data["_task_hint"] = (f"Risposta di chi ti ha delegato il task {tid}: prosegui il lavoro; "
                                  f"l'esito finale va con bus_reply sul messaggio della delega (o su "
                                  f"questo, bus_reply(message_id='{data['id']}')): porta il task_id "
                                  "e sveglia chi delega.")
        elif tid:
            data["_task_hint"] = (
                f"Task {tid} delegato a te: a lavoro finito, bus_reply(message_id='{data['id']}') con "
                "l'esito; porta il task_id e sveglia chi delega. Domande prima di finire: "
                f"bus_reply(message_id='{data['id']}', message_type='question'): la delega resta "
                "in casella per l'esito.")
        self.bus.clear_waiting_agent(me.agent, me.slug, answer=data)
        if a.get("archive"):
            dest = self._write(lambda: self.bus.archive_message(me.agent, a.get("message_id") or "", "read"))
            data["_location"]["archived_to"] = str(dest)
        return data

    def op_thread(self, caller: dict, a: dict, warnings: list) -> dict:
        tid = a.get("thread_id") or ""
        entries = self.bus.thread(tid)
        return {"thread_id": tid, "count": len(entries), "entries": entries}

    def op_waits(self, caller: dict, a: dict, warnings: list) -> dict:
        me = self._who(caller)
        rows = self.bus.list_waits(me.agent, None if a.get("all_sessions") else me.slug,
                                     bool(a.get("include_closed")))
        asks, unhandled = [], []
        for r in rows:
            if r.get("state") != "expected":
                continue
            legacy = {k: dict(v, type="question") for k, v in (r.get("questions") or {}).items()}
            for mid, e in {**legacy, **(r.get("returns") or {})}.items():
                if e.get("state") in ("handled", "seen") or e.get("answered_at"):
                    continue
                row = {"task_id": r["task_id"], "msg_id": mid, "from": e.get("from"), "at": e.get("at"),
                       "summary": e.get("summary"), "state": e.get("state"),
                       "pending_wake": (e.get("pending_wake") or {}).get("reason")}
                (asks if e.get("type") == "question" else unhandled).append(row)
        out: dict[str, Any] = {"agent": me.agent, "slug": me.slug, "count": len(rows), "waits": rows}
        if asks:
            out["waiting_on_you"] = asks
        if unhandled:
            out["unhandled_returns"] = unhandled
        return out

    def op_status(self, caller: dict, a: dict, warnings: list) -> dict:
        host = self.amap.host_self()
        try:
            me_id = self._who(caller)
            me: dict[str, Any] = {"agent": me_id.agent, "slug": me_id.slug, "host": host,
                                  "address": f"{me_id.agent}/{me_id.slug}"}
        except ErroreIndirizzo as exc:
            me = {"error": str(exc), "host": host}
        collected = self._collect()
        remote = {h: self.remoto.remote_sessions(h) for h in self.amap.remote_hosts()}
        names = [a["agent"]] if a.get("agent") else sorted(self.amap.agents())
        roster = []
        for name in names:
            if not self.amap.agent_exists(name):
                continue
            info = self.amap.agents()[name]
            vive = self.vivo.vive(name)
            live = [{"slug": s.slug, "host": host,
                     "address": f"{name}/{s.slug}" if len(vive) > 1 else name,
                     "workspace": s.workspace, "pending": self.bus.count_inbox(name, s.slug),
                     "stato": s.descrizione, "canale": s.canale}
                    for s in vive]
            for h, rs in remote.items():
                live += [{"slug": s["slug"], "host": h, "address": f"{name}@{h}/{s['slug']}",
                          "workspace": s.get("workspace_path"), "pending": s.get("pending", 0),
                          "last_seen": s.get("last_seen"), "stato": f"viva su {h} (registro remoto)"}
                         for s in rs if s.get("name") == name]
            roster.append({"agent": name, "role": info.get("role", ""), "domain": info.get("domain"),
                           "live_sessions": live, "broadcast_pending": self.bus.broadcast_pending(name)})
        out: dict[str, Any] = {"me": me, "roster": roster}
        if self.amap.hosts():
            hosts = {}
            for h in self.amap.hosts():
                if h == host:
                    continue
                hinfo: dict[str, Any] = {}
                if h in remote:
                    snap = self.remoto.remote_status(h)
                    hinfo["reachable"] = snap.get("ok", False)
                    hinfo["fetched_at"] = snap.get("fetched_at")
                q = self.home / "outbox" / h
                hinfo["queued_for_it"] = len(list(q.rglob("msg-*.json"))) if q.is_dir() else 0
                last = self.home / "hosts" / h / "last-collect.json"
                if last.is_file():
                    hinfo["last_collect"] = read_json(last)
                hosts[h] = hinfo
            out["hosts"] = hosts
            if collected:
                out["collected_now"] = collected
        return out

    # ---------- eventi di sessione ----------

    def op_heartbeat(self, caller: dict, a: dict, warnings: list) -> dict:
        me = self.amap.identify(caller)
        self._write(lambda: Store.heartbeat(self._conn(), me.agent, me.slug, me.host or "", iso_now(),
                                            workspace=caller.get("cwd"), caller=caller))
        return {"address": me.address, "stato": self.vivo.stato(me.agent, me.slug).descrizione}

    def op_event(self, caller: dict, a: dict, warnings: list) -> dict:
        me = self.amap.identify(caller)
        ev = a.get("event")
        if ev not in ("start", "prompt", "stop", "end"):
            raise BusError("event: start | prompt | stop | end")
        now = iso_now()
        if ev == "end":
            self._write(lambda: Store.end_session(self._conn(), me.agent, me.slug, me.host or "", now))
        else:
            self._write(lambda: Store.heartbeat(self._conn(), me.agent, me.slug, me.host or "", now,
                                                workspace=caller.get("cwd"), caller=caller, event=ev))
        return {"address": me.address, "event": ev}

    # ---------- vista globale per Orbit e bus-band (BR24-F4.3) ----------

    def op_vista(self, caller: dict, a: dict, warnings: list) -> dict:
        """Sola lettura, senza chiamante e senza heartbeat: i record nel formato dei file di
        oggi (``raw``), così i parser di Orbit e della band cambiano solo la fonte."""
        from datetime import timedelta
        from .tempo import iso, now
        adesso = now()
        cut = lambda k, d: iso(adesso - timedelta(seconds=float(a.get(k) or d)))  # noqa: E731
        host = self.amap.host_self() or ""
        r = self.store.read()
        sessioni = []
        for st in self.vivo.vive():
            row = r.execute("SELECT * FROM sessions WHERE agent=? AND slug=? AND host=?",
                            (st.agent, st.slug, host)).fetchone()
            onda = self.onda.find(st.agent, st.slug, host, row["claude_session_id"] if row else None)
            waiting = None
            if row is not None and row["waiting_agent"]:
                waiting = json.loads(row["waiting_agent"])
            sessioni.append({**st.as_dict(),
                             "claude_session_id": (onda or {}).get("sessionId") or (row["claude_session_id"] if row else None),
                             "onda_terminal_id": (onda or {}).get("terminalId") or (row["onda_terminal_id"] if row else None),
                             "started_at": row["started_at"] if row else None,
                             "last_heartbeat": row["last_heartbeat"] if row else None,
                             "waiting_agent": waiting, "onda": onda if onda and onda.get("state") != "exited" else onda})
        remoti = {}
        for h in self.amap.remote_hosts():
            snap = read_json(self.home / "hosts" / h / "status.json") or {}
            remoti[h] = {"ok": bool(snap.get("ok")), "fetched_at": snap.get("fetched_at"),
                         "sessioni": snap.get("sessions") or []}
        messaggi = [{"agent": x["to_agent"], "slug": x["to_slug"], "letto": x["state"] != "pending",
                     "state": x["state"], "raw": json.loads(x["raw"]), "_m": x}
                    for x in r.execute(
                        "SELECT id, to_agent, to_slug, state, raw, kind, type, task_id, in_reply_to, letto_at "
                        "FROM messages WHERE to_host=? AND "
                        "((state='pending' AND ts>=?) OR (state!='pending' AND ts>=?)) ORDER BY ts",
                        (host, cut("pending_s", 7 * 86400), cut("letti_s", 86400)))]
        # BR24-F6.6: accanto all'attesa, quando chi esegue ha letto la delega (null: non ancora,
        # o la delega è su un'altra macchina e la lettura resta là)
        attese = [{"agent": x["owner_agent"], "slug": x["owner_slug"], "raw": json.loads(x["raw"]),
                   "letto_at": x["letto_at"], "letto_da": x["letto_da"], "delega_qui": x["delega_qui"]}
                  for x in r.execute("SELECT w.owner_agent, w.owner_slug, w.raw, m.letto_at, m.letto_da, m.id IS NOT NULL AS delega_qui "
                                     "FROM waits w LEFT JOIN messages m ON m.id = w.delegation_msg WHERE "
                                     "w.state IN ('expected','expired') OR w.created_at>=? ORDER BY w.created_at",
                                     (cut("attese_s", 86400),))]
        # BR24-F6.7: chi ha la palla, calcolato qui per la banda e per l'Ufficio di Orbit
        vive = [{"agent": x["agent"], "slug": x["slug"], "host": x.get("host") or host,
                 "lavora": x.get("onda_state") == "working"
                 or (not isinstance(x.get("onda_state"), str) and x.get("ferma") is False)} for x in sessioni]
        rifs = [rf for x in attese if (rf := palla.rif_piano(x["raw"].get("description") or ""))]
        rifs += [rf for x in messaggi if not x["letto"]
                 if (rf := palla.rif_piano(x["raw"].get("intent") or "",
                                           str(palla._d(x["raw"].get("payload")).get("summary") or "")))]
        righe_orbit = self.orbit.righe(rifs)  # una query sola per attese e messaggi
        for x in attese:
            x.update(palla.stato_palla(x["raw"], x["letto_at"], vive, host, righe_orbit,
                                       letto_noto=bool(x.pop("delega_qui")), letto_da=x["letto_da"],
                                       risposta=self._risposta_dopo(r, x["raw"])))
        # BR24-F6.8: lo stato di ogni messaggio in casella per chi lo riceve
        for x in messaggi:
            m = x.pop("_m")
            if not x["letto"]:
                x.update(self._stato_messaggio(r, m, x["raw"], righe_orbit))
        agenti = [{"agent": k, **{f: (v or {}).get(f) for f in ("display_name", "role", "domain", "workspace_root",
                                                               "voice_id", "avatar_path", "host")}}
                  for k, v in sorted(self.amap.agents().items())]
        workspaces = {f"{x['agent']}/{x['slug']}": x["workspace"] for x in sessioni if x.get("workspace")}
        workspaces.update({x["agent"]: x["workspace_root"] for x in agenti if x.get("workspace_root")})
        doc = self.onda.doc()
        onda = {"valido": self.onda.valid(), "writtenAt": doc.get("writtenAt"), "sessioni": self.onda.entries()}
        return {"host": host, "generato_at": iso(adesso), "agenti": agenti,
                "sessioni": sessioni, "onda": onda, "remoti": remoti, "messaggi": messaggi, "attese": attese,
                "workspaces": workspaces}

    @staticmethod
    def _risposta_dopo(r, rec: dict) -> dict | None:
        """L'ultimo messaggio del delegante sul task dopo l'ultimo ritorno, e se chi esegue l'ha
        letto (letto_at, o già consumato). None se l'ultima mossa è di chi esegue."""
        rets = [e for e in (rec.get("returns") or {}).values() if isinstance(e, dict) and e.get("at")]
        if not rets or not rec.get("task_id"):
            return None
        ultimo = max(e["at"] for e in rets)
        # ts al secondo: >= e senza la delega stessa, che è del delegante ma viene prima
        m = r.execute("SELECT letto_at, state FROM messages WHERE task_id=? AND from_agent=? AND from_slug=? "
                      "AND ts>=? AND kind IS NOT 'delega' ORDER BY ts DESC, id DESC LIMIT 1",
                      (rec["task_id"], rec.get("agent") or "", rec.get("slug") or "", ultimo)).fetchone()
        if m is None:
            return None
        return {"letto": m["letto_at"] or ("consumato" if m["state"] != "pending" else None)}

    @staticmethod
    def _stato_messaggio(r, m, raw: dict, righe_orbit: list[dict]) -> dict:
        agent, slug = m["to_agent"], m["to_slug"]
        dalla = "from_agent=? AND (? IS NULL OR from_slug=?)"
        domanda_aperta = bool(m["kind"] == "delega" and m["task_id"] and r.execute(
            f"SELECT 1 FROM messages q WHERE q.type='question' AND q.task_id=? AND {dalla} AND NOT EXISTS "
            "(SELECT 1 FROM messages a WHERE a.in_reply_to=q.id) LIMIT 1",
            (m["task_id"], agent, slug, slug)).fetchone())
        # una risposta a un messaggio di questa sessione: a una sua domanda, a un suo esito
        risposta_a_me = bool(m["type"] == "response" and m["in_reply_to"] and r.execute(
            f"SELECT 1 FROM messages WHERE id=? AND {dalla}",
            (m["in_reply_to"], agent, slug, slug)).fetchone())
        ritorno_mio = bool(m["kind"] != "delega" and m["task_id"] and r.execute(
            "SELECT 1 FROM waits WHERE task_id=? AND owner_agent=? AND (? IS NULL OR owner_slug=?)",
            (m["task_id"], agent, slug, slug)).fetchone())
        return palla.stato_messaggio(raw, letto_at=m["letto_at"], domanda_aperta=domanda_aperta,
                                     risposta_a_me=risposta_a_me, ritorno_mio=ritorno_mio,
                                     orbit_righe=righe_orbit)

    # ---------- servizio ----------

    def op_ping(self, caller: dict, a: dict, warnings: list) -> dict:
        return {"versione": __version__, "schema": 1, "uptime_s": round(time.time() - self.started),
                "home": str(self.home), "host": self.amap.host_self(), "agent_map_errore": self.amap.errore}

    def op_stats(self, caller: dict, a: dict, warnings: list) -> dict:
        r = self.store.read()
        cnt = lambda q: r.execute(q).fetchone()[0]  # noqa: E731
        return {"messaggi": cnt("SELECT COUNT(*) FROM messages"),
                "pending": cnt("SELECT COUNT(*) FROM messages WHERE state='pending'"),
                "attese_aperte": cnt("SELECT COUNT(*) FROM waits WHERE state='expected'"),
                "sessioni_vive": len(self.vivo.vive()),
                "decisioni_sveglia": cnt("SELECT COUNT(*) FROM wake_decisions")}

    OPS: dict[str, Callable] = {
        "send": op_send, "reply": op_reply, "expect": op_expect, "handle": op_handle,
        "close": op_close, "archive": op_archive, "inbox": op_inbox, "read": op_read,
        "thread": op_thread, "waits": op_waits, "status": op_status,
        "session.heartbeat": op_heartbeat, "session.event": op_event,
        "admin.ping": op_ping, "admin.stats": op_stats, "vista": op_vista,
    }

    # ---------- utilità ----------

    def _su_deleghe(self, nome: str, *args) -> None:
        """DC29-F4: un errore del registro deleghe non tocca mai l'operazione del bus."""
        try:
            getattr(self.deleghe, nome)(*args)
        except Exception as exc:  # noqa: BLE001
            self.log("error", "deleghe_errore", evento=nome, error=repr(exc))

    def _attesa_chiusa(self, out: dict) -> None:
        """bus_close esito chiuso: se l'attesa è chiusa adesso, parte chiudi_delega."""
        w = out.get("wait") or {}
        tid, state = (w.get("task_id"), w.get("state")) if w else (out.get("task_id"), out.get("state"))
        if tid and state == "closed":
            self._su_deleghe("su_attesa_chiusa", tid)

    def raccolta(self) -> list[dict]:
        """Giro periodico verso gli altri host: consegna la coda, tira giù quello che hanno
        lasciato per noi e aggiorna lo stato delle loro sessioni."""
        out = self.remoto.collect(force=True)
        for h in self.amap.remote_hosts():
            self.remoto.remote_status(h, force=True)
        q = self.home / "outbox"

        def scrivi():
            c = self._conn()
            for r in c.execute("SELECT msg_id, host FROM remote_queue").fetchall():
                if not any((q / r["host"]).rglob(f"{r['msg_id']}.json")):
                    c.execute("DELETE FROM remote_queue WHERE msg_id=?", (r["msg_id"],))
                    c.execute("UPDATE messages SET delivery='remote' WHERE id=?", (r["msg_id"],))
        self._write(scrivi)
        if any(x.get("pulled") for x in out):
            self.sveglia_ora.set()
        return out

    def _collect(self) -> list[dict]:
        """Raccolta dagli altri host FUORI dal lock di scrittura: l'SSH può durare secondi e non
        deve fermare gli altri scrittori; ogni messaggio arrivato si registra in una transazione breve."""
        if not self.amap.remote_hosts():
            return []
        return self.remoto.collect()

    def _summarize(self, mid: str, scope: str, data: dict, own_slug: str) -> dict:
        return {"id": mid, "from": data.get("from"), "from_session": data.get("from_session"),
                "from_host": (data.get("context") or {}).get("host"),
                "reply_to": self.bus.reply_address(data), "type": data.get("type"),
                "intent": data.get("intent"), "priority": data.get("priority", "normal"),
                "requires_response": data.get("requires_response", False),
                "response_by": data.get("response_by"), "thread_id": data.get("thread_id"),
                "ts": data.get("ts"), "summary": (data.get("payload") or {}).get("summary", ""),
                "task_id": data.get("task_id"), "kind": data.get("kind"), "task_link": data.get("task_link"),
                "scope": scope, "for_this_session": scope in ("all-sessions", own_slug)}


def _target(caller: dict) -> dict:
    return {"onda_terminal_id": caller.get("onda_terminal_id"),
            "claude_session_id": caller.get("claude_session_id"),
            "tmux_pane": caller.get("tmux_pane")}
