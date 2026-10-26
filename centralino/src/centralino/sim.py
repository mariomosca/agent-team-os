"""Banco di prova a simulazione (BR24-F3.5).

Ogni scenario gira su un AB_HOME temporaneo, mai su quello vero: AGENT_MAP finto,
``onda/sessions.json`` scritto dal banco (come lo scriverebbe Onda), orologio iniettato,
nessuna sveglia vera (kick_wake muto) e SSH verso un comando che fallisce.
I casi del 24/10 (INVENTARIO §7) sono regressioni con il loro nome.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import traceback
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import tempo
from .hookops import HookOps
from .ops import Centralino
from .remoto import atomic_write, read_json
from .sveglia import Sveglia


class Rifiuto(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code


@dataclass
class Banco:
    home: Path
    t: datetime = field(default_factory=lambda: datetime(2026, 10, 24, 16, 0, tzinfo=timezone.utc))
    onda_sessions: dict = field(default_factory=dict)

    def __post_init__(self):
        self.root = self.home / "ws"
        tempo.imposta(lambda: self.t)
        os.environ["AB_SSH_BIN"] = "false"
        os.environ["AB_SSH_TIMEOUT"] = "1"
        amap = {
            "version": "2.0",
            "rules": [
                {"pattern": str(self.root / "work-hub"), "match": "prefix", "agent": "alita"},
                {"pattern": str(self.root / "Projects"), "match": "prefix", "agent": "kai"},
                {"pattern": str(self.root / "Projects" / "00-Brandart-PM-OS"), "match": "prefix", "agent": "leo"},
            ],
            "agents": {
                "alita": {"role": "hub", "domain": "global", "workspace_root": str(self.root / "work-hub")},
                "kai": {"role": "tech", "domain": "side", "workspace_root": str(self.root / "Projects")},
                "leo": {"role": "it", "domain": "brandart",
                        "workspace_root": str(self.root / "Projects" / "00-Brandart-PM-OS")},
            },
            "hosts": {"laptop": {"hub": True}, "mini": {"ssh": "mini", "bus_home": "/nessuno", "centralino": "centralino"},
                      "cloud": {"note": "alita-cloud via repo, nessun ssh"}},
            "cc_awareness": {"hub": "alita"},
        }
        self.home.mkdir(parents=True, exist_ok=True)
        (self.home / "AGENT_MAP.json").write_text(json.dumps(amap), encoding="utf-8")
        for a in amap["agents"]:
            (self.home / "inboxes" / a).mkdir(parents=True, exist_ok=True)
        amap["agents"]["kai"]["persona_file"] = "personas/kai.md"
        (self.home / "AGENT_MAP.json").write_text(json.dumps(amap), encoding="utf-8")
        (self.home / "personas").mkdir(exist_ok=True)
        (self.home / "personas" / "kai.md").write_text("# Kai\n", encoding="utf-8")
        self.c = Centralino(self.home, pid_alive=lambda pid: True)
        HookOps(self.c).registra()
        self.prompt: list[tuple[str, str]] = []   # (terminalId, testo) scritti dal finto Onda
        self.onda_rifiuta: str | None = None

        def finta_onda(method, params):
            if self.onda_rifiuta:
                return {"errorCode": "not_idle", "reason": self.onda_rifiuta}
            self.prompt.append((params["terminalId"], params["prompt"]))
            return {"submitted": True}
        self.sveglia = Sveglia(self.c, onda_call=finta_onda)
        self._scrivi_onda()

    # ---------- tempo e Onda ----------

    def avanza(self, secondi: float) -> None:
        self.t += timedelta(seconds=secondi)
        self._scrivi_onda()

    def onda(self, addr: str, state: str | None = "idle", has_text: bool = False) -> None:
        """Mette (o toglie, con state=None) una sessione nel finto sessions.json di Onda."""
        agent, _, slug = addr.partition("/")
        if state is None:
            self.onda_sessions.pop(addr, None)
        else:
            prev = self.onda_sessions.get(addr) or {}
            since = prev.get("stateSince") if prev.get("state") == state else None
            self.onda_sessions[addr] = {
                "sessionId": f"sess-{agent}-{slug}", "terminalId": f"pane-{agent}-{slug}", "agent": agent,
                "slug": slug, "cwd": str(self.cwd(addr)), "state": state,
                "stateSince": since or tempo.iso(self.t - timedelta(seconds=30)), "hasText": has_text,
                "host": "laptop"}
        self._scrivi_onda()

    def _scrivi_onda(self) -> None:
        atomic_write(self.home / "onda" / "sessions.json",
                     {"v": 1, "writtenAt": tempo.iso(self.t), "ondaPid": os.getpid(),
                      "sessions": list(self.onda_sessions.values())})
        p = self.home / "onda" / "sessions.json"
        st = p.stat()
        os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns + 1))  # mtime sempre nuovo, anche nello stesso istante

    # ---------- chiamate ----------

    def cwd(self, addr: str) -> Path:
        agent, _, slug = addr.partition("/")
        base = {"alita": self.root, "kai": self.root / "Projects", "leo": self.root / "Projects"}[agent]
        p = base / slug
        p.mkdir(parents=True, exist_ok=True)
        return p

    def caller(self, addr: str, heartbeat: bool = True) -> dict:
        return {"cwd": str(self.cwd(addr)), "heartbeat": heartbeat, "pid": 1}

    def op(self, addr: str | None, op: str, heartbeat: bool = True, **args) -> dict:
        req = {"v": 1, "id": "r-sim", "op": op, "args": args}
        if addr:
            req["caller"] = self.caller(addr, heartbeat)
        resp = self.c.handle_request(req)
        if not resp["ok"]:
            raise Rifiuto(resp["error"]["code"], resp["error"]["message"])
        return resp["result"]

    def stato(self, addr: str):
        agent, _, slug = addr.partition("/")
        return self.c.vivo.stato(agent, slug)

    def decisioni(self, addr: str) -> dict[str, tuple[str, str, str | None]]:
        """msg_id -> (regola, decisione, motivo): l'ultima decisione per ogni messaggio della sessione."""
        agent, _, slug = addr.partition("/")
        out = {}
        for x in self.c.store.read().execute(
                "SELECT msg_id, regola, decisione, motivo FROM wake_decisions WHERE agent=? AND slug=? ORDER BY id",
                (agent, slug)):
            out[x["msg_id"]] = (x["regola"], x["decisione"], x["motivo"])
        return out

    def giro(self) -> None:
        self.sveglia.giro()

    def prompt_a(self, addr: str) -> list[str]:
        agent, _, slug = addr.partition("/")
        return [t for term, t in self.prompt if term == f"pane-{agent}-{slug}"]

    def consegna(self, to: str, record: dict) -> str:
        """Un messaggio che arriva da fuori (cloud, altro host): entra dall'op ``consegna``."""
        agent, _, slug = to.partition("/")
        self.op(None, "consegna", record=record, agent=agent, slug=slug or None)
        return record["id"]

    def chiudi(self) -> None:
        self.c.store.close()
        tempo.imposta(None)


