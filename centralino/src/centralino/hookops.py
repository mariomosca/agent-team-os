"""Operazioni degli hook sottili (contratto §13.2) e consegna dal cloud (F4.4).

Gli hook di Claude Code chiamano ``agent-team-os-centralino.py``, che passa qui il
chiamante (cwd, pid della sessione, sessione Claude, pannello) e stampa quello che
ricevono indietro: il banner a inizio sessione, l'avviso per gli urgenti a ogni prompt,
la decisione di blocco dello Stop. Il registro delle sessioni è la tabella ``sessions``;
il centralino scrive anche la vista di compatibilità per Onda.
"""

from __future__ import annotations

from typing import Any

from . import regole
from .db import Store
from .dbbus import TASK_ID_RE, BusError
from .sveglia import attese_di
from .tempo import iso_now

STOP_CAP = 3


def _s(v, n: int) -> str:
    return " ".join(str(v or "").split())[:n]


def _riga(mid: str, m: dict) -> str:
    pl = m.get("payload") if isinstance(m.get("payload"), dict) else {}
    out = f"  - {mid}  [{m.get('priority') or 'normal'}] {m.get('type')}/{m.get('intent')} from {m.get('from')}"
    if pl.get("summary"):
        out += f"  | {_s(pl['summary'], 60)}"
    if m.get("response_by"):
        out += f"  (by {m['response_by']})"
    return out


