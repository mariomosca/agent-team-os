"""I file del bus nel formato di oggi (modalità ombra, BR24-F3.4).

Porting di ``agent_bus_mcp/bus.py`` di main dopo il passo 0 (BR24-F2: lock sulle attese,
proprietà per sessione con passaggio, chiusura per task_id), con due differenze volute:

- i tmp hanno un nome unico (``.<nome>.<pid>.<rand>.tmp``), così due scrittori non si
  pestano il tmp (INVENTARIO §2 punto 1);
- la consegna in casella prende il lock ``locks/<agente>.lock`` della lib.

Ogni scrittura avvisa ``self.osservatore`` (``message``/``wait``), che il centralino usa
per registrare lo stesso cambio in SQLite nella stessa transazione.
"""

from __future__ import annotations

import fcntl
import io
import json
import os
import random
import re
import shlex
import string
import subprocess
import tarfile
import time
from datetime import timedelta
from pathlib import Path
from typing import Protocol

from .indirizzi import AgentMap, ErroreIndirizzo, parse_addr
from .tempo import iso_now, now, parse_iso

DECLARED_TYPES = ("request", "brief", "handoff")
KINDS = ("delega", "comunicazione")
KIND_HELP = (
    "say what this {type} is with kind: 'delega' if you want the outcome back (the bus "
    "registers the wait and wakes you on every return), 'comunicazione' if nothing "
    "has to come back. There is no default."
)
ESITI = ("chiuso", "riarma", "risposto", "letto")
OPEN_WAIT_STATES = ("expected",)

WAKE_CHAIN_MIN = int(os.environ.get("AB_WAKE_CHAIN_MIN", "30"))
SESSION_TTL_MIN = int(os.environ.get("AB_SESSION_TTL_MIN", "240"))
SSH_BIN = os.environ.get("AB_SSH_BIN", "ssh")
SSH_TIMEOUT = int(os.environ.get("AB_SSH_TIMEOUT", "5"))
COLLECT_INTERVAL = int(os.environ.get("AB_COLLECT_INTERVAL", "30"))
REMOTE_STATUS_TTL = int(os.environ.get("AB_REMOTE_STATUS_TTL", "20"))

TASK_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{2,63}$")


class WaitsLock:
    """BR24-F2.4: le attese hanno più scrittori (centralino, giro di wake.py, sua chiudi):
    ognuno rilegge e scrive dentro lo stesso flock su locks/waits.flock (stesso nome in
    agent-team-os-wake.py e agent-bus-mcp)."""

    def __init__(self, home: Path) -> None:
        self.path = Path(home) / "locks" / "waits.flock"

    def __enter__(self) -> "WaitsLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.fh = self.path.open("a")
        fcntl.flock(self.fh, fcntl.LOCK_EX)
        return self

    def __exit__(self, *exc) -> None:
        fcntl.flock(self.fh, fcntl.LOCK_UN)
        self.fh.close()


def with_pending_wake(rec: dict) -> dict:
    """Il motivo dell'ultima sveglia rinviata ancora aperta, solo su un'attesa aperta."""
    why = [e["pending_wake"] for e in (rec.get("returns") or {}).values()
           if isinstance(e, dict) and e.get("state") == "pending" and e.get("pending_wake")]
    if why and rec.get("state") == "expected":
        rec["pending_wake"] = max(why, key=lambda w: w.get("checked_at") or "")
    else:
        rec.pop("pending_wake", None)
    return rec


class BusError(RuntimeError):
    """Rifiuto che tocca al chiamante correggere. ``code`` è il codice d'errore del contratto."""

    def __init__(self, message: str, code: str = "bad_request"):
        super().__init__(message)
        self.code = code


def _rand(n: int) -> str:
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=n))


def msg_id() -> str:
    return f"msg-{now().strftime('%Y%m%dT%H%M%SZ')}-{_rand(8)}"


def thread_id() -> str:
    return f"thread-{now().strftime('%Y%m%d')}-{_rand(6)}"


def new_task_id() -> str:
    return f"t-{now().strftime('%Y%m%d')}-{_rand(6)}"