def _msg(mid: str, frm: str, to: str, typ: str, ts: str, **extra) -> dict:
    agent, _, slug = frm.partition("/")
    return {"id": mid, "version": "1.0", "from": agent, "to": to.partition("/")[0],
            "thread_id": extra.pop("thread_id", "thread-sim"), "in_reply_to": None, "type": typ,
            "intent": extra.pop("intent", "sim"), "priority": extra.pop("priority", "normal"),
            "payload": {"summary": extra.pop("summary", "sim")}, "context_refs": [],
            "requires_response": False, "ts": ts, "from_session": frm if slug else None, **extra}


def _check(cond: bool, cosa: str) -> None:
    if not cond:
        raise AssertionError(cosa)


# ======================================================================
# Scenari
# ======================================================================

def caso_irjocy(b: Banco) -> None:
    """24/10 (i): l'esito finale non deve mai finire sotto un tetto."""
    b.onda("alita/work-hub", "idle")
    b.onda("kai/orbit", "idle")
    out = b.op("alita/work-hub", "send", to="kai/orbit", intent="lavoro", summary="fai X",
               message_type="request", kind="delega")
    tid = out["task_id"]
    _check(tid and out["expecting"]["task_id"] == tid, "la delega registra l'attesa")
    # dieci domande in un'ora, ognuna sveglia alita e alita risponde
    for i in range(10):
        q = b.op("kai/orbit", "send", to="alita/work-hub", intent=f"q{i}", summary=f"domanda {i}",
                 message_type="question", task_id=tid)
        b.giro()
        _check(b.decisioni("alita/work-hub")[q["id"]][1] == "sveglia", f"la domanda {i} sveglia")
        b.op("alita/work-hub", "handle", message_id=q["id"], esito="risposto", reply=f"ok {i}")
        b.avanza(60)
    q = b.op("kai/orbit", "send", to="alita/work-hub", intent="q10", summary="undicesima",
             message_type="question", task_id=tid)
    b.giro()
    d = b.decisioni("alita/work-hub")[q["id"]]
    _check(d[1:] == ("coda", "tetto_sessione"), f"l'undicesima domanda resta sotto il tetto: {d}")
    esito = b.op("kai/orbit", "send", to="alita/work-hub", intent="fatto", summary="esito finale",
                 message_type="response", task_id=tid)
    b.giro()
    d_esito = b.decisioni("alita/work-hub")[esito["id"]]
    _check(d_esito[:2] == ("esito", "sveglia"), f"l'esito sveglia sempre: {d_esito}")
    d_q = b.decisioni("alita/work-hub")[q["id"]]
    _check(d_q[1] == "sveglia" and (d_q[2] or "").startswith("accorpato"),
           f"la domanda trattenuta viaggia con la sveglia dell'esito (una sola sveglia): {d_q}")
    giri = {x["giro"] for x in b.c.store.read().execute(
        "SELECT giro FROM wake_decisions WHERE msg_id IN (?,?) AND decisione='sveglia'", (q["id"], esito["id"]))}
    _check(len(giri) == 1, "esito e domanda accorpati nello stesso giro")
    r = b.op("alita/work-hub", "handle", message_id=esito["id"], esito="chiuso")
    _check(r["wait"]["state"] == "closed", "chiuso chiude l'attesa")
    wait = json.loads(b.c.store.wait(tid)["raw"])
    _check("pending_wake" not in wait, "nessun pending_wake resta sull'attesa chiusa")


