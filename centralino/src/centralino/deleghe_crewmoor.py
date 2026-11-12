"""Deleghe che si chiudono da sole con Crewmoor: le deleghe che si chiudono da sole (DC29) senza il centralino (CM02-F3.3c).

Con il passaggio a Crewmoor (CM02-F3.6) il centralino si spegne, ma la colla fra deleghe, Onda e Orbit serve
ancora: lo spawn di Onda legato alla delega, la sessione chiusa e il worktree ritirato quando la delega finisce,
l'esito sul passo di Orbit (``delega_set``). Questo modulo riusa la logica di :mod:`centralino.deleghe` così
com'è, con un adattatore al posto del centralino:

- nessun daemon: ogni evento è un processo breve lanciato da un hook di Claude Code (``python -m
  centralino.deleghe_crewmoor <evento>``, JSON dell'hook su stdin); lo stato sta in un SQLite suo
  (``~/.config/crewmoor/deleghe.db``, ``DELEGHE_CREWMOOR_DB`` per spostarlo), con lock di SQLite fra processi;
- le deleghe arrivano da Crewmoor: PostToolUse su ``crewmoor_send`` con ``mode: delegation`` (il ``taskId`` è
  nella risposta), la chiusura da PostToolUse su ``crewmoor_close`` (``waitState: closed``);
- l'identità (agente, slug) dalla cartella con l'agent-map di Roster, come prima; la presenza non si guarda
  qui (vive in Roster): Onda dice se la sessione delegata è ferma, come prima;
- Orbit è quello online: URL dal ``.mcp.json`` del workspace (o ``AB_ORBIT_MCP_URL``), header con il gettone
  dell'agente della sessione in cui gira l'hook (``mcp-secrets.py headers orbit``): niente passaggi dal
  centralino, l'identità viene dal gettone (OA06);
- l'avviso «worktree tenuto» al delegante parte con ``crewmoor send`` (agente di servizio ``deleghe``).

Eventi (argomento): ``spawn`` e ``orfani`` (hook di onda_agent_spawn, post e pre), ``delega`` (PostToolUse di
crewmoor_send), ``chiusa`` (PostToolUse di crewmoor_close), ``start`` e ``fine`` (SessionStart, SessionEnd),
``giro`` (riprova pending e Orbit). Mai bloccante: un errore va nel log, l'uscita è sempre 0.
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable

from . import palla
from .deleghe import Deleghe, EsternoGiu, OrbitMcp, _Spento, _step_ref
from .indirizzi import AgentMap, ErroreIndirizzo
from .tempo import iso_now
from .vivo import OndaView

SCHEMA = """
CREATE TABLE IF NOT EXISTS deleghe (
  id                INTEGER PRIMARY KEY,
  task_id           TEXT UNIQUE,
  step_ref          TEXT,
  delegator         TEXT,
  delegator_host    TEXT,
  delegated_to      TEXT,
  sess_agent        TEXT,
  sess_slug         TEXT,
  claude_session_id TEXT,
  host              TEXT,
  spawn_by          TEXT,
  terminal_id       TEXT,
  onda_session_id   TEXT,
  workspace_id      TEXT,
  repo_root         TEXT,
  worktree_path     TEXT,
  branch            TEXT,
  base              TEXT,
  labels            TEXT,
  spawned_at        TEXT,
  sent_at           TEXT,
  linked_at         TEXT,
  closure_state     TEXT NOT NULL DEFAULT 'open',
  closure_reason    TEXT,
  closure_detail    TEXT,
  closure_at        TEXT,
  session_closed    INTEGER NOT NULL DEFAULT 0,
  notified_reason   TEXT,
  orbit_dirty       INTEGER NOT NULL DEFAULT 0,
  tried_at          TEXT,
  created_at        TEXT NOT NULL,
  updated_at        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS deleghe_term ON deleghe(terminal_id);
CREATE INDEX IF NOT EXISTS deleghe_sess ON deleghe(sess_agent, sess_slug);
CREATE INDEX IF NOT EXISTS deleghe_repo ON deleghe(repo_root, worktree_path);
CREATE TABLE IF NOT EXISTS waits (
  task_id    TEXT PRIMARY KEY,
  state      TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
  agent            TEXT NOT NULL,
  slug             TEXT NOT NULL,
  host             TEXT NOT NULL DEFAULT '',
  delegata         INTEGER NOT NULL DEFAULT 0,
  onda_terminal_id TEXT,
  onda_socket      TEXT,
  PRIMARY KEY (agent, slug, host)
);
"""

# I nomi degli strumenti del plugin Crewmoor come li vede un hook (con e senza il prefisso del plugin).
SEND_TOOLS = ("crewmoor_send",)
CLOSE_TOOLS = ("crewmoor_close",)


def db_path() -> Path:
    return Path(os.environ.get("DELEGHE_CREWMOOR_DB") or Path.home() / ".config" / "crewmoor" / "deleghe.db")


def bus_home() -> Path:
    """Dove stanno l'agent-map e il sessions.json di Onda (sola lettura): la cartella di agent-team-os."""
    return Path(os.environ.get("AB_HOME") or Path.home() / ".agent-team-os")


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path), timeout=15, isolation_level=None, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(SCHEMA)

    def read(self) -> sqlite3.Connection:
        return self.conn

    def wait(self, task_id: str):
        return self.conn.execute("SELECT * FROM waits WHERE task_id=?", (task_id,)).fetchone()


