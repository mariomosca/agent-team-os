"""Stato del centralino in SQLite: un solo processo scrive, sempre in transazione.

Le scritture passano tutte da ``Store.tx()`` (``BEGIN IMMEDIATE`` sotto un lock di
processo): è la coda unica di scrittura del contratto. Le letture usano una
connessione per thread, che con WAL non aspetta lo scrittore.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

SCHEMA_VERSION = 5

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS messages (
  id                TEXT PRIMARY KEY,
  ts                TEXT NOT NULL,
  from_agent        TEXT NOT NULL,
  from_slug         TEXT,
  from_host         TEXT,
  to_agent          TEXT NOT NULL,
  to_slug           TEXT,
  to_host           TEXT NOT NULL,
  type              TEXT NOT NULL,
  kind              TEXT,
  intent            TEXT,
  priority          TEXT NOT NULL DEFAULT 'normal',
  thread_id         TEXT NOT NULL,
  in_reply_to       TEXT,
  task_id           TEXT,
  requires_response INTEGER NOT NULL DEFAULT 0,
  response_by       TEXT,
  state             TEXT NOT NULL,
  esito             TEXT,
  handled_by        TEXT,
  handled_at        TEXT,
  delivery          TEXT NOT NULL,
  origin            TEXT NOT NULL,
  path              TEXT,
  raw               TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS messages_inbox  ON messages(to_agent, to_slug, state);
CREATE INDEX IF NOT EXISTS messages_task   ON messages(task_id);
CREATE INDEX IF NOT EXISTS messages_thread ON messages(thread_id, ts);

CREATE TABLE IF NOT EXISTS waits (
  task_id          TEXT PRIMARY KEY,
  owner_agent      TEXT NOT NULL,
  owner_slug       TEXT NOT NULL,
  owner_host       TEXT,
  description      TEXT,
  delegated_to     TEXT,
  executor_session TEXT,
  delegation_msg   TEXT,
  thread_id        TEXT,
  created_at       TEXT NOT NULL,
  deadline         TEXT,
  state            TEXT NOT NULL,
  last_activity    TEXT,
  closed_at        TEXT,
  closed_by        TEXT,
  close_reason     TEXT,
  target           TEXT,
  origin           TEXT NOT NULL,
  raw              TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS waits_owner ON waits(owner_agent, owner_slug, state);

CREATE TABLE IF NOT EXISTS wait_returns (
  task_id   TEXT NOT NULL,
  msg_id    TEXT NOT NULL,
  from_addr TEXT,
  at        TEXT,
  type      TEXT,
  state     TEXT NOT NULL,
  esito     TEXT,
  PRIMARY KEY (task_id, msg_id)
);

CREATE TABLE IF NOT EXISTS sessions (
  agent             TEXT NOT NULL,
  slug              TEXT NOT NULL,
  host              TEXT NOT NULL,
  workspace_path    TEXT,
  claude_session_id TEXT,
  onda_terminal_id  TEXT,
  tmux_pane         TEXT,
  pid               INTEGER,
  started_at        TEXT,
  last_heartbeat    TEXT,
  last_event        TEXT,
  ended_at          TEXT,
  PRIMARY KEY (agent, slug, host)
);

CREATE TABLE IF NOT EXISTS wake_decisions (
  id        INTEGER PRIMARY KEY,
  at        TEXT NOT NULL,
  giro      INTEGER NOT NULL,
  agent     TEXT NOT NULL,
  slug      TEXT NOT NULL,
  msg_id    TEXT NOT NULL,
  task_id   TEXT,
  regola    TEXT NOT NULL,
  decisione TEXT NOT NULL,
  motivo    TEXT,
  canale    TEXT
);
CREATE INDEX IF NOT EXISTS wake_msg ON wake_decisions(msg_id, agent, slug, id);
CREATE INDEX IF NOT EXISTS wake_at  ON wake_decisions(agent, slug, decisione, at);

CREATE TABLE IF NOT EXISTS remote_queue (
  msg_id     TEXT PRIMARY KEY,
  host       TEXT NOT NULL,
  queued_at  TEXT NOT NULL,
  attempts   INTEGER NOT NULL DEFAULT 0,
  last_error TEXT
);

CREATE TABLE IF NOT EXISTS thread_legacy (
  thread_id TEXT NOT NULL,
  id        TEXT NOT NULL,
  entry     TEXT NOT NULL,
  PRIMARY KEY (thread_id, id)
);
"""