def caso_orbit_non_viva(b: Banco) -> None:
    """24/10 (ii): sessione viva per Onda ma assente dal registro."""
    b.onda("kai/orbit", "working")
    st = b.op("alita/work-hub", "status", agent="kai")
    slugs = [s["slug"] for s in st["roster"][0]["live_sessions"]]
    _check(slugs == ["orbit"], f"status la dà viva senza registro: {slugs}")
    out = b.op("alita/work-hub", "send", to="kai/orbit", intent="brief", summary="x",
               message_type="brief", kind="comunicazione")
    _check(out["warning"] is None, f"nessun falso 'no live session': {out['warning']}")
    out = b.op("alita/work-hub", "send", to="kai/fantasma", intent="brief", summary="x",
               message_type="brief", kind="comunicazione")
    _check(out["warning"] and "no live session 'kai/fantasma'" in out["warning"],
           "l'avviso resta per una sessione davvero chiusa")


def caso_xn9lv0(b: Banco) -> None:
    """24/10 (iii): comunicazioni e messaggi del cloud senza task_id non si agganciano e non bloccano."""
    b.onda("kai/agent-team-os", "idle")
    out = b.op("kai/agent-team-os", "send", to="alita/work-hub", intent="review", summary="guarda",
               message_type="request", kind="delega")
    tid = out["task_id"]
    b.onda("kai/agent-team-os", None)          # la sessione delegante chiude
    b.avanza(600)                              # e il suo heartbeat scade
    b.onda("kai/orbit", "idle")
    _check(not b.stato("kai/agent-team-os").viva, "la delegante è chiusa")
    com = b.op("alita/work-hub", "send", to="kai", intent="onda-release", summary="comunicazione",
               message_type="brief", kind="comunicazione")
    cloud = b.consegna("kai", _msg("msg-20261024T013000Z-cloud001", "alita/cloud", "kai", "response",
                                   tempo.iso(b.t), summary="analisi notturna", context={"host": "cloud"}))
    b.giro()
    dec = b.decisioni("kai/orbit")
    _check(dec[com["id"]][:2] == ("nessuna", "niente"), f"comunicazione: niente sveglia {dec.get(com['id'])}")
    _check(dec[cloud][:2] == ("nessuna", "niente"), "cloud senza task_id: mai un ritorno")
    _check(b.sveglia.blocca_stop("kai", "orbit") == [], "non bloccano lo Stop")
    _check(not b.decisioni("kai/agent-team-os"), "nessuna sveglia cercata sulla sessione chiusa")
    row = b.c.store.message(cloud)
    _check(row["task_id"] is None, "il centralino non aggancia: task_id resta vuoto")
    r = b.op("kai/orbit", "read", message_id=com["id"])
    _check("_task_hint" not in r, "kai/orbit non si sente dire 'hai delegato'")
    b.op("kai/orbit", "handle", message_id=com["id"], esito="letto")
    c = b.op("kai/orbit", "handle", task_id=tid, esito="chiuso")   # passo 0: chiusura per task_id
    _check(c["state"] == "closed" and c["was"] == "expected", f"l'attesa della sessione chiusa si chiude: {c}")
    _check(b.c.store.wait(tid)["state"] == "closed", "SQLite allineato")


def caso_attese_non_chiudibili(b: Banco) -> None:
    """24/10 (iv): ritorni già archiviati, l'attesa si chiude con close."""
    b.onda("alita/work-hub", "idle")
    out = b.op("alita/work-hub", "send", to="kai/orbit", intent="x", summary="x", message_type="brief",
               kind="delega")
    tid = out["task_id"]
    ret = b.op("kai/orbit", "send", to="alita/work-hub", intent="fatto", summary="fatto",
               message_type="response", task_id=tid)
    b.op("alita/work-hub", "archive", message_id=ret["id"], state="done")
    w = b.op("alita/work-hub", "waits")
    _check([x["task_id"] for x in w["waits"]] == [tid], "dopo archive l'attesa resta aperta (comportamento di oggi)")
    try:
        b.op("alita/work-hub", "handle", message_id=ret["id"], esito="chiuso")
        raise AssertionError("handle su un messaggio archiviato deve rifiutare")
    except Rifiuto as exc:
        _check(exc.code == "already_filed", str(exc))
    c = b.op("alita/work-hub", "handle", task_id=tid, esito="chiuso")
    _check(c["state"] == "closed", "chiusura per task_id")
    _check(b.op("alita/work-hub", "waits")["count"] == 0, "nessuna attesa aperta")
    _check(b.c.store.wait(tid)["state"] == "closed", "SQLite allineato")


