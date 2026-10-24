"""Indirizzi e identità: l'unico posto del bus che risolve agente, slug e host.

Sostituisce le cinque implementazioni di oggi (INVENTARIO §5.1). Regole:

- agente da cwd: regola di AGENT_MAP.rules col prefisso più lungo, sul confine di
  cartella (``/a/work-hub`` combacia con ``/a/work-hub/x``, non con ``/a/work-hub-x``);
- slug da cwd: basename del path risolto, minuscolo, il resto piegato in ``-``
  (identico a ``slug_for_path`` di oggi, così le caselle ``@slug`` non cambiano);
- indirizzo ``agente[@host][/slug]`` o ``agente/slug@host``;
- host di un destinatario: ``@host`` esplicito, poi ``agents.<a>.host``, poi l'hub
  per l'agente hub, altrimenti questa macchina.
"""

from __future__ import annotations

import json
import os
import re
import threading
from dataclasses import dataclass
from pathlib import Path

SLUG_SAFE = re.compile(r"[^a-z0-9._-]")


class ErroreIndirizzo(ValueError):
    """Indirizzo, agente o host che il chiamante deve correggere."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def slug_for_path(path: str | os.PathLike[str]) -> str:
    p = Path(path).expanduser()
    try:
        p = p.resolve()
    except OSError:
        pass
    return SLUG_SAFE.sub("-", (p.name or "root").lower())


def parse_addr(addr: str) -> tuple[str, str | None, str | None]:
    """(agente, slug, host). 'kai@mini/crewmoor' e 'kai/crewmoor@mini' sono la stessa cosa.
    Uno slug non contiene mai '@' (slug_for_path lo piega), quindi la divisione è univoca."""
    addr = (addr or "").strip()
    head, sep, tail = addr.partition("/")
    host = None
    if "@" in head:
        head, _, host = head.partition("@")
    elif "@" in tail:
        tail, _, host = tail.rpartition("@")
    slug = tail.strip() if sep else ""
    return head.strip(), (slug or None), ((host or "").strip() or None)


def format_addr(agent: str, slug: str | None = None, host: str | None = None) -> str:
    return agent + (f"@{host}" if host else "") + (f"/{slug}" if slug else "")


def _under(path: str, prefix: str) -> bool:
    prefix = prefix.rstrip("/")
    return bool(prefix) and (path == prefix or path.startswith(prefix + "/"))


@dataclass(frozen=True)
class Identita:
    agent: str
    slug: str
    host: str | None

    @property
    def address(self) -> str:
        return f"{self.agent}/{self.slug}"


class AgentMap:
    """AGENT_MAP.json riletto quando cambia mtime. Un file malformato lascia in uso
    l'ultima versione buona (e ``errore`` lo dice)."""

    def __init__(self, home: Path):
        self.home = Path(home)
        self._lock = threading.Lock()
        self._mtime: float | None = None
        self._data: dict = {}
        self.errore: str | None = None

    @property
    def path(self) -> Path:
        return self.home / "AGENT_MAP.json"

    def data(self) -> dict:
        with self._lock:
            try:
                m = self.path.stat().st_mtime
            except OSError:
                self._data, self._mtime = {}, None
                return self._data
            if m != self._mtime:
                try:
                    d = json.loads(self.path.read_text(encoding="utf-8"))
                    if not isinstance(d, dict):
                        raise ValueError("non è un oggetto")
                    self._data, self.errore = d, None
                except (OSError, ValueError) as exc:
                    self.errore = f"AGENT_MAP.json illeggibile: {exc}"
                self._mtime = m
            return self._data

    # ---------- agenti ----------

    def agents(self) -> dict:
        a = self.data().get("agents") or {}
        return a if isinstance(a, dict) else {}

    def agent_exists(self, agent: str) -> bool:
        return agent in self.agents()

    def agent_for_cwd(self, cwd: str | os.PathLike[str]) -> str | None:
        raw = str(cwd).rstrip("/") or "/"
        try:
            resolved = str(Path(raw).expanduser().resolve())
        except OSError:
            resolved = raw
        best: tuple[int, str] | None = None
        for rule in self.data().get("rules") or []:
            pattern = (rule or {}).get("pattern") or ""
            agent = (rule or {}).get("agent") or ""
            if not pattern or not agent:
                continue
            if _under(raw, pattern) or _under(resolved, pattern):
                n = len(pattern.rstrip("/"))
                if best is None or n > best[0]:
                    best = (n, agent)
        return best[1] if best else None

    def home_slug(self, agent: str) -> str | None:
        root = (self.agents().get(agent) or {}).get("workspace_root")
        return slug_for_path(root) if root else None

    # ---------- host ----------

    def hosts(self) -> dict:
        h = self.data().get("hosts") or {}
        return h if isinstance(h, dict) else {}

    def hub_host(self) -> str | None:
        for name, conf in self.hosts().items():
            if (conf or {}).get("hub") is True:
                return name
        return None

    def host_self(self) -> str | None:
        env = os.environ.get("AB_HOST", "").strip()
        if env:
            return env
        try:
            name = (self.home / "HOST").read_text(encoding="utf-8").strip().splitlines()[0].strip()
            if name:
                return name
        except (OSError, IndexError):
            pass
        return self.hub_host()

    def remote_hosts(self) -> list[str]:
        me = self.host_self()
        return sorted(h for h, c in self.hosts().items() if h != me and (c or {}).get("ssh"))

    def host_bus_home(self, host: str) -> str:
        return (self.hosts().get(host) or {}).get("bus_home") or str(self.home)

    def resolve_host(self, agent: str, want: str | None = None) -> str | None:
        """Host verso cui va il messaggio; None = questa macchina."""
        me = self.host_self()
        if want:
            if want == me:
                return None
            if want not in self.hosts():
                known = ", ".join(sorted(self.hosts())) or 'nessuno ("hosts" manca in AGENT_MAP.json)'
                raise ErroreIndirizzo("bad_request", f"host sconosciuto '{want}'. Noti: {known}")
            return want
        pinned = (self.agents().get(agent) or {}).get("host")
        if not pinned:
            hub_agent = (self.data().get("cc_awareness") or {}).get("hub")
            if hub_agent and agent == hub_agent:
                pinned = self.hub_host()
        if not pinned or pinned == me or pinned not in self.hosts():
            return None
        return pinned

    # ---------- chiamante ----------

    def identify(self, caller: dict) -> Identita:
        """Identità di chi chiama dal suo ``caller`` (cwd + override AB_AGENT/AB_SESSION_SLUG)."""
        cwd = (caller or {}).get("cwd") or ""
        agent = (caller or {}).get("agent") or (self.agent_for_cwd(cwd) if cwd else None)
        if not agent:
            raise ErroreIndirizzo(
                "no_identity",
                f"nessun agente per {cwd or '(cwd assente)'}: imposta AB_AGENT, oppure lavora in una "
                "cartella elencata nelle rules di AGENT_MAP.json.")
        slug = (caller or {}).get("slug") or (slug_for_path(cwd) if cwd else None)
        if not slug:
            raise ErroreIndirizzo("no_identity", "slug della sessione non ricavabile: imposta AB_SESSION_SLUG")
        return Identita(agent, slug, self.host_self())
