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
    agent-team-os-wake.py gestisci --agent A --slug S --msg-path P --esito E [--task-id T]
    agent-team-os-wake.py report [--days 7] [--json]            misura della sveglia (BS21-F3.2)
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
# v2.8: un'attesa resta "expected" dopo ogni sveglia, finche' chi aspetta non la
# chiude (bus_gestisci chiuso -> closed). woken/seen/blocked sono gli stati finali
# della v2.6, che chiudeva l'attesa alla prima sveglia: restano chiusi.
TERMINAL_STATES = ("closed", "woken", "seen", "blocked", "expired", "cancelled")


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


# ---------------------------------------------------------------- aggancio (BS21-F1.3)
#
# Un ritorno dell'esecutore scritto come messaggio nuovo (senza task_id ne'
# in_reply_to) si aggancia all'attesa aperta su di lui, qui, dalla parte di chi
# riceve: funziona anche fra due macchine, dove chi spedisce non vede le attese.
# Ordine: risposta al messaggio della delega, stessa sessione esecutrice, stesso
# thread, stesso agente se l'attesa e' unica. Piu' attese possibili e nessun
# criterio che ne scelga una: il messaggio resta "da agganciare" con i candidati
# e lo decide chi riceve. L'esito va nel messaggio (task_id + task_link), cosi'
# lo vedono tutti i lettori, bash compreso.

OPEN_STATES = ("expected",)
# Chi riceve un messaggio di questi tipi non lo aggancia mai: e' un task nuovo
# (kind delega) o un avviso del bus.
UNLINKABLE_TYPES = ("cc-notify",)
LINK_SLACK_S = 120


def parse_target(addr: str | None) -> tuple[str, str | None, str | None]:
    """'kai' | 'kai/slug' | 'kai@mini/slug' -> (agente, slug, host)."""
    addr = (addr or "").strip()
    head, sep, tail = addr.partition("/")
    host = None
    if "@" in head:
        head, _, host = head.partition("@")
    elif "@" in tail:
        tail, _, host = tail.rpartition("@")
    return head.strip(), (tail.strip() if sep else "") or None, (host or "").strip() or None


def executor_session(rec: dict) -> str | None:
    """agente/slug di chi esegue: imparato dai ritorni, altrimenti dall'indirizzo della delega."""
    if rec.get("executor_session"):
        return rec["executor_session"]
    agent, slug, _ = parse_target(rec.get("delegated_to"))
    return f"{agent}/{slug}" if agent and slug else None


def linkable(msg: dict, agent: str) -> bool:
    if _msg_task_id(msg) or msg.get("task_link"):
        return False
    if msg.get("kind") == "delega" or msg.get("type") in UNLINKABLE_TYPES:
        return False
    return bool(msg.get("from")) and msg.get("from") != agent


def link_candidates(msg: dict, waits: list[dict]) -> list[dict]:
    """Le attese aperte su chi ha scritto il messaggio, nate prima del messaggio."""
    sender = msg.get("from")
    host = (msg.get("context") or {}).get("host")
    ts = parse_iso(msg.get("ts")) or time.time()
    out = []
    for w in waits:
        agent, _, w_host = parse_target(w.get("delegated_to"))
        if agent != sender:
            continue
        if w_host and host and w_host != host:
            continue
        if (parse_iso(w.get("created_at")) or 0) > ts + LINK_SLACK_S:
            continue
        out.append(w)
    return out


def choose_link(msg: dict, cands: list[dict]) -> tuple[dict | None, str, list[dict]]:
    """(attesa, criterio, candidati). attesa None e candidati non vuoti = da agganciare."""
    if not cands:
        return None, "", []
    irt = msg.get("in_reply_to")
    if irt:
        for w in cands:
            if (w.get("delegation") or {}).get("msg_id") == irt:
                return w, "reply", cands
    pool = cands
    fs = msg.get("from_session")
    same = [w for w in cands if fs and executor_session(w) == fs]
    if len(same) == 1:
        return same[0], "session", cands
    if same:
        pool = same
    th = msg.get("thread_id")
    by_thread = [w for w in pool if th and (w.get("delegation") or {}).get("thread_id") == th]
    if len(by_thread) == 1:
        return by_thread[0], "thread", cands
    if len(cands) == 1:
        return cands[0], "agent", cands
    return None, "", pool