def caso_passaggio_attesa(b: Banco) -> None:
    """Passo 0 (F2.6): l'attesa passa a un'altra sessione dell'agente solo se chi ha delegato non c'è più."""
    b.onda("kai/a", "idle")
    b.onda("kai/b", "idle")
    tid = b.op("kai/a", "send", to="alita/work-hub", intent="x", summary="x", message_type="request",
               kind="delega")["task_id"]
    r1 = b.op("alita/work-hub", "send", to="kai", intent="q", summary="q", message_type="response", task_id=tid)
    try:
        b.op("kai/b", "handle", message_id=r1["id"], esito="riarma")
        raise AssertionError("kai/a è viva: kai/b non può riarmare")
    except Rifiuto:
        pass
    b.onda("kai/a", None)
    b.avanza(600)
    out = b.op("kai/b", "handle", message_id=r1["id"], esito="riarma")
    _check(out["wait"]["state"] == "expected", f"kai/a chiusa: l'attesa passa a kai/b {out}")


def caso_delega_completa(b: Banco) -> None:
    """Delega con domanda, risposta all'esecutore, esito e chiusura; esecutore che muore a metà."""
    b.onda("alita/work-hub", "working")
    b.onda("kai/onda", "idle")
    d = b.op("alita/work-hub", "send", to="kai/onda", intent="build", summary="build", message_type="request",
             kind="delega")
    tid = d["task_id"]
    b.giro()
    _check(b.decisioni("kai/onda")[d["id"]][:2] == ("delega", "sveglia"), "la delega sveglia l'esecutore fermo")
    q = b.op("kai/onda", "send", to="alita/work-hub", intent="dubbio", summary="A o B?", message_type="question",
             task_id=tid)
    b.giro()
    _check(b.decisioni("alita/work-hub")[q["id"]][1:] == ("coda", "al_lavoro:working"),
           "domanda a una sessione al lavoro: in coda")
    _check(b.sveglia.blocca_stop("alita", "work-hub") == [q["id"]], "la domanda blocca lo Stop")
    b.onda("alita/work-hub", "idle")
    b.giro()
    _check(b.decisioni("alita/work-hub")[q["id"]][1] == "sveglia", "a fine turno la domanda sveglia")
    r = b.op("alita/work-hub", "handle", message_id=q["id"], esito="risposto", reply="B")
    b.giro()
    _check(b.decisioni("kai/onda")[r["sent"]["id"]][:2] == ("risposta", "sveglia"),
           "la risposta sveglia l'esecutore (riga 3)")
    b.onda("kai/onda", None)                       # l'esecutore muore
    b.avanza(300)
    b.onda("alita/work-hub", "idle")
    late = b.consegna("kai/onda", _msg("msg-20261024T170000Z-tardivo1", "alita/work-hub", "kai/onda",
                                       "response", tempo.iso(b.t), task_id=tid))
    b.giro()
    _check(b.decisioni("kai/onda")[late][1:] == ("niente", "non_viva"), "sessione chiusa: nessuna sveglia")
    e = b.op("kai/onda", "send", to="alita/work-hub", intent="fatto", summary="fatto", message_type="response",
             task_id=tid, heartbeat=False)
    b.giro()
    _check(b.decisioni("alita/work-hub")[e["id"]][:2] == ("esito", "sveglia"), "l'esito sveglia il delegante")
    _check(b.op("alita/work-hub", "handle", message_id=e["id"], esito="chiuso")["wait"]["state"] == "closed",
           "chiuso")


def caso_mini_irraggiungibile(b: Banco) -> None:
    out = b.op("alita/work-hub", "send", to="kai@mini/crewmoor", intent="x", summary="x",
               message_type="brief", kind="comunicazione")
    _check(out["queued"] is True and out["host"] == "mini", f"in coda per il mini: {out}")
    _check(any((b.home / "outbox" / "mini").rglob("msg-*.json")), "file in outbox/mini")
    row = b.c.store.read().execute("SELECT host FROM remote_queue WHERE msg_id=?", (out["id"],)).fetchone()
    _check(row is not None and row["host"] == "mini", "coda registrata in SQLite")


def caso_due_sessioni(b: Banco) -> None:
    """Proprietà dell'attesa per sessione: due sessioni di kai, un'attesa ciascuna."""
    b.onda("kai/a", "idle")
    b.onda("kai/b", "idle")
    ta = b.op("kai/a", "send", to="alita/work-hub", intent="x", summary="x", message_type="request",
              kind="delega")["task_id"]
    tb = b.op("kai/b", "send", to="alita/work-hub", intent="y", summary="y", message_type="request",
              kind="delega")["task_id"]
    ret = b.op("alita/work-hub", "send", to="kai", intent="fatto", summary="fatto a", message_type="response",
               task_id=ta)
    b.giro()
    _check(ret["id"] in b.decisioni("kai/a") and ret["id"] not in b.decisioni("kai/b"),
           "un ritorno nella casella condivisa va solo alla sessione proprietaria")
    _check(b.op("kai/a", "read", message_id=ret["id"]).get("_task_hint", "").startswith("Ritorno"),
           "kai/a riconosce il suo ritorno")
    _check(b.op("kai/b", "read", message_id=ret["id"]).get("_task_hint", "").startswith("Messaggio sul task"),
           "kai/b sa che il task è di un'altra sessione")
    try:
        b.op("kai/b", "handle", message_id=ret["id"], esito="riarma")
        raise AssertionError("kai/b non può riarmare l'attesa di kai/a")
    except Rifiuto:
        pass
    _check(b.op("kai/a", "waits")["count"] == 1 and b.op("kai/b", "waits")["count"] == 1, "un'attesa ciascuna")
    _check(tb != ta, "task diversi")


