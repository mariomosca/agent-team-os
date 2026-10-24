"""Trasporto verso gli altri host (il mini via SSH, il cloud via coda): solo spedizione e raccolta.

Ogni host con ``ssh`` ha il suo centralino (BR24-F5.7), indicato in AGENT_MAP da
``hosts.<host>.centralino`` (il comando da lanciare là). Una consegna è
``ssh <host> '<centralino> consegna'`` col messaggio su stdin; se non riesce, resta in
``outbox/<host>/`` finché la raccolta la porta. La raccolta spinge la coda, tira giù ``outbox/<laptop>/`` dell'altro host
in un solo ``tar`` e passa ogni messaggio a ``ricevi`` (il centralino lo mette in SQLite) prima
di cancellarlo là. Non conosce caselle né attese: sono del centralino.
"""

from __future__ import annotations

import io
import json
import os
import random
import shlex
import string
import subprocess
import sys
import tarfile
import time
from datetime import timedelta
from pathlib import Path
from typing import Callable

from .indirizzi import AgentMap
from .tempo import iso_now, now, parse_iso

SESSION_TTL_MIN = int(os.environ.get("AB_SESSION_TTL_MIN", "240"))
SSH_BIN = os.environ.get("AB_SSH_BIN", "ssh")
SSH_TIMEOUT = int(os.environ.get("AB_SSH_TIMEOUT", "5"))
COLLECT_INTERVAL = int(os.environ.get("AB_COLLECT_INTERVAL", "30"))
REMOTE_STATUS_TTL = int(os.environ.get("AB_REMOTE_STATUS_TTL", "20"))


def rand(n: int) -> str:
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=n))


