"""Ciclo di vita delle sessioni delegate (t-20261029-lbgswf, punto B).

Una sessione aperta per una delega restava accesa per sempre dopo l'esito, e "Squadra: N vive"
contava sessioni finite. Qui il centralino la PROPONE per la chiusura a chi l'ha delegata, senza
chiuderla: la chiusura resta una decisione del delegante.

Una sessione di questa macchina è proposta quando:

- è delegata: aperta con ``SESSIONE_DELEGATA=1`` (colonna ``sessions.delegata``) oppure
  destinataria di un'attesa di un'altra sessione negli ultimi giorni (``onda_agent_spawn`` e poi
  ``[delega]``, o ``bus_send``);
- non ha attese aperte su di sé (nessuna delega ancora da chiudere);
- è ferma da più di ``IDLE_H`` ore: Onda la dà ``idle`` da allora, o, senza Onda, l'ultimo evento
  di sessione è uno Stop di allora;
- la cartella è pulita e pushata (``git status --porcelain`` vuoto, nessun commit fuori dal
  remoto). Una cartella che non è un repo git conta come pulita, e la proposta lo dice.

La proposta è un evento ``sessione-da-chiudere`` (riga 6: non sveglia, non blocca lo Stop) al
delegante dell'ultima attesa, una volta per sessione Claude (chiave ``chiudi:<agente>/<slug>:<id>``
in ``meta``): nella banda compare fra le cose da fare, nel banner di SessionStart fra i messaggi.
"""

from __future__ import annotations

import json
import subprocess
from datetime import timedelta
from pathlib import Path

from .dbbus import stesso_destinatario
from .indirizzi import parse_addr
from .tempo import age_s, iso, iso_now, now

IDLE_H = 2
GIORNI_DELEGA = 7
OGNI_S = 300            # il controllo (con git) gira al più ogni 5 minuti
INTENT = "sessione-da-chiudere"


def stato_git(path: str | None) -> tuple[bool, str]:
    """(pulita e pushata, come lo si sa). Mai un'eccezione."""
    if not path or not Path(path).is_dir():
        return False, "cartella non trovata"

    def git(*args: str) -> subprocess.CompletedProcess | None:
        try:
            return subprocess.run(["git", "-C", path, *args], capture_output=True, text=True, timeout=10)
        except (OSError, subprocess.TimeoutExpired):
            return None

    r = git("rev-parse", "--is-inside-work-tree")
    if r is None:
        return False, "git non risponde"
    if r.returncode != 0:
        return True, "non è un repo git"
    r = git("status", "--porcelain")
    if r is None or r.returncode != 0:
        return False, "git status fallito"
    if r.stdout.strip():
        return False, f"{len(r.stdout.strip().splitlines())} file non committati"
    # commit non su nessun ramo remoto
    r = git("rev-list", "--count", "HEAD", "--not", "--remotes")
    if r is None or r.returncode != 0:
        return False, "git rev-list fallito"
    n = int((r.stdout or "0").strip() or 0)
    if n:
        return False, f"{n} commit non pushati"
    return True, "pulita e pushata"