def caso_heartbeat_senza_canale(b: Banco) -> None:
    """Sessione fuori da Onda (Alita in claude attach): viva per heartbeat, canale nessuno."""
    b.op("alita/work-hub", "session.event", event="start")  # F6.13: viva solo dopo un evento di sessione
    b.op("alita/work-hub", "session.heartbeat")
    st = b.stato("alita/work-hub")
    _check(st.viva and st.descrizione == "viva per heartbeat, canale: nessuno", st.descrizione)
    out = b.op("kai/orbit", "send", to="alita/work-hub", intent="x", summary="x", message_type="request",
               kind="delega")
    b.giro()
    _check(b.decisioni("alita/work-hub")[out["id"]][1:] == ("coda", "nessun_canale"), "in coda, nessun canale")
    b.avanza(121)
    _check(not b.stato("alita/work-hub").viva, "due battiti persi: chiusa")


def caso_bg_spare(b: Banco) -> None:
    """BR24-F6.13: un processo bg-spare di Claude Code carica il MCP e batte senza essere una
    sessione; e dopo la fine di una sessione il suo battito non la riapre (kai/onda-electron, 25/10)."""
    b.op("kai/onda-electron", "session.heartbeat")
    _check(not b.stato("kai/onda-electron").viva, "un battito senza eventi di sessione non è una sessione")
    b.op("kai/orbit", "session.event", event="start")
    _check(b.stato("kai/orbit").viva, "dopo start è viva")
    b.op("kai/orbit", "session.event", event="end")
    b.op("kai/orbit", "session.heartbeat")
    _check(not b.stato("kai/orbit").viva, "dopo end un battito non la riapre")
    b.op("kai/orbit", "session.event", event="start")
    _check(b.stato("kai/orbit").viva, "una sessione nuova nella stessa cartella sì")


# ======================================================================
# Scenari dell'uso reale: sveglia, hook, cloud, pulizia (F4.1, F4.2, F4.4, F5.1, F5.4)
# ======================================================================

def _nessun_file_in_casella(b: Banco) -> None:
    files = [p for p in (b.home / "inboxes").rglob("msg-*.json")]
    _check(not files, f"in modo attivo nessun file di casella: {files[:3]}")


def att_delega_completa(b: Banco) -> None:
    b.onda("alita/work-hub", "idle")
    b.onda("kai/onda", "idle")
    d = b.op("alita/work-hub", "send", to="kai/onda", intent="build", summary="fai la build",
             message_type="request", kind="delega")
    tid = d["task_id"]
    b.giro()
    _check(len(b.prompt_a("kai/onda")) == 1 and "nuova delega" in b.prompt_a("kai/onda")[0].lower(),
           f"la delega sveglia l'esecutore: {b.prompt}")
    q = b.op("kai/onda", "reply", message_id=d["id"], summary="A o B?", message_type="question")
    _check(q["sent"]["to"] == "alita/work-hub", f"la domanda va a chi ha delegato: {q}")
    b.onda("alita/work-hub", "working")
    b.giro()
    _check(not b.prompt_a("alita/work-hub"), "alita lavora: nessuna sveglia")
    inbox = b.op("alita/work-hub", "inbox")
    _check(inbox["mie_attese"][0]["da_gestire"][0]["pending_wake"] == "al_lavoro:working",
           f"il motivo del rinvio si vede in bus_inbox: {inbox.get('mie_attese')}")
    b.onda("alita/work-hub", "idle")
    b.giro()
    _check(len(b.prompt_a("alita/work-hub")) == 1 and "domanda sul task" in b.prompt_a("alita/work-hub")[0].lower(),
           f"a fine turno la domanda sveglia alita: {b.prompt}")
    qid = inbox["messages"][0]["id"]
    r = b.op("alita/work-hub", "reply", message_id=qid, summary="B")
    b.giro()
    _check(len(b.prompt_a("kai/onda")) == 2 and "risposta sul task" in b.prompt_a("kai/onda")[1].lower(),
           "la risposta sveglia l'esecutore")
    _check(b.op("alita/work-hub", "inbox")["count"] == 0, "la domanda risposta lascia la casella")
    e = b.op("kai/onda", "reply", message_id=r["sent"]["id"], summary="fatto, build verde")
    _check(e["sent"]["to"] == "alita/work-hub", "l'esito torna al delegante")
    b.giro()
    _check("ritorno sul task" in b.prompt_a("alita/work-hub")[-1].lower(), "l'esito sveglia il delegante")
    eid = b.op("alita/work-hub", "inbox")["messages"][0]["id"]
    c = b.op("alita/work-hub", "close", message_id=eid, esito="chiuso")
    _check(c["wait"]["state"] == "closed" and c["remaining"] == 0, f"chiuso: {c}")
    th = b.op("alita/work-hub", "read", thread_id=b.op("alita/work-hub", "read", message_id=eid)["thread_id"])
    _check(th["count"] == 4, f"il thread ha delega, domanda, risposta, esito: {th['count']}")
    _nessun_file_in_casella(b)


