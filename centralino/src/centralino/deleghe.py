"""Deleghe che si chiudono da sole (DC29-F4, contratto C3 in onda-electron
``docs/proposals/DC29-chiudi-delega.md``).

Il centralino è la colla fra bus, Onda e Orbit:

- **registro**: la tabella ``deleghe`` tiene gli spawn di Onda (hook PostToolUse su
  ``onda_agent_spawn`` → ``delega.spawn``) e le deleghe del bus (``bus_send`` kind ``delega``);
- **legame in entrambi gli ordini**: a ogni evento (spawn, bus_send di una delega, SessionStart)
  si cerca l'altro lato negli ultimi 30 minuti: stesso terminale o stessa sessione, oppure
  ``labels.ref`` uguale al passo, oppure stesso delegante verso lo stesso agente;
- **chiudi_delega** (b2): sessione chiusa (Onda ``agent.close`` killPty) solo se ferma e senza
  altre deleghe aperte, worktree ritirato con ``worktree.retire`` di Onda, esito sul passo via
  MCP HTTP di Orbit (``delega_set``), comunicazione al delegante se il worktree resta, una volta
  per motivo. Lock per repo, idempotente. Onda o Orbit giù: ``pending``, riprova al prossimo evento;
- **rete sugli orfani** (``delega.orfani``): worktree del repo senza sessione viva e con delega
  finita, o senza padrone ma già integrati. In dubbio si tiene.

Onda e Orbit veri si chiamano solo dal centralino di ``~/.agent-team-os`` (o con
``AB_DELEGHE_ESTERNI=1``): un centralino su un AB_HOME di prova non tocca mai repo veri.
"""

from __future__ import annotations

import json
import os
import subprocess
import threading
import time
import urllib.error
import urllib.request
from datetime import timedelta
from pathlib import Path
from typing import Any, Callable

from . import __version__, canale, palla
from .dbbus import BusError
from .indirizzi import ErroreIndirizzo, parse_addr
from .tempo import age_s, iso, iso_now, now, parse_iso

FINESTRA_S = 30 * 60        # legame: l'altro lato si cerca negli ultimi 30 minuti
RIPROVA_S = 60              # una delega pending si riprova al più una volta al minuto
RETIRE_TIMEOUT_S = 120      # worktree.retire fa fetch (30 s) e push del tag
LIST_TIMEOUT_S = 20
CLOSE_TIMEOUT_S = 10
ORBIT_TIMEOUT_S = 5
TAG_PREFIX = os.environ.get("AB_ARCHIVE_TAG_PREFIX") or "archivio/"
# motivi di worktree.retire che passano da soli: si riprova al prossimo evento
TRANSITORI = {"fetch_failed", "tag_push_failed", "git_error"}
APERTE = ("open", "pending", "kept")
INTENT_TENUTA = "delega-tenuta"


class EsternoGiu(Exception):
    """Onda o Orbit non risponde (o non è collegato): si riprova al prossimo evento."""


# ---------- Orbit: MCP HTTP ----------

def headers_orbit() -> dict[str, str]:
    """Gli header del server MCP ``orbit`` come li dà ``mcp-headers.sh`` (mcp-secrets.py legge
    secrets.env da solo). Il valore non si stampa né si scrive: resta in memoria per la chiamata."""
    cmd = os.environ.get("AB_ORBIT_HEADERS_CMD")
    argv = cmd.split() if cmd else [
        "/usr/bin/python3", "-I", str(Path.home() / ".claude" / "scripts" / "mcp-secrets.py"), "headers", "orbit"]
    try:
        out = subprocess.run(argv, capture_output=True, timeout=10, check=True).stdout
        h = json.loads(out)
    except (OSError, subprocess.SubprocessError, ValueError):
        raise EsternoGiu("header di Orbit non disponibili") from None  # niente stderr: potrebbe citare valori
    if not isinstance(h, dict):
        raise EsternoGiu("header di Orbit non validi")
    return {str(k): str(v) for k, v in h.items()}