class CicloVita:
    def __init__(self, central, git=stato_git):
        self.c = central
        self.git = git
        self.ultimo = 0.0

    def deleghe(self, agent: str, slug: str, host: str) -> list[dict]:
        """Deleghe ricevute da questa sessione negli ultimi GIORNI_DELEGA, le più recenti prima:
        ``{task_id, delegante, aperta, at}``. Dalle attese di questa macchina (anche quelle aperte
        da un prompt via Onda) e, per le deleghe arrivate da un'altra macchina, dai messaggi: lì
        l'attesa sta dal delegante, e la delega è chiusa per noi quando la sessione ha mandato un
        esito (``response``) sul task."""
        me = f"{agent}/{slug}"
        da = iso(now() - timedelta(days=GIORNI_DELEGA))
        r = self.c.store.read()
        out: dict[str, dict] = {}
        for x in r.execute("SELECT raw FROM waits WHERE created_at >= ?", (da,)):
            w = json.loads(x["raw"])
            if f"{w.get('agent')}/{w.get('slug')}" == me:
                continue
            esec = w.get("executor_session") or w.get("delegated_to")
            try:
                _, sl, h = parse_addr(esec or "")
            except Exception:  # noqa: BLE001
                continue
            if not sl or (h and host and h != host) or not stesso_destinatario(esec, me):
                continue
            chi = f"{w['agent']}/{w['slug']}"
            if w.get("host") and w.get("host") != host:
                chi = f"{w['agent']}@{w['host']}/{w['slug']}"
            out[w["task_id"]] = {"task_id": w["task_id"], "delegante": chi,
                                 "aperta": w.get("state") == "expected", "at": w.get("created_at") or ""}
        for x in r.execute("SELECT task_id, ts, raw FROM messages WHERE to_agent=? AND to_slug=? AND kind='delega' "
                           "AND ts >= ? AND task_id IS NOT NULL", (agent, slug, da)):
            if x["task_id"] in out:
                continue
            m = json.loads(x["raw"])
            chi = m.get("from_session") or m.get("from")
            h = (m.get("context") or {}).get("host") if isinstance(m.get("context"), dict) else None
            if chi and h and h != host:
                a, _, s_ = chi.partition("/")
                chi = f"{a}@{h}/{s_}" if s_ else f"{a}@{h}"
            esito = r.execute("SELECT 1 FROM messages WHERE from_agent=? AND from_slug=? AND type='response' "
                              "AND task_id=? LIMIT 1", (agent, slug, x["task_id"])).fetchone()
            out[x["task_id"]] = {"task_id": x["task_id"], "delegante": chi, "aperta": esito is None,
                                 "at": x["ts"]}
        return sorted(out.values(), key=lambda d: d["at"], reverse=True)

    def candidate(self) -> list[dict]:
        host = self.c.amap.host_self() or ""
        out = []
        for st in self.c.vivo.vive():
            row = self.c.store.read().execute("SELECT * FROM sessions WHERE agent=? AND slug=? AND host=?",
                                              (st.agent, st.slug, host)).fetchone()
            deleghe = self.deleghe(st.agent, st.slug, host)
            delegata = bool(row is not None and row["delegata"]) or bool(deleghe)
            if not delegata or any(d["aperta"] for d in deleghe):
                continue
            e = self.c.onda.find(st.agent, st.slug, host, row["claude_session_id"] if row else None)
            if e is not None and e.get("state") not in (None, "exited"):
                if e.get("state") != "idle":
                    continue
                ferma = age_s(e.get("stateSince"))
            elif row is not None and row["last_event"] == "stop":
                ferma = age_s(row["last_event_at"])
            else:
                continue
            if ferma is None or ferma < IDLE_H * 3600:
                continue
            out.append({"agent": st.agent, "slug": st.slug, "row": row, "onda": e, "ferma_s": ferma,
                        "delegante": deleghe[0]["delegante"] if deleghe else None,
                        "ultima": deleghe[0]["task_id"] if deleghe else None,
                        "workspace": st.workspace or (row["workspace_path"] if row else None)})
        return out

    def giro(self, forza: bool = False) -> list[str]:
        t = now().timestamp()
        if not forza and t - self.ultimo < OGNI_S:
            return []
        self.ultimo = t
        r = self.c.store.read()
        proposte = []
        for k in self.candidate():
            sid = (k["row"]["claude_session_id"] if k["row"] is not None else None) or \
                (k["onda"] or {}).get("sessionId") or "?"
            key = f"chiudi:{k['agent']}/{k['slug']}:{sid}"
            if r.execute("SELECT 1 FROM meta WHERE key=?", (key,)).fetchone():
                continue
            pulita, come = self.git(k["workspace"])
            if not pulita:
                continue
            if not k["delegante"]:
                continue  # solo SESSIONE_DELEGATA senza attese note: non si sa a chi proporla
            ore = int(k["ferma_s"] // 3600)
            term = (k["onda"] or {}).get("terminalId")
            come_chiudere = (f"onda_agent_close con killPty e terminalId {term}" if term
                             else "chiudila dal suo terminale (tmux o Onda)")
            testo = (f"La sessione delegata {k['agent']}/{k['slug']} non ha più deleghe aperte (ultima: "
                     f"{k['ultima']}), è ferma da {ore} ore e la cartella è {come}. Se non ti serve più, "
                     f"chiudila: {come_chiudere}. Proposta unica per questa sessione: il centralino non la "
                     "chiude da solo.")
            try:
                me_agent = k["agent"]
                out = self.c.bus.send(me_agent, k["delegante"], "event", INTENT,
                                      {"summary": testo, "sessione": f"{k['agent']}/{k['slug']}",
                                       "terminalId": term, "ultima_delega": k["ultima"], "git": come},
                                      sender_session=k["slug"])
                proposte.append(out["id"])
                self.c.log("info", "sessione_da_chiudere", sessione=f"{k['agent']}/{k['slug']}",
                           a=k["delegante"], ore=ore)
            except Exception as exc:  # noqa: BLE001 — una proposta mancata non ferma il giro
                self.c.log("warn", "sessione_da_chiudere_fallita", sessione=f"{k['agent']}/{k['slug']}",
                           errore=str(exc))
            self.c._write(lambda key=key: self.c._conn().execute(
                "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (key, iso_now())))
        return proposte