class _Bus:
    """Quello che Deleghe chiede al bus: l'avviso al delegante (crewmoor send) e la fine di sessione (niente)."""

    def __init__(self, central: "Centrale"):
        self.c = central

    def fine_sessione(self, agent: str, slug: str) -> None:
        return None

    def send(self, frm: str, to: str, message_type: str, intent: str, payload: dict, **_kw) -> None:
        argv = ["/usr/bin/python3", str(self.c.helper), "manda", "--da", "deleghe", to, intent, message_type,
                "normal", str(_kw.get("kind") or "comunicazione"), str(payload.get("summary") or intent)]
        try:
            r = subprocess.run(argv, capture_output=True, text=True, timeout=40)
        except (OSError, subprocess.SubprocessError) as exc:
            raise ErroreIndirizzo("crewmoor", f"crewmoor send non partito: {exc}") from None
        if r.returncode != 0:
            raise ErroreIndirizzo("crewmoor", (r.stdout or r.stderr).strip()[-300:])


class Centrale:
    """Il minimo del centralino che :class:`Deleghe` usa, senza socket né thread di servizio."""

    def __init__(self, home: Path | None = None, store: Store | None = None):
        self.home = Path(home or bus_home())
        self.store = store or Store(db_path())
        self.amap = AgentMap(self.home)
        self.onda = OndaView(self.home)
        self.orbit = palla.Orbit(Path(os.environ["AB_ORBIT_DB"]) if os.environ.get("AB_ORBIT_DB") else None)
        self.bus = _Bus(self)
        self.sveglia_ora = threading.Event()
        self.helper = Path(os.environ.get("CREWMOOR_CANALE_HELPER")
                           or Path.home() / ".config" / "crewmoor" / "canale-agenti.py")
        self.log_path = Path(os.environ.get("DELEGHE_CREWMOOR_LOG") or db_path().with_name("deleghe.log"))

    def _conn(self) -> sqlite3.Connection:
        return self.store.conn

    def _write(self, fn: Callable[[], Any]) -> Any:
        c = self.store.conn
        c.execute("BEGIN IMMEDIATE")
        try:
            out = fn()
        except BaseException:
            c.execute("ROLLBACK")
            raise
        c.execute("COMMIT")
        return out

    def log(self, livello: str, evento: str, **campi) -> None:
        try:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            with self.log_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps({"ts": iso_now(), "livello": livello, "evento": evento, **campi},
                                    ensure_ascii=False, default=str) + "\n")
        except OSError:
            pass


def orbit_url(cwd: str | None) -> str | None:
    """L'URL MCP di Orbit: AB_ORBIT_MCP_URL, poi il primo ``.mcp.json`` risalendo da ``cwd`` che ha ``orbit``,
    poi ``~/.claude.json``."""
    if os.environ.get("AB_ORBIT_MCP_URL"):
        return os.environ["AB_ORBIT_MCP_URL"]
    cands: list[Path] = []
    if cwd:
        p = Path(cwd).resolve()
        cands += [d / ".mcp.json" for d in (p, *p.parents)]
    cfg = Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home())
    cands += [cfg / ".claude.json", Path.home() / ".claude.json"]
    for f in cands:
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        url = ((d.get("mcpServers") or {}).get("orbit") or {}).get("url") if isinstance(d, dict) else None
        if isinstance(url, str) and url.startswith(("http://", "https://")):
            return url
    return None