class OrbitMcp:
    """Client minimo dello streamable HTTP di Orbit: initialize, initialized, tools/call."""

    def __init__(self, url: str | None = None, headers: Callable[[], dict] = headers_orbit,
                 timeout: float = ORBIT_TIMEOUT_S):
        self.url = url or os.environ.get("AB_ORBIT_MCP_URL") or "http://127.0.0.1:7331/mcp"
        self.headers = headers
        self.timeout = timeout

    def _post(self, body: dict, h: dict) -> tuple[dict, Any]:
        req = urllib.request.Request(self.url, data=json.dumps(body).encode("utf-8"), headers=h, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:  # solo 127.0.0.1
                raw = resp.read().decode("utf-8", "replace")
                rh = {k.lower(): v for k, v in resp.headers.items()}
        except urllib.error.HTTPError as exc:
            raise EsternoGiu(f"Orbit HTTP {exc.code}") from None
        except (OSError, ValueError) as exc:
            raise EsternoGiu(f"Orbit non risponde ({type(exc).__name__})") from None
        if not raw.strip():
            return rh, None
        if "text/event-stream" in rh.get("content-type", ""):
            msgs = []
            for line in raw.splitlines():
                if line.startswith("data:"):
                    try:
                        msgs.append(json.loads(line[5:].strip()))
                    except ValueError:
                        continue
            want = body.get("id")
            return rh, next((m for m in msgs if isinstance(m, dict) and m.get("id") == want), None)
        try:
            return rh, json.loads(raw)
        except ValueError:
            raise EsternoGiu("Orbit: risposta non JSON") from None

    def chiama(self, tool: str, args: dict) -> Any:
        h = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream",
             **self.headers()}
        rh, init = self._post({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2025-03-26", "capabilities": {},
            "clientInfo": {"name": "centralino", "version": __version__}}}, h)
        if not isinstance(init, dict) or init.get("error"):
            raise EsternoGiu("Orbit: initialize rifiutato")
        if rh.get("mcp-session-id"):
            h["mcp-session-id"] = rh["mcp-session-id"]
        self._post({"jsonrpc": "2.0", "method": "notifications/initialized"}, h)
        try:
            _, res = self._post({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                                 "params": {"name": tool, "arguments": args}}, h)
        finally:
            if h.get("mcp-session-id"):
                try:
                    urllib.request.urlopen(urllib.request.Request(self.url, headers=h, method="DELETE"),
                                           timeout=self.timeout).close()
                except (OSError, ValueError):
                    pass
        if not isinstance(res, dict):
            raise EsternoGiu(f"Orbit: nessuna risposta a {tool}")
        if res.get("error"):
            raise EsternoGiu(f"Orbit {tool}: {(res['error'] or {}).get('message')}")
        out = res.get("result") or {}
        testo = "".join(c.get("text") or "" for c in out.get("content") or [] if isinstance(c, dict))
        if out.get("isError"):
            raise EsternoGiu(f"Orbit {tool}: {testo[:200]}")
        if out.get("structuredContent") is not None:
            return out["structuredContent"]
        try:
            return json.loads(testo)
        except ValueError:
            return testo


class _Spento:
    """Onda e Orbit su un AB_HOME di prova: ogni chiamata è 'giù', nessun effetto vero."""

    def __call__(self, method: str, params: dict, timeout: float | None = None) -> dict:
        raise EsternoGiu(f"Onda non collegata a questo centralino ({method})")

    def chiama(self, tool: str, args: dict) -> Any:
        raise EsternoGiu(f"Orbit non collegato a questo centralino ({tool})")


def _real(p: str | None) -> str | None:
    if not p:
        return None
    try:
        return os.path.realpath(p)
    except OSError:
        return p


def _j(v) -> str | None:
    return None if v in (None, {}) else json.dumps(v, ensure_ascii=False, sort_keys=True)


def _labels(row) -> dict:
    try:
        v = json.loads(row["labels"] or "{}")
        return v if isinstance(v, dict) else {}
    except ValueError:
        return {}


def _epoch_ms(s: str | None) -> int | None:
    """Orbit vuole closure_at in epoch ms (C2, delega_set)."""
    dt = parse_iso(s)
    return int(dt.timestamp() * 1000) if dt else None


def _step_ref(*testi: str) -> str | None:
    rif = palla.rif_piano(*testi)
    return rif.codice if rif and rif.passo else None


def _norm_ref(v) -> str | None:
    rif = palla.rif_piano(str(v or ""))
    return rif.codice if rif and rif.passo else None