def _rewrite_message(p: Path, msg: dict) -> bool:
    """Riscrive un messaggio ancora in casella. False se intanto e' stato archiviato."""
    tmp = p.with_name(p.name + ".tmp")
    try:
        with tmp.open("w", encoding="utf-8") as fh:
            json.dump(msg, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
        if not p.exists():
            tmp.unlink()
            return False
        tmp.replace(p)
        return True
    except OSError:
        tmp.unlink(missing_ok=True)
        return False


def link_returns() -> dict[str, list[str]]:
    """Un giro di aggancio sulle caselle delle sessioni che aspettano, su questa macchina."""
    out: dict[str, list[str]] = {}
    me = host_self()
    by_agent: dict[str, list[dict]] = {}
    for w in list_waits():
        if w.get("state") not in OPEN_STATES:
            continue
        if w.get("host") and me and w["host"] != me:
            continue
        by_agent.setdefault(w["agent"], []).append(w)
    for agent, waits in by_agent.items():
        since = min(parse_iso(w.get("created_at")) or 0 for w in waits) - LINK_SLACK_S
        base = bus_home() / "inboxes" / agent
        dirs = [base] + ([d for d in base.iterdir() if d.is_dir() and d.name.startswith("@")]
                         if base.is_dir() else [])
        for d in dirs:
            # Un messaggio a una sessione si aggancia solo alle sue attese; uno
            # all'agente intero a quelle di tutte le sue sessioni.
            scope = [w for w in waits if d == base or d.name == f"@{w['slug']}"]
            if not scope:
                continue
            for p in sorted(d.glob("msg-*.json")):
                try:
                    if p.stat().st_mtime < since:
                        continue
                except OSError:
                    continue
                msg = read_json(p)
                if not msg or not linkable(msg, agent):
                    continue
                cands = link_candidates(msg, scope)
                w, by, pool = choose_link(msg, cands)
                if w is not None:
                    msg["task_id"] = w["task_id"]
                    msg["task_link"] = {"by": by, "at": now_iso()}
                    if _rewrite_message(p, msg):
                        out.setdefault("linked", []).append(f"{msg.get('id')}>{w['task_id']}")
                        log(f"agganciato {msg.get('id')} a {w['task_id']} ({by})")
                elif pool:
                    msg["task_link"] = {"by": "da_agganciare", "at": now_iso(),
                                        "candidates": [c["task_id"] for c in pool]}
                    if _rewrite_message(p, msg):
                        out.setdefault("da_agganciare", []).append(msg.get("id"))
                        log(f"da agganciare {msg.get('id')}: {', '.join(c['task_id'] for c in pool)}")
    return out


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
    """La sessione dell'atteso in sessions.json, se Onda e' vivo e la conosce.

    BS21-F3.3b: Onda scrive "exited" anche per una sessione solo ferma da oltre 30
    minuti senza eventi (SILENT_EXIT_MS, onda-sessions.ts): e' proprio la sessione
    da svegliare. Una voce exited del target vale quindi come canale; se la
    sessione e' finita davvero, la guardia di agent.prompt lo dice (no_session).
    Prima le voci vive, poi quelle exited; per sessione Claude, pane, agente/slug.
    """
    doc = read_json(bus_home() / "onda" / "sessions.json")
    if not doc or doc.get("v") != 1 or not _pid_alive(doc.get("ondaPid")):
        return None
    written = parse_iso(doc.get("writtenAt"))
    if written is None or time.time() - written > SESSIONS_FRESH_S:
        return None
    every = [s for s in doc.get("sessions") or [] if isinstance(s, dict)]
    live = [s for s in every if s.get("state") != "exited"]
    retired = [s for s in every if s.get("state") == "exited"]
    tgt = rec.get("target") or {}
    newest = lambda ss: max(ss, key=lambda s: s.get("stateSince") or "") if ss else None
    for pool in (live, retired):
        if tgt.get("claude_session_id"):
            for s in pool:
                if s.get("sessionId") == tgt["claude_session_id"]:
                    return s
        if tgt.get("onda_terminal_id"):
            if (s := newest([s for s in pool if s.get("terminalId") == tgt["onda_terminal_id"]])) is not None:
                return s
        if (s := newest([s for s in pool if s.get("agent") == rec["agent"] and s.get("slug") == rec["slug"]])) is not None:
            return s
    return None


def wake_via_onda(sess: dict, text: str) -> str:
    if sess.get("state") == "exited":
        # Voce ritirata per silenzio: lo stato vero lo sa solo Onda, che con onlyIfIdle
        # rifiuta se lavora, chiede, ha testo o la sessione e' finita (no_session).
        return _onda_prompt(sess, text)
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
    return _onda_prompt(sess, text)


def _onda_prompt(sess: dict, text: str) -> str:
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
#
# Anti-catena (BS21-F2.4). Una sessione al lavoro non si sveglia: i ritorni si
# accodano e arrivano tutti nella sveglia dopo. Sopra a questo, tre tetti all'ora:
# per sessione (MAX_PER_HOUR), per task e per coppia di agenti. Oltre il tetto il
# messaggio resta in casella con pending_wake = tetto_*, e il pannello lo mostra.
# wake_chain (v2.6) resta timbrato sui messaggi ma non blocca piu': le risposte
# sulla delega devono svegliare, ed e' il tetto a fermare un botta e risposta a vuoto.

TASK_MAX_PER_HOUR = int(os.environ.get("AB_WAKE_TASK_MAX_PER_HOUR", "4"))
PAIR_MAX_PER_HOUR = int(os.environ.get("AB_WAKE_PAIR_MAX_PER_HOUR", "6"))
# Un'attesa senza movimento per tanto si chiude da sola (scaduta, con avviso):
# altrimenti un'attesa dimenticata aggancerebbe per sempre i messaggi dell'esecutore.
WAIT_IDLE_H = float(os.environ.get("AB_WAIT_IDLE_H", "72"))
# Una response vecchia di piu' non sveglia l'esecutore (niente sveglie arretrate al rilascio).
ANSWER_WINDOW_S = float(os.environ.get("AB_WAKE_ANSWER_WINDOW_S", str(6 * 3600)))


def _stamps(f: Path) -> list[float]:
    cutoff = time.time() - 3600
    try:
        return [float(x) for x in f.read_text().split() if float(x) >= cutoff]
    except (OSError, ValueError):
        return []


def _stamp(f: Path) -> None:
    keep = _stamps(f) + [time.time()]
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text("\n".join(f"{x:.0f}" for x in keep) + "\n")


def wakes_last_hour(d: Path) -> int:
    return len(_stamps(d / ".wakes.log"))


def task_log(task_id: str) -> Path:
    return waits_root() / ".tasks" / f"{task_id}.log"


def pair_log(a: str, b: str) -> Path:
    return waits_root() / ".pairs" / ("~".join(sorted((a, b))) + ".log")


def note_wake(d: Path, task_id: str) -> None:
    _stamp(d / ".wakes.log")
    # Chi spedisce da questa sessione nei prossimi CHAIN_MIN minuti timbra wake_chain.
    write_json(d / ".woken.json", {"task_id": task_id, "at": now_iso()})


def _sender(msg: dict, my_host: str | None) -> str:
    sender = msg.get("from_session") or msg.get("from") or "?"
    host = (msg.get("context") or {}).get("host")
    if host and host != my_host:
        sender = f"{sender}@{host}"
    return sender


def _desc(rec: dict | None) -> str:
    desc = re.sub(r"\s+", " ", (rec or {}).get("description") or "").strip()
    return f" ({desc[:80]})" if desc else ""


GESTISCI = "gestisci ognuno con bus_gestisci (chiuso, riarma, risposto o letto)"


def item_text(it: dict) -> str:
    mid, tid, who = it["mid"], it["task_id"], it["sender"]
    kind = it["kind"]
    if kind == "question":
        return f"domanda sul task {tid}{_desc(it.get('rec'))} da {who}, che aspetta te (bus_read {mid})"
    if kind == "answer":
        return f"risposta sul task {tid} da {who} (bus_read {mid})"
    if kind == "delegation":
        what = f": {it['summary']}" if it.get("summary") else ""
        return f"nuova delega {tid} da {who}{what} (bus_read {mid})"
    if kind == "urgent":
        prio = "URGENTE" if it.get("priority") == "urgent" else "priorita' alta"
        return f"messaggio {prio} da {who} che chiede risposta (bus_read {mid})"
    if kind == "da_agganciare":
        return (f"messaggio da {who} da agganciare, puo' essere del task "
                f"{' o '.join(it['candidates'])} (bus_read {mid})")
    return f"ritorno sul task {tid}{_desc(it.get('rec'))} da {who} (bus_read {mid})"


def batch_text(items: list[dict]) -> str:
    """Una riga sola: una sveglia, N messaggi."""
    ups = [i for i in items if i["kind"] not in ("answer", "urgent", "delegation")]
    downs = [i for i in items if i["kind"] == "answer"]
    urg = [i for i in items if i["kind"] == "urgent"]
    dels = [i for i in items if i["kind"] == "delegation"]
    parts = []
    if len(ups) == 1:
        parts.append(item_text(ups[0])[0].upper() + item_text(ups[0])[1:] + f": leggilo e {GESTISCI.replace('ognuno', 'il messaggio')}.")
    elif ups:
        parts.append(f"{len(ups)} messaggi sulle tue deleghe: " + "; ".join(item_text(i) for i in ups)
                     + f". Leggili e {GESTISCI}.")
    if len(downs) == 1:
        parts.append(item_text(downs[0])[0].upper() + item_text(downs[0])[1:] + ": leggila e prosegui.")
    elif downs:
        parts.append(f"{len(downs)} risposte sui task che esegui: " + "; ".join(item_text(i) for i in downs)
                     + ". Leggile e prosegui.")
    if dels:
        lead = item_text(dels[0]) if len(dels) == 1 else f"{len(dels)} deleghe nuove: " + "; ".join(item_text(i) for i in dels)
        parts.append(lead[0].upper() + lead[1:] + ": leggila, fai il lavoro e manda l'esito con "
                     "message_type='response' e lo stesso task_id.")
    if urg:
        lead = item_text(urg[0]) if len(urg) == 1 else f"{len(urg)} messaggi urgenti: " + "; ".join(item_text(i) for i in urg)
        parts.append(lead[0].upper() + lead[1:] + ": leggi e gestisci con bus_gestisci.")
    return " ".join(parts)


def notify_expired(rec: dict, idle: bool = False) -> None:
    """Avviso di scadenza nella casella di chi aspetta. Non sveglia: niente task_id."""
    agent, slug = rec["agent"], rec["slug"]
    ts = now_iso()
    rand = "".join(random.choices(string.ascii_lowercase + string.digits, k=8))
    mid = f"msg-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{rand}"
    who = rec.get("delegated_to") or "chi hai delegato"
    if idle:
        summary = (f"Attesa del task {rec['task_id']} ({rec.get('description') or ''}) su {who} chiusa: "
                   f"nessun movimento da {WAIT_IDLE_H:.0f} ore. Se il lavoro e' ancora aperto, delega di nuovo.")
    else:
        summary = (f"Nessun esito per il task {rec['task_id']} ({rec.get('description') or ''}) "
                   f"da {who} entro {rec.get('deadline')}. Controlla la sessione delegata.")
    msg = {
        "id": mid, "version": "1.0", "from": agent, "from_session": f"{agent}/{slug}",
        "to": agent, "thread_id": f"thread-{ts[:10].replace('-', '')}-sveglia",
        "in_reply_to": None, "type": "event", "intent": "task-scaduto", "priority": "normal",
        "payload": {"summary": summary, "expired_task_id": rec["task_id"]},
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
    """Scrive text nel prompt della sessione. Postpone se non ora."""
    if wakes_last_hour(d) >= MAX_PER_HOUR:
        raise Postpone("tetto_orario")
    sess = onda_session(rec)
    pane = (rec.get("target") or {}).get("tmux_pane")
    if sess is not None:
        return wake_via_onda(sess, text)
    if pane:
        return wake_via_tmux(pane, text)
    raise Postpone("nessun_canale")


# BS21-F3.3b: una sessione da svegliare senza nessun canale (ne' Onda ne' tmux, o
# una sessione che Onda dice finita) non si rimanda in silenzio: dopo NOCHANNEL_S
# un avviso, una volta, nella casella dell'agente hub (priorita' alta).
NOCHANNEL_REASONS = ("nessun_canale", "onda:no_session")
NOCHANNEL_S = float(os.environ.get("AB_WAKE_NOCHANNEL_S", "300"))


def notify_nochannel(agent: str, slug: str, items: list[dict]) -> str | None:
    amap = read_json(bus_home() / "AGENT_MAP.json") or {}
    hub = (amap.get("cc_awareness") or {}).get("hub") or agent
    if hub not in (amap.get("agents") or {}):
        hub = agent
    ts = now_iso()
    rand = "".join(random.choices(string.ascii_lowercase + string.digits, k=8))
    mid = f"msg-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{rand}"
    why = items[0]["entry"].get("pending_wake", {}).get("reason", "nessun_canale")
    since = items[0]["entry"].get("pending_wake", {}).get("since", ts)
    what = "; ".join(item_text(i) for i in items)
    msg = {
        "id": mid, "version": "1.0", "from": hub, "from_session": f"{hub}/sveglia", "to": hub,
        "thread_id": f"thread-{ts[:10].replace('-', '')}-sveglia", "in_reply_to": None,
        "type": "event", "intent": "sveglia-senza-canale", "priority": "high",
        "payload": {
            "summary": (f"Sveglia senza canale: {agent}/{slug} ha {len(items)} messaggi da svegliare, "
                        f"ma dal {since} non trovo il suo pane ({why}: ne' Onda ne' tmux). "
                        f"Va svegliata a mano o riaperta. Messaggi: {what}."),
            "session": f"{agent}/{slug}", "reason": why,
            "messages": [i["mid"] for i in items],
        },
        "context_refs": [], "requires_response": False, "ts": ts,
    }
    if h := host_self():
        msg["context"] = {"host": h}
    write_json(bus_home() / "inboxes" / hub / f"{mid}.json", msg)
    log(f"senza canale {agent}/{slug}: avviso {mid} a {hub} ({len(items)} messaggi, {why})")
    return mid


def note_postponed(holder: dict, reason: str, label: str) -> None:
    prev = holder.get("pending_wake") or {}
    if prev.get("reason") != reason:
        log(f"rimandata {label}: {reason}")
    holder["pending_wake"] = {"reason": reason, "since": prev.get("since") or now_iso(),
                              "checked_at": now_iso()}


def expire_if_due(path: Path, rec: dict) -> str | None:
    """Scadenza senza ritorni, o attesa ferma da WAIT_IDLE_H ore: chiusa con un avviso."""
    now = time.time()
    dl = parse_iso(rec.get("deadline"))
    if dl is not None and now > dl and not rec.get("returns"):
        rec.update(state="expired", expired_at=now_iso())
        write_json(path, rec)
        notify_expired(rec)
        log(f"scaduto {rec['task_id']} {rec['agent']}/{rec['slug']}")
        return "expired"
    last = max(parse_iso(rec.get(k)) or 0 for k in ("created_at", "last_activity", "rearmed_at"))
    pending = any(e.get("state") == "pending" for e in (rec.get("returns") or {}).values())
    if WAIT_IDLE_H > 0 and last and now - last > WAIT_IDLE_H * 3600 and not pending:
        rec.update(state="expired", expired_at=now_iso(), note=f"inattiva da {WAIT_IDLE_H:.0f} ore")
        write_json(path, rec)
        notify_expired(rec, idle=True)
        log(f"scaduto per inattivita' {rec['task_id']} {rec['agent']}/{rec['slug']}")
        return "expired"
    return None


def collect_returns(path: Path, rec: dict, seen: list | None = None) -> list[dict]:
    """Aggiorna rec['returns'] con i messaggi del task arrivati a chi aspetta; ritorna
    quelli ancora da svegliare. Uno gia' letto (archiviato) prima della sveglia e'
    'seen'; una domanda a cui chi aspetta ha gia' risposto nel thread, anche."""
    returns = rec.setdefault("returns", {})
    legacy = rec.get("questions") or {}  # v2.7: domande gia' trattate prima della v2.8
    me = host_self()
    out = []
    for _p, msg, where in find_arrivals(rec):
        mid = msg.get("id")
        if not isinstance(mid, str):
            continue
        e = returns.get(mid)
        if e is None:
            e = {"from": msg.get("from_session") or msg.get("from"), "at": msg.get("ts") or now_iso(),
                 "type": msg.get("type"),
                 "summary": re.sub(r"\s+", " ", str((msg.get("payload") or {}).get("summary") or ""))[:160],
                 "state": "pending"}
            if (msg.get("context") or {}).get("host") and msg["context"]["host"] != rec.get("host"):
                e["host"] = msg["context"]["host"]
            if mid in legacy:
                e["state"] = legacy[mid].get("state") if legacy[mid].get("state") != "pending" else "pending"
            returns[mid] = e
            rec["last_activity"] = now_iso()
            if msg.get("from_session") and not rec.get("executor_session"):
                rec["executor_session"] = msg["from_session"]
        if e.get("state") != "pending":
            continue
        if where == "archived":
            e["state"] = "seen"
            e.pop("pending_wake", None)
            log(f"visto {rec['task_id']} {rec['agent']}/{rec['slug']} ({mid}): letto dalla sessione")
            if seen is not None:
                seen.append(rec["task_id"])
            continue
        if _is_question(msg) and answered(rec, msg):
            e.update(state="seen", answered_at=now_iso())
            e.pop("pending_wake", None)
            continue
        out.append({"kind": "question" if _is_question(msg) else "return", "agent": rec["agent"],
                    "slug": rec["slug"], "task_id": rec["task_id"], "mid": mid, "entry": e, "rec": rec,
                    "path": path, "sender": _sender(msg, me), "peer": msg.get("from") or "?"})
    return out


def collect_unlinked(waits_by_session: dict) -> list[dict]:
    """Messaggi 'da agganciare' (F1.3) ancora da segnalare: una sveglia ciascuno."""
    out = []
    me = host_self()
    for (agent, slug), recs in waits_by_session.items():
        base = bus_home() / "inboxes" / agent
        for d in (base / f"@{slug}", base):
            for p in sorted(d.glob("msg-*.json")) if d.is_dir() else []:
                msg = read_json(p)
                link = (msg or {}).get("task_link") or {}
                if link.get("by") != "da_agganciare" or link.get("woken_at"):
                    continue
                cands = link.get("candidates") or []
                if d == base and not any(r["task_id"] in cands for r in recs):
                    continue  # all'agente intero: lo segnala la sessione che ha i candidati
                out.append({"kind": "da_agganciare", "agent": agent, "slug": slug,
                            "task_id": (cands or ["?"])[0], "candidates": cands, "mid": msg.get("id"),
                            "entry": link, "msg_path": p, "msg": msg, "sender": _sender(msg, me),
                            "peer": msg.get("from") or "?"})
    return out


def executes(agent: str, slug: str, task_id: str) -> bool:
    """La sessione esegue il task: ci ha fatto una domanda (waiting_agent) o ne ha
    ricevuto la delega (un request/brief/handoff con quel task_id nella sua casella)."""
    reg = read_json(bus_home() / "registry" / f"{agent}.d" / f"{slug}.json") or {}
    if (reg.get("waiting_agent") or {}).get("task_id") == task_id:
        return True
    base = bus_home() / "inboxes" / agent
    for d in (base / f"@{slug}", base):
        for sub in ("", ".read", ".done"):
            for p in (d / sub).glob("msg-*.json") if (d / sub).is_dir() else []:
                m = read_json(p) or {}
                if _msg_task_id(m) == task_id and (m.get("kind") == "delega"
                                                   or m.get("type") in ("request", "brief", "handoff")):
                    return True
    return False


def answers_path(agent: str, slug: str) -> Path:
    return wait_dir(agent, slug) / ".answers.json"


def collect_answers(open_tasks: set) -> tuple[list[dict], dict]:
    """F2.3: la response di chi ha delegato sveglia l'esecutore fermo. E' una response
    con task_id nella casella di una sessione che quel task NON lo aspetta: quindi lo
    esegue. Funziona anche fra due macchine, perche' basta la casella dell'esecutore.
    Stato per messaggio in waits/<agente>/@<slug>/.answers.json."""
    out, states = [], {}
    root = bus_home() / "inboxes"
    if not root.is_dir():
        return out, states
    me = host_self()
    now = time.time()
    for adir in root.iterdir():
        if not adir.is_dir():
            continue
        for sdir in adir.iterdir():
            if not (sdir.is_dir() and sdir.name.startswith("@")):
                continue
            agent, slug = adir.name, sdir.name[1:]
            st = None
            for p in sorted(sdir.glob("msg-*.json")):
                try:
                    if now - p.stat().st_mtime > ANSWER_WINDOW_S:
                        continue
                except OSError:
                    continue
                msg = read_json(p)
                tid = _msg_task_id(msg or {})
                if not msg or not tid or msg.get("type") != "response" or msg.get("from") == agent:
                    continue
                w = find_wait(tid)
                if w is not None and w.parent == wait_dir(agent, slug):
                    continue  # e' un ritorno su una delega di questa sessione: lo fa collect_returns
                if st is None:
                    st = states.setdefault((agent, slug), read_json(answers_path(agent, slug)) or {})
                mid = msg.get("id")
                if mid not in st:
                    # Valutato una volta: chi non esegue il task non si sveglia per lui.
                    st[mid] = {"task_id": tid, "from": msg.get("from_session") or msg.get("from"),
                               "at": msg.get("ts") or now_iso(),
                               "state": "pending" if executes(agent, slug, tid) else "ignored"}
                e = st[mid]
                if e.get("state") != "pending":
                    continue
                out.append({"kind": "answer", "agent": agent, "slug": slug, "task_id": tid, "mid": mid,
                            "entry": e, "sender": _sender(msg, me), "peer": msg.get("from") or "?"})
    # una risposta archiviata prima della sveglia non sveglia piu'
    for (agent, slug), st in states.items():
        for mid, e in st.items():
            if e.get("state") == "pending" and not (bus_home() / "inboxes" / agent / f"@{slug}" / f"{mid}.json").exists():
                e["state"] = "seen"
                e.pop("pending_wake", None)
    return out, states


# Rete di sicurezza (BS21-F3.1): un messaggio high/urgent che chiede risposta sveglia
# il destinatario fermo anche fuori da una delega, al piu' una volta ogni
# URGENT_PAIR_S per coppia di agenti. Stato per messaggio in .urgent.json.
URGENT_PAIR_S = float(os.environ.get("AB_WAKE_URGENT_PAIR_S", "900"))
URGENT_PRIORITIES = ("urgent", "high")


def urgent_path(agent: str, slug: str) -> Path:
    return wait_dir(agent, slug) / ".urgent.json"


def urgent_log(a: str, b: str) -> Path:
    return waits_root() / ".urgent" / ("~".join(sorted((a, b))) + ".log")


def _last_stamp(f: Path) -> float:
    try:
        return max(float(x) for x in f.read_text().split())
    except (OSError, ValueError):
        return 0.0


def live_slugs(agent: str) -> list[str]:
    """Le sessioni vive di un agente su questa macchina, secondo Onda."""
    doc = read_json(bus_home() / "onda" / "sessions.json") or {}
    if not _pid_alive(doc.get("ondaPid")):
        return []
    return sorted({s.get("slug") for s in doc.get("sessions") or []
                   if isinstance(s, dict) and s.get("agent") == agent and s.get("slug")
                   and s.get("state") != "exited"})


def collect_urgent() -> tuple[list[dict], dict]:
    out, states = [], {}
    root = bus_home() / "inboxes"
    if not root.is_dir():
        return out, states
    me = host_self()
    now = time.time()
    for adir in sorted(root.iterdir()):
        if not adir.is_dir():
            continue
        agent = adir.name
        dirs = [(d, d.name[1:]) for d in adir.iterdir() if d.is_dir() and d.name.startswith("@")]
        dirs.append((adir, None))
        for d, slug in dirs:
            for p in sorted(d.glob("msg-*.json")):
                try:
                    if now - p.stat().st_mtime > ANSWER_WINDOW_S:
                        continue
                except OSError:
                    continue
                msg = read_json(p)
                if not msg or msg.get("priority") not in URGENT_PRIORITIES or msg.get("requires_response") is not True:
                    continue
                sender = msg.get("from")
                if not sender or sender == agent or msg.get("type") in UNLINKABLE_TYPES:
                    continue
                if msg.get("kind") == "delega":
                    continue  # le deleghe le sveglia collect_delegations
                tid = _msg_task_id(msg)
                if tid and msg.get("type") == "response":
                    continue  # risposta su un task: la tratta collect_answers
                w = find_wait(tid) if tid else None
                if w is not None and w.parent.parent.name == agent:
                    continue  # ritorno su una delega del destinatario: lo tratta collect_returns
                target = slug
                if target is None:
                    live = live_slugs(agent)
                    if len(live) != 1:
                        continue  # all'agente intero con piu' sessioni: ci pensa l'hook del prompt
                    target = live[0]
                st = states.setdefault((agent, target), read_json(urgent_path(agent, target)) or {})
                mid = msg.get("id")
                e = st.setdefault(mid, {"from": msg.get("from_session") or sender, "at": msg.get("ts") or now_iso(),
                                        "priority": msg.get("priority"), "state": "pending"})
                if e.get("state") != "pending":
                    continue
                out.append({"kind": "urgent", "agent": agent, "slug": target, "task_id": tid or "",
                            "mid": mid, "entry": e, "sender": _sender(msg, me), "peer": sender,
                            "priority": msg.get("priority")})
    for (agent, slug), st in states.items():
        for mid, e in st.items():
            if e.get("state") == "pending" and not any(
                    (bus_home() / "inboxes" / agent / sub / f"{mid}.json").exists() for sub in (f"@{slug}", "")):
                e["state"] = "seen"
                e.pop("pending_wake", None)
    return out, states


# Delega nuova (BS21-F3.3c, regola approvata da Mario il 21/10): una delega sveglia
# chi la riceve se e' fermo, cosi' nessuno fa da postino neanche all'andata.
#   - indirizzata a una sessione (agente/slug), o all'agente intero con una sola
#     sessione viva su questa macchina (altrimenti resta l'avviso dell'hook del prompt);
#   - priorita' non low;
#   - la sessione non ha chiesto di non essere svegliata (file .no-wake nella sua
#     cartella delle attese: waits/<agente>/@<slug>/.no-wake);
#   - "ferma" lo decide la guardia del canale (Onda onlyIfIdle, schermo tmux);
#   - una sveglia per delega, dentro i tetti di sessione, task e coppia.
# Le deleghe via onda_agent_prompt non passano da qui: arrivano gia' nel prompt.

def delegations_path(agent: str, slug: str) -> Path:
    return wait_dir(agent, slug) / ".delegations.json"


def collect_delegations() -> tuple[list[dict], dict]:
    out, states = [], {}
    root = bus_home() / "inboxes"
    if not root.is_dir():
        return out, states
    me = host_self()
    now = time.time()
    for adir in sorted(root.iterdir()):
        if not adir.is_dir():
            continue
        agent = adir.name
        dirs = [(d, d.name[1:]) for d in adir.iterdir() if d.is_dir() and d.name.startswith("@")]
        dirs.append((adir, None))
        for d, slug in dirs:
            for p in sorted(d.glob("msg-*.json")):
                try:
                    if now - p.stat().st_mtime > ANSWER_WINDOW_S:
                        continue
                except OSError:
                    continue
                msg = read_json(p)
                if not msg or msg.get("kind") != "delega" or msg.get("priority") == "low":
                    continue
                sender = msg.get("from")
                if not sender or sender == agent:
                    continue
                target = slug
                if target is None:
                    live = live_slugs(agent)
                    if len(live) != 1:
                        continue
                    target = live[0]
                if (wait_dir(agent, target) / ".no-wake").exists():
                    continue
                st = states.setdefault((agent, target), read_json(delegations_path(agent, target)) or {})
                mid = msg.get("id")
                e = st.setdefault(mid, {"task_id": _msg_task_id(msg), "from": msg.get("from_session") or sender,
                                        "at": msg.get("ts") or now_iso(), "state": "pending"})
                if e.get("state") != "pending":
                    continue
                out.append({"kind": "delegation", "agent": agent, "slug": target,
                            "task_id": _msg_task_id(msg) or "", "mid": mid, "entry": e,
                            "sender": _sender(msg, me), "peer": sender,
                            "summary": re.sub(r"\s+", " ", str((msg.get("payload") or {}).get("summary") or ""))[:80]})
    for (agent, slug), st in states.items():
        for mid, e in st.items():
            if e.get("state") == "pending" and not any(
                    (bus_home() / "inboxes" / agent / sub / f"{mid}.json").exists() for sub in (f"@{slug}", "")):
                e["state"] = "seen"
                e.pop("pending_wake", None)
    return out, states


def session_target(agent: str, slug: str, recs: list[dict]) -> dict:
    """Come raggiungere il prompt di una sessione: l'attesa piu' recente con un target,
    oppure la voce di registro (waiting_agent.target, v2.8), oppure solo agente/slug."""
    for r in sorted(recs, key=lambda r: r.get("created_at") or "", reverse=True):
        if r.get("target"):
            return {"agent": agent, "slug": slug, "target": r["target"]}
    reg = read_json(bus_home() / "registry" / f"{agent}.d" / f"{slug}.json") or {}
    tgt = ((reg.get("waiting_agent") or {}).get("target")) or reg.get("target") or {}
    return {"agent": agent, "slug": slug, "target": tgt if isinstance(tgt, dict) else {}}


def wake_sessions(items: list[dict], recs_by_session: dict) -> dict[str, list[str]]:
    """Una sveglia per sessione con tutti i suoi messaggi, dentro i tetti."""
    out: dict[str, list[str]] = {}
    groups: dict[tuple, list[dict]] = {}
    for it in items:
        groups.setdefault((it["agent"], it["slug"]), []).append(it)
    for (agent, slug), its in groups.items():
        d = wait_dir(agent, slug)
        label = f"{agent}/{slug}"
        ok = []
        for it in its:
            if it["kind"] == "urgent" and time.time() - _last_stamp(urgent_log(agent, it["peer"])) < URGENT_PAIR_S:
                note_postponed(it["entry"], "tetto_urgenti", f"urgente {label} ({it['mid']})")
            elif it["task_id"] and len(_stamps(task_log(it["task_id"]))) >= TASK_MAX_PER_HOUR:
                note_postponed(it["entry"], "tetto_task", f"{it['task_id']} {label} ({it['mid']})")
            elif len(_stamps(pair_log(agent, it["peer"]))) >= PAIR_MAX_PER_HOUR:
                note_postponed(it["entry"], "tetto_coppia", f"{it['task_id']} {label} ({it['mid']})")
            else:
                ok.append(it)
        for it in its:
            if it not in ok:
                out.setdefault("postponed", []).append(it["task_id"] or it["mid"])
        if not ok:
            continue
        tgt = session_target(agent, slug, recs_by_session.get((agent, slug), []))
        try:
            how = try_wake(tgt, d, batch_text(ok))
        except Postpone as p:
            for it in ok:
                note_postponed(it["entry"], str(p), f"{it['task_id'] or it['kind']} {label} ({it['mid']})")
                out.setdefault("postponed", []).append(it["task_id"] or it["mid"])
            if str(p) in NOCHANNEL_REASONS:
                late = [it for it in ok if not it["entry"].get("nochannel_notice")
                        and time.time() - (parse_iso(it["entry"]["pending_wake"].get("since")) or time.time()) >= NOCHANNEL_S]
                if late and (mid := notify_nochannel(agent, slug, late)):
                    for it in late:
                        it["entry"]["nochannel_notice"] = {"msg_id": mid, "at": now_iso()}
                    out.setdefault("nochannel", []).append(f"{agent}/{slug}")
            continue
        at = now_iso()
        for it in ok:
            it["entry"].update(state="woken", woken_at=at, woken_via=how)
            it["entry"].pop("pending_wake", None)
            it["woken"] = True
        # Prima si scrive lo stato, poi il resto: una sveglia per messaggio.
        persist(ok)
        note_wake(d, ok[0]["task_id"] or ok[0]["mid"])
        for tid in {it["task_id"] for it in ok if it["task_id"]}:
            _stamp(task_log(tid))
        for peer in {it["peer"] for it in ok}:
            _stamp(pair_log(agent, peer))
        for peer in {it["peer"] for it in ok if it["kind"] == "urgent"}:
            _stamp(urgent_log(agent, peer))
        log(f"sveglia {label} via {how}: " + ", ".join(f"{it['task_id'] or it['kind']}:{it['mid']}" for it in ok))
        for it in ok:
            if it["kind"] == "urgent":
                out.setdefault("urgent", []).append(it["mid"])
            elif it["kind"] == "delegation":
                out.setdefault("delegation", []).append(it["task_id"] or it["mid"])
            else:
                out.setdefault("woken", []).append(it["task_id"])
    return out


def persist(items: list[dict]) -> None:
    """Scrive dove sta lo stato di ogni messaggio: l'attesa, .answers.json o il messaggio."""
    for it in items:
        if it["kind"] in ("return", "question"):
            write_json(it["path"], it["rec"])
        elif it["kind"] == "da_agganciare":
            if it.get("woken"):
                it["entry"]["woken_at"] = it["entry"].get("woken_at") or now_iso()
                it["entry"].pop("state", None)
                it["entry"].pop("woken_via", None)
            it["msg"]["task_link"] = it["entry"]
            _rewrite_message(it["msg_path"], it["msg"])
        elif it["kind"] in ("answer", "urgent", "delegation"):
            f = {"answer": answers_path, "urgent": urgent_path,
                 "delegation": delegations_path}[it["kind"]](it["agent"], it["slug"])
            st = read_json(f) or {}
            st[it["mid"]] = it["entry"]
            write_json(f, st)


def wake_round() -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    me = host_self()
    recs_by_session: dict[tuple, list[dict]] = {}
    paths: dict[str, tuple[Path, dict]] = {}
    items: list[dict] = []
    for p in sorted(waits_root().glob("*/@*/*.json")):
        if p.name.startswith("."):
            continue
        rec = read_json(p)
        if not rec or rec.get("state") not in OPEN_STATES:
            continue
        if rec.get("host") and me and rec["host"] != me:
            continue  # l'atteso di un'altra macchina lo sveglia la sua sveglia
        try:
            seen: list = []
            found = collect_returns(p, rec, seen)
            if seen:
                out.setdefault("seen", []).extend(seen)
            if (r := expire_if_due(p, rec)) is not None:
                out.setdefault(r, []).append(rec["task_id"])
                continue
            write_json(p, rec)
        except Exception as exc:  # un atteso rotto non ferma gli altri
            log(f"errore su {p}: {exc!r}")
            out.setdefault("error", []).append(p.stem)
            continue
        items += found
        recs_by_session.setdefault((rec["agent"], rec["slug"]), []).append(rec)
        paths[rec["task_id"]] = (p, rec)
    items += collect_unlinked(recs_by_session)
    answers, states = collect_answers(set(paths))
    items += answers
    for (agent, slug), st in states.items():
        write_json(answers_path(agent, slug), st)
    urgent, ustates = collect_urgent()
    items += urgent
    for (agent, slug), st in ustates.items():
        write_json(urgent_path(agent, slug), st)
    dels, dstates = collect_delegations()
    items += dels
    for (agent, slug), st in dstates.items():
        write_json(delegations_path(agent, slug), st)
    for k, v in wake_sessions(items, recs_by_session).items():
        out.setdefault(k, []).extend(v)
    # i rimandi si scrivono anche senza sveglia (pending_wake, per il pannello)
    persist([it for it in items if not it.get("woken")])
    # sull'attesa, il perche' dell'ultimo rimando ancora aperto: lo legge il pannello
    for p, rec in paths.values():
        why = [e["pending_wake"] for e in (rec.get("returns") or {}).values()
               if e.get("state") == "pending" and e.get("pending_wake")]
        if why:
            rec["pending_wake"] = max(why, key=lambda w: w.get("checked_at") or "")
        else:
            rec.pop("pending_wake", None)
        write_json(p, rec)
    return {k: sorted(set(v)) for k, v in out.items()}


# ---------------------------------------------------------------- consumo esplicito (BS21-F2.2)
#
# Stesso contratto di bus_gestisci (agent-bus-mcp): il messaggio esce dalla casella
# solo con un esito, e su un ritorno di una delega propria l'esito muove l'attesa.
# Qui non si spedisce nulla: con "risposto" la risposta la manda chi chiama (/reply).

ESITI = ("chiuso", "riarma", "risposto", "letto")


def own_wait(agent: str, task_id: str | None) -> tuple[Path, dict] | None:
    if not task_id:
        return None
    p = find_wait(task_id)
    if p is None or p.parent.parent.name != agent:
        return None
    rec = read_json(p)
    return (p, rec) if rec else None


def gestisci(agent: str, slug: str, msg_path: Path, esito: str, task_id: str | None = None) -> dict:
    if esito not in ESITI:
        raise ValueError(f"esito non valido: {esito!r} (chiuso, riarma, risposto, letto)")
    if msg_path.parent.name in (".read", ".done"):
        raise ValueError(f"{msg_path.stem} e' gia' archiviato: niente da gestire")
    msg = read_json(msg_path)
    if msg is None:
        raise ValueError(f"{msg_path} non e' JSON leggibile")
    mid = msg.get("id") or msg_path.stem
    link = msg.get("task_link") or {}
    if task_id:
        if msg.get("task_id") and msg["task_id"] != task_id:
            raise ValueError(f"{mid} e' gia' del task {msg['task_id']}")
        if own_wait(agent, task_id) is None:
            raise ValueError(f"il task {task_id} non e' una delega di {agent}")
        if not msg.get("task_id"):
            msg["task_id"] = task_id
            msg["task_link"] = dict(link, by="scelto", at=now_iso())
    elif link.get("by") == "da_agganciare" and esito in ("chiuso", "riarma"):
        raise ValueError(f"{mid} e' da agganciare ({', '.join(link.get('candidates') or [])}): "
                         "di' a quale task con --task-id")
    own = own_wait(agent, _msg_task_id(msg))
    if esito == "riarma" and own is None:
        raise ValueError("riarma vale solo per un ritorno su una tua delega")
    now = now_iso()
    msg["handled"] = {"esito": esito, "at": now, "by": f"{agent}/{slug}"}
    dest = msg_path.parent / (".read" if esito == "letto" else ".done") / msg_path.name
    write_json(dest, msg)
    msg_path.unlink()
    out = {"message": mid, "esito": esito, "archived_to": str(dest)}
    if own is not None:
        wp, rec = own
        e = rec.setdefault("returns", {}).setdefault(mid, {
            "from": msg.get("from_session") or msg.get("from"), "at": msg.get("ts") or now,
            "type": msg.get("type"), "state": "pending"})
        e.update(state="handled", handled=esito, handled_at=now)
        e.pop("pending_wake", None)
        if esito == "chiuso":
            rec.update(state="closed", closed_at=now, closed_by=mid)
        elif esito in ("riarma", "risposto"):
            if rec.get("state") not in OPEN_STATES:
                rec["reopened_from"] = rec.get("state")
            rec["state"] = "expected"
            rec["last_activity"] = now
            if esito == "riarma":
                rec["rearmed_at"] = now
        write_json(wp, rec)
        out["wait"] = {"task_id": rec["task_id"], "state": rec["state"]}
    return out


# ---------------------------------------------------------------- misura (BS21-F3.2)
#
# Report settimanale di questa macchina: ritorni sulle deleghe e quanti hanno
# svegliato, messaggi non gestiti da oltre un'ora, attese scadute, sveglie
# fermate da un tetto. Obiettivo: oltre il 95% dei ritorni serviti (sveglia, o
# letti dalla sessione prima che servisse). Il giro della sveglia lo scrive il
# lunedi' dopo REPORT_HOUR in reports/, e sulla macchina dell'hub lo lascia nella
# casella dell'agente hub; sul mini lo aggiunge il custode al Telegram del lunedi'.

REPORT_HOUR = int(os.environ.get("AB_WAKE_REPORT_HOUR", "8"))
REPORT_TARGET = 0.95


def _in_window(iso_s: str | None, since: float) -> bool:
    t = parse_iso(iso_s)
    return t is not None and t >= since


def report(days: float = 7.0) -> dict:
    now = time.time()
    since = now - days * 86400
    me = host_self()
    r = {"host": me, "from": datetime.fromtimestamp(since, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
         "to": now_iso(), "deleghe": {"nuove": 0, "aperte": 0, "chiuse": 0, "scadute": 0, "scadute_inattive": 0},
         "ritorni": {"totale": 0, "con_sveglia": 0, "letti_prima": 0, "in_attesa": 0, "in_attesa_oltre_1h": 0},
         "risposte_esecutore": {"totale": 0, "con_sveglia": 0},
         "urgenti": {"totale": 0, "con_sveglia": 0},
         "deleghe_ricevute": {"totale": 0, "con_sveglia": 0},
         "tetti": {"episodi": 0, "per_motivo": {}},
         "non_gestiti_oltre_1h": {}, "obiettivo": REPORT_TARGET}
    for p in sorted(waits_root().glob("*/@*/*.json")) if waits_root().is_dir() else []:
        if p.name.startswith("."):
            continue
        w = read_json(p) or {}
        if not w.get("task_id") or (w.get("host") and me and w["host"] != me):
            continue
        st = w.get("state")
        if _in_window(w.get("created_at"), since):
            r["deleghe"]["nuove"] += 1
        if st == "expected":
            r["deleghe"]["aperte"] += 1
        if st == "closed" and _in_window(w.get("closed_at"), since):
            r["deleghe"]["chiuse"] += 1
        if st == "expired" and _in_window(w.get("expired_at"), since):
            r["deleghe"]["scadute_inattive" if w.get("note", "").startswith("inattiva") else "scadute"] += 1
        rets = dict(w.get("returns") or {})
        # v2.6/v2.7: l'esito in arrived (woken = con sveglia, seen = letto prima) e le domande
        if w.get("arrived", {}).get("msg_id") and w["arrived"]["msg_id"] not in rets:
            a = w["arrived"]
            rets[a["msg_id"]] = {"at": a.get("at"), "woken_at": w.get("woken_at"),
                                 "state": {"woken": "woken", "seen": "seen", "blocked": "blocked"}.get(
                                     st, "pending" if st == "expected" else "seen")}
        for qid, q in (w.get("questions") or {}).items():
            rets.setdefault(qid, q)
        for e in rets.values():
            if not _in_window(e.get("at"), since):
                continue
            r["ritorni"]["totale"] += 1
            if e.get("woken_at"):
                r["ritorni"]["con_sveglia"] += 1
            elif e.get("state") in ("seen", "handled"):
                r["ritorni"]["letti_prima"] += 1
            elif e.get("state") == "pending":
                r["ritorni"]["in_attesa"] += 1
                if now - (parse_iso(e.get("at")) or now) > 3600:
                    r["ritorni"]["in_attesa_oltre_1h"] += 1
    for name, key in ((".answers.json", "risposte_esecutore"), (".urgent.json", "urgenti"),
                      (".delegations.json", "deleghe_ricevute")):
        for f in waits_root().glob(f"*/@*/{name}") if waits_root().is_dir() else []:
            for e in (read_json(f) or {}).values():
                if not isinstance(e, dict) or e.get("state") == "ignored" or not _in_window(e.get("at"), since):
                    continue
                r[key]["totale"] += 1
                if e.get("woken_at"):
                    r[key]["con_sveglia"] += 1
    try:
        for line in (bus_home() / "logs" / "sveglia.log").read_text(encoding="utf-8").splitlines():
            ts, _, rest = line.partition(" ")
            m = re.search(r": (tetto_[a-z]+)$", rest)
            if m and rest.startswith("rimandata") and _in_window(ts, since):
                r["tetti"]["episodi"] += 1
                r["tetti"]["per_motivo"][m.group(1)] = r["tetti"]["per_motivo"].get(m.group(1), 0) + 1
    except OSError:
        pass
    inbox = bus_home() / "inboxes"
    for d in sorted(inbox.glob("*")) if inbox.is_dir() else []:
        for f in list(d.glob("msg-*.json")) + list(d.glob("@*/msg-*.json")):
            try:
                if now - f.stat().st_mtime <= 3600:
                    continue
            except OSError:
                continue
            who = d.name + ("/" + f.parent.name[1:] if f.parent != d else "")
            r["non_gestiti_oltre_1h"][who] = r["non_gestiti_oltre_1h"].get(who, 0) + 1
    rt = r["ritorni"]
    served = rt["con_sveglia"] + rt["letti_prima"]
    r["serviti"] = round(served / rt["totale"], 3) if rt["totale"] else None
    r["ok"] = r["serviti"] is None or r["serviti"] >= REPORT_TARGET
    return r


def report_text(r: dict) -> str:
    d, rt = r["deleghe"], r["ritorni"]
    pct = "n.d." if r["serviti"] is None else f"{r['serviti'] * 100:.0f}%"
    lines = [
        f"Sveglia del bus, {r['host'] or 'questa macchina'}, ultimi 7 giorni: ritorni serviti {pct} "
        f"(obiettivo {r['obiettivo'] * 100:.0f}%){'' if r['ok'] else ' SOTTO OBIETTIVO'}.",
        f"Deleghe: {d['nuove']} nuove, {d['aperte']} aperte, {d['chiuse']} chiuse, "
        f"{d['scadute']} scadute senza esito, {d['scadute_inattive']} chiuse per inattivita'.",
        f"Ritorni: {rt['totale']}, {rt['con_sveglia']} con sveglia, {rt['letti_prima']} letti prima, "
        f"{rt['in_attesa']} in attesa ({rt['in_attesa_oltre_1h']} da oltre un'ora).",
        f"Risposte all'esecutore: {r['risposte_esecutore']['totale']} ({r['risposte_esecutore']['con_sveglia']} con sveglia); "
        f"urgenti fuori delega: {r['urgenti']['totale']} ({r['urgenti']['con_sveglia']} con sveglia); "
        f"deleghe nuove a chi era fermo: {r['deleghe_ricevute']['totale']} ({r['deleghe_ricevute']['con_sveglia']} con sveglia).",
        f"Sveglie fermate da un tetto: {r['tetti']['episodi']}"
        + (" (" + ", ".join(f"{k} {v}" for k, v in sorted(r["tetti"]["per_motivo"].items())) + ")"
           if r["tetti"]["per_motivo"] else "") + ".",
    ]
    ng = r["non_gestiti_oltre_1h"]
    lines.append("Messaggi non gestiti da oltre un'ora: "
                 + (", ".join(f"{k} {v}" for k, v in sorted(ng.items(), key=lambda kv: -kv[1])) if ng else "nessuno") + ".")
    return "\n".join(lines)


def report_due() -> str | None:
    """La settimana ISO del report da scrivere ora, o None."""
    lt = time.localtime()
    if lt.tm_wday != 0 or lt.tm_hour < REPORT_HOUR:
        return None
    week = time.strftime("%G-W%V", lt)
    return None if (bus_home() / "reports" / f"sveglia-{week}.json").exists() else week


def weekly_report(week: str) -> Path:
    r = report()
    d = bus_home() / "reports"
    write_json(d / f"sveglia-{week}.json", r)
    (d / f"sveglia-{week}.txt").write_text(report_text(r) + "\n", encoding="utf-8")
    amap = read_json(bus_home() / "AGENT_MAP.json") or {}
    hub = (amap.get("cc_awareness") or {}).get("hub")
    hub_host = next((h for h, c in (amap.get("hosts") or {}).items() if (c or {}).get("hub") is True), None)
    me = host_self()
    if hub and (amap.get("agents") or {}).get(hub) is not None and (not hub_host or not me or me == hub_host):
        ts = now_iso()
        rand = "".join(random.choices(string.ascii_lowercase + string.digits, k=8))
        mid = f"msg-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{rand}"
        msg = {"id": mid, "version": "1.0", "from": hub, "to": hub, "type": "event", "intent": "report-sveglia",
               "thread_id": f"thread-{ts[:10].replace('-', '')}-sveglia", "in_reply_to": None,
               "priority": "normal" if r["ok"] else "high",
               "payload": {"summary": report_text(r), "report": str(d / f"sveglia-{week}.json")},
               "context_refs": [str(d / f"sveglia-{week}.json")], "requires_response": False, "ts": ts}
        if me:
            msg["context"] = {"host": me}
        write_json(bus_home() / "inboxes" / hub / f"{mid}.json", msg)
    log(f"report settimanale {week}: serviti {r['serviti']}")
    return d / f"sveglia-{week}.json"


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
    with Lock() as ok:
        if not ok:
            return {"skipped": ["un altro giro in corso"]}
        if registro_due():
            if pruned := registro_prune():
                out["registro"] = pruned
        # niente return se waits/ manca: le risposte all'esecutore (F2.3) non hanno attese qui
        try:
            for k, v in link_returns().items():
                out.setdefault(k, []).extend(v)
        except Exception as exc:  # l'aggancio non ferma la sveglia
            log(f"errore aggancio: {exc!r}")
        for k, v in wake_round().items():
            out.setdefault(k, []).extend(v)
        try:
            if (week := report_due()) is not None:
                out["report"] = [str(weekly_report(week))]
        except Exception as exc:  # il report non ferma la sveglia
            log(f"errore report: {exc!r}")
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
    g = sub.add_parser("gestisci")
    g.add_argument("--agent", required=True)
    g.add_argument("--slug", required=True)
    g.add_argument("--msg-path", required=True)
    g.add_argument("--esito", required=True, choices=ESITI)
    g.add_argument("--task-id")
    rp = sub.add_parser("report")
    rp.add_argument("--days", type=float, default=7.0)
    rp.add_argument("--json", action="store_true")
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
    if a.cmd == "gestisci":
        try:
            res = gestisci(a.agent, a.slug, Path(a.msg_path), a.esito, a.task_id)
        except (ValueError, OSError) as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 1
        print(json.dumps(res, ensure_ascii=False))
        return 0
    if a.cmd == "report":
        r = report(a.days)
        print(json.dumps(r, ensure_ascii=False, indent=2) if a.json else report_text(r))
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
