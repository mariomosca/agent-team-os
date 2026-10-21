#!/usr/bin/env python3
"""agent-team-os-wake.py — esiti attesi e sveglia della sessione idle (bus v2.6).

Chi delega con un task_id registra un *atteso* sulla propria sessione:

    $AB_HOME/waits/<agent>/@<slug>/<task_id>.json

Quando nella casella di quella sessione arriva un messaggio con lo stesso
task_id, questo script sveglia la sessione, una volta sola, scrivendo nel suo
prompt "Esito del task ... arrivato da ...: leggilo con bus_read ... e prosegui".

Regole (brief F4, 17 ott 2026; domande da DR18-F1.7, bus v2.7):
  1. si sveglia solo una sessione idle: mai mentre lavora, chiede qualcosa,
     o ha testo nella casella (Mario sta scrivendo);
  2. una sola sveglia per messaggio atteso: ogni question con il task_id
     sveglia una volta (questions[msg_id], l'atteso resta expected), poi l'esito
     sveglia una volta (expected -> woken, e il file non torna indietro);
  3. niente catene: un messaggio scritto da una sessione appena svegliata
     (campo wake_chain, timbrato da chi spedisce) non sveglia nessuno; un
     task_id ha un solo atteso su tutto il bus; tetto di sveglie per sessione
     all'ora;
  4. scadenza senza esito: un avviso nella casella di chi aspetta, una volta.

Canali per scrivere nel prompt:
  - Onda: socket JSON-RPC `agent.prompt` con onlyIfIdle (Onda rifiuta se la
    sessione lavora, chiede o ha testo). Serve un Onda che scriva `hasText` in
    sessions.json: senza, la guardia non c'e' e la sveglia aspetta.
  - tmux (Mac mini, sessioni fuori da Onda): capture-pane dello schermo, stessa
    lettura della casella che fa Onda, poi send-keys.

Gira con il python3 di sistema (3.9), solo stdlib. launchd lo lancia ogni 10 s
(install.sh --sveglia); chi spedisce un esito lo lancia subito in background.

    agent-team-os-wake.py [tick]                      un giro (e ogni 10 min la pulizia del registro)
    agent-team-os-wake.py expect --agent A --slug S --description D [--to ADDR]
                          [--deadline ISO] [--task-id ID]       stampa il task_id
    agent-team-os-wake.py list [--agent A [--slug S]] [--all]   JSON
"""

from __future__ import annotations

import argparse
import json
import os
import random
import re
import socket
import string
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

WAIT_VERSION = 1
MAX_PER_HOUR = int(os.environ.get("AB_WAKE_MAX_PER_HOUR", "6"))
CHAIN_MIN = int(os.environ.get("AB_WAKE_CHAIN_MIN", "30"))
# Una sessione appena tornata idle puo' ancora star disegnando: aspetta un attimo.
SETTLE_S = float(os.environ.get("AB_WAKE_SETTLE_S", "5"))
# sessions.json si riscrive almeno ogni 15 s: oltre questo Onda non lo sta aggiornando.
SESSIONS_FRESH_S = float(os.environ.get("AB_WAKE_SESSIONS_FRESH_S", "60"))
SOCKET_TIMEOUT_S = 20.0
TERMINAL_STATES = ("woken", "seen", "blocked", "expired", "cancelled")


def bus_home() -> Path:
    return Path(os.environ.get("AB_HOME") or Path.home() / ".agent-team-os")


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(s: str | None) -> float | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def read_json(p: Path) -> dict | None:
    try:
        with p.open(encoding="utf-8") as fh:
            d = json.load(fh)
        return d if isinstance(d, dict) else None
    except (OSError, ValueError):
        return None


def write_json(p: Path, data: dict) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    tmp.replace(p)


