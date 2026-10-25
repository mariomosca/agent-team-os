"""Chi ha la palla su una delega (BR24-F6.4, F6.6): lo stato che la banda e l'Ufficio di Orbit
mostrano accanto a ogni attesa, calcolato qui una volta sola perché le due viste non divergano.

Entra un'attesa (il record ``raw`` di ``waits``), quando chi esegue ha letto la delega, le
sessioni vive e, se c'è, Orbit in sola lettura (titolo del passo, e se il passo è di Mario).
Esce ``stato_palla`` più i dati per disegnare la riga. Era in ``mods/bus-band/hooks/waits.ts``.
"""

from __future__ import annotations

import re
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

from .tempo import now, parse_iso

# Glifo per stato: mai il solo colore (Mario è daltonico), la forma lo dice anche lei.
GLIFO = {"domanda": "?", "esito": "✉", "scaduta": "!", "mario": "●", "consegnato": "✓",
         "fermo": "◌", "chiusa": "○", "coda": "⧗", "lavora": "◐"}
# Prima le righe che chiedono qualcosa a chi ha delegato (o a Mario).
ORDINE = list(GLIFO)

TETTI = {"tetto_orario": "tetto di sveglie orarie raggiunto",
         "tetto_task": "tetto di sveglie per questo task",
         "tetto_coppia": "tetto di sveglie con questo agente",
         "tetto_urgenti": "un urgente da questo agente negli ultimi 15 min"}

APERTO = lambda e: (e.get("state") or "") not in ("handled", "seen", "ignored")  # noqa: E731


def _d(v) -> dict:
    return v if isinstance(v, dict) else {}


def _s(v) -> str | None:
    return v if isinstance(v, str) and v != "" else None


# ---------- codice del piano e titolo ----------

PIANO_RE = re.compile(r"(?:^|[^A-Za-z0-9])([A-Za-z]{2,4}\d{2})-(f\d{1,2})(?:\.(\d{1,2}[a-z]?))?(?![A-Za-z0-9])", re.I)


@dataclass(frozen=True)
class Rif:
    piano: str
    fase: str
    passo: str | None

    @property
    def codice(self) -> str:
        return f"{self.piano}-{self.passo or self.fase}"


def rif_piano(*testi: str) -> Rif | None:
    """ON24-F3, am18-f15, BR24-F6.4, br24-f8.3b in un intent o in un summary."""
    for t in testi:
        m = PIANO_RE.search(t or "")
        if m:
            fase = m.group(2).upper()
            return Rif(m.group(1).upper(), fase, f"{fase}.{m.group(3).lower()}" if m.group(3) else None)
    return None


def una_riga(s: str) -> str:
    return " ".join((s or "").split())


def titolo_breve(descrizione: str, titolo_orbit: str | None) -> str:
    """Il titolo di Orbit (la prima clausola, se non è troppo corta), altrimenti la prima frase
    della descrizione senza l'intent, il codice e l'apertura di Alita ("Piano ON24, fase F3, nuova:")."""
    if titolo_orbit and titolo_orbit.strip():
        t = una_riga(titolo_orbit)
        m = re.match(r"^(.{15,}?)(?: \(|: |\. | - )", t)
        return m.group(1) if m else t
    t = una_riga(descrizione)
    t = re.sub(r"^[a-z0-9][a-z0-9._-]*: ", "", t, flags=re.I)
    t = re.sub(r"^[A-Za-z]{2,4}\d{2}-F\d+(?:\.\w+)?:? ", "", t, flags=re.I)
    t = re.sub(r"^(?:piano )?[A-Za-z]{2,4}\d{2}, fase F\d+(?:\.\w+)?(?:, [^:,]{1,20})?: ", "", t, flags=re.I)
    m = re.match(r"^(.+?[.!?])(?:\s|$)", t)
    return m.group(1) if m else t


# ---------- Orbit in sola lettura ----------

ORBIT_DB = Path.home() / "Library" / "Application Support" / "Orbit" / "orbit.db"
ORBIT_TTL_S = 60