def att_stop(b: Banco) -> None:
    b.onda("kai/orbit", "idle")
    b.op("alita/work-hub", "send", to="kai/orbit", intent="x", summary="x", message_type="brief", kind="comunicazione")
    s = b.op("kai/orbit", "hook.stop")
    _check(s["block"] is False, f"una comunicazione non blocca lo Stop: {s}")
    b.op("alita/work-hub", "send", to="kai/orbit", intent="y", summary="y", message_type="request", kind="delega")
    d = next(m for m in b.op("kai/orbit", "inbox")["messages"] if m["intent"] == "y")
    blocchi = [b.op("kai/orbit", "hook.stop")["block"] for _ in range(4)]
    _check(blocchi == [True, True, True, False], f"blocca 3 volte, poi allenta: {blocchi}")
    # BR24-F6.14: il tetto è del messaggio, non del turno: altri turni (sveglie, notifiche) non lo riaprono
    _check([b.op("kai/orbit", "hook.stop")["block"] for _ in range(3)] == [False] * 3, "non riparte a ogni turno")
    b.avanza(5)
    b.op("alita/work-hub", "send", to="kai/orbit", intent="z", summary="altra", message_type="request", kind="delega")
    s = b.op("kai/orbit", "hook.stop")
    _check(s["block"] is True and s["pending"] == 1, f"una delega nuova blocca, la vecchia no: {s}")
    b.avanza(5)
    b.op("alita/work-hub", "send", to="kai/orbit", intent="w", summary="novità", message_type="event",
         task_id=d["task_id"])
    s = b.op("kai/orbit", "hook.stop")
    _check(s["block"] is True and s["pending"] == 2, f"una novità sul task rimette in conto la vecchia: {s}")


def att_hook_start(b: Banco) -> None:
    b.op("alita/work-hub", "send", to="kai/orbit", intent="ciao", summary="benvenuto", message_type="brief",
         kind="comunicazione", priority="high")
    st = b.op("kai/orbit", "hook.start")
    _check("Inbox: 1 pending (1 urgent/high)" in st["banner"] and st["persona"] == "# Kai\n", st["banner"])
    reg = read_json(b.home / "registry" / "kai.d" / "orbit.json")
    _check(reg and reg["pid"] == 1 and reg["active"], f"vista di compatibilità per Onda: {reg}")
    p = b.op("kai/orbit", "hook.prompt")
    _check(p["additionalContext"] and "1 urgent/high" in p["additionalContext"], "avviso degli urgenti")
    b.op("kai/orbit", "hook.end")
    _check(not (b.home / "registry" / "kai.d" / "orbit.json").exists(), "fine sessione: fuori dal registro di Onda")


def att_scadenza(b: Banco) -> None:
    b.onda("alita/work-hub", "idle")
    dl = tempo.iso(b.t + timedelta(minutes=30))
    d = b.op("alita/work-hub", "send", to="kai/orbit", intent="x", summary="x", message_type="request",
             kind="delega", response_by=dl)
    b.avanza(3600)
    b.onda("alita/work-hub", "idle")
    b.giro()
    w = b.c.store.wait(d["task_id"])
    _check(w["state"] == "expired", f"deadline passata senza ritorni: scaduta ({w['state']})")
    ib = b.op("alita/work-hub", "inbox")["messages"]
    _check(sorted(m["intent"] for m in ib) == ["messaggi-fermi-senza-canale", "task-scaduto"],
           f"avviso a chi aspetta, e la delega a kai/orbit ferma senza canale (AM26-F2.2): {ib}")
    _check(not b.prompt_a("alita/work-hub"), "l'avviso di scadenza non sveglia")


def att_senza_canale(b: Banco) -> None:
    """Una delega a una sessione viva ma senza canale: in coda, e dopo 10 minuti un solo avviso
    azionabile all'hub (che ha un canale), che lo sveglia."""
    b.onda("alita/work-hub", "idle")
    b.op("kai/orbit", "session.event", event="start")
    b.op("kai/orbit", "session.heartbeat")
    out = b.op("alita/work-hub", "send", to="kai/orbit", intent="x", summary="x", message_type="request",
               kind="delega")
    b.giro()
    _check(b.decisioni("kai/orbit")[out["id"]][1:] == ("coda", "nessun_canale"), "in coda, nessun canale")
    fermi = lambda: [m for m in b.op("alita/work-hub", "inbox")["messages"]  # noqa: E731
                     if m["intent"] == "messaggi-fermi-senza-canale"]
    b.avanza(301)
    b.op("kai/orbit", "session.heartbeat")
    b.giro()
    _check(not fermi(), "a 5 minuti ancora niente")
    b.avanza(301)
    b.op("kai/orbit", "session.heartbeat")
    b.onda("alita/work-hub", "idle")
    b.giro()
    _check(len(fermi()) == 1 and "kai/orbit" in fermi()[0]["summary"], f"un avviso dopo 10 minuti: {fermi()}")
    b.avanza(60)
    b.op("kai/orbit", "session.heartbeat")
    b.onda("alita/work-hub", "idle")
    b.giro()
    _check(len(fermi()) == 1, "uno solo per gruppo")
    _check(any("URGENTE" in t for t in b.prompt_a("alita/work-hub")), "l'avviso sveglia l'hub")


