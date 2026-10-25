"""Regola unica di sessione viva (contratto §5).

Una sessione (agente, slug, host) è viva se Onda la dà viva oppure se ha un heartbeat
negli ultimi 120 s. Nient'altro. ``onda/sessions.json`` si legge così com'è
(contratto v1 di Onda): il centralino non scrive in ``onda/`` e non chiede nulla a Onda.

Viva non vuol dire raggiungibile: il canale di sveglia è il pannello di Onda, oppure
tmux se la sessione ha dichiarato un ``tmux_pane``.
"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass
from pathlib import Path

from .tempo import age_s

ONDA_FRESH_S = 60       # sessions.json più vecchio di così: Onda non è affidabile
HEARTBEAT_S = 120       # heartbeat del MCP ogni 60 s: due battiti persi = chiusa
SETTLE_S = 5            # stato idle da meno di così: non si sveglia ancora


def _pid_alive(pid) -> bool:
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


class OndaView:
    """sessions.json di Onda, riletto quando cambia mtime."""

    def __init__(self, home: Path, pid_alive=_pid_alive):
        self.path = Path(home) / "onda" / "sessions.json"
        self._pid_alive = pid_alive
        self._lock = threading.Lock()
        self._mtime: float | None = None
        self._doc: dict = {}

    def doc(self) -> dict:
        with self._lock:
            try:
                m = self.path.stat().st_mtime
            except OSError:
                self._doc, self._mtime = {}, None
                return self._doc
            if m != self._mtime:
                try:
                    d = json.loads(self.path.read_text(encoding="utf-8"))
                    self._doc = d if isinstance(d, dict) else {}
                except (OSError, ValueError):
                    pass  # scrittura a metà: si tiene la lettura precedente
                self._mtime = m
            return self._doc

    def valid(self) -> bool:
        d = self.doc()
        if d.get("v") != 1 or not self._pid_alive(d.get("ondaPid")):
            return False
        age = age_s(d.get("writtenAt"))
        return age is not None and age <= ONDA_FRESH_S

    def entries(self) -> list[dict]:
        if not self.valid():
            return []
        return [s for s in self.doc().get("sessions") or [] if isinstance(s, dict)]

    def find(self, agent: str, slug: str, host: str | None = None,
             claude_session_id: str | None = None) -> dict | None:
        """La voce di quella sessione: prima per claude_session_id, poi agent+slug; le vive prima."""
        best = None
        for s in self.entries():
            if host and s.get("host") and s.get("host") != host:
                continue
            hit = (claude_session_id and s.get("sessionId") == claude_session_id) or (
                s.get("agent") == agent and s.get("slug") == slug)
            if not hit:
                continue
            if s.get("state") != "exited":
                return s
            best = best or s
        return best


@dataclass
class StatoSessione:
    agent: str
    slug: str
    host: str
    viva: bool
    per: str | None          # onda | heartbeat | None
    canale: str              # onda | tmux | nessuno
    onda_state: str | None
    ferma: bool              # idle, casella vuota, assestata: si può svegliare adesso
    workspace: str | None = None

    @property
    def descrizione(self) -> str:
        if not self.viva:
            return "chiusa"
        return f"viva per {self.per}, canale: {self.canale}"

    def as_dict(self) -> dict:
        return {"agent": self.agent, "slug": self.slug, "host": self.host, "viva": self.viva,
                "per": self.per, "canale": self.canale, "onda_state": self.onda_state,
                "ferma": self.ferma, "workspace": self.workspace, "stato": self.descrizione}


class Vivacita:
    def __init__(self, onda: OndaView, store, host: str | None):
        self.onda = onda
        self.store = store
        self.host = host or ""

    def _db_row(self, agent: str, slug: str):
        return self.store.read().execute(
            "SELECT * FROM sessions WHERE agent=? AND slug=? AND host=?", (agent, slug, self.host)).fetchone()

    def stato(self, agent: str, slug: str) -> StatoSessione:
        row = self._db_row(agent, slug)
        e = self.onda.find(agent, slug, self.host, row["claude_session_id"] if row else None)
        onda_viva = e is not None and e.get("state") != "exited"
        hb = None
        # BR24-F6.13: viva per heartbeat solo con un evento di sessione alle spalle (start,
        # prompt, stop): un processo bg-spare di Claude Code carica il MCP e batte ogni 60 s
        # senza essere una sessione, e dopo "end" il battito non la riapre
        if row is not None and row["ended_at"] is None and row["last_event"] not in (None, "end"):
            hb = age_s(row["last_heartbeat"])
        hb_viva = hb is not None and hb <= HEARTBEAT_S
        per = "onda" if onda_viva else ("heartbeat" if hb_viva else None)
        if onda_viva:
            canale = "onda"
        elif row is not None and row["tmux_pane"]:
            canale = "tmux"
        else:
            canale = "nessuno"
        ferma = False
        if onda_viva:
            settled = age_s(e.get("stateSince"))
            ferma = (e.get("state") == "idle" and e.get("hasText") is False
                     and settled is not None and settled >= SETTLE_S)
        elif canale == "tmux":
            ferma = hb_viva  # lo stato lo legge via_tmux dallo schermo: occupata o con testo = Rinvio
        return StatoSessione(agent, slug, self.host, onda_viva or hb_viva, per, canale,
                             e.get("state") if e else None, ferma,
                             (e or {}).get("cwd") or (row["workspace_path"] if row else None))

    def vive(self, agent: str | None = None) -> list[StatoSessione]:
        """Sessioni vive di questa macchina (di un agente, o tutte)."""
        keys: set[tuple[str, str]] = set()
        for s in self.onda.entries():
            if s.get("agent") and s.get("slug") and s.get("state") != "exited" \
                    and (not s.get("host") or s.get("host") == self.host):
                keys.add((s["agent"], s["slug"]))
        for row in self.store.read().execute(
                "SELECT agent, slug FROM sessions WHERE host=? AND ended_at IS NULL", (self.host,)):
            keys.add((row["agent"], row["slug"]))
        out = []
        for a, s in sorted(keys):
            if agent and a != agent:
                continue
            st = self.stato(a, s)
            if st.viva:
                out.append(st)
        return out

    def slug_vivi(self, agent: str) -> list[str]:
        return [s.slug for s in self.vive(agent)]