def log(msg: str) -> None:
    d = bus_home() / "logs"
    try:
        d.mkdir(parents=True, exist_ok=True)
        with (d / "sveglia.log").open("a", encoding="utf-8") as fh:
            fh.write(f"{now_iso()} {msg}\n")
    except OSError:
        pass


def host_self() -> str | None:
    env = os.environ.get("AB_HOST", "").strip()
    if env:
        return env
    try:
        name = (bus_home() / "HOST").read_text(encoding="utf-8").strip().splitlines()[0].strip()
        if name:
            return name
    except (OSError, IndexError):
        pass
    amap = read_json(bus_home() / "AGENT_MAP.json") or {}
    for name, conf in (amap.get("hosts") or {}).items():
        if (conf or {}).get("hub") is True:
            return name
    return None


# ---------------------------------------------------------------- registro

def waits_root() -> Path:
    return bus_home() / "waits"


def wait_dir(agent: str, slug: str) -> Path:
    return waits_root() / agent / f"@{slug}"


def new_task_id() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    rand = "".join(random.choices(string.ascii_lowercase + string.digits, k=6))
    return f"t-{stamp}-{rand}"


TASK_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{2,63}$")


def find_wait(task_id: str) -> Path | None:
    root = waits_root()
    if not root.is_dir():
        return None
    for p in root.glob(f"*/@*/{task_id}.json"):
        return p
    return None


def expect(agent: str, slug: str, description: str, to: str | None = None,
           deadline: str | None = None, task_id: str | None = None,
           target: dict | None = None, delegation: dict | None = None) -> dict:
    """Registra un atteso. Un task_id ha un solo atteso su tutto il bus."""
    task_id = task_id or new_task_id()
    if not TASK_ID_RE.match(task_id):
        raise ValueError(f"task_id non valido: {task_id!r}")
    if deadline and parse_iso(deadline) is None:
        raise ValueError(f"deadline non ISO 8601: {deadline!r}")
    existing = find_wait(task_id)
    if existing is not None:
        raise ValueError(f"il task {task_id} ha gia' un atteso ({existing.parent.parent.name}/"
                         f"{existing.parent.name[1:]}): un task, un solo atteso")
    rec = {
        "v": WAIT_VERSION,
        "task_id": task_id,
        "agent": agent,
        "slug": slug,
        "host": host_self(),
        "description": description.strip()[:200],
        "delegated_to": to,
        "created_at": now_iso(),
        "deadline": deadline,
        "state": "expected",
        "target": {k: v for k, v in (target or {}).items() if v},
    }
    if delegation and any(delegation.values()):
        # BS21: messaggio e thread della delega, per agganciare i ritorni scritti come messaggi nuovi.
        rec["delegation"] = {k: v for k, v in delegation.items() if v}
    write_json(wait_dir(agent, slug) / f"{task_id}.json", rec)
    return rec


def list_waits(agent: str | None = None, slug: str | None = None, include_closed: bool = False) -> list[dict]:
    root = waits_root()
    if not root.is_dir():
        return []
    pattern = f"{agent or '*'}/@{slug or '*'}/*.json"
    out = []
    for p in sorted(root.glob(pattern)):
        if p.name.startswith("."):
            continue  # .woken.json: stato della sveglia, non un atteso
        rec = read_json(p)
        if not rec or not rec.get("task_id"):
            continue
        if not include_closed and rec.get("state") in TERMINAL_STATES and rec.get("state") != "expired":
            continue
        rec["_path"] = str(p)
        out.append(rec)
    return out


# ---------------------------------------------------------------- arrivi

def _msg_task_id(msg: dict) -> str | None:
    tid = msg.get("task_id")
    if not tid and isinstance(msg.get("payload"), dict):
        tid = msg["payload"].get("task_id")
    return tid if isinstance(tid, str) else None


def _is_question(msg: dict) -> bool:
    return msg.get("type") == "question"


