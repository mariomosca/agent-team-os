"""SQLite fonte unica (BR24-F5.1): stesse operazioni di FileBus, stato solo nel database.

Eredita da FileBus il trasporto (SSH verso il mini, coda ``outbox/<host>/`` letta dalla
raccolta e da alita-memoria-sync), gli indirizzi e le regole d'invio; riscrive tutto ciò
che legge o scrive lo stato: caselle, attese, thread, registro. Nessun file di casella,
attesa o thread viene più scritto. L'unico file che resta è la vista di compatibilità
``registry/<agente>.d/<slug>.json`` per Onda (``scrivi_registro``).

Qui si registrano anche i ritorni di una delega quando arrivano (prima lo faceva il giro
di wake.py, ``collect_returns``): un messaggio con il task_id di un'attesa aperta, diretto
alla sessione che aspetta, entra in ``returns`` come pending.
"""

from __future__ import annotations

import json
from pathlib import Path

from .db import Store
from .files import (ESITI, OPEN_WAIT_STATES, TASK_ID_RE, BusError, FileBus, atomic_write, msg_id,
                    new_task_id, thread_id, with_pending_wake)
from .indirizzi import parse_addr
from .tempo import iso_now, parse_iso

RITORNI = ("response", "question")


def _d(v) -> dict:
    return v if isinstance(v, dict) else {}


