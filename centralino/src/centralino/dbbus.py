"""Il bus in SQLite: caselle, attese, thread, registro delle sessioni (BR24-F5.5).

SQLite è l'unica fonte. Nessun file di casella, attesa o thread viene scritto: l'unico file
che resta è la vista di compatibilità ``registry/<agente>.d/<slug>.json`` per Onda
(``scrivi_registro``). Il trasporto verso gli altri host sta in ``remoto.py``.

Qui si registrano anche i ritorni di una delega quando arrivano: un messaggio con il task_id
di un'attesa aperta, diretto alla sessione che aspetta, entra in ``returns`` come pending.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from .db import Store
from .indirizzi import AgentMap, ErroreIndirizzo, parse_addr
from .remoto import Remoto, atomic_write, rand
from .tempo import iso_now, now, parse_iso

RITORNI = ("response", "question")
DECLARED_TYPES = ("request", "brief", "handoff")
KINDS = ("delega", "comunicazione")
KIND_HELP = (
    "say what this {type} is with kind: 'delega' if you want the outcome back (the bus "
    "registers the wait and wakes you on every return), 'comunicazione' if nothing "
    "has to come back. There is no default."
)
ESITI = ("chiuso", "riarma", "risposto", "letto")
OPEN_WAIT_STATES = ("expected",)
TASK_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{2,63}$")


class BusError(RuntimeError):
    """Rifiuto che tocca al chiamante correggere. ``code`` è il codice d'errore del contratto."""

    def __init__(self, message: str, code: str = "bad_request"):
        super().__init__(message)
        self.code = code


def msg_id() -> str:
    return f"msg-{now().strftime('%Y%m%dT%H%M%SZ')}-{rand(8)}"


def thread_id() -> str:
    return f"thread-{now().strftime('%Y%m%d')}-{rand(6)}"


def new_task_id() -> str:
    return f"t-{now().strftime('%Y%m%d')}-{rand(6)}"


def with_pending_wake(rec: dict) -> dict:
    """Il motivo dell'ultima sveglia rinviata ancora aperta, solo su un'attesa aperta."""
    why = [e["pending_wake"] for e in (rec.get("returns") or {}).values()
           if isinstance(e, dict) and e.get("state") == "pending" and e.get("pending_wake")]
    if why and rec.get("state") == "expected":
        rec["pending_wake"] = max(why, key=lambda w: w.get("checked_at") or "")
    else:
        rec.pop("pending_wake", None)
    return rec


def _d(v) -> dict:
    return v if isinstance(v, dict) else {}


