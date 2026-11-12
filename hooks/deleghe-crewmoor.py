#!/usr/bin/env python3
"""deleghe-crewmoor.py — lanciatore degli hook delle deleghe con Crewmoor (CM02-F3.3c).

    deleghe-crewmoor.py <evento>      evento: spawn | orfani | delega | chiusa | start | fine | giro

Fa qualcosa solo con l'interruttore su Crewmoor (~/.config/crewmoor/canale = crewmoor, AGENTI_CANALE vince);
altrimenti esce subito e il bus fa come prima. Passa il JSON dell'hook a ``python -m centralino.deleghe_crewmoor`` nel
progetto del centralino (DELEGHE_CREWMOOR_PROJECT per spostarlo) e non aspetta: «orfani» al più 2,5 s (Onda conta il
tetto dei worktree dopo), gli altri niente. Mai bloccante, solo stdlib, gira con /usr/bin/python3 -I.

Hook da mettere nelle config di Claude Code nella finestra del passaggio (CM02-F3.6):
  PreToolUse  mcp__onda__onda_agent_spawn                 → agent-team-os-onda-spawn.py pre   (c'è già)
  PostToolUse mcp__onda__onda_agent_spawn                 → agent-team-os-onda-spawn.py post  (c'è già)
  PostToolUse mcp__plugin_crewmoor_crewmoor__crewmoor_send  → deleghe-crewmoor.py delega
  PostToolUse mcp__plugin_crewmoor_crewmoor__crewmoor_close → deleghe-crewmoor.py chiusa
  SessionStart                                             → deleghe-crewmoor.py start
  SessionEnd                                               → deleghe-crewmoor.py fine
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

EVENTI = {"spawn", "orfani", "delega", "chiusa", "start", "fine", "giro"}
ATTESA_S = {"orfani": 2.5}


def su_crewmoor() -> bool:
    v = os.environ.get("AGENTI_CANALE")
    if v is None:
        f = Path(os.environ.get("CREWMOOR_CANALE_FILE") or Path.home() / ".config" / "crewmoor" / "canale")
        try:
            v = f.read_text(encoding="utf-8")
        except OSError:
            return False
    return v.strip().lower() == "crewmoor"


def lancia(evento: str, dati: bytes) -> None:
    progetto = os.environ.get("DELEGHE_CREWMOOR_PROJECT") or str(
        Path.home() / "Projects" / "01-Building" / "agent-team-os" / "centralino")
    uv = os.environ.get("DELEGHE_CREWMOOR_UV") or next(
        (p for p in ("/opt/homebrew/bin/uv", "/usr/local/bin/uv", str(Path.home() / ".local" / "bin" / "uv"))
         if Path(p).exists()), "uv")
    p = subprocess.Popen([uv, "run", "--quiet", "--frozen", "--project", progetto, "python", "-m",
                          "centralino.deleghe_crewmoor", evento], stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True)
    try:
        p.stdin.write(dati)
        p.stdin.close()
    except OSError:
        return
    attesa = ATTESA_S.get(evento)
    if attesa:
        try:
            p.wait(attesa)
        except subprocess.TimeoutExpired:
            pass  # finisce da solo: il processo è in una sessione sua


def main(argv: list[str]) -> int:
    evento = argv[1] if len(argv) > 1 else ""
    if evento not in EVENTI or not su_crewmoor():
        return 0
    try:
        lancia(evento, sys.stdin.buffer.read())
    except Exception:  # mai fermare la sessione per le deleghe
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
