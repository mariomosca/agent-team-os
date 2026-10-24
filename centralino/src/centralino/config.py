"""Modo del centralino: ``$AB_HOME/centralino.json``.

    {"fonte": "file" | "sqlite", "sveglia": "ombra" | "attiva", "raccolta": false | true}

Senza file vale il modo ombra (passo 1): i file sono la fonte, la sveglia decide e
basta, la raccolta dal mini la fa ancora launchd. Solo lo script di passaggio scrive
il modo attivo, e quello di ritorno lo rimette in ombra.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

FONTI = ("file", "sqlite")
SVEGLIE = ("ombra", "attiva")


@dataclass(frozen=True)
class Modo:
    fonte: str = "file"
    sveglia: str = "ombra"
    raccolta: bool = False

    @property
    def sqlite(self) -> bool:
        return self.fonte == "sqlite"

    @property
    def attiva(self) -> bool:
        return self.sveglia == "attiva"

    def as_dict(self) -> dict:
        return {"fonte": self.fonte, "sveglia": self.sveglia, "raccolta": self.raccolta}


def path(home: Path) -> Path:
    return Path(home) / "centralino.json"


def leggi(home: Path) -> Modo:
    try:
        d = json.loads(path(home).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return Modo()
    if not isinstance(d, dict):
        return Modo()
    fonte = d.get("fonte") if d.get("fonte") in FONTI else "file"
    sveglia = d.get("sveglia") if d.get("sveglia") in SVEGLIE else "ombra"
    return Modo(fonte, sveglia, bool(d.get("raccolta")))


def scrivi(home: Path, modo: Modo) -> None:
    p = path(home)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(modo.as_dict(), indent=2) + "\n", encoding="utf-8")
    tmp.replace(p)
