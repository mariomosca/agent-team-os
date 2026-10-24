"""Tabella di sveglia e blocco dello Stop (contratto §6). Funzioni pure, senza I/O.

| regola         | riconoscimento                                                      | ferma   | al lavoro | Stop |
|----------------|---------------------------------------------------------------------|---------|-----------|------|
| esito          | response col task_id di un'attesa expected della sessione           | sveglia sempre, fuori tetto | coda | sì |
| domanda        | question col task_id di un'attesa expected della sessione           | sveglia | coda      | sì   |
| risposta       | response con task_id: attesa expected di un altro, o nessuna attesa qui (il delegante risponde all'esecutore) | sveglia | coda | sì |
| delega         | request/brief/handoff con kind=delega (o vecchio, senza kind, con requires_response) | sveglia | coda | sì |
| da_agganciare  | task_link.by = da_agganciare (lo scrive wake.py nel passo 1)        | sveglia | coda      | sì   |
| urgente        | priority=urgent, nessuna delle precedenti                            | sveglia, 1 ogni 15 min per coppia di sessioni | coda | sì |
| domanda_libera | question fuori da una delega aperta della sessione                  | niente  | niente    | sì   |
| nessuna        | tutto il resto                                                      | niente  | niente    | no   |

Lo Stop segue ``ab_msg_actionable`` della lib dopo il passo 0 (BR24-F2.7), così nel confronto
ombra restano solo le differenze vere. Un messaggio senza task_id non è mai un ritorno. Un
solo tetto: 10 sveglie all'ora per sessione, l'esito non conta e non è mai trattenuto. Più
messaggi per la stessa sessione nello stesso giro fanno una sola sveglia.
"""

from __future__ import annotations

from dataclasses import dataclass

TETTO_ORARIO = 10
URGENTI_COPPIA_S = 15 * 60
DELEGA_TYPES = ("request", "brief", "handoff")
SVEGLIANO = ("esito", "domanda", "risposta", "delega", "da_agganciare", "urgente")
BLOCCANO = SVEGLIANO + ("domanda_libera",)


@dataclass(frozen=True)
class Attesa:
    task_id: str
    owner_agent: str
    owner_slug: str
    executor_session: str | None    # agente/slug di chi esegue, quando noto
    delegated_to: str | None        # indirizzo dato alla delega
    state: str

    @property
    def aperta(self) -> bool:
        return self.state == "expected"


def classifica(msg: dict, agent: str, slug: str, attese: dict[str, Attesa]) -> str:
    """Regola del messaggio ``msg`` (record v1.0) per la sessione destinataria agent/slug.
    ``attese``: tutte le attese note (aperte e chiuse), per task_id."""
    payload = msg.get("payload") if isinstance(msg.get("payload"), dict) else {}
    link = msg.get("task_link") if isinstance(msg.get("task_link"), dict) else {}
    raw_tid = msg.get("task_id") or payload.get("task_id")
    tid = raw_tid if isinstance(raw_tid, str) else None
    typ = msg.get("type")
    w = attese.get(tid) if tid else None
    mine = w is not None and w.owner_agent == agent and w.owner_slug == slug
    if w is not None and w.aperta and mine:
        if typ == "response":
            return "esito"
        if typ == "question":
            return "domanda"
    if typ == "response" and tid and (w is None or (w.aperta and not mine)):
        return "risposta"
    if link.get("by") == "da_agganciare":
        return "da_agganciare"
    if typ in DELEGA_TYPES and (msg.get("kind") == "delega"
                                or (not msg.get("kind") and msg.get("requires_response") is True)):
        return "delega"
    if msg.get("priority") == "urgent":
        return "urgente"
    if typ == "question":
        return "domanda_libera"
    return "nessuna"


def destinatari(msg: dict, to_agent: str, to_slug: str | None, attese: dict[str, Attesa],
                vive: list[str]) -> list[str]:
    """Slug delle sessioni a cui il messaggio è rivolto. Casella condivisa (to_slug None):
    il proprietario dell'attesa se il messaggio è un ritorno, altrimenti ogni sessione viva
    (semantica di oggi nel passo 1)."""
    if to_slug:
        return [to_slug]
    tid = msg.get("task_id") if isinstance(msg.get("task_id"), str) else None
    w = attese.get(tid) if tid else None
    if w is not None and w.aperta and w.owner_agent == to_agent and msg.get("type") in ("response", "question"):
        return [w.owner_slug]
    return list(vive)


def blocca_stop(regola: str) -> bool:
    return regola in BLOCCANO


@dataclass(frozen=True)
class Decisione:
    msg_id: str
    regola: str
    decisione: str          # sveglia | coda | niente
    motivo: str | None
    canale: str | None


def decidi(stato, items: list[tuple[str, str, str | None]], sveglie_ultima_ora: int,
           urgenti_recenti: set[str]) -> list[Decisione]:
    """Decisioni per UNA sessione in un giro.

    ``stato``: StatoSessione (viva, canale, ferma, onda_state).
    ``items``: (msg_id, regola, mittente agente/slug) dei messaggi non ancora svegliati.
    ``sveglie_ultima_ora``: sveglie non-esito della sessione nell'ultima ora.
    ``urgenti_recenti``: mittenti che hanno già svegliato questa sessione con un urgente
    negli ultimi 15 minuti.
    """
    out: list[Decisione] = []
    attivi = [(m, r, f) for m, r, f in items if r in SVEGLIANO]
    for m, r, _ in items:
        if r not in SVEGLIANO:
            out.append(Decisione(m, r, "niente", None, None))
    if not attivi:
        return out
    if not stato.viva:
        return out + [Decisione(m, r, "niente", "non_viva", None) for m, r, _ in attivi]
    if stato.canale == "nessuno":
        return out + [Decisione(m, r, "coda", "nessun_canale", None) for m, r, _ in attivi]
    if not stato.ferma:
        motivo = f"al_lavoro:{stato.onda_state}" if stato.onda_state else "al_lavoro"
        return out + [Decisione(m, r, "coda", motivo, None) for m, r, _ in attivi]

    # chi può svegliare adesso, prima dell'accorpamento
    def sveglia_da_sola(r: str, frm: str | None) -> tuple[bool, str | None]:
        if r == "esito":
            return True, None
        if sveglie_ultima_ora >= TETTO_ORARIO:
            return False, "tetto_sessione"
        if r == "urgente" and frm in urgenti_recenti:
            return False, "tetto_urgenti"
        return True, None

    verdetti = [(m, r, *sveglia_da_sola(r, f)) for m, r, f in attivi]
    se_sveglia = any(ok for _, _, ok, _ in verdetti)
    for m, r, ok, motivo in verdetti:
        if se_sveglia:
            # accorpamento: chi era trattenuto viaggia con la sveglia del giro
            out.append(Decisione(m, r, "sveglia", None if ok else f"accorpato:{motivo}", stato.canale))
        else:
            out.append(Decisione(m, r, "coda", motivo, None))
    return out