class DbBus:
    def __init__(self, home: Path, amap: AgentMap, central):
        self.home = Path(home)
        self.amap = amap
        self.c = central
        self.remoto = Remoto(self.home, amap, lambda agent, slug, mid, rec:
                             self.accogli(rec, agent, slug, origin="mini", mid=mid))
        # sessione viva secondo la regola unica (il centralino la imposta): serve al passaggio
        # di un'attesa quando la sessione che ha delegato non c'è più
        self.session_live = lambda agent, slug: False

    # ---------- agenti e host ----------

    def agents(self) -> dict:
        return self.amap.agents()

    def agent_exists(self, agent: str) -> bool:
        return self.amap.agent_exists(agent)

    def host_self(self) -> str | None:
        return self.amap.host_self()

    def hosts(self) -> dict:
        return self.amap.hosts()

    def resolve_host(self, agent: str, want: str | None = None) -> str | None:
        try:
            return self.amap.resolve_host(agent, want)
        except ErroreIndirizzo as exc:
            raise BusError(str(exc), exc.code) from None

    def routing_denied(self, sender: str, recipient: str) -> str | None:
        rules = (self.amap.data().get("routing_rules") or {}).get(sender, {})
        if recipient in (rules.get("deny") or []):
            return rules.get("reason", "isolation rule")
        return None

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

    def count_inbox(self, agent, slug=None, all_sessions=False):
        return len(self._pending(agent, slug, all_sessions))

    def inbox_items(self, agent, slug=None, all_sessions=False):
        return [(r["id"], r["to_slug"] or "all-sessions", json.loads(r["raw"]))
                for r in self._pending(agent, slug, all_sessions)]

    def broadcast_pending(self, agent):
        return self._r().execute("SELECT COUNT(*) FROM messages WHERE to_agent=? AND to_slug IS NULL "
                                 "AND state='pending' AND to_host=?", (agent, self.host_self() or "")).fetchone()[0]

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

    def _deliver(self, record, sender, agent, slug, to_host, warning):
        mid, task_id = record["id"], record.get("task_id")
        if to_host:
            outcome = self.remoto.deliver_remote(to_host, agent, slug, record)
            def scrivi(c):
                Store.put_message(c, record, state="pending", to_slug=slug, to_host=to_host, path=None,
                                  origin="centralino", delivery="queued" if outcome == "queued" else "remote")
                if outcome == "queued":  # la raccolta la toglie da qui quando la coda si svuota
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

    def send(self, sender: str, to: str, msg_type: str, intent: str, payload: dict,
             priority: str = "normal", thread: str | None = None, in_reply_to: str | None = None,
             context_refs: list[str] | None = None, requires_response: bool = False,
             response_by: str | None = None, sender_session: str | None = None,
             task_id: str | None = None, kind: str | None = None, target: dict | None = None,
             live_slugs=None) -> dict:
        """``live_slugs(agent) -> list[str] | None``: sessioni vive per la regola unica (None = ignoto)."""
        agent, slug, want_host = parse_addr(to)
        if not slug and not want_host and self.resolve_host(agent, None) is None and self.agent_exists(agent):
            if (nudo := self.slug_nudo(agent)):
                slug, to = nudo, f"{agent}/{nudo}"
        kind = self.check_kind(msg_type, kind)
        if kind == "delega" and not sender_session:
            raise BusError("a delegation needs the sender's session, to know whom to wake")
        if task_id is None and in_reply_to and kind != "delega":
            task_id = self.task_of(sender, in_reply_to)
        if task_id is not None and not TASK_ID_RE.match(task_id):
            raise BusError(f"invalid task_id {task_id!r}: 3-64 chars of [A-Za-z0-9._-]")
        if not self.agent_exists(agent):
            known = ", ".join(sorted(self.agents())) or "none configured"
            raise BusError(f"unknown agent '{agent}'. Known: {known}", "unknown_agent")
        if (reason := self.routing_denied(sender, agent)) is not None:
            raise BusError(f"routing denied {sender} -> {agent} ({reason})")
        to_host = self.resolve_host(agent, want_host)

        warning = None
        if slug and not to_host and live_slugs is not None:
            live = live_slugs(agent)
            if live is not None and slug not in live:
                warning = (f"no live session '{agent}/{slug}' (live: {', '.join(live) or 'none'}). "
                           "Delivered anyway; it will be read when that session starts.")

        mid = msg_id()
        thread = thread or thread_id()
        wait = None
        if kind == "delega":
            wait = self.expect(sender, sender_session, f"{intent}: {payload.get('summary') or ''}",
                               delegated_to=to, deadline=response_by, task_id=task_id,
                               target=target, delegation={"msg_id": mid, "thread_id": thread})
            task_id = wait["task_id"]
            requires_response = True
        record = {
            "id": mid, "version": "1.0", "from": sender, "to": agent, "thread_id": thread,
            "in_reply_to": in_reply_to, "type": msg_type, "intent": intent, "priority": priority,
            "payload": payload, "context_refs": context_refs or [],
            "requires_response": requires_response, "ts": iso_now(),
        }
        if response_by:
            record["response_by"] = response_by
        if sender_session:
            record["from_session"] = f"{sender}/{sender_session}"
        if task_id:
            record["task_id"] = task_id
        if kind:
            record["kind"] = kind
        if me := self.host_self():
            record["context"] = {"host": me}

        if sender_session and task_id:
            if msg_type == "question":
                dest = f"{agent}@{to_host}" if to_host else agent
                self.set_waiting_agent(sender, sender_session, {
                    "agent": agent, "address": dest + (f"/{slug}" if slug else ""),
                    "task_id": task_id, "msg_id": mid, "since": record["ts"],
                    "summary": " ".join(str(payload.get("summary") or "").split())[:160],
                })
            elif msg_type == "response":
                self.clear_waiting_agent(sender, sender_session, task_id=task_id)

        out = self._deliver(record, sender, agent, slug, to_host, warning)
        if wait:
            out["expecting"] = {"task_id": task_id, "deadline": wait["deadline"]}
            out["note"] = (f"attesa registrata {task_id}: ogni ritorno di {to} su questo task "
                           "ti sveglia se sei fermo")
        return out

    @staticmethod
    def check_kind(msg_type: str, kind: str | None) -> str | None:
        if kind is not None and kind not in KINDS:
            raise BusError(f"kind must be 'delega' or 'comunicazione', not {kind!r}", "kind_required")
        if msg_type in DECLARED_TYPES:
            if kind is None:
                raise BusError(KIND_HELP.format(type=msg_type), "kind_required")
            return kind
        if kind == "delega":
            raise BusError(f"a {msg_type} cannot be a delegation: send a request, brief or "
                           "handoff with kind='delega'", "kind_required")
        return None

    def reply_address(self, record: dict) -> str:
        sender = record.get("from") or ""
        fs = record.get("from_session") or ""
        host = _d(record.get("context")).get("host")
        slug = fs.partition("/")[2] if "/" in fs else ""
        if host and host != self.host_self():
            return f"{sender}@{host}" + (f"/{slug}" if slug else "")
        return f"{sender}/{slug}" if slug else sender

    # ---------- attese ----------

    def _wait(self, task_id: str) -> dict | None:
        r = self._r().execute("SELECT raw FROM waits WHERE task_id=?", (task_id,)).fetchone()
        return json.loads(r["raw"]) if r else None

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

    def scrivi_registro(self, agent: str, slug: str) -> None:
        """Vista di compatibilità per Onda: ``registry/<agente>.d/<slug>.json`` col pid della
        sessione, che Onda usa per risalire allo slug dal terminale. Niente altro la legge."""
        r = self.c.store.read().execute("SELECT * FROM sessions WHERE agent=? AND slug=? AND host=?",
                                        (agent, slug, self.host_self() or "")).fetchone()
        f = self.home / "registry" / f"{agent}.d" / f"{slug}.json"
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


__all__ = ["DbBus", "BusError", "msg_id", "thread_id", "parse_addr"]