def read_json(path: Path) -> dict | None:
    try:
        with path.open(encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def atomic_write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{rand(6)}.tmp")
    try:
        with tmp.open("w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
        tmp.replace(path)
    finally:
        tmp.unlink(missing_ok=True)


class Remoto:
    """``ricevi(agent, slug, msg_id, record) -> bool | None``: True = nuovo, False = c'era già,
    None = non scritto (il messaggio resta sull'host e si riprova)."""

    def __init__(self, home: Path, amap: AgentMap, ricevi: Callable[[str, str | None, str, dict], bool | None]):
        self.home = Path(home)
        self.amap = amap
        self.ricevi = ricevi

    def hosts(self) -> dict:
        return self.amap.hosts()

    def ssh(self, host: str, command: str, data: bytes | None = None) -> subprocess.CompletedProcess | None:
        target = (self.hosts().get(host) or {}).get("ssh")
        if not target:
            return None
        ssh_dir = Path.home() / ".ssh"
        ssh_dir.mkdir(exist_ok=True)
        argv = [SSH_BIN, "-o", "BatchMode=yes", "-o", f"ConnectTimeout={SSH_TIMEOUT}",
                "-o", "ControlMaster=auto", "-o", f"ControlPath={ssh_dir}/cm-%r@%h:%p",
                "-o", "ControlPersist=10m", target, command]
        try:
            return subprocess.run(argv, input=data if data is not None else b"",
                                  capture_output=True, timeout=SSH_TIMEOUT + 25)
        except (OSError, subprocess.TimeoutExpired):
            return None

    def consegna(self, host: str, agent: str, slug: str | None, data: bytes) -> bool:
        """Il messaggio passa al centralino dell'altro host, che lo mette nella sua casella."""
        cmd = (self.hosts().get(host) or {}).get("centralino")
        if not cmd:
            print(f"hosts.{host}.centralino manca in AGENT_MAP: messaggio in coda, non consegnabile", file=sys.stderr)
            return False
        doc = {"agent": agent, "slug": slug, "record": json.loads(data), "origine": self.amap.host_self() or "host"}
        res = self.ssh(host, f"{cmd} consegna", json.dumps(doc, ensure_ascii=False).encode("utf-8"))
        return res is not None and res.returncode == 0

    @staticmethod
    def _da_rel(rel: str) -> tuple[str, str | None]:
        """``<agente>/[@slug/]<id>.json`` (coda e raccolta) → (agente, slug)."""
        parts = rel.removeprefix("inboxes/").split("/")
        slug = parts[1][1:] if len(parts) == 3 and parts[1].startswith("@") else None
        return parts[0], slug

    def queue_for_host(self, host: str, rel: str, data: bytes) -> Path:
        dest = self.home / "outbox" / host / rel.removeprefix("inboxes/")
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_name(f".{dest.name}.{os.getpid()}.{rand(6)}.tmp")
        tmp.write_bytes(data)
        tmp.replace(dest)
        return dest

    def deliver_remote(self, host: str, agent: str, slug: str | None, record: dict) -> str:
        rel = f"inboxes/{agent}" + (f"/@{slug}" if slug else "") + f"/{record['id']}.json"
        data = (json.dumps(record, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        if (self.hosts().get(host) or {}).get("ssh") and self.consegna(host, agent, slug, data):
            return "delivered"
        self.queue_for_host(host, rel, data)
        return "queued"

    def _host_state(self, host: str) -> Path:
        d = self.home / "hosts" / host
        d.mkdir(parents=True, exist_ok=True)
        return d

    @staticmethod
    def _age(path: Path) -> float:
        try:
            return time.time() - path.stat().st_mtime
        except OSError:
            return float("inf")

    def collect(self, force: bool = False, only: str | None = None) -> list[dict]:
        out = []
        for host in self.amap.remote_hosts():
            if only and host != only:
                continue
            res = self._collect_host(host, force)
            if res is not None:
                out.append(res)
        return out

    def _collect_host(self, host: str, force: bool) -> dict | None:
        state = self._host_state(host)
        stamp = state / "last-collect"
        if not force and self._age(stamp) < COLLECT_INTERVAL:
            return None
        lock = self.home / "locks" / f"collect-{host}.lock"
        lock.parent.mkdir(parents=True, exist_ok=True)
        try:
            lock.mkdir()
        except FileExistsError:
            if self._age(lock) <= 120:
                return None
            try:
                lock.rmdir()
                lock.mkdir()
            except OSError:
                return None
        try:
            stamp.touch()
            pushed = pulled = 0
            reachable = True
            qdir = self.home / "outbox" / host
            for f in sorted(qdir.rglob("msg-*.json")) if qdir.is_dir() else []:
                agent, slug = self._da_rel(str(f.relative_to(qdir)))
                if self.consegna(host, agent, slug, f.read_bytes()):
                    f.unlink()
                    pushed += 1
                else:
                    reachable = False
                    break
            me = self.amap.host_self()
            if reachable and me:
                got = self._pull(host, me)
                if got is None:
                    reachable = False
                else:
                    pulled = got
            queued = len(list(qdir.rglob("msg-*.json"))) if qdir.is_dir() else 0
            res = {"host": host, "at": iso_now(), "status": "ok" if reachable else "unreachable",
                   "pushed": pushed, "pulled": pulled, "queued": queued}
            atomic_write(state / "last-collect.json", res)
            return res
        finally:
            try:
                lock.rmdir()
            except OSError:
                pass

    def _pull(self, host: str, me: str) -> int | None:
        q = shlex.quote(f"{self.amap.host_bus_home(host)}/outbox/{me}")
        res = self.ssh(host, f"cd {q} 2>/dev/null || exit 0; find . -name 'msg-*.json' -type f | tar -cf - -T -")
        if res is None or res.returncode != 0:
            return None
        pulled = 0
        done: list[str] = []
        if res.stdout:
            try:
                tar = tarfile.open(fileobj=io.BytesIO(res.stdout))
                members = [m for m in tar.getmembers() if m.isfile()]
            except tarfile.TarError:
                return 0
            for m in sorted(members, key=lambda m: m.name):
                rel = m.name.removeprefix("./")
                parts = rel.split("/")
                if ".." in parts or not (2 <= len(parts) <= 3) or not parts[-1].startswith("msg-"):
                    continue
                try:
                    record = json.loads(tar.extractfile(m).read())
                except json.JSONDecodeError:
                    continue
                if not isinstance(record, dict) or not self.amap.agent_exists(parts[0]):
                    continue
                agent, slug = self._da_rel(rel)
                if len(parts) == 3 and slug is None:
                    continue
                esito = self.ricevi(agent, slug, Path(parts[-1]).stem, record)
                if esito is None:
                    continue  # non scritto: resta sull'host e si riprova
                done.append("./" + rel)
                if esito:
                    pulled += 1
        if done:
            self.ssh(host, f"cd {q} && xargs rm -f", ("\n".join(done) + "\n").encode())
        return pulled

    def remote_status(self, host: str, force: bool = False) -> dict:
        cache = self._host_state(host) / "status.json"
        prev = read_json(cache)
        if not force and prev is not None and self._age(cache) < REMOTE_STATUS_TTL:
            return prev
        q = shlex.quote(self.amap.host_bus_home(host))
        cmd = (
            f"cd {q} 2>/dev/null || exit 0\n"
            "find registry -path 'registry/*.d/*.json' -type f 2>/dev/null | while read -r f; do "
            "printf 'R\\t'; tr '\\n' ' ' < \"$f\"; echo; done\n"
            f"{(self.hosts().get(host) or {}).get('centralino') or 'false'} pendenti | awk '{{print \"I\\t\" $0}}'"
        )
        res = self.ssh(host, cmd)
        if res is not None and res.returncode == 0:
            sessions, pending = [], []
            for line in res.stdout.decode("utf-8", "replace").splitlines():
                if line.startswith("R\t"):
                    try:
                        d = json.loads(line[2:])
                        if isinstance(d, dict):
                            sessions.append(d)
                    except json.JSONDecodeError:
                        pass
                elif line.startswith("I\t"):
                    pending.append(line[2:])
            data = {"host": host, "fetched_at": iso_now(), "ok": True, "sessions": sessions, "pending": pending}
        else:
            data = dict(prev or {"host": host, "fetched_at": None, "sessions": [], "pending": []})
            data.update({"ok": False, "error": "unreachable", "checked_at": iso_now()})
        atomic_write(cache, data)
        return data

    def remote_sessions(self, host: str) -> list[dict]:
        snap = self.remote_status(host)
        adesso = now()
        out = []
        for s in snap.get("sessions", []):
            name, slug = s.get("name"), s.get("slug")
            if not name or not slug:
                continue
            ts = parse_iso(s.get("last_seen"))
            if ts is not None and (adesso - ts) > timedelta(minutes=SESSION_TTL_MIN):
                continue
            root, own = f"inboxes/{name}/", f"inboxes/{name}/@{slug}/"
            pend = sum(1 for p in snap.get("pending", [])
                       if p.startswith(own) or (p.startswith(root) and "/" not in p[len(root):]))
            out.append({**s, "host": host, "pending": pend})
        out.sort(key=lambda d: (d["name"], d.get("last_seen") or ""))
        return out