def find_arrivals(rec: dict) -> list[tuple[Path, dict, str]]:
    """I messaggi con il task_id dell'atteso, dal piu' vecchio: (path, msg, 'pending'|'archived').

    Cerca nella casella della sessione e in quella condivisa dell'agente, archivi
    compresi: un messaggio che la sessione ha gia' letto da sola non va svegliato.
    Si guardano solo i file piu' recenti dell'atteso, cosi' .done/ lunghi costano poco.
    """
    agent, slug, tid = rec["agent"], rec["slug"], rec["task_id"]
    since = (parse_iso(rec.get("created_at")) or 0) - 120
    base = bus_home() / "inboxes" / agent
    own = f"{agent}/{slug}"
    hits = []
    for d in (base / f"@{slug}", base):
        for sub, state in (("", "pending"), (".read", "archived"), (".done", "archived")):
            dd = d / sub if sub else d
            if not dd.is_dir():
                continue
            for p in dd.glob("msg-*.json"):
                try:
                    if p.stat().st_mtime < since:
                        continue
                except OSError:
                    continue
                msg = read_json(p)
                if not msg or _msg_task_id(msg) != tid:
                    continue
                if msg.get("from_session") == own:
                    continue  # chi aspetta non si sveglia da solo
                hits.append((msg.get("ts") or "", p, msg, state))
    hits.sort(key=lambda h: (h[0], str(h[1])))
    return [(p, msg, state) for _, p, msg, state in hits]


def find_arrival(rec: dict) -> tuple[Path, dict, str] | None:
    """Il primo esito (non una question) con il task_id dell'atteso."""
    return next((h for h in find_arrivals(rec) if not _is_question(h[1])), None)


def answered(rec: dict, msg: dict) -> bool:
    """Chi aspetta ha gia' scritto nel thread della domanda, dopo la domanda."""
    tid = msg.get("thread_id")
    if not isinstance(tid, str) or "/" in tid:
        return False
    # Due formati: threads/<id>.jsonl (agent-bus-mcp) e threads/<id>.json (lib, .messages[]).
    entries: list = []
    try:
        for line in (bus_home() / "threads" / f"{tid}.jsonl").read_text(encoding="utf-8").splitlines():
            try:
                entries.append(json.loads(line))
            except ValueError:
                continue
    except OSError:
        pass
    entries += (read_json(bus_home() / "threads" / f"{tid}.json") or {}).get("messages") or []
    at = msg.get("ts") or ""
    return any(isinstance(e, dict) and e.get("from") == rec["agent"] and (e.get("ts") or "") > at
               and e.get("id") != msg.get("id") for e in entries)


# ---------------------------------------------------------------- schermo

BUSY_RE = re.compile(
    r"(esc to interrupt|ctrl\+c to interrupt|\b(?:Running|Contemplating|Crunching|Thinking|Working|"
    r"Generating|Pondering)\b[^\n]*…|…\s*\(\s*(?:\d+\s*s|↓))", re.I)
PASTED_RE = re.compile(r"\[Pasted text #\d+")
RULE_RE = re.compile(r"^[\s]*[─━\-═]{8,}\s*$")
PROMPT_RE = re.compile(r"^\s*[❯>][  ]?(.*)$")
MENU_ROW_RE = re.compile(r"^\s*\d+[.)]\s|^(?:yes|no)\b", re.I)
PLACEHOLDER_RE = re.compile(r"^try\s*[\"“]", re.I)
ASK_MARKERS = ("doyouwantto", "allowonce", "allowalways", "doyoutrustthe", "quicksafetycheck",
               "trustthisfolder", "entertoselect", "askuserquestion")