class DelegheCrewmoor(Deleghe):
    """Deleghe sincrone (un processo per evento) con il passo done letto da Orbit online."""

    def __init__(self, central: Centrale, cwd: str | None = None, onda_call=None, orbit=None):
        if orbit is None:
            url = orbit_url(cwd)
            orbit = OrbitMcp(url=url, timeout=8) if url else _Spento()
        super().__init__(central, onda_call=onda_call, orbit=orbit, sync=True)
        self._passi: dict[str, bool] = {}

    def _passo_done(self, step_ref: str | None) -> bool:
        rif = palla.rif_piano(step_ref or "")
        if not rif or not rif.passo:
            return False
        if rif.codice in self._passi:
            return self._passi[rif.codice]
        done = False
        try:
            doc = self.orbit.chiama("plan_get", {"plan_id": rif.piano})
            for fase in (doc or {}).get("phases") or []:
                for passo in fase.get("steps") or []:
                    if str(passo.get("key") or "").upper() == rif.passo.upper():
                        done = passo.get("state") == "done"
        except (EsternoGiu, AttributeError, TypeError, ValueError):
            done = super()._passo_done(step_ref)  # Orbit giù: la copia locale, se c'è
        self._passi[rif.codice] = done
        return done


# ---------- eventi ----------

def _caller(event: dict) -> dict:
    return {"cwd": os.environ.get("CLAUDE_PROJECT_DIR") or event.get("cwd") or os.getcwd(),
            "agent": os.environ.get("CREWMOOR_AGENT") or os.environ.get("AB_AGENT") or None,
            "slug": os.environ.get("CREWMOOR_SESSION_SLUG") or os.environ.get("AB_SESSION_SLUG") or None,
            "claude_session_id": event.get("session_id") or os.environ.get("CLAUDE_CODE_SESSION_ID"),
            "onda_terminal_id": os.environ.get("ONDA_TERMINAL_ID"), "heartbeat": False}


def _risultato(resp) -> dict | None:
    """La risposta di uno strumento MCP come la passa Claude Code (oggetto, blocchi di testo, structuredContent)."""
    if isinstance(resp, dict):
        if isinstance(resp.get("structuredContent"), dict):
            return resp["structuredContent"]
        if any(k in resp for k in ("terminalId", "errorCode", "taskId", "messageId", "waitState")):
            return resp
        if isinstance(resp.get("content"), list):
            return _risultato(resp["content"])
        if isinstance(resp.get("text"), str):
            return _risultato(resp["text"])
        return None
    if isinstance(resp, list):
        for x in resp:
            out = _risultato(x)
            if out is not None:
                return out
        return None
    if isinstance(resp, str):
        try:
            return _risultato(json.loads(resp))
        except ValueError:
            return None
    return None


def _strumento(event: dict) -> str:
    """Il nome dello strumento senza prefisso MCP (``mcp__plugin_crewmoor_crewmoor__crewmoor_send`` → ``crewmoor_send``)."""
    return str(event.get("tool_name") or "").rsplit("__", 1)[-1]


def _indirizzo(to: str) -> tuple[str | None, str | None]:
    """``@kai/crewmoor``, ``@kai@laptop/crewmoor``, ``kai`` → (agente, slug)."""
    a = (to or "").strip().lstrip("@")
    if not a:
        return None, None
    agente, _, slug = a.partition("/")
    return agente.split("@", 1)[0].lower() or None, slug or None


