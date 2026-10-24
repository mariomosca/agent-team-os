"""Canale di sveglia: scrivere un prompt nella sessione ferma (porting di wake.py, BR24-F4.1).

Onda è il canale principale: ``agent.prompt`` con ``onlyIfIdle`` sul suo socket, e Onda
rifiuta se la sessione lavora, chiede, ha testo nella casella o è finita. tmux è il
ripiego, solo per le sessioni che hanno dichiarato un ``tmux_pane``. Ogni rifiuto è un
``Rinvio`` col motivo: il giro dopo si riprova.
"""

from __future__ import annotations

import json
import os
import random
import re
import socket
import subprocess
import time
from pathlib import Path

from .tempo import age_s

SOCKET_TIMEOUT_S = 20.0
SETTLE_S = 5.0

BUSY_RE = re.compile(
    r"(esc to interrupt|ctrl\+c to interrupt|\b(?:Running|Contemplating|Crunching|Thinking|Working|"
    r"Generating|Pondering)\b[^\n]*…|…\s*\(\s*(?:\d+\s*s|↓))", re.I)
PASTED_RE = re.compile(r"\[Pasted text #\d+")
RULE_RE = re.compile(r"^[\s]*[─━\-═]{8,}\s*$")
PROMPT_RE = re.compile(r"^\s*[❯>][  ]?(.*)$")
MENU_ROW_RE = re.compile(r"^\s*\d+[.)]\s|^(?:yes|no)\b", re.I)
PLACEHOLDER_RE = re.compile(r"^try\s*[\"“]", re.I)
ASK_MARKERS = ("doyouwantto", "allowonce", "allowalways", "doyoutrustthe", "quicksafetycheck",
               "trustthisfolder", "entertoselect", "askuserquestion")


class Rinvio(Exception):
    """Non ora: si riprova al prossimo giro. ``str(exc)`` è il motivo."""


def read_prompt_box(screen: str) -> str | None:
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


def via_onda(sess: dict, text: str, call=onda_call) -> str:
    if sess.get("state") != "exited":
        # Voce exited = ritirata per silenzio: lo stato vero lo sa solo Onda (onlyIfIdle).
        if "hasText" not in sess:
            raise Rinvio("onda_senza_guardia")
        if sess.get("state") != "idle":
            raise Rinvio(f"onda:{sess.get('state')}")
        if sess.get("hasText"):
            raise Rinvio("onda:has_text")
        since = age_s(sess.get("stateSince"))
        if since is not None and since < SETTLE_S:
            raise Rinvio("onda:settling")
    try:
        res = call("agent.prompt", {"terminalId": sess["terminalId"], "prompt": text, "onlyIfIdle": True})
    except OSError as exc:
        raise Rinvio(f"onda:socket:{exc}")
    if res.get("errorCode") == "not_idle":
        raise Rinvio(f"onda:{res.get('reason') or 'not_idle'}")
    if res.get("errorCode"):
        raise Rinvio(f"onda:{res.get('errorCode')}")
    return "onda" if res.get("submitted") else "onda:non_confermato"


def _tmux(*args: str) -> subprocess.CompletedProcess | None:
    try:
        return subprocess.run([os.environ.get("AB_TMUX_BIN") or "tmux", *args], capture_output=True, text=True,
                              timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return None


def via_tmux(pane: str, text: str) -> str:
    r = _tmux("capture-pane", "-p", "-t", pane)
    if r is None or r.returncode != 0:
        raise Rinvio("tmux:pane_assente")
    why = screen_refusal(r.stdout)
    if why:
        raise Rinvio(f"tmux:{why}")
    r = _tmux("send-keys", "-t", pane, "-l", text)
    if r is None or r.returncode != 0:
        raise Rinvio("tmux:send_fallito")
    time.sleep(0.3)
    _tmux("send-keys", "-t", pane, "Enter")
    time.sleep(1.5)
    after = (_tmux("capture-pane", "-p", "-t", pane) or subprocess.CompletedProcess([], 0, "")).stdout or ""
    box = read_prompt_box("\n".join(after.split("\n")[-40:]))
    ok = bool(BUSY_RE.search(after)) or (box is not None and text[:20] not in box)
    return "tmux" if ok else "tmux:non_confermato"