# Colonne aggiunte dopo la v1: (tabella, colonna, definizione). Si aggiungono se mancano.
MIGRAZIONI = [
    ("sessions", "waiting_agent", "TEXT"),          # v2: domanda in attesa di risposta (era nel registro)
    ("sessions", "drain_blocks", "INTEGER NOT NULL DEFAULT 0"),  # v2: blocchi consecutivi dello Stop
    ("messages", "letto_at", "TEXT"),               # v4 (BR24-F6.6): prima lettura della delega da chi la esegue
    ("messages", "letto_da", "TEXT"),               # v4: la sessione che l'ha letta
]


def _d(v) -> dict:
    """I record vecchi hanno a volte una stringa dove oggi c'è un oggetto: vale come assente."""
    return v if isinstance(v, dict) else {}


def _s(v) -> str | None:
    return v if isinstance(v, str) else None


def _j(v) -> str | None:
    return None if v is None else json.dumps(v, ensure_ascii=False, sort_keys=True)


class Store:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._wlock = threading.RLock()
        self._w = self._connect()
        self._local = threading.local()
        with self._wlock:
            self._backup_prima_di_migrare()
            self._w.executescript(SCHEMA)
            for table, col, decl in MIGRAZIONI:
                cols = {r["name"] for r in self._w.execute(f"PRAGMA table_info({table})")}
                if col not in cols:
                    self._w.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")
            self._v3()
            self._v4()
            self._v5()
            self._w.execute("INSERT OR REPLACE INTO meta(key, value) VALUES ('schema_version', ?)",
                            (str(SCHEMA_VERSION),))

    def _backup_prima_di_migrare(self) -> None:
        """Un database di uno schema più vecchio si copia accanto prima di toccarlo:
        ``centralino.db.bak-v<vecchio>-<ora>``, con la backup API (coerente anche in WAL)."""
        try:
            r = self._w.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
        except sqlite3.OperationalError:
            return  # database nuovo, niente da salvare
        if r is None or int(r["value"]) >= SCHEMA_VERSION:
            return
        import time
        dest = self.path.with_name(f"{self.path.name}.bak-v{r['value']}-{time.strftime('%Y%m%d-%H%M%S')}")
        out = sqlite3.connect(dest)
        try:
            self._w.backup(out)
        finally:
            out.close()

    def _v3(self) -> None:
        """v3 (F5.5): niente più modo ombra. Via la tabella dei confronti e le decisioni solo
        decise (mai eseguite), poi la colonna ``modo``. Una volta sola, sui database più vecchi."""
        r = self._w.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
        if r is None or int(r["value"]) >= 3:
            return
        self._w.execute("DROP TABLE IF EXISTS shadow_diffs")
        cols = {c["name"] for c in self._w.execute("PRAGMA table_info(wake_decisions)")}
        if "modo" in cols:
            self._w.execute("DELETE FROM wake_decisions WHERE modo='ombra'")
            try:
                self._w.execute("ALTER TABLE wake_decisions DROP COLUMN modo")
            except sqlite3.OperationalError:
                pass  # SQLite vecchio: la colonna resta col suo default, nessuno la legge più
        self._w.execute("DELETE FROM meta WHERE key='sveglia_log_offset'")

    def _v4(self) -> None:
        """v4 (BR24-F6.6): le deleghe di prima non dicono se chi esegue le ha lette. Valgono come
        lette alla prima sveglia consegnata, se c'è, altrimenti all'ora della migrazione
        (``letto_da`` vuoto): meglio "ci lavora" come prima che un "in coda" falso su una delega
        già aperta da giorni."""
        r = self._w.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
        if r is None or int(r["value"]) >= 4:
            return
        from .tempo import iso_now
        self._w.execute(
            "UPDATE messages SET letto_at=COALESCE((SELECT MIN(w.at) FROM wake_decisions w WHERE "
            "w.msg_id=messages.id AND w.decisione='sveglia'), ?) WHERE kind='delega' AND letto_at IS NULL",
            (iso_now(),))

    def _v5(self) -> None:
        """v5 (BR24-F6.12): gli slug in minuscolo ovunque (kai/Orbit è kai/orbit). Due sessioni
        che diventano la stessa: resta la più recente."""
        r = self._w.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
        if r is None or int(r["value"]) >= 5:
            return
        for table, col in (("messages", "to_slug"), ("messages", "from_slug"), ("waits", "owner_slug"),
                           ("wake_decisions", "slug")):
            self._w.execute(f"UPDATE {table} SET {col}=lower({col}) WHERE {col} != lower({col})")
        for x in self._w.execute("SELECT agent, slug, host, last_heartbeat FROM sessions "
                                 "WHERE slug != lower(slug)").fetchall():
            gemella = self._w.execute("SELECT last_heartbeat FROM sessions WHERE agent=? AND slug=? AND host=?",
                                      (x["agent"], x["slug"].lower(), x["host"])).fetchone()
            if gemella is not None and (gemella["last_heartbeat"] or "") >= (x["last_heartbeat"] or ""):
                self._w.execute("DELETE FROM sessions WHERE agent=? AND slug=? AND host=?",
                                (x["agent"], x["slug"], x["host"]))
                continue
            if gemella is not None:
                self._w.execute("DELETE FROM sessions WHERE agent=? AND slug=? AND host=?",
                                (x["agent"], x["slug"].lower(), x["host"]))
            self._w.execute("UPDATE sessions SET slug=? WHERE agent=? AND slug=? AND host=?",
                            (x["slug"].lower(), x["agent"], x["slug"], x["host"]))

    def _connect(self) -> sqlite3.Connection:
        c = sqlite3.connect(self.path, timeout=10, isolation_level=None, check_same_thread=False)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA synchronous=NORMAL")
        c.execute("PRAGMA foreign_keys=ON")
        c.execute("PRAGMA busy_timeout=10000")
        return c

    def close(self) -> None:
        with self._wlock:
            self._w.close()

    # ---------- transazioni ----------

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        """L'unica via di scrittura: una transazione IMMEDIATE alla volta nel processo."""
        with self._wlock:
            self._w.execute("BEGIN IMMEDIATE")
            try:
                yield self._w
            except BaseException:
                self._w.execute("ROLLBACK")
                raise
            else:
                self._w.execute("COMMIT")

    def read(self) -> sqlite3.Connection:
        c = getattr(self._local, "c", None)
        if c is None:
            c = self._connect()
            self._local.c = c
        return c

    # ---------- messaggi ----------

    @staticmethod
    def put_message(c: sqlite3.Connection, record: dict, *, state: str, to_slug: str | None,
                    to_host: str, path: str | None, origin: str, delivery: str = "local",
                    mid: str | None = None) -> None:
        """``mid``: l'id con cui le tool trovano il messaggio, cioè il nome del file. Coincide
        con ``record["id"]`` tranne in qualche file vecchio rinominato a mano."""
        fs = _s(record.get("from_session")) or ""
        from_slug = (fs.partition("/")[2] or None) if "/" in fs else None
        handled = _d(record.get("handled"))
        c.execute(
            """INSERT INTO messages (id, ts, from_agent, from_slug, from_host, to_agent, to_slug, to_host,
                 type, kind, intent, priority, thread_id, in_reply_to, task_id, requires_response,
                 response_by, state, esito, handled_by, handled_at, delivery, origin, path, raw)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(id) DO UPDATE SET
                 to_slug=excluded.to_slug, task_id=excluded.task_id, kind=excluded.kind,
                 state=excluded.state, esito=excluded.esito, handled_by=excluded.handled_by,
                 handled_at=excluded.handled_at, path=excluded.path, raw=excluded.raw""",
            (mid or record["id"], _s(record.get("ts")) or "", _s(record.get("from")) or "", from_slug,
             _s(_d(record.get("context")).get("host")), record.get("to") or "", to_slug, to_host,
             record.get("type") or "", record.get("kind"), record.get("intent"),
             record.get("priority") or "normal", record.get("thread_id") or "",
             _s(record.get("in_reply_to")), _s(record.get("task_id")),
             1 if record.get("requires_response") else 0, record.get("response_by"),
             state, handled.get("esito"), handled.get("by"), handled.get("at"),
             delivery, origin, path, _j(record)))

    @staticmethod
    def segna_letta(c: sqlite3.Connection, mid: str, at: str, by: str | None) -> None:
        """BR24-F6.6: il messaggio ``mid`` è arrivato a chi lo riceve (lettura, sveglia consegnata,
        per una delega anche il primo ritorno). Conta solo la prima volta."""
        c.execute("UPDATE messages SET letto_at=?, letto_da=? WHERE id=? AND letto_at IS NULL",
                  (at, by, mid))

    def message(self, mid: str) -> sqlite3.Row | None:
        return self.read().execute("SELECT * FROM messages WHERE id=?", (mid,)).fetchone()

    # ---------- attese ----------

    @staticmethod
    def put_wait(c: sqlite3.Connection, rec: dict, *, origin: str) -> None:
        deleg = _d(rec.get("delegation"))
        c.execute(
            """INSERT INTO waits (task_id, owner_agent, owner_slug, owner_host, description, delegated_to,
                 executor_session, delegation_msg, thread_id, created_at, deadline, state, last_activity,
                 closed_at, closed_by, close_reason, target, origin, raw)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(task_id) DO UPDATE SET
                 executor_session=excluded.executor_session, deadline=excluded.deadline,
                 state=excluded.state, last_activity=excluded.last_activity,
                 closed_at=excluded.closed_at, closed_by=excluded.closed_by,
                 close_reason=excluded.close_reason, raw=excluded.raw""",
            (rec["task_id"], rec.get("agent") or "", rec.get("slug") or "", rec.get("host"),
             rec.get("description"), rec.get("delegated_to"), rec.get("executor_session"),
             deleg.get("msg_id"), deleg.get("thread_id"), rec.get("created_at") or "",
             rec.get("deadline"), rec.get("state") or "", rec.get("last_activity"),
             rec.get("closed_at") or rec.get("cancelled_at"), rec.get("closed_by"),
             rec.get("note") or rec.get("close_reason"), _j(rec.get("target")), origin, _j(rec)))
        c.execute("DELETE FROM wait_returns WHERE task_id=?", (rec["task_id"],))
        for mid, e in _d(rec.get("returns")).items():
            e = _d(e)
            c.execute("INSERT INTO wait_returns (task_id, msg_id, from_addr, at, type, state, esito) "
                      "VALUES (?,?,?,?,?,?,?)",
                      (rec["task_id"], mid, e.get("from"), e.get("at"), e.get("type"),
                       e.get("state") or "pending", e.get("handled")))

    def wait(self, task_id: str) -> sqlite3.Row | None:
        return self.read().execute("SELECT * FROM waits WHERE task_id=?", (task_id,)).fetchone()

    # ---------- sessioni ----------

    @staticmethod
    def heartbeat(c: sqlite3.Connection, agent: str, slug: str, host: str, now: str, *,
                  workspace: str | None = None, caller: dict | None = None, event: str | None = None) -> None:
        caller = caller or {}
        c.execute(
            """INSERT INTO sessions (agent, slug, host, workspace_path, claude_session_id, onda_terminal_id,
                 tmux_pane, pid, started_at, last_heartbeat, last_event, ended_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,NULL)
               ON CONFLICT(agent, slug, host) DO UPDATE SET
                 workspace_path=COALESCE(excluded.workspace_path, workspace_path),
                 claude_session_id=COALESCE(excluded.claude_session_id, claude_session_id),
                 onda_terminal_id=COALESCE(excluded.onda_terminal_id, onda_terminal_id),
                 tmux_pane=COALESCE(excluded.tmux_pane, tmux_pane),
                 pid=COALESCE(excluded.pid, pid),
                 started_at=CASE WHEN ended_at IS NOT NULL AND excluded.last_event IS NOT NULL
                                 THEN excluded.started_at ELSE started_at END,
                 last_heartbeat=excluded.last_heartbeat,
                 last_event=COALESCE(excluded.last_event, last_event),
                 -- BR24-F6.13: un heartbeat senza evento (il MCP di un processo bg-spare) non
                 -- riapre una sessione chiusa; la riapre solo un evento di sessione (start, prompt)
                 ended_at=CASE WHEN excluded.last_event IS NULL AND (ended_at IS NOT NULL OR last_event='end')
                               THEN COALESCE(ended_at, last_heartbeat) ELSE NULL END""",
            (agent, slug, host, workspace, caller.get("claude_session_id"), caller.get("onda_terminal_id"),
             caller.get("tmux_pane"), caller.get("pid"), now, now, event))

    @staticmethod
    def end_session(c: sqlite3.Connection, agent: str, slug: str, host: str, now: str) -> None:
        c.execute("UPDATE sessions SET ended_at=?, last_event='end' WHERE agent=? AND slug=? AND host=?",
                  (now, agent, slug, host))

    def sessions(self) -> list[sqlite3.Row]:
        return self.read().execute("SELECT * FROM sessions").fetchall()