def read_prompt_box(screen: str) -> str | None:
    """Testo della casella del prompt, '' se vuota, None se non c'e' casella.

    Porting di readPromptBox (onda-electron shared/agent-input.ts): la casella e'
    la riga col glifo subito sotto una riga orizzontale.
    """
    lines = screen.split("\n")
    end = len(lines)
    while end > 0 and not lines[end - 1].strip():
        end -= 1
    lines = lines[max(0, end - 24):end]
    for i in range(len(lines) - 1, 0, -1):
        m = PROMPT_RE.match(lines[i])
        if not m:
            continue
        j = i - 1
        while j >= 0 and not lines[j].strip():
            j -= 1
        if j < 0 or not RULE_RE.match(lines[j]):
            continue
        text = m.group(1).strip()
        if MENU_ROW_RE.match(text):
            return None
        if PLACEHOLDER_RE.match(text):
            text = ""
        for k in range(i + 1, len(lines)):
            if RULE_RE.match(lines[k]) or not lines[k].strip():
                break
            text += "\n" + lines[k].strip()
        return text
    return None


def screen_refusal(screen: str) -> str | None:
    """Perche' non scrivere ora in questo schermo, o None se si puo'."""
    tail = "\n".join(screen.split("\n")[-40:])
    if BUSY_RE.search(tail):
        return "busy"
    flat = re.sub(r"\s+", "", tail).lower()
    if any(m in flat for m in ASK_MARKERS):
        return "asking"
    if PASTED_RE.search(tail):
        return "has_text"
    box = read_prompt_box(tail)
    if box is None:
        return "no_box"
    if box.strip():
        return "has_text"
    return None


# ---------------------------------------------------------------- canali

class Postpone(Exception):
    """Non ora: si riprova al prossimo giro."""


def _pid_alive(pid) -> bool:
    try:
        os.kill(int(pid), 0)
        return True
    except (OSError, ValueError, TypeError):
        return False


def onda_socket_path() -> str:
    return os.environ.get("ONDA_SOCKET") or str(Path.home() / ".config" / "onda" / "onda.sock")


def onda_call(method: str, params: dict) -> dict:
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(SOCKET_TIMEOUT_S)
    try:
        s.connect(onda_socket_path())
        rid = random.randint(1, 1 << 30)
        s.sendall((json.dumps({"jsonrpc": "2.0", "method": method, "params": params, "id": rid}) + "\n").encode())
        buf = b""
        while True:
            chunk = s.recv(65536)
            if not chunk:
                raise OSError("Onda ha chiuso la connessione")
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                if not line.strip():
                    continue
                try:
                    resp = json.loads(line)
                except ValueError:
                    continue
                if resp.get("id") != rid:
                    continue
                if resp.get("error"):
                    raise OSError(resp["error"].get("message", "errore Onda"))
                return resp.get("result") or {}
    finally:
        s.close()


def onda_session(rec: dict) -> dict | None:
    """La sessione dell'atteso in sessions.json, se Onda e' vivo e la conosce."""
    doc = read_json(bus_home() / "onda" / "sessions.json")
    if not doc or doc.get("v") != 1 or not _pid_alive(doc.get("ondaPid")):
        return None
    written = parse_iso(doc.get("writtenAt"))
    if written is None or time.time() - written > SESSIONS_FRESH_S:
        return None
    live = [s for s in doc.get("sessions") or [] if isinstance(s, dict) and s.get("state") != "exited"]
    tgt = rec.get("target") or {}
    if tgt.get("claude_session_id"):
        for s in live:
            if s.get("sessionId") == tgt["claude_session_id"]:
                return s
    if tgt.get("onda_terminal_id"):
        same = [s for s in live if s.get("terminalId") == tgt["onda_terminal_id"]]
        if same:
            return max(same, key=lambda s: s.get("stateSince") or "")
    same = [s for s in live if s.get("agent") == rec["agent"] and s.get("slug") == rec["slug"]]
    if same:
        return max(same, key=lambda s: s.get("stateSince") or "")
    return None