class HookOps:
    def __init__(self, central):
        self.c = central

    # ---------- eventi ----------

    def _evento(self, caller: dict, ev: str):
        me = self.c.amap.identify(caller)
        now = iso_now()
        if ev == "end":
            self.c._write(lambda: Store.end_session(self.c._conn(), me.agent, me.slug, me.host or "", now))
        else:
            self.c._write(lambda: Store.heartbeat(self.c._conn(), me.agent, me.slug, me.host or "", now,
                                                  workspace=caller.get("cwd"), caller=caller, event=ev))
        self.c.bus.scrivi_registro(me.agent, me.slug)
        return me

    def op_start(self, caller: dict, a: dict, warnings: list) -> dict:
        me = self._evento(caller, "start")
        info = self.c.amap.agents().get(me.agent) or {}
        items = self.c.bus.inbox_items(me.agent, me.slug)
        alte = sum(1 for _, _, m in items if m.get("priority") in ("urgent", "high"))
        host = f"@{me.host}" if me.host else ""
        righe = ["=== Agent Team OS ===",
                 f"You are {info.get('display_name') or me.agent} ({me.agent}{host}/{me.slug}). "
                 f"Role: {info.get('role') or ''}",
                 f"Workspace: {caller.get('cwd')}", ""]
        if not items:
            righe.append("Inbox: empty.")
        else:
            righe.append(f"Inbox: {len(items)} pending" + (f" ({alte} urgent/high)" if alte else ""))
            righe.append("")
            righe += [_riga(mid, m) for mid, _, m in items[-5:]]
        mie = [w for w in self.c.bus.list_waits(me.agent, me.slug) if w.get("state") == "expected"]
        if mie:
            righe += ["", f"Deleghe aperte: {len(mie)} ("
                      + ", ".join(f"{w['task_id']} a {w.get('delegated_to') or '?'}" for w in mie[:5]) + ")"]
        righe += ["", "Bus: bus_inbox, bus_read, bus_send, bus_reply, bus_close, bus_status (MCP agent-bus).",
                  "=== End Agent Team OS ==="]
        persona = None
        pf = info.get("persona_file")
        if pf:
            p = self.c.home / pf
            if p.is_file():
                persona = p.read_text(encoding="utf-8")
        return {"address": f"{me.agent}/{me.slug}", "display": info.get("display_name") or me.agent,
                "banner": "\n".join(righe), "persona": persona}

    def op_prompt(self, caller: dict, a: dict, warnings: list) -> dict:
        me = self._evento(caller, "prompt")
        alte = [(mid, m) for mid, _, m in self.c.bus.inbox_items(me.agent, me.slug)
                if m.get("priority") in ("urgent", "high")]
        if not alte:
            return {"additionalContext": None}
        righe = []
        for mid, m in alte[:3]:
            pl = m.get("payload") if isinstance(m.get("payload"), dict) else {}
            r = f"  - [{m.get('priority')}] {m.get('type')}/{m.get('intent')} from {m.get('from_session') or m.get('from')} (id: {mid})"
            if pl.get("summary"):
                r += f"\n    {_s(pl['summary'], 120)}"
            righe.append(r)
        txt = (f"[agent-team-os] You have {len(alte)} urgent/high messages in inbox for {me.agent}/{me.slug}:\n"
               + "\n".join(righe) + "\nConsider pausing to read them with bus_read, or bus_inbox for an overview.")
        return {"additionalContext": txt}

    def op_stop(self, caller: dict, a: dict, warnings: list) -> dict:
        me = self.c.amap.identify(caller)
        cap = int(a.get("cap") or STOP_CAP)
        attese = attese_di(self.c.store)
        bloccano = []
        for mid, scope, m in self.c.bus.inbox_items(me.agent, me.slug):
            regola = regole.classifica(m, me.agent, me.slug, attese)
            if regola == "delega" and self._domanda_aperta(me, m.get("task_id"), mid):
                continue  # BR24-F5.5d: aspetta la risposta alla sua domanda, torna azionabile con lei
            if regole.blocca_stop(regola):
                bloccano.append((mid, m))
        host = me.host or ""
        row = self.c.store.read().execute("SELECT drain_blocks FROM sessions WHERE agent=? AND slug=? AND host=?",
                                          (me.agent, me.slug, host)).fetchone()
        n = (row["drain_blocks"] if row else 0) or 0

        def setta(v):
            def f():
                Store.heartbeat(self.c._conn(), me.agent, me.slug, host, iso_now(), workspace=caller.get("cwd"),
                                caller=caller, event="stop")
                self.c._conn().execute("UPDATE sessions SET drain_blocks=? WHERE agent=? AND slug=? AND host=?",
                                       (v, me.agent, me.slug, host))
            self.c._write(f)
        if not bloccano or n >= cap:
            setta(0)
            self.c.bus.scrivi_registro(me.agent, me.slug)
            return {"block": False, "pending": len(bloccano), "allentato": bool(bloccano)}
        setta(n + 1)
        self.c.bus.scrivi_registro(me.agent, me.slug)
        lista = "\n".join(f"  {mid}  from={m.get('from_session') or m.get('from')}  intent={m.get('intent') or m.get('type')}"
                          for mid, m in bloccano[:5])
        reason = (f"{len(bloccano)} message(s) asking for an action not handled yet (block {n + 1}/{cap}). "
                  "Read each one and consume it before stopping: bus_reply to answer, bus_close with "
                  "chiuso (done), riarma (keep waiting on your delegation) or letto.\n" + lista)
        return {"block": True, "reason": reason, "pending": len(bloccano)}

    def _domanda_aperta(self, me, tid, delega_id: str) -> bool:
        """L'esecutore ha fatto una domanda sul task e da allora non gli è tornato niente."""
        if not tid:
            return False
        r = self.c.store.read().execute(
            "SELECT MAX(CASE WHEN from_agent=? AND from_slug=? AND type='question' THEN ts END) AS q, "
            "MAX(CASE WHEN to_agent=? AND id<>? THEN ts END) AS dopo FROM messages WHERE task_id=?",
            (me.agent, me.slug, me.agent, delega_id, tid)).fetchone()
        return bool(r["q"]) and (r["dopo"] is None or r["dopo"] < r["q"])

    def op_end(self, caller: dict, a: dict, warnings: list) -> dict:
        me = self._evento(caller, "end")
        return {"address": f"{me.agent}/{me.slug}"}

    # ---------- consegna dal cloud e da altri host (F4.4) ----------

    def op_consegna(self, caller: dict, a: dict, warnings: list) -> dict:
        """Un messaggio scritto fuori da questa macchina (alita-cloud via alita-memoria-sync).
        Stesse regole di tutti: è un ritorno solo se porta un task_id."""
        rec = a.get("record")
        if not isinstance(rec, dict) or not str(rec.get("id") or "").startswith("msg-"):
            raise BusError("consegna: record senza id msg-…")
        agent = a.get("agent") or rec.get("to")
        slug = a.get("slug") or None
        if not self.c.amap.agent_exists(agent or ""):
            raise BusError(f"consegna: agente sconosciuto {agent!r}", "unknown_agent")
        tid = rec.get("task_id")
        if tid is not None and not (isinstance(tid, str) and TASK_ID_RE.match(tid)):
            raise BusError(f"consegna: task_id non valido {tid!r}")
        nuovo = self.c.bus.accogli(rec, agent, slug, origin=a.get("origine") or "cloud")
        if nuovo:
            self.c.sveglia_ora.set()
        return {"id": rec["id"], "nuovo": nuovo, "to": f"{agent}/{slug}" if slug else agent}

    def registra(self) -> None:
        h = self
        ops: dict[str, Any] = {
            "hook.start": lambda c, caller, a, w: h.op_start(caller, a, w),
            "hook.prompt": lambda c, caller, a, w: h.op_prompt(caller, a, w),
            "hook.stop": lambda c, caller, a, w: h.op_stop(caller, a, w),
            "hook.end": lambda c, caller, a, w: h.op_end(caller, a, w),
            "consegna": lambda c, caller, a, w: h.op_consegna(caller, a, w),
        }
        self.c.OPS = {**self.c.OPS, **ops}