def read_json(path: Path) -> dict | None:
    try:
        with path.open(encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def atomic_write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{_rand(6)}.tmp")
    try:
        with tmp.open("w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
        tmp.replace(path)
    finally:
        tmp.unlink(missing_ok=True)


def location_of(path: Path, home: Path) -> tuple[str, str | None, str] | None:
    """(agente, slug o None, stato) di un file messaggio sotto inboxes/."""
    try:
        parts = path.relative_to(home / "inboxes").parts
    except ValueError:
        return None
    if len(parts) < 2 or not parts[-1].startswith("msg-"):
        return None
    agent, rest = parts[0], list(parts[1:-1])
    state = "pending"
    if rest and rest[-1] in (".read", ".done"):
        state = rest.pop()[1:]
    if not rest:
        return agent, None, state
    if len(rest) == 1 and rest[0].startswith("@"):
        return agent, rest[0][1:], state
    return None


class Osservatore(Protocol):
    def message(self, record: dict, *, path: Path | None, state: str, to_slug: str | None,
                to_host: str, delivery: str) -> None: ...

    def wait(self, rec: dict) -> None: ...


class _Muto:
    def message(self, record, **kw):  # noqa: D401
        pass

    def wait(self, rec):
        pass


class FileBus:
    def __init__(self, home: Path, amap: AgentMap | None = None):
        self.home = Path(home)
        self.amap = amap or AgentMap(self.home)
        self.osservatore: Osservatore = _Muto()
        # sessione viva secondo la regola unica (il centralino la imposta); serve al passaggio
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

    # ---------- lock della lib ----------

    def _lock(self, agent: str) -> Path | None:
        d = self.home / "locks" / f"{agent}.lock"
        d.parent.mkdir(parents=True, exist_ok=True)
        for _ in range(40):
            try:
                d.mkdir()
                return d
            except FileExistsError:
                try:
                    if time.time() - d.stat().st_mtime > 30:  # lock lasciato da un processo morto
                        d.rmdir()
                        continue
                except OSError:
                    pass
                time.sleep(0.05)
        return None  # come la lib dopo 20 tentativi: si scrive lo stesso, il rename resta atomico

    @staticmethod
    def _unlock(d: Path | None) -> None:
        if d is not None:
            try:
                d.rmdir()
            except OSError:
                pass

    # ---------- SSH e altri host (v2.5) ----------

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

    def remote_put(self, host: str, rel: str, data: bytes) -> bool:
        bh = self.amap.host_bus_home(host)
        target = f"{bh}/{rel}"
        d = os.path.dirname(target)
        dirs = [shlex.quote(d)]
        if os.path.basename(d).startswith("@"):
            dirs += [shlex.quote(f"{d}/.read"), shlex.quote(f"{d}/.done")]
        q = shlex.quote(target)
        res = self.ssh(host, f"mkdir -p {' '.join(dirs)} && cat > {q}.tmp && mv {q}.tmp {q}", data)
        return res is not None and res.returncode == 0

    def queue_for_host(self, host: str, rel: str, data: bytes) -> Path:
        dest = self.home / "outbox" / host / rel.removeprefix("inboxes/")
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_name(f".{dest.name}.{os.getpid()}.{_rand(6)}.tmp")
        tmp.write_bytes(data)
        tmp.replace(dest)
        return dest

    def deliver_remote(self, host: str, agent: str, slug: str | None, record: dict) -> str:
        rel = f"inboxes/{agent}" + (f"/@{slug}" if slug else "") + f"/{record['id']}.json"
        data = (json.dumps(record, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        if (self.hosts().get(host) or {}).get("ssh") and self.remote_put(host, rel, data):
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
        lock = self.home / "locks" / f"collect-{host}.lock"  # stesso lock della lib
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
                rel = "inboxes/" + str(f.relative_to(qdir))
                if self.remote_put(host, rel, f.read_bytes()):
                    f.unlink()
                    pushed += 1
                else:
                    reachable = False
                    break
            me = self.host_self()
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
                raw = tar.extractfile(m).read()
                try:
                    record = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if not isinstance(record, dict) or not self.agent_exists(parts[0]):
                    continue
                agent = parts[0]
                slug = parts[1][1:] if len(parts) == 3 and parts[1].startswith("@") else None
                if len(parts) == 3 and slug is None:
                    continue
                esito = self._ricevi(agent, slug, Path(parts[-1]).stem, record)
                if esito is None:
                    continue  # non scritto: resta sull'host e si riprova
                done.append("./" + rel)
                if esito:
                    pulled += 1
        if done:
            self.ssh(host, f"cd {q} && xargs rm -f", ("\n".join(done) + "\n").encode())
        return pulled

    def _ricevi(self, agent: str, slug: str | None, mid: str, record: dict) -> bool | None:
        """Un messaggio arrivato da un altro host. True = scritto, False = c'era già,
        None = non scritto (resta sull'host)."""
        dest = self.home / "inboxes" / agent / (f"@{slug}" if slug else "") / f"{mid}.json"
        if any(p.exists() for p in (dest, dest.parent / ".read" / dest.name, dest.parent / ".done" / dest.name)):
            return False
        if slug:
            (dest.parent / ".read").mkdir(parents=True, exist_ok=True)
            (dest.parent / ".done").mkdir(parents=True, exist_ok=True)
        atomic_write(dest, record)
        if read_json(dest) is None:
            return None
        self._append_thread(record)
        self.osservatore.message(record, path=dest, state="pending", to_slug=slug,
                                 to_host=self.host_self() or "", delivery="local")
        return True

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
            "find inboxes -name 'msg-*.json' -type f -not -path '*/.read/*' -not -path '*/.done/*' "
            "2>/dev/null | awk '{print \"I\\t\" $0}'"
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

    def routing_denied(self, sender: str, recipient: str) -> str | None:
        rules = (self.amap.data().get("routing_rules") or {}).get(sender, {})
        if recipient in (rules.get("deny") or []):
            return rules.get("reason", "isolation rule")
        return None

    # ---------- registro (lettura, e waiting_agent) ----------

    def registry_dir(self, agent: str) -> Path:
        return self.home / "registry" / f"{agent}.d"

    def registry_sessions(self, agent: str) -> list[dict]:
        """Voci del registro di oggi, con ``stale`` secondo il TTL della lib (per il confronto)."""
        out = []
        adesso = now()
        for entry in sorted(self.registry_dir(agent).glob("*.json")):
            data = read_json(entry)
            if not data or not data.get("slug"):
                continue
            ts = parse_iso(data.get("last_seen"))
            data["stale"] = ts is not None and (adesso - ts) > timedelta(minutes=SESSION_TTL_MIN)
            out.append(data)
        return out

    def set_waiting_agent(self, agent: str, slug: str, info: dict) -> None:
        f = self.registry_dir(agent) / f"{slug}.json"
        rec = read_json(f)
        if rec is None:
            return
        rec["waiting_agent"] = info
        atomic_write(f, rec)

    def clear_waiting_agent(self, agent: str, slug: str, task_id: str | None = None,
                            answer: dict | None = None) -> bool:
        f = self.registry_dir(agent) / f"{slug}.json"
        rec = read_json(f)
        w = (rec or {}).get("waiting_agent")
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
        rec.pop("waiting_agent", None)
        atomic_write(f, rec)
        return True

    # ---------- caselle ----------

    def inbox_root(self, agent: str) -> Path:
        return self.home / "inboxes" / agent

    def session_dir(self, agent: str, slug: str) -> Path:
        return self.inbox_root(agent) / f"@{slug}"

    def list_inbox(self, agent: str, slug: str | None = None, all_sessions: bool = False) -> list[Path]:
        root = self.inbox_root(agent)
        if not root.is_dir():
            return []
        files = list(root.glob("msg-*.json"))
        if slug and not all_sessions:
            files += list(self.session_dir(agent, slug).glob("msg-*.json"))
        else:
            for sub in root.iterdir():
                if sub.is_dir() and sub.name.startswith("@"):
                    files += list(sub.glob("msg-*.json"))
        return sorted(files)

    def count_inbox(self, agent: str, slug: str | None = None, all_sessions: bool = False) -> int:
        return len(self.list_inbox(agent, slug, all_sessions))

    def inbox_items(self, agent: str, slug: str | None = None, all_sessions: bool = False) -> list[tuple[str, str, dict]]:
        """(id, scope, record) dei messaggi pending: scope = slug della casella o 'all-sessions'."""
        out = []
        for p in self.list_inbox(agent, slug, all_sessions):
            data = read_json(p)
            if data is None:
                continue
            loc = location_of(p, self.home)
            out.append((p.stem, loc[1] if loc and loc[1] else "all-sessions", data))
        return out

    def broadcast_pending(self, agent: str) -> int:
        root = self.inbox_root(agent)
        return len(list(root.glob("msg-*.json"))) if root.is_dir() else 0

    def wait_owner(self, task_id: str) -> tuple[str, str] | None:
        p = self.find_wait(task_id) if task_id else None
        return (p.parent.parent.name, p.parent.name[1:]) if p else None

    def resolve_message(self, agent: str, partial_id: str) -> Path | None:
        root = self.inbox_root(agent)
        if not root.is_dir() or not partial_id:
            return None
        pending = [p for p in self.list_inbox(agent, all_sessions=True) if p.name.startswith(partial_id)]
        if pending:
            return sorted(pending)[0]
        archived = [p for p in root.rglob(f"{partial_id}*.json")
                    if any(part in (".read", ".done") for part in p.parts)]
        return sorted(archived)[0] if archived else None

    def read_message(self, agent: str, partial_id: str) -> dict:
        path = self.resolve_message(agent, partial_id)
        if path is None:
            raise BusError(f"no message matching '{partial_id}' in {agent}'s inbox", "not_found")
        data = read_json(path)
        if data is None:
            raise BusError(f"{path.name} is not readable JSON", "internal")
        holder = path.parent.name
        if holder in (".read", ".done"):
            holder = path.parent.parent.name
        data["_location"] = {
            "path": str(path),
            "scope": holder[1:] if holder.startswith("@") else "all-sessions",
            "state": "archived" if path.parent.name in (".read", ".done") else "pending",
        }
        return data

    def archive_message(self, agent: str, partial_id: str, state: str = "done") -> Path:
        if state not in ("read", "done"):
            raise BusError("state must be 'read' or 'done'")
        path = self.resolve_message(agent, partial_id)
        if path is None:
            raise BusError(f"no message matching '{partial_id}' in {agent}'s inbox", "not_found")
        if path.parent.name in (".read", ".done"):
            return path
        dest_dir = path.parent / f".{state}"
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / path.name
        path.rename(dest)
        record = read_json(dest)
        if record:
            loc = location_of(dest, self.home)
            self.osservatore.message(record, path=dest, state=state, to_slug=loc[1] if loc else None,
                                     to_host=self.host_self() or "", delivery="local")
        return dest

    # ---------- invio ----------

    def send(self, sender: str, to: str, msg_type: str, intent: str, payload: dict,
             priority: str = "normal", thread: str | None = None, in_reply_to: str | None = None,
             context_refs: list[str] | None = None, requires_response: bool = False,
             response_by: str | None = None, sender_session: str | None = None,
             task_id: str | None = None, kind: str | None = None, target: dict | None = None,
             live_slugs=None) -> dict:
        """``live_slugs(agent) -> list[str] | None``: sessioni vive per la regola unica (None = ignoto)."""
        agent, slug, want_host = parse_addr(to)
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
            if chain := self.wake_chain(sender, sender_session):
                record["wake_chain"] = chain
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

        try:
            out = self._deliver(record, sender, agent, slug, to_host, warning)
        except Exception:
            if wait:
                self.wait_dir(sender, sender_session).joinpath(f"{task_id}.json").unlink(missing_ok=True)
            raise
        if wait:
            out["expecting"] = {"task_id": task_id, "deadline": wait["deadline"]}
            out["note"] = (f"attesa registrata {task_id}: ogni ritorno di {to} su questo task "
                           "ti sveglia se sei fermo")
        return out

    def _deliver(self, record: dict, sender: str, agent: str, slug: str | None,
                 to_host: str | None, warning: str | None) -> dict:
        mid, task_id = record["id"], record.get("task_id")
        if to_host:
            outcome = self.deliver_remote(to_host, agent, slug, record)
            self._append_thread(record)
            self._append_outbox(sender, record, slug)
            self.osservatore.message(record, path=None, state="pending", to_slug=slug, to_host=to_host,
                                     delivery="queued" if outcome == "queued" else "remote")
            addr = f"{agent}@{to_host}" + (f"/{slug}" if slug else "")
            if outcome == "queued":
                if (self.hosts().get(to_host) or {}).get("ssh"):
                    warning = (f"{to_host} unreachable now: queued in outbox/{to_host}/, "
                               "delivered at the next collection.")
                else:
                    warning = (f"queued in outbox/{to_host}/: {to_host} collects it "
                               "at its next round (it cannot be reached from here).")
            return {"id": mid, "delivered_to": addr, "host": to_host, "queued": outcome == "queued",
                    "path": None, "warning": warning, "task_id": task_id}

        if slug:
            target_dir = self.session_dir(agent, slug)
            (target_dir / ".read").mkdir(parents=True, exist_ok=True)
            (target_dir / ".done").mkdir(parents=True, exist_ok=True)
        else:
            target_dir = self.inbox_root(agent)
            target_dir.mkdir(parents=True, exist_ok=True)
        dest = target_dir / f"{mid}.json"
        lock = self._lock(agent)
        try:
            atomic_write(dest, record)
        finally:
            self._unlock(lock)
        self._append_thread(record)
        self._append_outbox(sender, record, slug)
        self.osservatore.message(record, path=dest, state="pending", to_slug=slug,
                                 to_host=self.host_self() or "", delivery="local")
        return {"id": mid, "delivered_to": f"{agent}/{slug}" if slug else agent,
                "host": self.host_self(), "queued": False, "path": str(dest), "warning": warning,
                "task_id": task_id}

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

    # ---------- attese ----------

    def waits_root(self) -> Path:
        return self.home / "waits"

    def wait_dir(self, agent: str, slug: str) -> Path:
        return self.waits_root() / agent / f"@{slug}"

    def find_wait(self, task_id: str) -> Path | None:
        root = self.waits_root()
        if not root.is_dir():
            return None
        return next(iter(root.glob(f"*/@*/{task_id}.json")), None)

    def expect(self, agent: str, slug: str, description: str, delegated_to: str | None = None,
               deadline: str | None = None, task_id: str | None = None,
               target: dict | None = None, delegation: dict | None = None) -> dict:
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
        with WaitsLock(self.home):
            if (existing := self.find_wait(task_id)) is not None:
                raise BusError(f"task {task_id} already has a waiter "
                               f"({existing.parent.parent.name}/{existing.parent.name[1:]}): one task, one waiter",
                               "conflict")
            atomic_write(self.wait_dir(agent, slug) / f"{task_id}.json", rec)
        self.osservatore.wait(rec)
        return rec

    def list_waits(self, agent: str, slug: str | None = None, include_closed: bool = False) -> list[dict]:
        root = self.waits_root()
        if not root.is_dir():
            return []
        out = []
        for p in sorted(root.glob(f"{agent}/@{slug or '*'}/*.json")):
            if p.name.startswith("."):
                continue
            rec = read_json(p)
            if not rec or not rec.get("task_id"):
                continue
            if not include_closed and rec.get("state") not in ("expected", "expired"):
                continue
            out.append(rec)
        return out

    def wake_chain(self, agent: str, slug: str) -> dict | None:
        rec = read_json(self.wait_dir(agent, slug) / ".woken.json")
        if not rec or not rec.get("at"):
            return None
        at = parse_iso(rec["at"])
        if at is None or now() - at > timedelta(minutes=WAKE_CHAIN_MIN):
            return None
        return {"task_id": rec.get("task_id"), "at": rec["at"], "session": f"{agent}/{slug}"}

    def task_of(self, agent: str, message_id: str) -> str | None:
        path = self.resolve_message(agent, message_id)
        data = read_json(path) if path else None
        tid = (data or {}).get("task_id") or ((data or {}).get("payload") or {}).get("task_id")
        return tid if isinstance(tid, str) else None

    def own_wait(self, agent: str, task_id: str | None, slug: str | None = None) -> tuple[Path, dict] | None:
        """L'attesa di task_id se l'ha registrata questa sessione (BR24-F2.6, per sessione).
        Un'altra sessione dello stesso agente la riceve solo se quella che ha delegato non è
        più viva. Senza slug: qualunque sessione dell'agente (chiusura esplicita per task_id)."""
        if not task_id:
            return None
        p = self.find_wait(task_id)
        if p is None or p.parent.parent.name != agent:
            return None
        if slug and p.parent.name != f"@{slug}" and self.session_live(agent, p.parent.name[1:]):
            return None
        rec = read_json(p)
        return (p, rec) if rec else None

    def handle(self, agent: str, slug: str, message_id: str, esito: str,
               task_id: str | None = None, reply: str | None = None, live_slugs=None,
               reply_type: str = "response", details: dict | None = None) -> dict:
        if esito not in ESITI:
            raise BusError(f"esito must be one of {', '.join(ESITI)}, not {esito!r}")
        path = self.resolve_message(agent, message_id)
        if path is None:
            raise BusError(f"no message matching '{message_id}' in {agent}'s inbox", "not_found")
        if path.parent.name in (".read", ".done"):
            raise BusError(f"{path.stem} is already filed ({path.parent.name[1:]}): nothing to handle",
                           "already_filed")
        data = read_json(path)
        if data is None:
            raise BusError(f"{path.name} is not readable JSON", "internal")
        mid = data.get("id") or path.stem

        link = data.get("task_link") or {}
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
            sent = self.send(sender=agent, to=self.reply_address(data), msg_type=reply_type,
                             intent=f"re-{data.get('intent') or 'risposta'}",
                             payload={"summary": reply.strip(), **(details or {})},
                             thread=data.get("thread_id"), in_reply_to=mid, sender_session=slug, task_id=tid,
                             live_slugs=live_slugs)
            out["sent"] = {"id": sent["id"], "to": sent["delivered_to"], "queued": sent.get("queued")}

        now = iso_now()
        data["handled"] = {"esito": esito, "at": now, "by": f"{agent}/{slug}"}
        dest_dir = path.parent / (".read" if esito == "letto" else ".done")
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / path.name
        atomic_write(dest, data)
        path.unlink(missing_ok=True)
        out["archived_to"] = str(dest)
        loc = location_of(dest, self.home)
        self.osservatore.message(data, path=dest, state=dest_dir.name[1:], to_slug=loc[1] if loc else None,
                                 to_host=self.host_self() or "", delivery="local")
        self.clear_waiting_agent(agent, slug, answer=data)

        if own is not None:
            wpath = own[0]
            with WaitsLock(self.home):  # si rilegge dentro il lock: il giro della sveglia può averla appena scritta
                rec = read_json(wpath) or own[1]
                e = (rec.setdefault("returns", {})).setdefault(mid, {
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
                atomic_write(wpath, with_pending_wake(rec))
            self.osservatore.wait(rec)
            out["wait"] = {"task_id": rec["task_id"], "state": rec["state"]}
        return out

    def close_wait(self, agent: str, slug: str, task_id: str, note: str | None = None) -> dict:
        """BR24-F2.3: chiude l'attesa di un task dell'agente senza un messaggio da gestire
        (ritorni già archiviati, delega di una sessione precedente). I ritorni ancora in casella
        vanno in .done con esito chiuso e sono elencati nel risultato. Come wake.py chiudi."""
        if not TASK_ID_RE.match(task_id or ""):
            raise BusError(f"invalid task_id {task_id!r}")
        own = self.own_wait(agent, task_id)
        if own is None:
            raise BusError(f"task {task_id} is not a delegation of {agent}: no wait to close", "not_found")
        wpath = own[0]
        now = iso_now()
        filed: list[dict] = []
        base = self.inbox_root(agent)
        for d in (base / f"@{slug}", base / wpath.parent.name, base):
            for p in sorted(d.glob("msg-*.json")) if d.is_dir() else []:
                m = read_json(p)
                if not m or (m.get("task_id") or (m.get("payload") or {}).get("task_id")) != task_id \
                        or m.get("kind") == "delega":
                    continue
                m["handled"] = {"esito": "chiuso", "at": now, "by": f"{agent}/{slug}", "via": "close"}
                dest = p.parent / ".done" / p.name
                atomic_write(dest, m)
                p.unlink(missing_ok=True)
                loc = location_of(dest, self.home)
                self.osservatore.message(m, path=dest, state="done", to_slug=loc[1] if loc else None,
                                         to_host=self.host_self() or "", delivery="local")
                filed.append({"id": m.get("id") or p.stem, "type": m.get("type"),
                              "summary": str((m.get("payload") or {}).get("summary") or "")[:200]})
        with WaitsLock(self.home):
            rec = read_json(wpath) or own[1]
            prev = rec.get("state")
            for f in filed:
                e = (rec.setdefault("returns", {})).setdefault(f["id"], {"type": f["type"], "at": now})
                e.update(state="handled", handled="chiuso", handled_at=now)
                e.pop("pending_wake", None)
            if prev in OPEN_WAIT_STATES or prev == "expired":
                rec.update(state="closed", closed_at=now, closed_by=f"close:{agent}/{slug}")
                if note:
                    rec["note"] = note
            atomic_write(wpath, with_pending_wake(rec))
        self.osservatore.wait(rec)
        self.clear_waiting_agent(agent, slug, task_id=task_id)
        return {"task_id": task_id, "state": rec["state"], "was": prev, "filed": filed}

    def reply_address(self, record: dict) -> str:
        sender = record.get("from") or ""
        fs = record.get("from_session") or ""
        host = (record.get("context") or {}).get("host")
        slug = fs.partition("/")[2] if "/" in fs else ""
        if host and host != self.host_self():
            return f"{sender}@{host}" + (f"/{slug}" if slug else "")
        return f"{sender}/{slug}" if slug else sender

    # ---------- thread e log ----------

    def thread(self, tid: str) -> list[dict]:
        """Unisce i due formati di oggi: ``.jsonl`` (MCP, sync) e ``.json`` (lib)."""
        out: dict[str, dict] = {}
        f = self.home / "threads" / f"{tid}.jsonl"
        if f.is_file():
            for line in f.read_text(encoding="utf-8").splitlines():
                try:
                    e = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(e, dict):
                    out.setdefault(e.get("id") or f"_{len(out)}", e)
        legacy = read_json(self.home / "threads" / f"{tid}.json")
        for m in (legacy or {}).get("messages") or []:
            if isinstance(m, dict) and m.get("id") not in out:
                out[m.get("id") or f"_{len(out)}"] = {k: m.get(k) for k in
                                                     ("id", "from", "to", "type", "intent", "ts") if m.get(k)}
        return sorted(out.values(), key=lambda e: e.get("ts") or "")

    def _append_thread(self, record: dict) -> None:
        d = self.home / "threads"
        d.mkdir(parents=True, exist_ok=True)
        entry = {k: record[k] for k in ("id", "from", "to", "type", "intent", "ts", "in_reply_to", "task_id")
                 if record.get(k) is not None}
        entry["summary"] = (record.get("payload") or {}).get("summary", "")
        with (d / f"{record['thread_id']}.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def _append_outbox(self, sender: str, record: dict, slug: str | None) -> None:
        d = self.home / "outbox"
        d.mkdir(parents=True, exist_ok=True)
        entry = {"id": record["id"], "to": record["to"], "slug": slug,
                 "type": record["type"], "intent": record["intent"], "ts": record["ts"]}
        with (d / f"{sender}.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