def su_delega(d: DelegheCrewmoor, event: dict) -> dict:
    """PostToolUse di crewmoor_send: una delega (mode delegation, taskId nella risposta) entra nel registro."""
    inp = event.get("tool_input") if isinstance(event.get("tool_input"), dict) else {}
    res = _risultato(event.get("tool_response")) or {}
    task_id = res.get("taskId")
    if not task_id or (inp.get("mode") or "") != "delegation":
        return {"registrato": False, "motivo": "non è una delega"}
    try:
        me = d.c.amap.identify(_caller(event))
    except ErroreIndirizzo as exc:
        return {"registrato": False, "motivo": str(exc)}
    agente, slug = _indirizzo(str(inp.get("to") or ""))
    agente = ((res.get("to") or {}).get("name") or agente or "").lower() or None
    ref = inp.get("stepRef") or _step_ref(str(inp.get("title") or ""), str(inp.get("goal") or ""),
                                          str(inp.get("text") or ""))
    ts = iso_now()
    d.c._write(lambda: d.c._conn().execute(
        "INSERT OR IGNORE INTO deleghe (task_id, step_ref, delegator, delegator_host, delegated_to, sess_agent, "
        "sess_slug, host, sent_at, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (task_id, ref, f"{me.agent}/{me.slug}", me.host, str(inp.get("to") or ""), agente, slug,
         d.c.amap.host_self(), ts, ts, ts)))
    d.c._write(lambda: d.c._conn().execute(
        "INSERT OR REPLACE INTO waits (task_id, state, updated_at) VALUES (?, 'open', ?)", (task_id, ts)))
    legate = d._lega()
    d._dopo_evento()
    return {"registrato": True, "task_id": task_id, "step_ref": ref, "legate": legate}


def su_chiusa(d: DelegheCrewmoor, event: dict) -> dict:
    """PostToolUse di crewmoor_close: l'attesa chiusa (o annullata) chiude la delega come un bus_close."""
    res = _risultato(event.get("tool_response")) or {}
    inp = event.get("tool_input") if isinstance(event.get("tool_input"), dict) else {}
    task_id = res.get("taskId") or inp.get("taskId")
    stato = res.get("waitState")
    if not task_id or stato not in ("closed", "cancelled"):
        return {"chiusa": False, "motivo": f"attesa {stato or 'non chiusa'}"}
    d.c._write(lambda: d.c._conn().execute(
        "INSERT OR REPLACE INTO waits (task_id, state, updated_at) VALUES (?, 'closed', ?)", (task_id, iso_now())))
    d.su_attesa_chiusa(task_id)
    return {"chiusa": True, "task_id": task_id}


def gestisci(evento: str, event: dict, d: DelegheCrewmoor | None = None) -> dict:
    caller = _caller(event)
    d = d or DelegheCrewmoor(Centrale(), cwd=caller["cwd"])
    if evento == "spawn":
        res = _risultato(event.get("tool_response"))
        inp = event.get("tool_input") if isinstance(event.get("tool_input"), dict) else {}
        return d.op_spawn(caller, {"result": res or {}, "input": inp}) if res else {"registrato": False}
    if evento == "orfani":
        inp = event.get("tool_input") if isinstance(event.get("tool_input"), dict) else {}
        wt = inp.get("worktree") if isinstance(inp.get("worktree"), dict) else {}
        return d.orfani(wt["repoRoot"]) if wt.get("repoRoot") else {"saltato": "spawn senza worktree"}
    if evento == "delega":
        return su_delega(d, event) if _strumento(event) in SEND_TOOLS else {"saltato": _strumento(event)}
    if evento == "chiusa":
        return su_chiusa(d, event) if _strumento(event) in CLOSE_TOOLS else {"saltato": _strumento(event)}
    if evento in ("start", "fine"):
        try:
            me = d.c.amap.identify(caller)
        except ErroreIndirizzo as exc:
            return {"saltato": str(exc)}
        if evento == "start":
            d.su_start(me, caller)
        else:
            d.su_fine_sessione(me.agent, me.slug)
        return {"ok": True, "sessione": f"{me.agent}/{me.slug}"}
    if evento == "giro":
        d._dopo_evento()
        return {"ok": True}
    return {"saltato": f"evento sconosciuto {evento}"}


def main(argv: list[str]) -> int:
    evento = argv[1] if len(argv) > 1 else ""
    t0 = time.monotonic()
    central = None
    try:
        central = Centrale()
        raw = sys.stdin.read() if not sys.stdin.isatty() else ""
        event = json.loads(raw) if raw.strip() else {}
        caller = _caller(event if isinstance(event, dict) else {})
        out = gestisci(evento, event if isinstance(event, dict) else {}, DelegheCrewmoor(central, cwd=caller["cwd"]))
        if os.environ.get("DELEGHE_CREWMOOR_STAMPA") == "1":
            print(json.dumps(out, ensure_ascii=False, default=str))
        central.log("debug", "deleghe_evento", cosa=evento, ms=int((time.monotonic() - t0) * 1000))
    except Exception as exc:  # noqa: BLE001 - un hook non deve mai fermare la sessione
        if central is not None:
            central.log("error", "deleghe_errore", cosa=evento, error=repr(exc))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
