"""Le operazioni del centralino (contratto §3): stesse risposte delle 9 tool di oggi.

Nel passo 1 i file restano la fonte per tutti i lettori, quindi le letture (inbox, read,
waits, thread) leggono i file; ogni scrittura passa da ``Store.tx()`` e da ``FileBus``,
che la notifica a ``_Specchio`` dentro la stessa transazione: se i file falliscono la
transazione si annulla, e SQLite non è mai avanti ai file.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable

from . import __version__, config
from .db import Store
from .files import BusError, FileBus, location_of
from .indirizzi import AgentMap, ErroreIndirizzo
from .log import Log
from .tempo import iso_now
from .vivo import OndaView, Vivacita

PRIORITA = {"urgent": 0, "high": 1, "normal": 2, "low": 3}


class _Specchio:
    """Riceve le scritture di FileBus e le replica nella transazione aperta."""

    def __init__(self, central: "Centralino"):
        self.c = central

    def message(self, record: dict, *, path: Path | None, state: str, to_slug: str | None,
                to_host: str, delivery: str) -> None:
        if self.c._tx_conn is None:
            # scrittura fatta fuori dal lock (raccolta via SSH): si registra al prossimo _flush
            self.c._pendenti.append(dict(record=record, path=path, state=state, to_slug=to_slug,
                                         to_host=to_host, delivery=delivery))
            return
        conn = self.c._conn()
        Store.put_message(conn, record, state=state, to_slug=to_slug, to_host=to_host,
                          path=str(path) if path else None, origin="centralino", delivery=delivery,
                          mid=path.stem if path else None)
        if delivery == "queued":
            conn.execute("INSERT OR REPLACE INTO remote_queue (msg_id, host, queued_at) VALUES (?,?,?)",
                         (record["id"], to_host, iso_now()))

    def wait(self, rec: dict) -> None:
        Store.put_wait(self.c._conn(), rec, origin="centralino")


class Centralino:
    def __init__(self, home: Path, db_path: Path | None = None, log_path: Path | None = None,
                 kick_wake: Callable[[dict], None] | None = None, pid_alive=None):
        self.home = Path(home)
        self.modo = config.leggi(self.home)
        self.amap = AgentMap(self.home)
        self.store = Store(db_path or self.home / "centralino.db")
        self.files = FileBus(self.home, self.amap)
        self.files.osservatore = _Specchio(self)
        self.onda = OndaView(self.home, pid_alive) if pid_alive else OndaView(self.home)
        self.vivo = Vivacita(self.onda, self.store, self.amap.host_self())
        self.files.session_live = lambda agent, slug: self.vivo.stato(agent, slug).viva
        self.log = Log(log_path)
        self._kick = kick_wake if kick_wake is not None else _kick_wake
        self._tx_conn = None
        self._pendenti: list[dict] = []
        self.started = time.time()

    # ---------- transazione corrente ----------

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
        out = self._write(lambda: self.files.send(
            sender=me.agent, to=a.get("to") or "", msg_type=a.get("message_type") or "request",
            intent=a.get("intent") or "", payload=payload, priority=a.get("priority") or "normal",
            thread=a.get("thread_id"), in_reply_to=a.get("in_reply_to"),
            context_refs=a.get("context_refs"), requires_response=bool(a.get("requires_response")),
            response_by=a.get("response_by"), sender_session=me.slug, task_id=a.get("task_id"),
            kind=kind, target=_target(caller), live_slugs=self._live))
        if kind != "delega":
            self._kick(caller)
        return out

    def op_reply(self, caller: dict, a: dict, warnings: list) -> dict:
        me = self._who(caller)
        orig = self.files.read_message(me.agent, a.get("in_reply_to") or "")
        typ = a.get("message_type") or "response"
        if typ not in ("response", "question"):
            raise BusError("reply: message_type 'response' o 'question'")
        payload: dict[str, Any] = {"summary": a.get("summary") or ""}
        if a.get("details"):
            payload.update(a["details"])
        out = self._write(lambda: self.files.send(
            sender=me.agent, to=self.files.reply_address(orig), msg_type=typ,
            intent=a.get("intent") or f"re-{orig.get('intent') or 'risposta'}", payload=payload,
            priority=a.get("priority") or "normal", thread=orig.get("thread_id"), in_reply_to=orig["id"],
            sender_session=me.slug, task_id=orig.get("task_id"), live_slugs=self._live))
        self._kick(caller)
        return out

    def op_expect(self, caller: dict, a: dict, warnings: list) -> dict:
        me = self._who(caller)
        rec = self._write(lambda: self.files.expect(
            me.agent, me.slug, a.get("description") or "", delegated_to=a.get("delegated_to"),
            deadline=a.get("deadline"), task_id=a.get("task_id"), target=_target(caller)))
        addr = f"{me.agent}/{me.slug}"
        if me.host and self.amap.hosts():
            addr = f"{me.agent}@{me.host}/{me.slug}"
        return {"task_id": rec["task_id"], "deadline": rec["deadline"],
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
            out = self._write(lambda: self.files.close_wait(me.agent, me.slug, a["task_id"]))
            out["remaining"] = self.files.count_inbox(me.agent, me.slug)
            return out
        out = self._write(lambda: self.files.handle(me.agent, me.slug, a.get("message_id") or "",
                                                    a.get("esito") or "", task_id=a.get("task_id"),
                                                    reply=a.get("reply"), live_slugs=self._live))
        if out.get("sent") and not out["sent"].get("queued"):
            self._kick(caller)
        out["remaining"] = self.files.count_inbox(me.agent, me.slug)
        return out

    def op_close(self, caller: dict, a: dict, warnings: list) -> dict:
        me = self._who(caller)
        out = self._write(lambda: self.files.close_wait(me.agent, me.slug, a.get("task_id") or "", a.get("note")))
        out["remaining"] = self.files.count_inbox(me.agent, me.slug)
        return out

    def op_archive(self, caller: dict, a: dict, warnings: list) -> dict:
        me = self._who(caller)
        state = a.get("state") or "done"
        dest = self._write(lambda: self.files.archive_message(me.agent, a.get("message_id") or "", state))
        return {"archived": a.get("message_id"), "state": state, "path": str(dest),
                "remaining": self.files.count_inbox(me.agent, me.slug)}

    def op_inbox(self, caller: dict, a: dict, warnings: list) -> dict:
        me = self._who(caller)
        self._collect()
        items = []
        for path in self.files.list_inbox(me.agent, me.slug, all_sessions=bool(a.get("all_sessions"))):
            try:
                data = self.files.read_message(me.agent, path.stem)
            except BusError:
                continue
            row = self._summarize(path, data, me.slug)
            if a.get("unread_only") and not row["requires_response"]:
                continue
            items.append(row)
        items.sort(key=lambda r: (PRIORITA.get(r["priority"], 2), r["ts"] or ""))
        return {"agent": me.agent, "slug": me.slug, "count": len(items), "messages": items}

    def op_read(self, caller: dict, a: dict, warnings: list) -> dict:
        me = self._who(caller)
        data = self.files.read_message(me.agent, a.get("message_id") or "")
        data["_reply_to"] = self.files.reply_address(data)
        link = data.get("task_link") or {}
        tid = data.get("task_id")
        gest = (f"bus_gestisci(message_id='{data['id']}', esito=...): chiuso (finito), riarma (non ha "
                "finito, aspetti ancora), risposto (con reply: la risposta va all'esecutore con lo stesso "
                "task_id e lo sveglia), letto")
        if link.get("by") == "da_agganciare":
            data["_task_hint"] = (
                f"Da agganciare: {data.get('from')} ha piu' deleghe aperte da te "
                f"({', '.join(link.get('candidates') or [])}) e questo messaggio non dice a quale "
                f"appartiene. Decidi tu: {gest}, con task_id='<quello giusto>'.")
        elif tid and self.files.own_wait(me.agent, tid, me.slug) is not None:
            what = "Domanda" if data.get("type") == "question" else "Ritorno"
            data["_task_hint"] = (f"{what} sul task {tid} che hai delegato a {data['_reply_to']}. "
                                  f"Gestiscilo con {gest}.")
        elif tid and (other := self.files.find_wait(tid)) is not None and other.parent.parent.name == me.agent:
            # BR24-F2.6: delegato da un'altra sessione viva di questo agente, non da questa
            data["_task_hint"] = (f"Messaggio sul task {tid}, delegato da un'altra sessione ({me.agent}/"
                                  f"{other.parent.name[1:]}), non da te: non chiuderlo per lei. Se non ti "
                                  f"riguarda, bus_gestisci(message_id='{data['id']}', esito='letto').")
        elif tid and data.get("type") == "response":
            data["_task_hint"] = (f"Risposta di chi ti ha delegato il task {tid}: prosegui il lavoro; "
                                  f"l'esito finale va con bus_send(to='{data['_reply_to']}', "
                                  f"message_type='response', task_id='{tid}').")
        elif tid:
            data["_task_hint"] = (
                f"Delegated task {tid}: when done, bus_send(to='{data['_reply_to']}', "
                f"message_type='response', in_reply_to='{data['id']}') — the task_id travels with "
                "it and wakes the sender. Questions on the way: message_type='question', same "
                "task_id (in_reply_to carries it). Every return wakes the delegator.")
        self.files.clear_waiting_agent(me.agent, me.slug, answer=data)
        if a.get("archive"):
            dest = self._write(lambda: self.files.archive_message(me.agent, a.get("message_id") or "", "read"))
            data["_location"]["archived_to"] = str(dest)
        return data

    def op_thread(self, caller: dict, a: dict, warnings: list) -> dict:
        tid = a.get("thread_id") or ""
        entries = self.files.thread(tid)
        return {"thread_id": tid, "count": len(entries), "entries": entries}

    def op_waits(self, caller: dict, a: dict, warnings: list) -> dict:
        me = self._who(caller)
        rows = self.files.list_waits(me.agent, None if a.get("all_sessions") else me.slug,
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
        remote = {h: self.files.remote_sessions(h) for h in self.amap.remote_hosts()}
        names = [a["agent"]] if a.get("agent") else sorted(self.amap.agents())
        roster = []
        for name in names:
            if not self.amap.agent_exists(name):
                continue
            info = self.amap.agents()[name]
            vive = self.vivo.vive(name)
            live = [{"slug": s.slug, "host": host,
                     "address": f"{name}/{s.slug}" if len(vive) > 1 else name,
                     "workspace": s.workspace, "pending": self.files.count_inbox(name, s.slug),
                     "stato": s.descrizione, "canale": s.canale}
                    for s in vive]
            for h, rs in remote.items():
                live += [{"slug": s["slug"], "host": h, "address": f"{name}@{h}/{s['slug']}",
                          "workspace": s.get("workspace_path"), "pending": s.get("pending", 0),
                          "last_seen": s.get("last_seen"), "stato": f"viva su {h} (registro remoto)"}
                         for s in rs if s.get("name") == name]
            root = self.files.inbox_root(name)
            roster.append({"agent": name, "role": info.get("role", ""), "domain": info.get("domain"),
                           "live_sessions": live,
                           "broadcast_pending": len(list(root.glob("msg-*.json"))) if root.is_dir() else 0})
        out: dict[str, Any] = {"me": me, "roster": roster}
        if self.amap.hosts():
            hosts = {}
            for h in self.amap.hosts():
                if h == host:
                    continue
                hinfo: dict[str, Any] = {}
                if h in remote:
                    snap = self.files.remote_status(h)
                    hinfo["reachable"] = snap.get("ok", False)
                    hinfo["fetched_at"] = snap.get("fetched_at")
                q = self.home / "outbox" / h
                hinfo["queued_for_it"] = len(list(q.rglob("msg-*.json"))) if q.is_dir() else 0
                last = self.home / "hosts" / h / "last-collect.json"
                if last.is_file():
                    from .files import read_json
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
        oggi (``raw``), così i parser di Orbit e della band cambiano solo la fonte. Legge
        SQLite (in modo ombra è lo specchio dei file, al più 5 s indietro)."""
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
            elif not self.modo.sqlite:
                from .files import read_json
                waiting = (read_json(self.home / "registry" / f"{st.agent}.d" / f"{st.slug}.json") or {}).get(
                    "waiting_agent")
            sessioni.append({**st.as_dict(),
                             "claude_session_id": (onda or {}).get("sessionId") or (row["claude_session_id"] if row else None),
                             "onda_terminal_id": (onda or {}).get("terminalId") or (row["onda_terminal_id"] if row else None),
                             "started_at": row["started_at"] if row else None,
                             "last_heartbeat": row["last_heartbeat"] if row else None,
                             "waiting_agent": waiting, "onda": onda if onda and onda.get("state") != "exited" else onda})
        remoti = {}
        for h in self.amap.remote_hosts():
            from .files import read_json
            snap = read_json(self.home / "hosts" / h / "status.json") or {}
            remoti[h] = {"ok": bool(snap.get("ok")), "fetched_at": snap.get("fetched_at"),
                         "sessioni": snap.get("sessions") or []}
        messaggi = [{"agent": x["to_agent"], "slug": x["to_slug"], "letto": x["state"] != "pending",
                     "state": x["state"], "raw": json.loads(x["raw"])}
                    for x in r.execute(
                        "SELECT to_agent, to_slug, state, raw FROM messages WHERE to_host=? AND "
                        "((state='pending' AND ts>=?) OR (state!='pending' AND ts>=?)) ORDER BY ts",
                        (host, cut("pending_s", 7 * 86400), cut("letti_s", 86400)))]
        attese = [{"agent": x["owner_agent"], "slug": x["owner_slug"], "raw": json.loads(x["raw"])}
                  for x in r.execute("SELECT owner_agent, owner_slug, raw FROM waits WHERE "
                                     "state IN ('expected','expired') OR created_at>=? ORDER BY created_at",
                                     (cut("attese_s", 86400),))]
        agenti = [{"agent": k, **{f: (v or {}).get(f) for f in ("display_name", "role", "domain", "workspace_root",
                                                               "voice_id", "avatar_path", "host")}}
                  for k, v in sorted(self.amap.agents().items())]
        workspaces = {f"{x['agent']}/{x['slug']}": x["workspace"] for x in sessioni if x.get("workspace")}
        workspaces.update({x["agent"]: x["workspace_root"] for x in agenti if x.get("workspace_root")})
        return {"host": host, "generato_at": iso(adesso), "modo": self.modo.as_dict(), "agenti": agenti,
                "sessioni": sessioni, "remoti": remoti, "messaggi": messaggi, "attese": attese,
                "workspaces": workspaces}

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
                "decisioni_sveglia": cnt("SELECT COUNT(*) FROM wake_decisions"),
                "differenze": cnt("SELECT COUNT(*) FROM shadow_diffs")}

    # ombra.* sono aggiunte da ombra.py (Ombra.registra)

    OPS: dict[str, Callable] = {
        "send": op_send, "reply": op_reply, "expect": op_expect, "handle": op_handle,
        "close": op_close, "archive": op_archive, "inbox": op_inbox, "read": op_read,
        "thread": op_thread, "waits": op_waits, "status": op_status,
        "session.heartbeat": op_heartbeat, "session.event": op_event,
        "admin.ping": op_ping, "admin.stats": op_stats, "vista": op_vista,
    }

    # ---------- utilità ----------

    def _collect(self) -> list[dict]:
        """Raccolta dagli altri host FUORI dal lock di scrittura: l'SSH può durare secondi e non
        deve fermare gli altri scrittori. Quello che arriva si registra dopo, in una transazione breve."""
        if not self.amap.remote_hosts():
            return []
        out = self.files.collect()
        if self._pendenti:
            pendenti, self._pendenti = self._pendenti, []

            def scrivi():
                for p in pendenti:
                    self.files.osservatore.message(p.pop("record"), **p)
            self._write(scrivi)
        return out

    def _summarize(self, path: Path, data: dict, own_slug: str) -> dict:
        loc = location_of(path, self.home)
        scope = loc[1] if loc and loc[1] else "all-sessions"
        return {"id": path.stem, "from": data.get("from"), "from_session": data.get("from_session"),
                "from_host": (data.get("context") or {}).get("host"),
                "reply_to": self.files.reply_address(data), "type": data.get("type"),
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


def _kick_wake(caller: dict) -> None:
    """Un giro della sveglia di oggi subito, come faceva il MCP (fire and forget)."""
    script = os.environ.get("AB_WAKE_SCRIPT")
    if not script:
        cfg = caller.get("config_dir") or str(Path.home() / ".claude")
        script = str(Path(cfg) / "scripts" / "agent-team-os-wake.py")
    if not Path(script).is_file():
        return
    py = "/usr/bin/python3" if Path("/usr/bin/python3").exists() else sys.executable
    try:
        subprocess.Popen([py, "-I", script, "tick"], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True)
    except OSError:
        pass
