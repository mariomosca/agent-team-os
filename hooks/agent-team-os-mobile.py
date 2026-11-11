#!/usr/bin/env python3
"""Mobile mode for a Claude Code session (MM31).

Mario follows a session from the phone (Remote Control) while the agent works
on the Mac. In mobile mode, for THAT session only:
  - the agent gets the mobile rules on every prompt (no `# VOICE` line, short
    answers, SendUserFile instead of pbcopy, anything that needs hands on the
    Mac said first, as a blocker);
  - voice-notify stays silent for that session (it reads the same state file);
  - the Mac is kept awake with caffeinate until a deadline.

One script, two faces:
  hook   registered on UserPromptSubmit, Stop, SessionStart and SessionEnd;
         reads the hook payload on stdin. Never blocks: exit 0 on every path.
  CLI    agent-team-os-mobile.py on|off|status [--session ID] [--hours N]
         (the session defaults to $CLAUDE_CODE_SESSION_ID).

State: $AB_HOME/mobile/<session_id>.json (default ~/.agent-team-os). Machine
level on purpose: both Claude accounts and the voice engine read it.

The switch:
  - by prompt: "mobile on", "/mobile on", "modalità mobile", "sono al telefono",
    "sono fuori" (on); "mobile off", "/mobile off", "sono tornato", "sono al
    mac" (off); "mobile on 4h" sets the duration. Only at the start of the prompt.
  - automatic: the transcript records `remote_session_change` attachments, with
    a claude.ai url while Remote Control is connected and url null when it is
    not. Connected -> on (source auto). Disconnected -> off, only if the
    automatic switch turned it on. A manual "off" holds for that connection.

Stdlib only.
"""

from __future__ import annotations

import datetime as dt
import json
import mmap
import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path

DEFAULT_HOURS = 8
WEEKEND_RESUME_HOUR = 8  # Monday 08:00 is the default end of a weekend away
CAFFEINATE = os.environ.get("AB_MOBILE_CAFFEINATE") or "/usr/bin/caffeinate"
CAFFEINATE_FLAGS = ["-i", "-m", "-s"]  # idle, disk, system (on AC); the display may sleep
CLEAR_HANDOFF_S = 600


# ── paths and state ──────────────────────────────────────────────────────────

def home() -> Path:
    return Path(os.environ.get("AB_HOME") or Path.home() / ".agent-team-os")


def mobile_dir() -> Path:
    return home() / "mobile"


def state_path(session_id: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", session_id)
    return mobile_dir() / f"{safe}.json"


def now() -> float:
    fake = os.environ.get("AB_MOBILE_NOW")
    return float(fake) if fake else time.time()


def read_json(p: Path) -> dict | None:
    try:
        data = json.loads(p.read_text())
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def write_json(p: Path, data: dict) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + f".tmp{os.getpid()}")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    os.replace(tmp, p)


def load(session_id: str) -> dict:
    return read_json(state_path(session_id)) or {"session_id": session_id, "on": False}


def is_active(st: dict, t: float | None = None) -> bool:
    """On and not expired. The same rule voice-notify applies."""
    if not st.get("on"):
        return False
    until = st.get("until")
    return until is None or (t if t is not None else now()) < float(until)


def all_states() -> list[dict]:
    out = []
    d = mobile_dir()
    if not d.is_dir():
        return out
    for p in d.glob("*.json"):
        if p.name == "caffeinate.json":
            continue
        st = read_json(p)
        if st and st.get("session_id"):
            out.append(st)
    return out


def seat() -> str | None:
    """The terminal the session lives in: survives /clear, which changes session_id."""
    for k in ("ONDA_TERMINAL_ID", "TMUX_PANE"):
        if os.environ.get(k):
            return f"{k}={os.environ[k]}"
    return None


# ── deadline ─────────────────────────────────────────────────────────────────