def caso_am26_cloud_a_kai_nudo(b: Banco) -> None:
    """AM26-F2.2, 26/10: quattro richieste di Mario da alita/cloud a `kai` (nudo). L'unica sessione
    viva di Kai era kai/lighthouse, viva per battito ma senza pannello: le richieste sono rimaste
    ferme 4 ore e ad Alita arrivavano solo eventi "sveglia senza canale"."""
    b.onda("alita/work-hub", "idle")
    b.op("kai/lighthouse", "session.event", event="start")
    b.op("kai/lighthouse", "session.heartbeat")
    ids = []
    for i, intent in enumerate(("alita-mobile-fix", "alita-mobile-nuova-sessione",
                                "alita-mobile-android-auto", "alita-mobile-android-auto")):
        rec = _msg(f"msg-20261024T16000{i}Z-am26cl0{i}", "alita/cloud", "kai", "request", tempo.iso(b.t),
                   intent=intent, requires_response=True, context={"host": "cloud"})
        ids.append(b.consegna("kai", rec))
    # un invio a `kai` nudo da questa macchina: lighthouse non si sceglie, resta nella casella condivisa
    nudo = b.op("alita/work-hub", "send", to="kai", intent="y", summary="y", message_type="request",
                kind="comunicazione")
    _check(nudo["delivered_to"] == "kai", f"nudo senza sessioni con canale: casella condivisa ({nudo['delivered_to']})")
    b.giro()
    _check(not b.decisioni("kai/lighthouse"), "kai/lighthouse senza canale non riceve niente")
    _check(b.sveglia.blocca_stop("kai", "lighthouse") == [], "lo Stop di lighthouse non si blocca")
    for _ in range(2):
        b.avanza(301)
        b.op("kai/lighthouse", "session.heartbeat")
        b.onda("alita/work-hub", "idle")
        b.giro()
    fermi = [m for m in b.op("alita/work-hub", "inbox")["messages"] if m["intent"] == "messaggi-fermi-senza-canale"]
    _check(len(fermi) == 1, f"un solo avviso per il gruppo kai da alita/cloud: {fermi}")
    sm = fermi[0]["summary"]
    _check(sm.startswith("4 messaggi per kai da alita/cloud@cloud fermi da 10 min")
           and "vive senza canale: lighthouse" in sm and all(i in sm for i in ids), sm)
    _check(fermi[0]["priority"] == "urgent", "azionabile: sveglia Alita e ne tiene lo Stop")
    b.avanza(30)
    b.onda("alita/work-hub", "idle")
    b.giro()
    _check(any("URGENTE" in t for t in b.prompt_a("alita/work-hub")), "Alita svegliata")
    # Mario apre kai/alita-mobile in Onda: la prima sessione con canale le riceve tutte e quattro
    b.onda("kai/alita-mobile", "idle")
    b.giro()
    dec = b.decisioni("kai/alita-mobile")
    _check(all(dec.get(i, ("", ""))[1] == "sveglia" for i in ids), f"svegliata con le quattro: {dec}")
    ib = {m["id"] for m in b.op("kai/alita-mobile", "inbox")["messages"]}
    _check(set(ids) <= ib, "le vede in bus_inbox")


def caso_am26_tmux_e_hub_muto(b: Banco) -> None:
    """AM26-F2.2 sul mini: tmux è un canale come Onda. E se l'hub stesso non ha canale, una notifica
    di sistema al posto del messaggio."""
    notifiche: list[tuple[str, str]] = []
    b.sveglia.notifica = lambda titolo, testo: notifiche.append((titolo, testo))
    b.c.handle_request({"v": 1, "id": "t", "op": "hook.start", "args": {},
                        "caller": {**b.caller("kai/crewmoor"), "tmux_pane": "%9"}})
    _check(b.c.vivo.slug_con_canale("kai") == ["crewmoor"], "tmux conta come canale")
    out = b.op("leo/x", "send", to="kai", intent="z", summary="z", message_type="request", kind="comunicazione")
    _check(out["delivered_to"] == "kai/crewmoor", f"l'unica con canale (tmux): {out['delivered_to']}")
    b.op("alita/work-hub", "session.event", event="start")  # Alita viva ma senza canale
    rec = _msg("msg-20261024T160500Z-am26hub0", "alita/cloud", "leo", "request", tempo.iso(b.t),
               requires_response=True, context={"host": "cloud"})
    b.consegna("leo", rec)
    for _ in range(2):
        b.avanza(301)
        b.op("alita/work-hub", "session.heartbeat")
        b.c.handle_request({"v": 1, "id": "h", "op": "session.heartbeat", "args": {},
                            "caller": {**b.caller("kai/crewmoor"), "tmux_pane": "%9"}})
        b.giro()
    _check(len(notifiche) == 1 and "per leo da alita/cloud@cloud" in notifiche[0][1], f"notifica: {notifiche}")
    _check(not [m for m in b.op("alita/work-hub", "inbox")["messages"]
                if m["intent"] == "messaggi-fermi-senza-canale"], "niente messaggio a un hub muto")
    b.avanza(60)
    b.giro()
    _check(len(notifiche) == 1, "una notifica per gruppo")