class Deleghe:
    def __init__(self, central, onda_call=None, orbit=None, sync: bool = False):
        self.c = central
        reale = os.environ.get("AB_DELEGHE_ESTERNI") == "1" or (
            _real(str(central.home)) == _real(str(Path.home() / ".agent-team-os")))
        self.onda_call = onda_call or (canale.onda_call if reale else _Spento())
        self.orbit = orbit or (OrbitMcp() if reale else _Spento())
        self.sync = sync
        self._locks: dict[str, threading.RLock] = {}
        self._locks_lock = threading.Lock()

    def registra(self) -> None:
        d = self
        self.c.OPS = {**self.c.OPS,
                      "delega.spawn": lambda c, caller, a, w: d.op_spawn(caller, a),
                      "delega.chiudi": lambda c, caller, a, w: d.op_chiudi(caller, a),
                      "delega.orfani": lambda c, caller, a, w: d.op_orfani(caller, a)}

    # ---------- servizio ----------

    def _lock(self, repo: str | None) -> threading.RLock:
        k = _real(repo) or ""
        with self._locks_lock:
            return self._locks.setdefault(k, threading.RLock())

    def _fai(self, fn: Callable[[], Any], nome: str, attendi_s: float | None = None) -> Any:
        """In un thread suo (Onda e git possono durare decine di secondi), o subito se sync."""
        if self.sync:
            return fn()
        box: dict = {}

        def corri():
            try:
                box["out"] = fn()
            except Exception as exc:  # noqa: BLE001 - un errore qui non deve toccare il bus
                self.c.log("error", "delega_errore", lavoro=nome, error=repr(exc))
                box["out"] = {"errore": repr(exc)}
        t = threading.Thread(target=corri, name=f"delega-{nome}", daemon=True)
        t.start()
        if attendi_s:
            t.join(attendi_s)
            if "out" in box:
                return box["out"]
        return {"avviato": True}

    def _onda(self, method: str, params: dict, timeout: float) -> dict:
        try:
            res = self.onda_call(method, params, timeout=timeout)
        except EsternoGiu:
            raise
        except (OSError, ValueError) as exc:
            raise EsternoGiu(f"Onda: {exc}") from None
        return res if isinstance(res, dict) else {}

    def _righe(self, where: str, args: tuple = ()) -> list:
        return self.c.store.read().execute(f"SELECT * FROM deleghe WHERE {where}", args).fetchall()

    def _riga(self, rid: int):
        return self.c.store.read().execute("SELECT * FROM deleghe WHERE id=?", (rid,)).fetchone()

    def _aggiorna(self, rid: int, **campi) -> None:
        campi["updated_at"] = iso_now()
        cols = ", ".join(f"{k}=?" for k in campi)
        self.c._write(lambda: self.c._conn().execute(f"UPDATE deleghe SET {cols} WHERE id=?",
                                                     (*campi.values(), rid)))

    # ---------- registro: spawn ----------

    def op_spawn(self, caller: dict, a: dict) -> dict:
        """Hook PostToolUse su onda_agent_spawn: ``result`` è la risposta dello spawn, ``input``
        i parametri. Uno spawn senza terminalId (rifiutato, worktree_cap) non si registra."""
        r = a.get("result") if isinstance(a.get("result"), dict) else {}
        inp = a.get("input") if isinstance(a.get("input"), dict) else {}
        tid = r.get("terminalId")
        if not tid:
            return {"registrato": False, "motivo": r.get("errorCode") or "nessun terminalId"}
        wt = r.get("worktree") if isinstance(r.get("worktree"), dict) else {}
        wi = inp.get("worktree") if isinstance(inp.get("worktree"), dict) else {}
        path = wt.get("path") or wt.get("worktreePath")
        cwd = r.get("cwd") or path or inp.get("workspaceRootPath")
        labels = r.get("labels") if isinstance(r.get("labels"), dict) else (
            inp.get("labels") if isinstance(inp.get("labels"), dict) else {})
        try:
            by = self.c.amap.identify(caller)
            spawn_by = f"{by.agent}/{by.slug}"
        except ErroreIndirizzo:
            spawn_by = None
        sess_agent = sess_slug = None
        if cwd:
            try:
                me = self.c.amap.identify({"cwd": cwd})
                sess_agent, sess_slug = me.agent, me.slug
            except ErroreIndirizzo:
                pass
        campi = {"spawn_by": spawn_by, "terminal_id": tid, "onda_session_id": r.get("sessionId"),
                 "workspace_id": r.get("workspaceId"), "repo_root": wt.get("repoRoot") or wi.get("repoRoot"),
                 "worktree_path": path, "branch": wt.get("branch") or wi.get("branch"),
                 "base": wt.get("base") or wt.get("mergedInto"), "labels": _j(labels),
                 "sess_agent": sess_agent, "sess_slug": sess_slug, "host": self.c.amap.host_self()}
        ts = iso_now()

        def scrivi():
            c = self.c._conn()
            prima = c.execute("SELECT id FROM deleghe WHERE terminal_id=? AND closure_state!='closed' "
                              "ORDER BY id DESC LIMIT 1", (tid,)).fetchone()
            if prima is not None:  # reusedAgent: stesso terminale, si aggiorna e basta
                vivi = {k: v for k, v in campi.items() if v is not None}
                c.execute(f"UPDATE deleghe SET {', '.join(f'{k}=?' for k in vivi)}, updated_at=? WHERE id=?",
                          (*vivi.values(), ts, prima["id"]))
                return prima["id"]
            cur = c.execute(f"INSERT INTO deleghe ({', '.join(campi)}, spawned_at, created_at, updated_at) "
                            f"VALUES ({', '.join('?' * len(campi))}, ?, ?, ?)", (*campi.values(), ts, ts, ts))
            return cur.lastrowid
        rid = self.c._write(scrivi)
        legate = self._lega()
        self.c.log("info", "delega_spawn", terminale=tid, worktree=path, legate=legate)
        self._dopo_evento()
        # legato, lo spawn è confluito nella riga della delega: la si ritrova dal terminale
        row = self._riga(rid) or next(iter(self._righe("terminal_id=? ORDER BY id DESC LIMIT 1", (tid,))), None)
        return {"registrato": True, "id": row["id"] if row else rid, "task_id": row["task_id"] if row else None,
                "legate": legate}

    # ---------- registro: delega del bus ----------

    def su_delega(self, me, a: dict, out: dict) -> None:
        """Dopo un bus_send kind delega andato a buon fine (consegnato su questa macchina)."""
        task_id = (out.get("expecting") or {}).get("task_id") or out.get("task_id")
        if not task_id or out.get("host") != self.c.amap.host_self():
            return
        agent, slug, _ = parse_addr(out.get("delivered_to") or "")
        ref = _step_ref(a.get("intent") or "", a.get("summary") or "")
        ts = iso_now()
        self.c._write(lambda: self.c._conn().execute(
            "INSERT OR IGNORE INTO deleghe (task_id, step_ref, delegator, delegator_host, delegated_to, "
            "sess_agent, sess_slug, host, sent_at, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (task_id, ref, f"{me.agent}/{me.slug}", me.host, out.get("delivered_to"), agent, slug or None,
             self.c.amap.host_self(), ts, ts, ts)))
        self._lega()
        self._dopo_evento()

    # ---------- registro: SessionStart ----------

    def su_start(self, me, caller: dict) -> None:
        """La sessione partita in un terminale di uno spawn ne prende l'identità; poi il legame."""
        tid = caller.get("onda_terminal_id")
        if not tid:
            return
        self.c._write(lambda: self.c._conn().execute(
            "UPDATE deleghe SET sess_agent=?, sess_slug=?, claude_session_id=COALESCE(?, claude_session_id), "
            "session_closed=0, updated_at=? WHERE terminal_id=? AND closure_state!='closed'",
            (me.agent, me.slug, caller.get("claude_session_id"), iso_now(), tid)))
        self._lega()
        self._dopo_evento()

    # ---------- legame ----------

    @staticmethod
    def _punteggio(s, d) -> int:
        """Quanto lo spawn ``s`` è della delega ``d``. Serve almeno 2; sessioni diverse: 0."""
        if s["sess_agent"] and d["sess_agent"] and s["sess_agent"] != d["sess_agent"]:
            return 0
        if s["sess_slug"] and d["sess_slug"] and s["sess_slug"] != d["sess_slug"]:
            return 0
        p = 0
        if s["sess_agent"] and s["sess_agent"] == d["sess_agent"] and s["sess_slug"] and s["sess_slug"] == d["sess_slug"]:
            p += 3
        if d["step_ref"] and _norm_ref(_labels(s).get("ref")) == d["step_ref"]:
            p += 2
        if s["spawn_by"] and s["spawn_by"] == d["delegator"] and s["sess_agent"] == d["sess_agent"]:
            p += 2
        return p

    def _lega(self) -> list[str]:
        """Lega gli spawn liberi alle deleghe libere degli ultimi 30 minuti, in qualunque ordine
        siano arrivati; una delega nuova verso una sessione già legata eredita spawn e worktree."""
        cut = iso(now() - timedelta(seconds=FINESTRA_S))
        ts = iso_now()

        def scrivi():
            c = self.c._conn()
            legate = []
            deleghe = c.execute("SELECT * FROM deleghe WHERE task_id IS NOT NULL AND spawned_at IS NULL "
                                "AND closure_state='open' AND sent_at>=? ORDER BY sent_at DESC", (cut,)).fetchall()
            for d in deleghe:
                spawns = c.execute("SELECT * FROM deleghe WHERE task_id IS NULL AND spawned_at>=? "
                                   "ORDER BY spawned_at DESC, id DESC", (cut,)).fetchall()
                best = max(spawns, key=lambda s: self._punteggio(s, d), default=None)
                if best is not None and self._punteggio(best, d) >= 2:
                    c.execute(
                        "UPDATE deleghe SET spawn_by=?, terminal_id=?, onda_session_id=?, workspace_id=?, "
                        "repo_root=?, worktree_path=?, branch=?, base=?, labels=?, spawned_at=?, "
                        "sess_agent=COALESCE(sess_agent, ?), sess_slug=COALESCE(sess_slug, ?), "
                        "claude_session_id=COALESCE(claude_session_id, ?), "
                        "step_ref=COALESCE(step_ref, ?), linked_at=?, orbit_dirty=1, updated_at=? WHERE id=?",
                        (best["spawn_by"], best["terminal_id"], best["onda_session_id"], best["workspace_id"],
                         best["repo_root"], best["worktree_path"], best["branch"], best["base"], best["labels"],
                         best["spawned_at"], best["sess_agent"], best["sess_slug"], best["claude_session_id"],
                         _norm_ref(_labels(best).get("ref")), ts, ts, d["id"]))
                    c.execute("DELETE FROM deleghe WHERE id=?", (best["id"],))
                    legate.append(d["task_id"])
                    continue
                if not (d["sess_agent"] and d["sess_slug"]):
                    continue
                # seconda delega alla stessa sessione: stesso terminale e stesso worktree
                prima = c.execute("SELECT * FROM deleghe WHERE sess_agent=? AND sess_slug=? AND spawned_at "
                                  "IS NOT NULL AND task_id IS NOT NULL AND session_closed=0 AND id!=? "
                                  "ORDER BY id DESC LIMIT 1", (d["sess_agent"], d["sess_slug"], d["id"])).fetchone()
                if prima is not None:
                    c.execute("UPDATE deleghe SET spawn_by=?, terminal_id=?, onda_session_id=?, workspace_id=?, "
                              "repo_root=?, worktree_path=?, branch=?, base=?, labels=?, spawned_at=?, "
                              "claude_session_id=COALESCE(claude_session_id, ?), linked_at=?, orbit_dirty=1, "
                              "updated_at=? WHERE id=?",
                              (prima["spawn_by"], prima["terminal_id"], prima["onda_session_id"],
                               prima["workspace_id"], prima["repo_root"], prima["worktree_path"], prima["branch"],
                               prima["base"], prima["labels"], prima["spawned_at"], prima["claude_session_id"],
                               ts, ts, d["id"]))
                    legate.append(d["task_id"])
            return legate
        legate = self.c._write(scrivi)
        if legate:
            self.c.log("info", "delega_legata", task_id=legate)
        return legate

    # ---------- dopo ogni evento: Orbit da riscrivere, pending da riprovare ----------

    def _dopo_evento(self) -> None:
        sporche = self._righe("orbit_dirty=1 AND step_ref IS NOT NULL")
        cut = iso(now() - timedelta(seconds=RIPROVA_S))
        pending = self._righe("closure_state='pending' AND (tried_at IS NULL OR tried_at<?)", (cut,))
        if not sporche and not pending:
            return

        def lavora():
            for r in sporche:
                self._orbit_scrivi(r["id"])
            for r in pending:
                self._chiudi_riga(r["id"], r["closure_reason"] or "riprova")
            return {"orbit": len(sporche), "riprovate": len(pending)}
        self._fai(lavora, "dopo-evento")

    def _orbit_scrivi(self, rid: int) -> bool:
        r = self._riga(rid)
        if r is None:
            return False
        if not r["step_ref"]:
            if r["orbit_dirty"]:
                self._aggiorna(rid, orbit_dirty=0)
            return False
        sess = f"{r['sess_agent']}/{r['sess_slug']}" if r["sess_agent"] and r["sess_slug"] else r["delegated_to"]
        args = {"step_ref": r["step_ref"], "bus_task_id": r["task_id"], "terminal_id": r["terminal_id"],
                "delegator": r["delegator"], "session_addr": sess, "session_claude_id": r["claude_session_id"],
                "host": r["host"], "workspace_id": r["workspace_id"], "repo_root": r["repo_root"],
                "worktree_path": r["worktree_path"], "branch": r["branch"], "labels": _labels(r) or None,
                "closure_state": r["closure_state"], "closure_reason": r["closure_reason"],
                "closure_detail": r["closure_detail"], "closure_at": _epoch_ms(r["closure_at"])}
        try:
            self.orbit.chiama("delega_set", {k: v for k, v in args.items() if v is not None})
        except EsternoGiu as exc:
            self.c.log("warn", "orbit_giu", step_ref=r["step_ref"], motivo=str(exc))
            self._aggiorna(rid, orbit_dirty=1)
            return False
        self._aggiorna(rid, orbit_dirty=0)
        return True

    # ---------- chiudi_delega (b2) ----------

    def op_chiudi(self, caller: dict, a: dict) -> dict:
        """Da Orbit (passo a done) o a mano: ``step_ref`` o ``task_id``, ``reason``. Fire-and-forget:
        torna subito; ``attendi=true`` aspetta l'esito."""
        if not (a.get("step_ref") or a.get("task_id")):
            raise BusError("delega.chiudi: serve step_ref o task_id")
        reason = a.get("reason") or "manuale"
        if a.get("task_id"):
            righe = self._righe("task_id=?", (a["task_id"],))
        else:
            ref = _norm_ref(a["step_ref"]) or str(a["step_ref"]).upper()
            righe = self._righe("step_ref=?", (ref,))
        ids = [r["id"] for r in righe]
        if not ids:
            return {"trovate": 0}
        out = self._fai(lambda: [self._chiudi_riga(i, reason) for i in ids], "chiudi",
                        attendi_s=None if not a.get("attendi") else RETIRE_TIMEOUT_S)
        return {"trovate": len(ids), "esiti": out}

    def su_attesa_chiusa(self, task_id: str) -> None:
        """bus_close esito chiuso (o bus_close con solo task_id): la delega è finita."""
        righe = self._righe("task_id=? AND closure_state!='closed'", (task_id,))
        for r in righe:
            self._fai(lambda rid=r["id"]: self._chiudi_riga(rid, "bus_close"), "chiudi")
        if not righe:
            self._dopo_evento()

    def su_fine_sessione(self, agent: str, slug: str) -> None:
        """SessionEnd: la sessione è finita. Si chiudono le sue deleghe finite (attesa chiusa o
        passo done), poi la rete sugli orfani del suo repo. Le deleghe ancora aperte restano."""
        righe = self._righe("sess_agent=? AND sess_slug=? AND spawned_at IS NOT NULL", (agent, slug))
        if not righe:
            self._dopo_evento()
            return
        self.c._write(lambda: self.c._conn().execute(
            "UPDATE deleghe SET session_closed=1, updated_at=? WHERE sess_agent=? AND sess_slug=? "
            "AND spawned_at IS NOT NULL", (iso_now(), agent, slug)))

        def lavora():
            esiti = [self._chiudi_riga(r["id"], "session_end") for r in righe
                     if r["closure_state"] != "closed" and self._finita(r)]
            for repo in sorted({r["repo_root"] for r in righe if r["repo_root"]}):
                esiti.append(self.orfani(repo))
            return esiti
        self._fai(lavora, "fine-sessione")

    def _attesa_chiusa(self, task_id: str | None) -> bool:
        if not task_id:
            return False
        w = self.c.store.wait(task_id)
        return w is not None and w["state"] == "closed"

    def _passo_done(self, step_ref: str | None) -> bool:
        rif = palla.rif_piano(step_ref or "")
        path = self.c.orbit.path
        if not rif or not rif.passo or path is None or not Path(path).exists():
            return False
        import sqlite3
        try:
            c = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=1)
            try:
                r = c.execute("SELECT t.state FROM plans p JOIN plan_phases ph ON ph.plan_id=p.id "
                              "JOIN tasks t ON t.phase_id=ph.id WHERE upper(p.code)=? AND t.step_key=?",
                              (rif.piano, rif.passo)).fetchone()
            finally:
                c.close()
        except sqlite3.Error:
            return False
        return r is not None and r[0] == "done"

    def _finita(self, r) -> bool:
        return self._attesa_chiusa(r["task_id"]) or self._passo_done(r["step_ref"])

    def _altre_aperte(self, r) -> list[str]:
        """Altre deleghe aperte sulla stessa sessione (stesso terminale o stessa sessione del bus)."""
        out = []
        for x in self._righe("id!=? AND task_id IS NOT NULL AND closure_state!='closed' AND "
                             "((terminal_id IS NOT NULL AND terminal_id=?) OR (sess_agent=? AND sess_slug=?))",
                             (r["id"], r["terminal_id"], r["sess_agent"], r["sess_slug"])):
            if not self._finita(x):
                out.append(x["task_id"])
        return out

    def _sessione(self, r) -> tuple[str, str | None]:
        """Chiude la sessione della delega se ferma e libera. Torna (esito, dettaglio):
        chiusa | gia_chiusa | altre_deleghe | al_lavoro | onda_giu | non_mia."""
        if r["session_closed"]:
            return "gia_chiusa", None
        tid = r["terminal_id"]
        if not tid and r["sess_agent"] and r["sess_slug"]:
            s = self.c.store.read().execute("SELECT * FROM sessions WHERE agent=? AND slug=? AND host=?",
                                            (r["sess_agent"], r["sess_slug"], r["host"] or "")).fetchone()
            if s is not None and s["delegata"] and s["onda_terminal_id"]:
                tid = s["onda_terminal_id"]
        if not tid:
            return "non_mia", None  # una sessione che non è nata per la delega non si chiude
        altre = self._altre_aperte(r)
        if altre:
            return "altre_deleghe", ", ".join(altre)
        if not self.c.onda.valid():
            return "onda_giu", "sessions.json di Onda assente o vecchio"
        e = next((x for x in self.c.onda.entries() if x.get("terminalId") == tid), None)
        if e is None or e.get("state") == "exited":
            self._aggiorna(r["id"], session_closed=1)
            return "gia_chiusa", None
        settled = age_s(e.get("stateSince"))
        if e.get("state") not in ("idle", "waiting") or e.get("hasText") or (settled is not None and settled < 5):
            return "al_lavoro", f"stato Onda {e.get('state')}" + (", testo nella casella" if e.get("hasText") else "")
        params: dict[str, Any] = {"terminalId": tid, "killPty": True}
        if e.get("sessionId") or r["onda_session_id"]:
            params["sessionId"] = e.get("sessionId") or r["onda_session_id"]
        try:
            res = self._onda("agent.close", params, CLOSE_TIMEOUT_S)
        except EsternoGiu as exc:
            return "onda_giu", str(exc)
        if res.get("errorCode"):
            return "al_lavoro", f"agent.close: {res.get('errorCode')}"
        self._aggiorna(r["id"], session_closed=1)
        return "chiusa", None

    def _retire(self, path: str, workspace_id: str | None, appena_chiusa: bool) -> dict:
        params: dict[str, Any] = {"path": path, "archiveTagPrefix": TAG_PREFIX, "deleteBranch": True}
        if workspace_id:
            params["workspaceId"] = workspace_id
        res = self._onda("worktree.retire", params, RETIRE_TIMEOUT_S)
        if not res.get("retired") and res.get("reason") == "in_use" and appena_chiusa:
            time.sleep(float(os.environ.get("AB_RETIRE_ATTESA_S") or 2))  # il PTY appena chiuso esce
            res = self._onda("worktree.retire", params, RETIRE_TIMEOUT_S)
        return res

    @staticmethod
    def _dettaglio_ritirato(res: dict) -> str:
        parti = ["worktree tolto"]
        by = res.get("integratedBy")
        parti.append({"ancestor": "merge riconosciuto", "tree": "squash riconosciuto (tree)",
                      "pr": "PR mergiata"}.get(by, f"integrato ({by})") if by else "integrato")
        if res.get("tag"):
            parti.append(f"tag {res['tag']}")
        if res.get("branchKept"):
            parti.append(f"branch tenuto: {res['branchKept']}")
        elif res.get("branchDeleted"):
            parti.append("branch cancellato" + (" anche su origin" if res.get("remoteBranchDeleted") else ""))
        return ", ".join(parti)

    def _chiudi_riga(self, rid: int, reason: str) -> dict:
        r0 = self._riga(rid)
        if r0 is None:
            return {"id": rid, "stato": "assente"}
        with self._lock(r0["repo_root"]):
            r = self._riga(rid)
            if r is None or r["closure_state"] == "closed":
                return {"id": rid, "task_id": r["task_id"] if r else None, "stato": "closed", "gia": True}
            self._aggiorna(rid, tried_at=iso_now())
            stato, motivo, dettaglio = self._procedura(r)
            self._aggiorna(rid, closure_state=stato, closure_reason=motivo, closure_detail=dettaglio,
                           closure_at=iso_now(), orbit_dirty=1 if r["step_ref"] else 0)
            self.c.log("info", "delega_chiusura", task_id=r["task_id"], step_ref=r["step_ref"], perche=reason,
                       stato=stato, motivo=motivo)
        self._orbit_scrivi(rid)
        if stato == "kept":
            self._avvisa(rid)
        return {"id": rid, "task_id": r["task_id"], "step_ref": r["step_ref"], "stato": stato,
                "motivo": motivo, "dettaglio": dettaglio}

    def _procedura(self, r) -> tuple[str, str, str | None]:
        """(closure_state, closure_reason, closure_detail) per la riga ``r``."""
        sess, sdet = self._sessione(r)
        note = {"chiusa": "sessione chiusa", "gia_chiusa": "sessione già chiusa",
                "non_mia": None, "altre_deleghe": f"sessione tenuta per {sdet}"}.get(sess)
        if sess in ("al_lavoro", "onda_giu"):
            return "pending", f"session_{sess}", sdet
        path = r["worktree_path"]
        if not path:
            return "closed", "senza_worktree", note
        if sess == "altre_deleghe":
            return "closed", "altre_deleghe", f"sessione e worktree restano per {sdet}"
        try:
            res = self._retire(path, r["workspace_id"], appena_chiusa=sess == "chiusa")
        except EsternoGiu as exc:
            return "pending", "onda_giu", str(exc)
        if res.get("retired"):
            return "closed", "retired", "; ".join(x for x in (note, self._dettaglio_ritirato(res)) if x)
        why = res.get("reason") or "sconosciuto"
        if why == "not_a_worktree" and not Path(path).exists():
            return "closed", "worktree_assente", "; ".join(x for x in (note, "worktree già tolto") if x)
        if why in TRANSITORI:
            return "pending", why, res.get("detail")
        return "kept", why, res.get("detail")

    def _avvisa(self, rid: int) -> None:
        """Worktree tenuto: comunicazione al delegante, una volta per motivo."""
        r = self._riga(rid)
        if r is None or not r["delegator"] or r["notified_reason"] == r["closure_reason"]:
            return
        d_agent, _, d_slug = r["delegator"].partition("/")
        me = self.c.amap.host_self()
        to = f"{d_agent}@{r['delegator_host']}/{d_slug}" if r["delegator_host"] and r["delegator_host"] != me \
            else r["delegator"]
        frm, frm_slug = (r["sess_agent"], r["sess_slug"]) if r["sess_agent"] and r["sess_slug"] else (d_agent, d_slug)
        cosa = {"dirty": "ci sono modifiche o file non tracciati: committa e pusha, o butta via",
                "not_integrated": "il lavoro non è nella base: fai il merge (o la PR) e poi chiudi di nuovo",
                "not_pushed": "il branch su origin non è uguale a quello locale: pusha",
                "in_use": "c'è ancora un processo vivo dentro il worktree",
                "no_remote": "il repo non ha origin: il worktree resta finché non lo togli a mano"}.get(
            r["closure_reason"], "controlla il motivo e chiudi a mano se serve")
        testo = (f"Delega {r['task_id'] or ''} ({r['step_ref'] or 'senza passo'}): il worktree "
                 f"{r['worktree_path']} resta, motivo {r['closure_reason']}"
                 + (f" ({r['closure_detail']})" if r["closure_detail"] else "")
                 + f". Cosa fare: {cosa}. La chiusura riparte da sola al prossimo bus_close, a fine sessione "
                   "o allo spawn successivo nel repo. Avviso unico per questo motivo.")
        try:
            self.c.bus.send(frm, to, "brief", INTENT_TENUTA,
                            {"summary": testo, "worktree": r["worktree_path"], "motivo": r["closure_reason"],
                             "dettaglio": r["closure_detail"], "delega_task_id": r["task_id"],
                             "step_ref": r["step_ref"]},
                            sender_session=frm_slug, kind="comunicazione")
        except (BusError, ErroreIndirizzo) as exc:
            self.c.log("warn", "avviso_tenuta_fallito", task_id=r["task_id"], errore=str(exc))
            return
        self._aggiorna(rid, notified_reason=r["closure_reason"])
        self.c.sveglia_ora.set()

    # ---------- rete sugli orfani ----------

    def op_orfani(self, caller: dict, a: dict) -> dict:
        """Hook PreToolUse su onda_agent_spawn con worktree, prima che Onda conti il tetto.
        Aspetta al più ``attendi_s`` (default 2,5 s), poi lascia finire il lavoro in un thread."""
        repo = a.get("repo_root")
        if not repo:
            raise BusError("delega.orfani: serve repo_root")
        attendi = float(a.get("attendi_s") if a.get("attendi_s") is not None else 2.5)
        return self._fai(lambda: self.orfani(repo), "orfani", attendi_s=attendi or None)

    def orfani(self, repo: str) -> dict:
        out: dict[str, Any] = {"repo_root": repo, "chiusi": [], "tenuti": [], "segnalati": []}
        with self._lock(repo):
            try:
                res = self._onda("worktree.list", {"repoRoot": repo, "fetch": False}, LIST_TIMEOUT_S)
            except EsternoGiu as exc:
                out["errore"] = str(exc)
                return out
            for w in res.get("worktrees") or []:
                if not isinstance(w, dict) or not w.get("path") or _real(w["path"]) == _real(repo):
                    continue
                path = w["path"]
                if w.get("live"):
                    out["tenuti"].append({"path": path, "motivo": "sessione viva"})
                    continue
                righe = self._righe("worktree_path=? ORDER BY id DESC", (path,))
                aperte = [x for x in righe if x["task_id"] and x["closure_state"] != "closed"]
                if aperte:
                    for x in aperte:
                        if self._finita(x):
                            self.c._write(lambda rid=x["id"]: self.c._conn().execute(
                                "UPDATE deleghe SET session_closed=1 WHERE id=?", (rid,)))  # nessuna sessione viva
                            out["chiusi"].append(self._chiudi_riga(x["id"], "orfano"))
                        else:
                            out["tenuti"].append({"path": path, "task_id": x["task_id"], "motivo": "delega aperta"})
                    continue
                ref = _norm_ref((w.get("labels") or {}).get("ref")) if isinstance(w.get("labels"), dict) else None
                if not righe and ref and not self._passo_done(ref):
                    out["tenuti"].append({"path": path, "step_ref": ref, "motivo": "passo non done"})
                    continue
                if not righe and not ref and not w.get("integratedInto"):
                    out["segnalati"].append({"path": path, "branch": w.get("branch"),
                                             "motivo": "nessun padrone, non integrato"})
                    continue
                # padrone chiuso, passo done, o nessun padrone ma già integrato: retire decide
                try:
                    rr = self._retire(path, w.get("workspaceId"), appena_chiusa=False)
                except EsternoGiu as exc:
                    out["errore"] = str(exc)
                    break
                if rr.get("retired"):
                    out["chiusi"].append({"path": path, "dettaglio": self._dettaglio_ritirato(rr)})
                else:
                    out["segnalati"].append({"path": path, "branch": w.get("branch"),
                                             "motivo": rr.get("reason"), "dettaglio": rr.get("detail")})
        if out["chiusi"] or out["segnalati"]:
            self.c.log("info", "orfani", repo=repo, chiusi=len(out["chiusi"]), segnalati=len(out["segnalati"]))
        return out