def wake_via_onda(sess: dict, text: str) -> str:
    if "hasText" not in sess:
        # Onda senza guardia: non sa dire se Mario sta scrivendo. Meglio aspettare.
        raise Postpone("onda_senza_guardia")
    if sess.get("state") != "idle":
        raise Postpone(f"onda:{sess.get('state')}")
    if sess.get("hasText"):
        raise Postpone("onda:has_text")
    since = parse_iso(sess.get("stateSince"))
    if since is not None and time.time() - since < SETTLE_S:
        raise Postpone("onda:settling")
    try:
        res = onda_call("agent.prompt", {"terminalId": sess["terminalId"], "prompt": text,
                                         "onlyIfIdle": True})
    except OSError as exc:
        raise Postpone(f"onda:socket:{exc}")
    if res.get("errorCode") == "not_idle":
        raise Postpone(f"onda:{res.get('reason') or 'not_idle'}")
    if res.get("errorCode"):
        raise Postpone(f"onda:{res.get('errorCode')}")
    return "onda" if res.get("submitted") else "onda:non_confermato"


def _tmux(*args: str) -> subprocess.CompletedProcess | None:
    try:
        return subprocess.run([os.environ.get("AB_TMUX_BIN") or "tmux", *args], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return None


def tmux_screen(pane: str) -> str | None:
    r = _tmux("capture-pane", "-p", "-t", pane)
    return r.stdout if r is not None and r.returncode == 0 else None


def wake_via_tmux(pane: str, text: str) -> str:
    screen = tmux_screen(pane)
    if screen is None:
        raise Postpone("tmux:pane_assente")
    why = screen_refusal(screen)
    if why:
        raise Postpone(f"tmux:{why}")
    r = _tmux("send-keys", "-t", pane, "-l", text)
    if r is None or r.returncode != 0:
        raise Postpone("tmux:send_fallito")
    time.sleep(0.3)
    _tmux("send-keys", "-t", pane, "Enter")
    time.sleep(1.5)
    after = tmux_screen(pane) or ""
    box = read_prompt_box("\n".join(after.split("\n")[-40:]))
    ok = bool(BUSY_RE.search(after)) or (box is not None and text[:20] not in box)
    return "tmux" if ok else "tmux:non_confermato"


# ---------------------------------------------------------------- regole

def wakes_last_hour(d: Path) -> int:
    f = d / ".wakes.log"
    cutoff = time.time() - 3600
    try:
        return sum(1 for line in f.read_text().split() if float(line) >= cutoff)
    except (OSError, ValueError):
        return 0


def note_wake(d: Path, task_id: str) -> None:
    d.mkdir(parents=True, exist_ok=True)
    keep = []
    f = d / ".wakes.log"
    cutoff = time.time() - 3600
    try:
        keep = [x for x in f.read_text().split() if float(x) >= cutoff]
    except (OSError, ValueError):
        pass
    keep.append(f"{time.time():.0f}")
    f.write_text("\n".join(keep) + "\n")
    # Chi spedisce da questa sessione nei prossimi CHAIN_MIN minuti timbra wake_chain.
    write_json(d / ".woken.json", {"task_id": task_id, "at": now_iso()})


def chain_blocked(msg: dict) -> bool:
    """Un messaggio scritto da una sessione appena svegliata non sveglia nessuno."""
    wc = msg.get("wake_chain")
    if not isinstance(wc, dict):
        return False
    at = parse_iso(wc.get("at"))
    return at is None or (parse_iso(msg.get("ts")) or time.time()) - at <= CHAIN_MIN * 60 + 60


def wake_text(rec: dict, msg: dict) -> str:
    sender = msg.get("from_session") or msg.get("from") or "?"
    host = (msg.get("context") or {}).get("host")
    if host and host != rec.get("host"):
        sender = f"{sender}@{host}"
    desc = re.sub(r"\s+", " ", rec.get("description") or "").strip()
    what = f" ({desc})" if desc else ""
    return (f"Esito del task {rec['task_id']}{what} arrivato da {sender}: "
            f"leggilo con bus_read {msg.get('id')} e prosegui.")


def question_text(rec: dict, msg: dict) -> str:
    sender = msg.get("from_session") or msg.get("from") or "?"
    host = (msg.get("context") or {}).get("host")
    if host and host != rec.get("host"):
        sender = f"{sender}@{host}"
    desc = re.sub(r"\s+", " ", rec.get("description") or "").strip()
    what = f" ({desc})" if desc else ""
    return (f"Domanda sul task {rec['task_id']}{what} da {sender}, che aspetta te: "
            f"leggila con bus_read {msg.get('id')} e rispondi nel thread. L'esito ti sveglia dopo.")


def notify_expired(rec: dict) -> None:
    """Avviso di scadenza nella casella di chi aspetta. Non sveglia: niente task_id."""
    agent, slug = rec["agent"], rec["slug"]
    ts = now_iso()
    rand = "".join(random.choices(string.ascii_lowercase + string.digits, k=8))
    mid = f"msg-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{rand}"
    who = rec.get("delegated_to") or "chi hai delegato"
    msg = {
        "id": mid, "version": "1.0", "from": agent, "from_session": f"{agent}/{slug}",
        "to": agent, "thread_id": f"thread-{ts[:10].replace('-', '')}-sveglia",
        "in_reply_to": None, "type": "event", "intent": "task-scaduto", "priority": "normal",
        "payload": {
            "summary": (f"Nessun esito per il task {rec['task_id']} ({rec.get('description') or ''}) "
                        f"da {who} entro {rec.get('deadline')}. Controlla la sessione delegata."),
            "expired_task_id": rec["task_id"],
        },
        "context_refs": [], "requires_response": False, "ts": ts,
    }
    if h := host_self():
        msg["context"] = {"host": h}
    d = bus_home() / "inboxes" / agent / f"@{slug}"
    (d / ".read").mkdir(parents=True, exist_ok=True)
    (d / ".done").mkdir(parents=True, exist_ok=True)
    write_json(d / f"{mid}.json", msg)


# ---------------------------------------------------------------- giro

def try_wake(rec: dict, d: Path, text: str) -> str:
    """Scrive text nel prompt della sessione che aspetta. Postpone se non ora."""
    if wakes_last_hour(d) >= MAX_PER_HOUR:
        raise Postpone("tetto_orario")
    sess = onda_session(rec)
    pane = (rec.get("target") or {}).get("tmux_pane")
    if sess is not None:
        return wake_via_onda(sess, text)
    if pane:
        return wake_via_tmux(pane, text)
    raise Postpone("nessun_canale")


def note_postponed(holder: dict, reason: str, label: str) -> None:
    prev = holder.get("pending_wake") or {}
    if prev.get("reason") != reason:
        log(f"rimandata {label}: {reason}")
    holder["pending_wake"] = {"reason": reason, "since": prev.get("since") or now_iso(),
                              "checked_at": now_iso()}


def process_questions(path: Path, rec: dict, questions: list, has_result: bool) -> str | None:
    """Una sveglia per ogni question del task, senza chiudere l'atteso. Al piu' una per giro."""
    qs = rec.setdefault("questions", {})
    label = f"{rec['task_id']} {rec['agent']}/{rec['slug']}"
    out = None
    for _, msg, where in questions:
        qid = msg.get("id")
        if not isinstance(qid, str):
            continue
        q = qs.get(qid) or {
            "from": msg.get("from_session") or msg.get("from"),
            "at": msg.get("ts") or now_iso(),
            "summary": re.sub(r"\s+", " ", str((msg.get("payload") or {}).get("summary") or ""))[:160],
            "state": "pending",
        }
        if (msg.get("context") or {}).get("host") and msg["context"]["host"] != rec.get("host"):
            q["host"] = msg["context"]["host"]
        qs[qid] = q
        if not q.get("answered_at") and answered(rec, msg):
            q["answered_at"] = now_iso()
        if q["state"] != "pending":
            continue
        if where == "archived" or q.get("answered_at") or has_result:
            q["state"] = "seen"
            q.pop("pending_wake", None)
            log(f"domanda vista {label} ({qid}): niente sveglia")
            continue
        if chain_blocked(msg):
            q["state"] = "blocked"
            q.pop("pending_wake", None)
            log(f"catena bloccata domanda {label} ({qid})")
            continue
        if out == "woken_question":
            continue  # una sveglia per giro: la prossima domanda al giro dopo
        try:
            how = try_wake(rec, path.parent, question_text(rec, msg))
        except Postpone as p:
            note_postponed(q, str(p), f"domanda {label} ({qid})")
            out = out or "postponed"
            continue
        q.update(state="woken", woken_at=now_iso(), woken_via=how)
        q.pop("pending_wake", None)
        # Prima si scrive lo stato, poi il resto: una sveglia per messaggio.
        write_json(path, rec)
        note_wake(path.parent, rec["task_id"])
        log(f"sveglia domanda {label} via {how} (msg {qid})")
        out = "woken_question"
    write_json(path, rec)
    return out


def process(path: Path) -> str | None:
    rec = read_json(path)
    if not rec or rec.get("state") != "expected":
        return None
    me = host_self()
    if rec.get("host") and me and rec["host"] != me:
        return None  # l'atteso di un'altra macchina lo sveglia la sua sveglia
    hits = find_arrivals(rec)
    questions = [h for h in hits if _is_question(h[1])]
    results = [h for h in hits if not _is_question(h[1])]
    q_out = process_questions(path, rec, questions, bool(results)) if questions else None
    now = time.time()
    if not results:
        dl = parse_iso(rec.get("deadline"))
        if dl is not None and now > dl:
            rec.update(state="expired", expired_at=now_iso())
            write_json(path, rec)
            notify_expired(rec)
            log(f"scaduto {rec['task_id']} {rec['agent']}/{rec['slug']}")
            return "expired"
        return q_out

    mpath, msg, where = results[0]
    rec["arrived"] = {"msg_id": msg.get("id"), "from": msg.get("from_session") or msg.get("from"),
                      "at": rec.get("arrived", {}).get("at") or now_iso()}
    if where == "archived":
        rec.update(state="seen", closed_at=now_iso())
        rec.pop("pending_wake", None)
        write_json(path, rec)
        log(f"visto {rec['task_id']} {rec['agent']}/{rec['slug']}: letto dalla sessione, niente sveglia")
        return "seen"
    if chain_blocked(msg):
        rec.update(state="blocked", closed_at=now_iso(),
                   note=f"esito da una sessione svegliata per {msg['wake_chain'].get('task_id')}: niente catena")
        rec.pop("pending_wake", None)
        write_json(path, rec)
        log(f"catena bloccata {rec['task_id']} {rec['agent']}/{rec['slug']}")
        return "blocked"

    try:
        how = try_wake(rec, path.parent, wake_text(rec, msg))
    except Postpone as p:
        note_postponed(rec, str(p), f"{rec['task_id']} {rec['agent']}/{rec['slug']}")
        write_json(path, rec)
        return "postponed"

    # Prima si chiude l'atteso, poi il resto: una sveglia per task anche se qui sotto fallisse.
    rec.update(state="woken", woken_at=now_iso(), woken_via=how)
    rec.pop("pending_wake", None)
    write_json(path, rec)
    note_wake(path.parent, rec["task_id"])
    log(f"sveglia {rec['task_id']} {rec['agent']}/{rec['slug']} via {how} (msg {msg.get('id')})")
    return "woken"


class Lock:
    def __init__(self) -> None:
        self.path = bus_home() / "locks" / "wake.lock"

    def __enter__(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.path.mkdir()
            self.ok = True
        except FileExistsError:
            try:
                stale = time.time() - self.path.stat().st_mtime > 120
            except OSError:
                stale = True
            if stale:
                try:
                    self.path.rmdir()
                    self.path.mkdir()
                    self.ok = True
                except OSError:
                    self.ok = False
            else:
                self.ok = False
        return self.ok

    def __exit__(self, *exc) -> None:
        if self.ok:
            try:
                self.path.rmdir()
            except OSError:
                pass


# Pulizia del registro (OR18-F3.3): ogni REGISTRO_S secondi il giro della sveglia
# chiama ab_registry_prune della lib accanto a questo script. 0 = mai.
REGISTRO_S = float(os.environ.get("AB_WAKE_REGISTRO_S", "600"))


def registro_due() -> bool:
    if REGISTRO_S <= 0:
        return False
    stamp = bus_home() / "locks" / "registro.last"
    try:
        return time.time() - stamp.stat().st_mtime >= REGISTRO_S
    except OSError:
        return True


def registro_prune() -> list[str]:
    """Le voci di registro di sessioni morte vanno in registry/.stale-<data>/ (lib)."""
    stamp = bus_home() / "locks" / "registro.last"
    stamp.parent.mkdir(parents=True, exist_ok=True)
    stamp.touch()
    lib = Path(__file__).resolve().parent / "agent-team-os-lib.sh"
    if not lib.is_file():
        return []
    env = dict(os.environ, AB_HOME=str(bus_home()))
    env["PATH"] = env.get("PATH", "") + ":/opt/homebrew/bin:/usr/local/bin"
    try:
        r = subprocess.run(["/bin/bash", "-c", 'source "$1" && ab_registry_prune', "registro", str(lib)],
                           capture_output=True, text=True, timeout=60, env=env)
    except (OSError, subprocess.TimeoutExpired) as exc:
        log(f"registro: errore {exc!r}")
        return []
    lines = [l for l in r.stdout.splitlines() if l.strip()]
    for l in lines:
        log(l)
    return lines


def tick() -> dict:
    out: dict[str, list[str]] = {}
    root = waits_root()
    with Lock() as ok:
        if not ok:
            return {"skipped": ["un altro giro in corso"]}
        if registro_due():
            if pruned := registro_prune():
                out["registro"] = pruned
        if not root.is_dir():
            return out
        for p in sorted(root.glob("*/@*/*.json")):
            if p.name.startswith("."):
                continue
            try:
                r = process(p)
            except Exception as exc:  # un atteso rotto non ferma gli altri
                log(f"errore su {p}: {exc!r}")
                r = "error"
            if r:
                out.setdefault(r, []).append(p.stem)
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="agent-team-os-wake")
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("tick")
    e = sub.add_parser("expect")
    e.add_argument("--agent", required=True)
    e.add_argument("--slug", required=True)
    e.add_argument("--description", required=True)
    e.add_argument("--to")
    e.add_argument("--deadline")
    e.add_argument("--task-id")
    e.add_argument("--msg-id")
    e.add_argument("--thread-id")
    l = sub.add_parser("list")
    l.add_argument("--agent")
    l.add_argument("--slug")
    l.add_argument("--all", action="store_true")
    a = ap.parse_args(argv)

    if a.cmd == "expect":
        target = {"onda_terminal_id": os.environ.get("ONDA_TERMINAL_ID"),
                  "claude_session_id": os.environ.get("CLAUDE_CODE_SESSION_ID"),
                  "tmux_pane": os.environ.get("TMUX_PANE")}
        try:
            rec = expect(a.agent, a.slug, a.description, a.to, a.deadline, a.task_id, target,
                         {"msg_id": a.msg_id, "thread_id": a.thread_id})
        except ValueError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 1
        print(rec["task_id"])
        return 0
    if a.cmd == "list":
        print(json.dumps(list_waits(a.agent, a.slug, a.all), ensure_ascii=False, indent=2))
        return 0
    res = tick()
    if res:
        print(json.dumps(res, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