def default_until(t: float) -> float:
    """End of the weekend if it is weekend (from Friday 14:00), else +8 hours."""
    d = dt.datetime.fromtimestamp(t)
    wd = d.weekday()  # Mon=0 .. Sun=6
    if wd in (5, 6) or (wd == 4 and d.hour >= 14):
        days = (7 - wd) % 7 or 7
        monday = (d + dt.timedelta(days=days)).replace(hour=WEEKEND_RESUME_HOUR, minute=0, second=0, microsecond=0)
        return monday.timestamp()
    return t + DEFAULT_HOURS * 3600


def fmt_time(ts: float) -> str:
    d = dt.datetime.fromtimestamp(ts)
    giorni = ["lunedì", "martedì", "mercoledì", "giovedì", "venerdì", "sabato", "domenica"]
    return f"{giorni[d.weekday()]} {d.day:02d}/{d.month:02d} alle {d.hour:02d}:{d.minute:02d}"


# ── prompt switch ────────────────────────────────────────────────────────────

_ON = re.compile(
    r"^(?:/mobile(?:\s+on)?|(?:modalit[aà]\s+)?mobile\s+on|modalit[aà]\s+mobile(?:\s+on)?|"
    r"sono\s+al\s+telefono|sono\s+fuori|ti\s+seguo\s+dal\s+telefono|seguo\s+dal\s+telefono)\b"
)
_OFF = re.compile(
    r"^(?:/mobile\s+off|(?:modalit[aà]\s+)?mobile\s+off|modalit[aà]\s+mobile\s+off|"
    r"sono\s+tornat[oa]|sono\s+(?:di\s+nuovo\s+)?al\s+mac|sono\s+rientrat[oa])\b"
)
_STATUS = re.compile(r"^/mobile\s+(?:status|stato)\b")
_HOURS = re.compile(r"\b(\d{1,3})\s*(?:h|ore|ora)\b")


def parse_switch(prompt: str) -> tuple[str | None, float | None]:
    """('on'|'off'|'status'|None, hours or None). Only the start of the first line counts."""
    if not isinstance(prompt, str):
        return None, None
    first = prompt.strip().splitlines()[0] if prompt.strip() else ""
    line = first.lower().strip().lstrip("*_` ").rstrip(" .!")
    if _STATUS.match(line):
        return "status", None
    explicit = bool(re.match(r"^(?:/mobile\b|(?:modalit[aà]\s+)?mobile\s+o(?:n|ff)\b|modalit[aà]\s+mobile\b)", line))
    if not explicit and len(line) > 60:
        return None, None  # "sono fuori strada con questa idea…" is not a switch
    if _OFF.match(line):
        return "off", None
    if _ON.match(line):
        m = _HOURS.search(line)
        hours = float(m.group(1)) if m else None
        return "on", hours if hours and hours > 0 else None
    return None, None


# ── remote control detection ─────────────────────────────────────────────────

_RSC = b'"type":"remote_session_change"'


def remote_control(transcript_path: str | None) -> tuple[bool, str | None] | None:
    """(connected, url) from the LAST remote_session_change of the transcript;
    None when the transcript has none (never connected, or unknown)."""
    if not transcript_path:
        return None
    try:
        with open(transcript_path, "rb") as f:
            size = os.fstat(f.fileno()).st_size
            if size == 0:
                return None
            with mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as mm:
                i = mm.rfind(_RSC)
                if i < 0:
                    return None
                start = mm.rfind(b"\n", 0, i) + 1
                end = mm.find(b"\n", i)
                line = mm[start: end if end >= 0 else size]
        rec = json.loads(line)
        att = rec.get("attachment") or {}
        url = att.get("url") or None
        return (bool(url), url)
    except Exception:
        return None


# ── caffeinate ───────────────────────────────────────────────────────────────

def _caf_path() -> Path:
    return mobile_dir() / "caffeinate.json"