class Orbit:
    """Righe di Orbit per i codici delle attese: una query in sola lettura, tenuta un minuto.
    Vuoto dove Orbit non c'è (il mini) o se la query fallisce."""

    def __init__(self, path: Path | None = ORBIT_DB):
        self.path = path
        self._cache: tuple[tuple, float, list[dict]] | None = None

    def righe(self, rifs: list[Rif]) -> list[dict]:
        if not rifs or self.path is None or not self.path.exists():
            return []
        piani = sorted({r.piano for r in rifs})
        fasi = sorted({r.fase for r in rifs})
        chiave = (tuple(piani), tuple(fasi))
        if self._cache and self._cache[0] == chiave and time.monotonic() - self._cache[1] < ORBIT_TTL_S:
            return self._cache[2]
        q = ("SELECT p.code AS code, ph.key AS phase, ph.title AS phase_title, t.step_key, t.title, t.state, "
             "t.assignee FROM plans p JOIN plan_phases ph ON ph.plan_id = p.id "
             "LEFT JOIN tasks t ON t.phase_id = ph.id "
             f"WHERE upper(p.code) IN ({','.join('?' * len(piani))}) AND ph.key IN ({','.join('?' * len(fasi))}) "
             "AND coalesce(p.status, 'active') = 'active'")
        try:
            c = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True, timeout=1)
            try:
                c.row_factory = sqlite3.Row
                righe = [dict(r) for r in c.execute(q, [*piani, *fasi])]
            finally:
                c.close()
        except sqlite3.Error:
            righe = []
        self._cache = (chiave, time.monotonic(), righe)
        return righe


def _mario(assignee) -> bool:
    return bool(re.match(r"^mario\b", assignee or "", re.I))


def info_orbit(righe: list[dict], rif: Rif) -> dict | None:
    """Titolo del passo (o della fase) e se la palla è di Mario: il passo è suo, o suo è un
    passo aperto della stessa fase."""
    mie = [r for r in righe if (r.get("code") or "").upper() == rif.piano and r.get("phase") == rif.fase]
    if not mie:
        return None
    passo = next((r for r in mie if r.get("step_key") == rif.passo), None) if rif.passo else None
    aperto = lambda r: r.get("step_key") is not None and r.get("state") != "done"  # noqa: E731
    return {"titolo": (passo or {}).get("title") or mie[0].get("phase_title"),
            "mario": (passo is not None and _mario(passo.get("assignee")))
            or any(aperto(r) and _mario(r.get("assignee")) for r in mie)}


# ---------- sessioni ----------

ADDR_RE = re.compile(r"^([^@/]+)(?:@([^/]+))?(?:/(.+))?$")


def _indirizzo(addr: str | None):
    m = ADDR_RE.match(addr or "")
    return (m.group(1), m.group(2), m.group(3)) if m else None


def esecutore_vivo(addr: str | None, vive: list[dict], host: str) -> dict | None:
    """La sessione esecutrice, se è viva: stesso agente, stesso slug quando l'indirizzo lo dice."""
    a = _indirizzo(addr)
    if a is None:
        return None
    trovate = [s for s in vive if s["agent"] == a[0] and (a[2] is None or s["slug"] == a[2])
               and s["host"] == (a[1] or host)]
    return next((s for s in trovate if s["lavora"]), trovate[0] if trovate else None)


def vivo_altrove(addr: str | None, vive: list[dict], host: str) -> list[str]:
    """L'agente dell'esecutore vivo in altre cartelle della stessa macchina: prima chi lavora."""
    a = _indirizzo(addr)
    if a is None:
        return []
    altre = [s for s in vive if s["agent"] == a[0] and s["host"] == (a[1] or host) and s["slug"] != a[2]]
    return [s["slug"] for s in sorted(altre, key=lambda s: (not s["lavora"], s["slug"]))]


# ---------- lo stato ----------

