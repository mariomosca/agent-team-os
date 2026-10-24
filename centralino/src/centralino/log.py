"""Log strutturato: una riga JSON per evento in logs/centralino.jsonl."""

from __future__ import annotations

import json
import threading
from pathlib import Path

from .tempo import iso_now

_MAX_BYTES = 20 * 1024 * 1024


class Log:
    def __init__(self, path: Path | None):
        self.path = Path(path) if path else None
        self._lock = threading.Lock()

    def __call__(self, livello: str, evento: str, **campi) -> None:
        if self.path is None:
            return
        riga = {"ts": iso_now(), "livello": livello, "evento": evento, **campi}
        data = json.dumps(riga, ensure_ascii=False, default=str) + "\n"
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            try:
                if self.path.stat().st_size > _MAX_BYTES:
                    self.path.replace(self.path.with_suffix(".1.jsonl"))
            except OSError:
                pass
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(data)