def att_agente_nudo(b: Banco) -> None:
    b.onda("kai/orbit", "idle")
    out = b.op("alita/work-hub", "send", to="kai", intent="x", summary="x", message_type="brief", kind="comunicazione")
    _check(out["delivered_to"] == "kai/orbit", f"una sola sessione viva: va a lei ({out['delivered_to']})")
    b.onda("kai/onda", "idle")
    out = b.op("alita/work-hub", "send", to="kai", intent="x", summary="x", message_type="brief", kind="comunicazione")
    _check(out["delivered_to"] == "kai", f"due vive e nessuna di casa: casella condivisa ({out['delivered_to']})")


def att_cloud(b: Banco) -> None:
    b.onda("kai/orbit", "idle")
    d = b.op("kai/orbit", "send", to="alita@cloud", intent="analisi", summary="analizza", message_type="request",
             kind="delega")
    _check(d["queued"] is True, "verso il cloud: coda in outbox/cloud")
    senza = _msg("msg-20261024T200000Z-cloudnot", "alita/cloud", "kai", "response", tempo.iso(b.t),
                 context={"host": "cloud"})
    con = _msg("msg-20261024T200100Z-cloudtid", "alita/cloud", "kai", "response", tempo.iso(b.t),
               context={"host": "cloud"}, task_id=d["task_id"])
    r1 = b.op(None, "consegna", record=senza, agent="kai")
    r2 = b.op(None, "consegna", record=con, agent="kai", slug="orbit")
    _check(r1["nuovo"] and r2["nuovo"], "consegnati")
    _check(not b.op(None, "consegna", record=con, agent="kai", slug="orbit")["nuovo"], "una seconda consegna non duplica")
    b.giro()
    dec = b.decisioni("kai/orbit")
    _check(dec[senza["id"]][0] == "nessuna", f"senza task_id non è un ritorno: {dec[senza['id']]}")
    _check(dec[con["id"]][:2] == ("esito", "sveglia"), f"con task_id è l'esito: {dec[con['id']]}")
    w = b.c.store.wait(d["task_id"])
    _check(con["id"] in json.loads(w["raw"])["returns"], "ritorno registrato sull'attesa")


def att_pulizia(b: Banco) -> None:
    from .pulizia import Pulizia
    b.onda("kai/vecchia", "idle")
    b.op("kai/vecchia", "hook.start")
    d = b.op("alita/work-hub", "send", to="kai/vecchia", intent="x", summary="x", message_type="request",
             kind="delega")
    c = b.op("alita/work-hub", "send", to="kai/vecchia", intent="y", summary="y", message_type="brief",
             kind="comunicazione")
    b.op("kai/vecchia", "hook.end")
    b.onda("kai/vecchia", None)
    b.onda("kai/orbit", "idle")
    b.avanza(25 * 3600)
    b.onda("kai/orbit", "idle")
    out = Pulizia(b.c).giro()
    # due sessioni escono: kai/vecchia (chiusa) e alita/work-hub (nessun segno di vita da 25 ore)
    _check(out["spostati"] == 1 and out["archiviati"] == 1 and out["sessioni"] == 2, f"pulizia: {out}")
    _check(b.c.store.message(d["id"])["to_slug"] == "orbit", "la delega passa all'unica sessione viva")
    _check(b.c.store.message(c["id"])["state"] == "read", "la comunicazione si archivia")


SCENARI = [caso_irjocy, caso_orbit_non_viva, caso_xn9lv0, caso_attese_non_chiudibili, caso_passaggio_attesa,
           caso_delega_completa, caso_mini_irraggiungibile, caso_due_sessioni, caso_heartbeat_senza_canale, caso_bg_spare,
           att_delega_completa, att_stop, att_hook_start, att_scadenza, att_senza_canale, att_agente_nudo,
           att_cloud, att_pulizia, caso_am26_cloud_a_kai_nudo, caso_am26_tmux_e_hub_muto]


def esegui(scenario, base: Path | None = None) -> None:
    tmp = Path(tempfile.mkdtemp(prefix="centralino-sim-", dir=base))
    b = Banco(tmp / "bus")
    try:
        scenario(b)
    finally:
        b.chiudi()
        shutil.rmtree(tmp, ignore_errors=True)


def main(verbose: bool = False) -> int:
    falliti = 0
    for s in SCENARI:
        try:
            esegui(s)
            print(f"ok    {s.__name__}")
        except Exception as exc:  # noqa: BLE001
            falliti += 1
            print(f"FALLITO {s.__name__}: {exc}")
            if verbose:
                traceback.print_exc()
    n = len(SCENARI)
    print(f"\n{n - falliti}/{n} scenari ok")
    return 1 if falliti else 0