def _pid_is_caffeinate(pid: int) -> bool:
    try:
        out = subprocess.run(["/bin/ps", "-p", str(pid), "-o", "command="], capture_output=True, text=True, timeout=2).stdout
        return "caffeinate" in out
    except Exception:
        return False


def caffeinate_sync(t: float | None = None) -> dict:
    """One caffeinate for all mobile sessions, until the latest deadline.
    Only our own process (pid in caffeinate.json) is ever touched."""
    t = t if t is not None else now()
    wanted = [float(s["until"]) for s in all_states() if is_active(s, t) and s.get("until")]
    target = max(wanted) if wanted else None
    cur = read_json(_caf_path()) or {}
    pid = cur.get("pid")
    alive = bool(pid) and _pid_is_caffeinate(int(pid))

    if target is None:
        if alive:
            try:
                os.kill(int(pid), signal.SIGTERM)
            except Exception:
                pass
        try:
            _caf_path().unlink()
        except FileNotFoundError:
            pass
        return {"running": False}

    if alive and abs(float(cur.get("until", 0)) - target) < 60:
        return {"running": True, "pid": pid, "until": target}
    if alive:
        try:
            os.kill(int(pid), signal.SIGTERM)
        except Exception:
            pass
    secs = max(60, int(target - t))
    try:
        p = subprocess.Popen(
            [CAFFEINATE, *CAFFEINATE_FLAGS, "-t", str(secs)],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        write_json(_caf_path(), {"pid": p.pid, "until": target, "started": t})
        return {"running": True, "pid": p.pid, "until": target}
    except Exception as e:
        return {"running": False, "error": str(e)}


def lid_warning() -> str | None:
    """A warning when closing the lid would put the Mac to sleep: no external display."""
    if os.environ.get("AB_MOBILE_DISPLAYS"):  # tests: "internal" or "internal,external"
        kinds = os.environ["AB_MOBILE_DISPLAYS"].split(",")
        external = any(k != "internal" for k in kinds)
    else:
        try:
            out = subprocess.run(["/usr/sbin/system_profiler", "SPDisplaysDataType", "-json"],
                                 capture_output=True, text=True, timeout=4).stdout
            data = json.loads(out or "{}")
            screens = [s for g in data.get("SPDisplaysDataType", []) for s in g.get("spdisplays_ndrvs", []) or []]
            external = any(s.get("spdisplays_connection_type") != "spdisplays_internal" for s in screens)
        except Exception:
            return None
    if external:
        return None
    return ("Nessun monitor esterno: se il coperchio del Mac viene chiuso, il Mac dorme anche con caffeinate "
            "e la sessione si ferma. Va lasciato aperto (meglio se attaccato alla corrente).")


# ── switching ────────────────────────────────────────────────────────────────

def turn_on(session_id: str, *, source: str, hours: float | None = None, cwd: str | None = None,
            rc_url: str | None = None, t: float | None = None) -> dict:
    t = t if t is not None else now()
    st = load(session_id)
    st.update({
        "session_id": session_id,
        "on": True,
        "source": source,
        "since": t,
        "until": t + hours * 3600 if hours else default_until(t),
        "cwd": cwd or st.get("cwd"),
        "seat": seat() or st.get("seat"),
        "rc_url": rc_url if rc_url is not None else st.get("rc_url"),
    })
    st.pop("off_rc_url", None)
    write_json(state_path(session_id), st)
    caffeinate_sync(t)
    return st


def turn_off(session_id: str, *, reason: str, rc_url: str | None = None, t: float | None = None) -> dict:
    t = t if t is not None else now()
    st = load(session_id)
    st.update({"on": False, "off_at": t, "off_reason": reason})
    if reason in ("manual", "expired") and rc_url:
        st["off_rc_url"] = rc_url  # a manual off holds for this Remote Control connection
    write_json(state_path(session_id), st)
    caffeinate_sync(t)
    return st


def forget(session_id: str, t: float | None = None) -> None:
    try:
        state_path(session_id).unlink()
    except FileNotFoundError:
        pass
    caffeinate_sync(t)


# ── what the agent reads ─────────────────────────────────────────────────────

def rules(st: dict) -> str:
    until = fmt_time(float(st["until"])) if st.get("until") else "a nuovo ordine"
    why = "il remote control è connesso" if st.get("source") == "auto" else "l'ha chiesto Mario"
    return (
        f"[modalità mobile ON per questa sessione ({why}), fino a {until}. "
        "Mario segue dal telefono e NON è davanti al Mac.]\n"
        "- Niente riga `# VOICE`: la voce di questa sessione è spenta. La regola del persona file è sospesa, non cancellata.\n"
        "- Risposte corte, leggibili su un telefono: niente tabelle larghe, elenchi brevi, blocchi di codice stretti.\n"
        "- File da consegnare: SendUserFile. Testo da copiare: in un blocco di codice nella risposta (pbcopy non gli arriva).\n"
        "- Se serve Mario fisicamente al Mac (login, trust di una cartella, app da riaprire, permessi macOS, coperchio, "
        "chiavette), dillo SUBITO, in testa alla risposta, come bloccante: ora non può farlo. Poi vai avanti con il resto.\n"
        "- Non dare istruzioni da tastiera (Cmd+V, \"apri il terminale\") come se fosse lì.\n"
        "- Spegnere: \"mobile off\" oppure /mobile off."
    )


def notice_on(st: dict, lid: str | None) -> str:
    until = fmt_time(float(st["until"]))
    lines = [f"[mobile] Modalità mobile ATTIVATA ora per questa sessione: voce spenta qui, Mac sveglio (caffeinate) fino a {until}."]
    if st.get("source") == "auto":
        lines.append("Accesa in automatico: il remote control risulta connesso. Si spegne con \"mobile off\".")
    if lid:
        lines.append(f"Avvertenza da dire a Mario: {lid}")
    lines.append("Confermalo a Mario in una riga.")
    return "\n".join(lines)


def notice_off(reason: str) -> str:
    why = {
        "manual": "su richiesta",
        "expired": "scadenza raggiunta",
        "rc-disconnected": "il remote control si è staccato",
    }.get(reason, reason)
    return (f"[mobile] Modalità mobile SPENTA per questa sessione ({why}): la voce torna, regole normali "
            "(riga `# VOICE` a fine lavoro). caffeinate fermato se nessun'altra sessione è mobile. Confermalo in una riga.")


def notice_status(st: dict) -> str:
    if is_active(st):
        return f"[mobile] Stato: ON ({st.get('source')}), fino a {fmt_time(float(st['until']))}."
    return "[mobile] Stato: OFF per questa sessione."


# ── hook ─────────────────────────────────────────────────────────────────────

def handle(hook: dict) -> str | None:
    """Returns the additionalContext for UserPromptSubmit/SessionStart, or None."""
    event = hook.get("hook_event_name")
    sid = hook.get("session_id") or os.environ.get("CLAUDE_CODE_SESSION_ID")
    if not sid:
        return None
    t = now()
    cwd = os.environ.get("CLAUDE_PROJECT_DIR") or hook.get("cwd")

    if event == "SessionEnd":
        reason = hook.get("reason")
        st = read_json(state_path(sid))
        if st and reason == "clear" and st.get("on") and st.get("seat"):
            st["cleared_at"] = t  # the next session on the same terminal inherits it
            write_json(state_path(sid), st)
            return None
        forget(sid, t)
        return None

    notes: list[str] = []
    st = read_json(state_path(sid))

    if event == "SessionStart" and st is None and hook.get("source") == "clear":
        here = seat()
        for old in all_states():
            if here and old.get("seat") == here and old.get("cleared_at") and t - float(old["cleared_at"]) < CLEAR_HANDOFF_S:
                new = {k: v for k, v in old.items() if k != "cleared_at"}
                new["session_id"] = sid
                write_json(state_path(sid), new)
                try:
                    state_path(old["session_id"]).unlink()
                except FileNotFoundError:
                    pass
                st = new
                break

    rc = remote_control(hook.get("transcript_path"))
    rc_url = rc[1] if rc else None

    # expiry: like a manual off, it holds for the current Remote Control connection
    if st and st.get("on") and not is_active(st, t):
        st = turn_off(sid, reason="expired", rc_url=rc_url, t=t)
        notes.append(notice_off("expired"))

    # explicit switch, by prompt
    action, hours = parse_switch(hook.get("prompt", "")) if event == "UserPromptSubmit" else (None, None)
    if action == "on":
        st = turn_on(sid, source="manual", hours=hours, cwd=cwd, rc_url=rc_url, t=t)
        notes.append(notice_on(st, lid_warning()))
    elif action == "off":
        st = turn_off(sid, reason="manual", rc_url=rc_url, t=t)
        notes.append(notice_off("manual"))
    elif action == "status":
        notes.append(notice_status(st or {}))
    elif rc is not None:
        # automatic switch, from the Remote Control state
        connected = rc[0]
        if connected and not (st and is_active(st, t)) and (st or {}).get("off_rc_url") != rc_url:
            st = turn_on(sid, source="auto", cwd=cwd, rc_url=rc_url, t=t)
            notes.append(notice_on(st, lid_warning()))
        elif not connected and st and is_active(st, t) and st.get("source") == "auto":
            st = turn_off(sid, reason="rc-disconnected", t=t)
            notes.append(notice_off("rc-disconnected"))

    if event not in ("UserPromptSubmit", "SessionStart"):
        return None  # Stop: state only, nothing to say
    if st and is_active(st, t):
        notes.append(rules(st))
    return "\n\n".join(notes) if notes else None


def main_hook() -> None:
    try:
        raw = sys.stdin.read()
        hook = json.loads(raw) if raw.strip() else {}
    except Exception:
        return
    if not isinstance(hook, dict):
        return
    try:
        ctx = handle(hook)
    except Exception:
        return
    if ctx:
        print(json.dumps({"hookSpecificOutput": {"hookEventName": hook.get("hook_event_name"), "additionalContext": ctx}},
                         ensure_ascii=False))


def main_cli(argv: list[str]) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="agent-team-os-mobile.py", description="Modalità mobile per sessione (MM31)")
    ap.add_argument("action", choices=["on", "off", "status", "list"])
    ap.add_argument("--session", default=os.environ.get("CLAUDE_CODE_SESSION_ID"))
    ap.add_argument("--hours", type=float)
    a = ap.parse_args(argv)
    if a.action == "list":
        for s in all_states():
            flag = "ON " if is_active(s) else "off"
            until = fmt_time(float(s["until"])) if s.get("until") else "-"
            print(f"{flag} {s['session_id']}  {s.get('source', '-')}  fino a {until}  {s.get('cwd') or ''}")
        caf = read_json(_caf_path())
        print(f"caffeinate: {'pid ' + str(caf['pid']) + ' fino a ' + fmt_time(float(caf['until'])) if caf else 'spento'}")
        return 0
    if not a.session:
        print("serve --session (o CLAUDE_CODE_SESSION_ID)", file=sys.stderr)
        return 2
    if a.action == "on":
        st = turn_on(a.session, source="manual", hours=a.hours, cwd=os.getcwd())
        print(notice_on(st, lid_warning()))
    elif a.action == "off":
        turn_off(a.session, reason="manual")
        print(notice_off("manual"))
    else:
        print(notice_status(load(a.session)))
    return 0


if __name__ == "__main__":
    if len(sys.argv) > 1:
        sys.exit(main_cli(sys.argv[1:]))
    main_hook()
    sys.exit(0)