def _eta(iso_s: str | None) -> str:
    t = parse_iso(iso_s)
    m = 0 if t is None else max(0, int((now() - t).total_seconds() // 60))
    return "meno di 1 min" if m < 1 else f"{m} min" if m < 120 else f"{m // 60} h"


def stato_palla(rec: dict, letto_at: str | None, vive: list[dict], host: str,
                orbit_righe: list[dict] | None = None, *, letto_noto: bool = True,
                letto_da: str | None = None) -> dict:
    """``stato_palla`` di un'attesa più i campi della riga. ``vive``: dict con agent, slug,
    host, lavora. ``letto_noto`` False: nessuno dice se la delega è letta (resta "ci lavora")."""
    rets = list(_d(rec.get("returns")).items())
    domande = [(k, e) for k, e in _d(rec.get("questions")).items() if isinstance(e, dict)]
    domande += [(k, e) for k, e in rets if isinstance(e, dict) and e.get("type") == "question"]
    aperte = sorted({k: e for k, e in domande if not e.get("answered_at") and APERTO(e)}.items(),
                    key=lambda x: x[1].get("at") or "")
    esiti = sorted([(k, e) for k, e in rets if isinstance(e, dict) and e.get("type") != "question" and APERTO(e)],
                   key=lambda x: x[1].get("at") or "")
    ultimo = max((e for _, e in rets if isinstance(e, dict)), key=lambda e: e.get("at") or "", default=None)
    riarmata = ultimo is not None and ultimo.get("handled") == "riarma"
    esecutore = _s(rec.get("executor_session")) or _s(letto_da) or _s(rec.get("delegated_to"))
    rif = rif_piano(rec.get("description") or "")
    orbit = info_orbit(orbit_righe or [], rif) if rif else None
    ex = esecutore_vivo(esecutore, vive, host)

    if rec.get("state") == "expired":
        stato = "scaduta"
    elif aperte:
        stato = "domanda"
    elif esiti:
        stato = "esito"
    elif riarmata and orbit and orbit["mario"]:
        stato = "mario"
    elif ex is not None and ex["lavora"]:
        stato = "coda" if letto_noto and letto_at is None else "lavora"
    elif riarmata:
        stato = "consegnato"  # ha consegnato e la palla è fuori da lui
    elif ex is not None:
        stato = "fermo"
    else:
        stato = "chiusa"

    a = _indirizzo(esecutore)
    chi = (a[0] if a else "chi esegue").capitalize()
    altrove = vivo_altrove(esecutore, vive, host) if stato == "chiusa" else []
    parola = {
        "domanda": "domanda per te", "esito": "esito da leggere", "scaduta": "scaduta",
        "mario": "tocca a Mario", "consegnato": f"{chi} ha consegnato", "fermo": f"{chi} fermo",
        "chiusa": f"{chi} non aperto" + (f" qui · vivo in {altrove[0]}" + (f" +{len(altrove) - 1}" if len(altrove) > 1 else "")
                                        if altrove else ""),
        "coda": f"in coda: {chi} occupato", "lavora": f"{chi} ci lavora"}[stato]
    delega = _s(_d(rec.get("delegation")).get("msg_id"))
    leggi = aperte[0][0] if stato == "domanda" else (esiti[0][0] if esiti else delega) if stato == "esito" else delega
    motivo = _s(_d(rec.get("pending_wake")).get("reason"))
    tetto = motivo is not None and motivo.startswith("tetto_")
    return {
        "stato_palla": stato,
        "riga": {"glifo": GLIFO[stato], "parola": parola, "ordine": ORDINE.index(stato),
                 "codice": rif.codice if rif else None, "piano": rif.piano if rif else None,
                 "titolo": titolo_breve(rec.get("description") or "", orbit["titolo"] if orbit else None)
                 or rec.get("task_id"),
                 "esecutore": esecutore or "?", "vivo_in": altrove, "da": _eta(rec.get("created_at")),
                 "leggi": f"/read {leggi}" if leggi else None,
                 "allerta": stato in ("domanda", "esito", "scaduta") or tetto,
                 "nota": TETTI.get(motivo, motivo) if tetto else None},
    }