class DbBus(FileBus):
    def __init__(self, home: Path, amap, central):
        super().__init__(home, amap)
        self.c = central

    # ---------- transazioni ----------

    def _tx(self, fn):
        """Dentro la transazione aperta dall'operazione, o in una propria (raccolta, hook)."""
        if self.c._tx_conn is not None:
            return fn(self.c._tx_conn)
        return self.c._write(lambda: fn(self.c._conn()))

    def _r(self):
        return self.c._tx_conn if self.c._tx_conn is not None else self.c.store.read()

    # ---------- messaggi ----------

    def _row(self, agent: str, partial: str):
        if not partial:
            return None
        return self._r().execute(
            "SELECT * FROM messages WHERE to_agent=? AND (id=? OR id LIKE ?) "
            "ORDER BY (state='pending') DESC, id LIMIT 1", (agent, partial, partial + "%")).fetchone()

    def _pending(self, agent: str, slug: str | None, all_sessions: bool):
        host = self.host_self() or ""
        q = "SELECT id, to_slug, raw FROM messages WHERE to_agent=? AND to_host=? AND state='pending'"
        args: list = [agent, host]
        if slug and not all_sessions:
            q += " AND (to_slug IS NULL OR to_slug=?)"
            args.append(slug)
        return self._r().execute(q + " ORDER BY id", args).fetchall()

    def list_inbox(self, agent, slug=None, all_sessions=False):
        return [r["id"] for r in self._pending(agent, slug, all_sessions)]

    def count_inbox(self, agent, slug=None, all_sessions=False):
        return len(self._pending(agent, slug, all_sessions))

    def inbox_items(self, agent, slug=None, all_sessions=False):
        return [(r["id"], r["to_slug"] or "all-sessions", json.loads(r["raw"]))
                for r in self._pending(agent, slug, all_sessions)]

    def broadcast_pending(self, agent):
        return self._r().execute("SELECT COUNT(*) FROM messages WHERE to_agent=? AND to_slug IS NULL "
                                 "AND state='pending' AND to_host=?", (agent, self.host_self() or "")).fetchone()[0]

    def resolve_message(self, agent, partial_id):
        r = self._row(agent, partial_id)
        return r["id"] if r else None

    def read_message(self, agent, partial_id):
        r = self._row(agent, partial_id)
        if r is None:
            raise BusError(f"no message matching '{partial_id}' in {agent}'s inbox", "not_found")
        data = json.loads(r["raw"])
        data["_location"] = {"path": f"centralino.db:{r['id']}", "scope": r["to_slug"] or "all-sessions",
                             "state": "pending" if r["state"] == "pending" else "archived"}
        return data

    def _set_state(self, conn, mid: str, state: str, record: dict | None = None) -> None:
        if record is None:
            conn.execute("UPDATE messages SET state=? WHERE id=?", (state, mid))
            return
        h = _d(record.get("handled"))
        conn.execute("UPDATE messages SET state=?, esito=?, handled_by=?, handled_at=?, task_id=?, raw=? WHERE id=?",
                     (state, h.get("esito"), h.get("by"), h.get("at"), record.get("task_id"),
                      json.dumps(record, ensure_ascii=False, sort_keys=True), mid))

    def archive_message(self, agent, partial_id, state="done"):
        if state not in ("read", "done"):
            raise BusError("state must be 'read' or 'done'")
        r = self._row(agent, partial_id)
        if r is None:
            raise BusError(f"no message matching '{partial_id}' in {agent}'s inbox", "not_found")
        if r["state"] == "pending":
            self._tx(lambda c: self._set_state(c, r["id"], state))
        return f"centralino.db:{r['id']}:{state if r['state'] == 'pending' else r['state']}"

    def task_of(self, agent, message_id):
        r = self._row(agent, message_id)
        if r is None:
            return None
        data = json.loads(r["raw"])
        tid = data.get("task_id") or _d(data.get("payload")).get("task_id")
        return tid if isinstance(tid, str) else None

    def wake_chain(self, agent, slug):
        return None  # v2.6: non blocca più niente, nel v3 non si timbra

    def _deliver(self, record, sender, agent, slug, to_host, warning):
        mid, task_id = record["id"], record.get("task_id")
        if to_host:
            outcome = self.deliver_remote(to_host, agent, slug, record)
            def scrivi(c):
                Store.put_message(c, record, state="pending", to_slug=slug, to_host=to_host, path=None,
                                  origin="centralino", delivery="queued" if outcome == "queued" else "remote")
                if outcome == "queued":
                    c.execute("INSERT OR REPLACE INTO remote_queue (msg_id, host, queued_at) VALUES (?,?,?)",
                              (mid, to_host, iso_now()))
            self._tx(scrivi)
            addr = f"{agent}@{to_host}" + (f"/{slug}" if slug else "")
            if outcome == "queued":
                warning = (f"{to_host} unreachable now: queued in outbox/{to_host}/, delivered at the next collection."
                           if (self.hosts().get(to_host) or {}).get("ssh") else
                           f"queued in outbox/{to_host}/: {to_host} collects it at its next round "
                           "(it cannot be reached from here).")
            return {"id": mid, "delivered_to": addr, "host": to_host, "queued": outcome == "queued",
                    "path": None, "warning": warning, "task_id": task_id}
        self.accogli(record, agent, slug, origin="centralino")
        return {"id": mid, "delivered_to": f"{agent}/{slug}" if slug else agent, "host": self.host_self(),
                "queued": False, "path": f"centralino.db:{mid}", "warning": warning, "task_id": task_id}

    def accogli(self, record: dict, agent: str, slug: str | None, origin: str, mid: str | None = None) -> bool:
        """Un messaggio entra in una casella di questa macchina (invio, mini, cloud). False se c'era già."""
        mid = mid or record["id"]

        def scrivi(c):
            if c.execute("SELECT 1 FROM messages WHERE id=?", (mid,)).fetchone():
                return False
            Store.put_message(c, record, state="pending", to_slug=slug, to_host=self.host_self() or "",
                              path=None, origin=origin, mid=mid)
            self._registra_ritorno(c, record, agent, slug, mid)
            return True
        return self._tx(scrivi)

    def _ricevi(self, agent, slug, mid, record):
        return self.accogli(record, agent, slug, origin="mini", mid=mid)

    # ---------- agente nudo (decisione 2 di Alita) ----------

    def slug_nudo(self, agent: str) -> str | None:
        """Dove va un messaggio a un agente senza sessione: l'unica sessione viva se ce n'è
        una sola, altrimenti la sessione di casa (workspace_root) se è viva, altrimenti la
        casella condivisa (None), che non sveglia nessuno e chi apre una sessione vede."""
        vive = self.c.vivo.slug_vivi(agent)
        if len(vive) == 1:
            return vive[0]
        casa = self.amap.home_slug(agent)
        if casa and casa in vive:
            return casa
        return None

    def send(self, sender, to, msg_type, intent, payload, **kw):
        agent, slug, want_host = parse_addr(to)
        if not slug and not want_host and self.resolve_host(agent, None) is None and self.agent_exists(agent):
            if (nudo := self.slug_nudo(agent)):
                to = f"{agent}/{nudo}"
        return super().send(sender, to, msg_type, intent, payload, **kw)

    # ---------- attese ----------

    def _wait(self, task_id: str) -> dict | None:
        r = self._r().execute("SELECT raw FROM waits WHERE task_id=?", (task_id,)).fetchone()
        return json.loads(r["raw"]) if r else None

    def find_wait(self, task_id):
        return task_id if task_id and self._wait(task_id) is not None else None

    def wait_owner(self, task_id):
        w = self._wait(task_id) if task_id else None
        return (w.get("agent"), w.get("slug")) if w else None

    def _save_wait(self, c, rec: dict) -> dict:
        Store.put_wait(c, with_pending_wake(rec), origin="centralino")
        return rec

    def _registra_ritorno(self, c, record: dict, agent: str, slug: str | None, mid: str) -> None:
        tid = record.get("task_id")
        if not isinstance(tid, str) or record.get("type") not in RITORNI:
            return
        r = c.execute("SELECT raw FROM waits WHERE task_id=?", (tid,)).fetchone()
        if r is None:
            return
        rec = json.loads(r["raw"])
        if rec.get("state") not in OPEN_WAIT_STATES or rec.get("agent") != agent or slug not in (None, rec.get("slug")):
            return
        frm = record.get("from_session") or record.get("from") or "?"
        if frm == f"{rec.get('agent')}/{rec.get('slug')}":
            return  # la sessione che aspetta non si manda ritorni da sola
        host = _d(record.get("context")).get("host")
        if host and host != self.host_self():
            frm = f"{frm}@{host}"
        rec.setdefault("returns", {})[mid] = {
            "from": frm, "at": record.get("ts") or iso_now(), "type": record.get("type"),
            "summary": " ".join(str(_d(record.get("payload")).get("summary") or "").split())[:160],
            "state": "pending"}
        if not rec.get("executor_session") and record.get("from_session"):
            rec["executor_session"] = record["from_session"]
        rec["last_activity"] = record.get("ts") or iso_now()
        self._save_wait(c, rec)

    def expect(self, agent, slug, description, delegated_to=None, deadline=None, task_id=None,
               target=None, delegation=None):
        task_id = task_id or new_task_id()
        if not TASK_ID_RE.match(task_id):
            raise BusError(f"invalid task_id {task_id!r}: 3-64 chars of [A-Za-z0-9._-]")
        if deadline and parse_iso(deadline) is None:
            raise BusError(f"deadline is not ISO 8601: {deadline!r}")
        rec = {"v": 1, "task_id": task_id, "agent": agent, "slug": slug, "host": self.host_self(),
               "description": description.strip()[:200], "delegated_to": delegated_to,
               "created_at": iso_now(), "deadline": deadline, "state": "expected",
               "target": {k: v for k, v in (target or {}).items() if v}}
        if delegation:
            rec["delegation"] = {k: v for k, v in delegation.items() if v}

        def scrivi(c):
            if (w := c.execute("SELECT owner_agent, owner_slug FROM waits WHERE task_id=?", (task_id,)).fetchone()):
                raise BusError(f"task {task_id} already has a waiter ({w['owner_agent']}/{w['owner_slug']}): "
                               "one task, one waiter", "conflict")
            return self._save_wait(c, rec)
        return self._tx(scrivi)

    def list_waits(self, agent, slug=None, include_closed=False):
        q = "SELECT raw FROM waits WHERE owner_agent=?"
        args: list = [agent]
        if slug:
            q += " AND owner_slug=?"
            args.append(slug)
        if not include_closed:
            q += " AND state IN ('expected','expired')"
        return [json.loads(r["raw"]) for r in self._r().execute(q + " ORDER BY task_id", args)]

    def own_wait(self, agent, task_id, slug=None):
        if not task_id:
            return None
        rec = self._wait(task_id)
        if rec is None or rec.get("agent") != agent:
            return None
        if slug and rec.get("slug") != slug and self.session_live(agent, rec.get("slug") or ""):
            return None
        return (task_id, rec)

    def handle(self, agent, slug, message_id, esito, task_id=None, reply=None, live_slugs=None,
               reply_type="response", details=None):
        if esito not in ESITI:
            raise BusError(f"esito must be one of {', '.join(ESITI)}, not {esito!r}")
        r = self._row(agent, message_id)
        if r is None:
            raise BusError(f"no message matching '{message_id}' in {agent}'s inbox", "not_found")
        if r["state"] != "pending":
            raise BusError(f"{r['id']} is already filed ({r['state']}): nothing to handle", "already_filed")
        data = json.loads(r["raw"])
        mid = r["id"]
        link = _d(data.get("task_link"))
        if task_id:
            if not TASK_ID_RE.match(task_id):
                raise BusError(f"invalid task_id {task_id!r}")
            have = data.get("task_id")
            if have and have != task_id:
                raise BusError(f"{mid} already belongs to task {have}, not {task_id}", "conflict")
            if self.own_wait(agent, task_id, slug) is None:
                raise BusError(f"task {task_id} is not a delegation of {agent}/{slug}: nothing to hook it to",
                               "not_yours")
            if not have:
                data["task_id"] = task_id
                data["task_link"] = {**link, "by": "scelto", "at": iso_now()}
        elif link.get("by") == "da_agganciare" and esito in ("chiuso", "riarma"):
            raise BusError(f"{mid} is da agganciare ({', '.join(link.get('candidates') or [])}): "
                           "say which task with task_id")
        tid = data.get("task_id")
        own = self.own_wait(agent, tid, slug)
        if esito == "riarma" and own is None:
            raise BusError("riarma needs a return on a delegation of yours: this message has none")
        if esito == "risposto" and not (reply or "").strip():
            raise BusError("risposto needs the answer text in reply")

        out: dict = {"message": mid, "esito": esito, "task_id": tid}
        if esito == "risposto":
            payload = {"summary": reply.strip(), **(details or {})}
            sent = self.send(sender=agent, to=self.reply_address(data), msg_type=reply_type,
                             intent=f"re-{data.get('intent') or 'risposta'}", payload=payload,
                             thread=data.get("thread_id"), in_reply_to=mid, sender_session=slug, task_id=tid,
                             live_slugs=live_slugs)
            out["sent"] = {"id": sent["id"], "to": sent["delivered_to"], "queued": sent.get("queued"),
                           "warning": sent.get("warning")}
        now = iso_now()
        data["handled"] = {"esito": esito, "at": now, "by": f"{agent}/{slug}"}
        state = "read" if esito == "letto" else "done"

        def scrivi(c):
            self._set_state(c, mid, state, data)
            self.clear_waiting_agent(agent, slug, answer=data)
            if own is None:
                return None
            rec = json.loads(c.execute("SELECT raw FROM waits WHERE task_id=?", (tid,)).fetchone()["raw"])
            e = rec.setdefault("returns", {}).setdefault(mid, {
                "from": data.get("from_session") or data.get("from"), "at": data.get("ts") or now,
                "type": data.get("type"), "state": "pending"})
            e.update(state="handled", handled=esito, handled_at=now)
            e.pop("pending_wake", None)
            if esito == "chiuso":
                rec.update(state="closed", closed_at=now, closed_by=mid)
            elif esito in ("riarma", "risposto"):
                if rec.get("state") not in OPEN_WAIT_STATES:
                    rec["reopened_from"] = rec.get("state")
                rec["state"] = "expected"
                rec["last_activity"] = now
                if esito == "riarma":
                    rec["rearmed_at"] = now
            return self._save_wait(c, rec)
        rec = self._tx(scrivi)
        out["archived_to"] = f"centralino.db:{mid}:{state}"
        if rec is not None:
            out["wait"] = {"task_id": rec["task_id"], "state": rec["state"]}
        return out

    def close_wait(self, agent, slug, task_id, note=None):
        if not TASK_ID_RE.match(task_id or ""):
            raise BusError(f"invalid task_id {task_id!r}")
        own = self.own_wait(agent, task_id)
        if own is None:
            raise BusError(f"task {task_id} is not a delegation of {agent}: no wait to close", "not_found")
        now = iso_now()

        def scrivi(c):
            filed = []
            for r in c.execute("SELECT id, raw FROM messages WHERE to_agent=? AND state='pending' AND task_id=?",
                               (agent, task_id)).fetchall():
                m = json.loads(r["raw"])
                if m.get("kind") == "delega":
                    continue
                m["handled"] = {"esito": "chiuso", "at": now, "by": f"{agent}/{slug}", "via": "close"}
                self._set_state(c, r["id"], "done", m)
                filed.append({"id": r["id"], "type": m.get("type"),
                              "summary": str(_d(m.get("payload")).get("summary") or "")[:200]})
            rec = json.loads(c.execute("SELECT raw FROM waits WHERE task_id=?", (task_id,)).fetchone()["raw"])
            prev = rec.get("state")
            for f in filed:
                e = rec.setdefault("returns", {}).setdefault(f["id"], {"type": f["type"], "at": now})
                e.update(state="handled", handled="chiuso", handled_at=now)
                e.pop("pending_wake", None)
            if prev in OPEN_WAIT_STATES or prev == "expired":
                rec.update(state="closed", closed_at=now, closed_by=f"close:{agent}/{slug}")
                if note:
                    rec["note"] = note
            self._save_wait(c, rec)
            self.clear_waiting_agent(agent, slug, task_id=task_id)
            return {"task_id": task_id, "state": rec["state"], "was": prev, "filed": filed}
        return self._tx(scrivi)

    # ---------- registro (sessions) ----------

    def set_waiting_agent(self, agent, slug, info):
        host = self.host_self() or ""
        self._tx(lambda c: c.execute("UPDATE sessions SET waiting_agent=? WHERE agent=? AND slug=? AND host=?",
                                     (json.dumps(info, ensure_ascii=False), agent, slug, host)))

    def clear_waiting_agent(self, agent, slug, task_id=None, answer=None):
        host = self.host_self() or ""
        r = self._r().execute("SELECT waiting_agent FROM sessions WHERE agent=? AND slug=? AND host=?",
                              (agent, slug, host)).fetchone()
        w = json.loads(r["waiting_agent"]) if r and r["waiting_agent"] else None
        if not isinstance(w, dict):
            return False
        if answer is not None:
            if answer.get("from") != w.get("agent"):
                return False
            same = (answer.get("in_reply_to") == w.get("msg_id")
                    or (answer.get("task_id") and answer.get("task_id") == w.get("task_id")))
            if not same:
                return False
        elif task_id and task_id != w.get("task_id"):
            return False
        self._tx(lambda c: c.execute("UPDATE sessions SET waiting_agent=NULL WHERE agent=? AND slug=? AND host=?",
                                     (agent, slug, host)))
        return True

    def registry_sessions(self, agent):
        return [dict(r) for r in self._r().execute(
            "SELECT * FROM sessions WHERE agent=? AND host=? AND ended_at IS NULL", (agent, self.host_self() or ""))]

    def scrivi_registro(self, agent: str, slug: str) -> None:
        """Vista di compatibilità per Onda: ``registry/<agente>.d/<slug>.json`` col pid della
        sessione, che Onda usa per risalire allo slug dal terminale. Niente altro la legge."""
        r = self.c.store.read().execute("SELECT * FROM sessions WHERE agent=? AND slug=? AND host=?",
                                        (agent, slug, self.host_self() or "")).fetchone()
        f = self.registry_dir(agent) / f"{slug}.json"
        if r is None or r["ended_at"]:
            f.unlink(missing_ok=True)
            return
        rec = {"name": agent, "slug": slug, "active": True, "last_seen": r["last_heartbeat"],
               "workspace_path": r["workspace_path"], "session_started": r["started_at"], "pid": r["pid"]}
        if self.host_self():
            rec["host"] = self.host_self()
        atomic_write(f, rec)

    # ---------- thread ----------

    def thread(self, tid):
        out: dict[str, dict] = {}
        for r in self._r().execute("SELECT raw FROM messages WHERE thread_id=? ORDER BY ts", (tid,)):
            m = json.loads(r["raw"])
            e = {k: m[k] for k in ("id", "from", "to", "type", "intent", "ts", "in_reply_to", "task_id")
                 if m.get(k) is not None}
            e["summary"] = _d(m.get("payload")).get("summary", "")
            out[e.get("id") or f"_{len(out)}"] = e
        for r in self._r().execute("SELECT id, entry FROM thread_legacy WHERE thread_id=?", (tid,)):
            out.setdefault(r["id"], json.loads(r["entry"]))
        return sorted(out.values(), key=lambda e: e.get("ts") or "")

    def _append_thread(self, record):
        pass  # il thread è la tabella messages

    def _append_outbox(self, sender, record, slug):
        pass  # l'audit è la tabella messages


__all__ = ["DbBus", "msg_id", "thread_id", "parse_addr"]
