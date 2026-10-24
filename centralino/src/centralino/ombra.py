"""Modalità ombra (contratto §7, BR24-F3.4).

Tre giri, lanciati dal daemon:

- ``importa()`` ogni 5 s: i file scritti da altri (lib, wake.py, alita-memoria-sync,
  hook onda-prompt, raccolta del mini) entrano in SQLite. Una cartella si rilegge solo se
  il suo mtime è cambiato: ogni scrittura del bus è tmp+rename, quindi cambia la cartella.
- ``giro_sveglia()`` ogni 10 s: la tabella di sveglia (regole.py) decide per ogni messaggio
  pending e scrive la decisione in ``wake_decisions``. Non sveglia nessuno.
- ``confronta()`` ogni 10 minuti: SQLite contro file (messaggi, attese), regola unica
  contro registro (sessioni), decisioni ombra contro ``logs/sveglia.log`` (sveglia), blocco
  dello Stop di oggi contro la tabella (stop). Le differenze previste dalle regole nuove
  hanno ``attesa`` valorizzata; quelle con ``attesa`` NULL sono il problema.
"""

from __future__ import annotations

import json
import os
import re
from collections import Counter, defaultdict
from datetime import timedelta
from pathlib import Path

from . import regole
from .db import Store
from .files import location_of, read_json
from .tempo import age_s, iso, iso_now, now

LAG_S = 15  # un file cambiato da meno di così può non essere ancora importato

RE_SVEGLIA = re.compile(r"^(\S+) sveglia (\S+) via (\S+): (.+)$")
RE_RIMANDATA = re.compile(r"^(\S+) rimandata (\S+) (\S+) \((msg-[^)]+)\): (.+)$")


class Ombra:
    def __init__(self, central):
        self.c = central
        self.home = central.home
        self._dir_mtime: dict[str, float] = {}
        self._wait_mtime: dict[str, float] = {}
        self._giro = 0

    # ---------- import ----------

    def importa(self, completo: bool = False, solo_nuovi: bool = False) -> dict:
        """``solo_nuovi`` (modo attivo, rete di sicurezza): entra solo un messaggio o
        un'attesa che SQLite non conosce, scritto da qualcosa fuori dal centralino; i file
        vecchi non toccano mai lo stato del database."""
        n_msg = n_wait = 0
        host = self.c.amap.host_self() or ""
        cambiati: list[tuple[Path, dict, tuple]] = []
        inbox = self.home / "inboxes"
        if inbox.is_dir():
            for d, dirs, files in os.walk(inbox):
                if Path(d) != inbox:  # sotto inboxes/<agente>: solo @slug, .read, .done
                    dirs[:] = [x for x in dirs if x.startswith("@") or x in (".read", ".done")]
                try:
                    m = os.stat(d).st_mtime
                except OSError:
                    continue
                if not completo and self._dir_mtime.get(d) == m:
                    continue
                self._dir_mtime[d] = m
                for f in files:
                    if not f.startswith("msg-") or not f.endswith(".json"):
                        continue
                    p = Path(d) / f
                    loc = location_of(p, self.home)
                    rec = read_json(p) if loc else None
                    if rec:
                        cambiati.append((p, rec, loc))
        attese: list[dict] = []
        wroot = self.home / "waits"
        if wroot.is_dir():
            for p in wroot.glob("*/@*/*.json"):
                if p.name.startswith("."):
                    continue
                try:
                    m = p.stat().st_mtime
                except OSError:
                    continue
                if not completo and self._wait_mtime.get(str(p)) == m:
                    continue
                self._wait_mtime[str(p)] = m
                rec = read_json(p)
                if rec and rec.get("task_id"):
                    attese.append(rec)
        if not cambiati and not attese:
            return {"messaggi": 0, "attese": 0}
        scartati: list = []

        def scrivi():
            nonlocal n_msg, n_wait
            conn = self.c._conn()
            noti = set()
            if solo_nuovi:
                ids = [p.stem for p, _, _ in cambiati]
                for i in range(0, len(ids), 500):
                    chunk = ids[i:i + 500]
                    noti |= {r[0] for r in conn.execute(
                        f"SELECT id FROM messages WHERE id IN ({','.join('?' * len(chunk))})", chunk)}
            for p, rec, (agent, slug, state) in cambiati:
                if p.stem in noti:
                    continue
                if solo_nuovi:
                    self.c.log("warn", "scritto_fuori_dal_centralino", path=str(p))
                try:
                    Store.put_message(conn, rec, state=state, to_slug=slug, to_host=host, path=str(p),
                                      origin="ingest", mid=p.stem)
                    n_msg += 1
                except Exception as exc:  # noqa: BLE001 - un record storto non ferma l'import
                    scartati.append(str(p))
                    self.c.log("warn", "import_scartato", path=str(p), error=repr(exc))
            for rec in attese:
                if solo_nuovi and conn.execute("SELECT 1 FROM waits WHERE task_id=?", (rec["task_id"],)).fetchone():
                    continue
                try:
                    Store.put_wait(conn, rec, origin="ingest")
                    n_wait += 1
                except Exception as exc:  # noqa: BLE001
                    scartati.append(rec.get("task_id"))
                    self.c.log("warn", "import_scartato", task_id=rec.get("task_id"), error=repr(exc))
        self.c._write(scrivi)
        return {"messaggi": n_msg, "attese": n_wait, "scartati": scartati}

    # ---------- sveglia in ombra ----------

    def _attese(self) -> dict[str, regole.Attesa]:
        rows = self.c.store.read().execute(
            "SELECT task_id, owner_agent, owner_slug, executor_session, delegated_to, state FROM waits").fetchall()
        return {r["task_id"]: regole.Attesa(r["task_id"], r["owner_agent"], r["owner_slug"],
                                            r["executor_session"], r["delegated_to"], r["state"])
                for r in rows}

    def destinazioni(self, attese=None) -> dict[tuple[str, str], list[tuple[str, str, str | None]]]:
        """(agente, slug) -> [(msg_id, regola, mittente)] per i messaggi pending di questa macchina."""
        attese = attese if attese is not None else self._attese()
        host = self.c.amap.host_self() or ""
        vive_cache: dict[str, list[str]] = {}
        out: dict[tuple[str, str], list] = defaultdict(list)
        for r in self.c.store.read().execute(
                "SELECT id, to_agent, to_slug, raw FROM messages WHERE state='pending' AND to_host=?", (host,)):
            rec = json.loads(r["raw"])
            agent = r["to_agent"]
            if agent not in vive_cache:
                vive_cache[agent] = self.c.vivo.slug_vivi(agent)
            for slug in regole.destinatari(rec, agent, r["to_slug"], attese, vive_cache[agent]):
                out[(agent, slug)].append((r["id"], regole.classifica(rec, agent, slug, attese),
                                           rec.get("from_session") or rec.get("from")))
        return out

    def giro_sveglia(self, esegui=None) -> list[regole.Decisione]:
        """Un giro della tabella di sveglia. Senza ``esegui`` (modo ombra) le decisioni si
        scrivono e basta. Con ``esegui(agent, slug, stato, decisioni) -> canale`` (modo attivo)
        le sveglie si fanno davvero: un ``Rinvio`` le trasforma in coda col suo motivo."""
        from .canale import Rinvio
        modo = "attiva" if esegui else "ombra"
        self._giro += 1
        giro = int(now().timestamp() * 1000)
        attese = self._attese()
        r = self.c.store.read()
        gia = {(x["msg_id"], x["agent"], x["slug"]) for x in r.execute(
            "SELECT msg_id, agent, slug FROM wake_decisions WHERE decisione='sveglia' AND modo=?", (modo,))}
        ultime: dict[tuple, tuple] = {}
        for x in r.execute("SELECT msg_id, agent, slug, decisione, motivo FROM wake_decisions WHERE modo=? ORDER BY id",
                           (modo,)):
            ultime[(x["msg_id"], x["agent"], x["slug"])] = (x["decisione"], x["motivo"])
        ora_fa = iso(now() - timedelta(hours=1))
        quarto_fa = iso(now() - timedelta(seconds=regole.URGENTI_COPPIA_S))
        tutte: list[tuple[str, str, regole.Decisione]] = []
        for (agent, slug), items in self.destinazioni(attese).items():
            items = [i for i in items if (i[0], agent, slug) not in gia]
            if not items:
                continue
            stato = self.c.vivo.stato(agent, slug)
            n_ora = r.execute(
                "SELECT COUNT(DISTINCT giro) FROM wake_decisions WHERE agent=? AND slug=? AND modo=? "
                "AND decisione='sveglia' AND regola!='esito' AND at>=?", (agent, slug, modo, ora_fa)).fetchone()[0]
            urg = {x[0] for x in r.execute(
                "SELECT m.from_agent || '/' || COALESCE(m.from_slug, '') FROM wake_decisions w "
                "JOIN messages m ON m.id=w.msg_id WHERE w.agent=? AND w.slug=? AND w.regola='urgente' "
                "AND w.decisione='sveglia' AND w.modo=? AND w.at>=?", (agent, slug, modo, quarto_fa))}
            decs = regole.decidi(stato, items, n_ora, urg)
            sveglie = [d for d in decs if d.decisione == "sveglia"]
            if esegui and sveglie:
                try:
                    canale = esegui(agent, slug, stato, sveglie)
                    decs = [regole.Decisione(d.msg_id, d.regola, d.decisione, d.motivo, canale)
                            if d.decisione == "sveglia" else d for d in decs]
                except Rinvio as exc:
                    decs = [regole.Decisione(d.msg_id, d.regola, "coda", str(exc), None)
                            if d.decisione == "sveglia" else d for d in decs]
            for d in decs:
                tutte.append((agent, slug, d))
        task_of = {}
        nuove = [(a, s, d) for a, s, d in tutte if ultime.get((d.msg_id, a, s)) != (d.decisione, d.motivo)]
        if nuove:
            for a, s, d in nuove:
                row = self.c.store.message(d.msg_id)
                task_of[d.msg_id] = row["task_id"] if row else None

            def scrivi():
                conn = self.c._conn()
                at = iso_now()
                for a, s, d in nuove:
                    conn.execute("INSERT INTO wake_decisions (at, giro, agent, slug, msg_id, task_id, regola, "
                                 "decisione, motivo, canale, modo) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                                 (at, giro, a, s, d.msg_id, task_of.get(d.msg_id), d.regola, d.decisione,
                                  d.motivo, d.canale, modo))
            self.c._write(scrivi)
        self.ultimo_giro = nuove
        return [d for _, _, d in tutte]

    def blocca_stop(self, agent: str, slug: str) -> list[str]:
        """Messaggi che, secondo la tabella, bloccano lo Stop di agent/slug."""
        dest = self.destinazioni()
        return [m for m, regola, _ in dest.get((agent, slug), []) if regole.blocca_stop(regola)]

    # ---------- confronto ----------

    def confronta(self) -> dict:
        self.importa()
        giro = int(now().timestamp())
        diffs: list[tuple[str, str, str | None, object, object]] = []
        diffs += self._cmp_messaggi()
        diffs += self._cmp_attese()
        diffs += self._cmp_sessioni()
        diffs += self._cmp_sveglia()
        diffs += self._cmp_stop()
        if diffs:
            def scrivi():
                conn = self.c._conn()
                at = iso_now()
                for area, chiave, attesa, sq, fi in diffs:
                    conn.execute("INSERT INTO shadow_diffs (at, giro, area, chiave, attesa, sqlite, file) "
                                 "VALUES (?,?,?,?,?,?,?)",
                                 (at, giro, area, chiave, attesa, json.dumps(sq, ensure_ascii=False, default=str),
                                  json.dumps(fi, ensure_ascii=False, default=str)))
            self.c._write(scrivi)
        per = Counter((a, at or "NON ATTESA") for a, _, at, _, _ in diffs)
        self.c.log("info", "ombra_confronto", differenze=len(diffs),
                   per_area={f"{a}:{at}": n for (a, at), n in per.items()})
        return {"giro": giro, "differenze": len(diffs),
                "non_attese": sum(1 for d in diffs if d[2] is None)}

    def _recente(self, p: Path) -> bool:
        try:
            return now().timestamp() - p.stat().st_mtime < LAG_S
        except OSError:
            return True

    def _cmp_messaggi(self):
        out = []
        files: dict[str, tuple[str | None, str, Path]] = {}
        inbox = self.home / "inboxes"
        for p in inbox.rglob("msg-*.json") if inbox.is_dir() else []:
            loc = location_of(p, self.home)
            if loc:
                files[p.stem] = (loc[1], loc[2], p)
        host = self.c.amap.host_self() or ""
        db = {r["id"]: r for r in self.c.store.read().execute(
            "SELECT id, to_slug, state, delivery, task_id FROM messages WHERE to_host=?", (host,))}
        for mid, (slug, state, p) in files.items():
            if self._recente(p):
                continue
            r = db.get(mid)
            if r is None:
                out.append(("messaggi", mid, None, None, {"slug": slug, "state": state}))
            elif (r["to_slug"], r["state"]) != (slug, state):
                out.append(("messaggi", mid, None, {"slug": r["to_slug"], "state": r["state"]},
                            {"slug": slug, "state": state}))
        for mid, r in db.items():
            if mid not in files and r["delivery"] == "local":
                out.append(("messaggi", mid, None, {"slug": r["to_slug"], "state": r["state"]}, None))
        return out

    def _cmp_attese(self):
        out = []
        wroot = self.home / "waits"
        files = {}
        for p in wroot.glob("*/@*/*.json") if wroot.is_dir() else []:
            if p.name.startswith("."):
                continue
            rec = read_json(p)
            if rec and rec.get("task_id") and not self._recente(p):
                files[rec["task_id"]] = rec
        r = self.c.store.read()
        db = {x["task_id"]: x for x in r.execute("SELECT task_id, state, owner_agent, owner_slug FROM waits")}
        rets: dict[str, dict] = defaultdict(dict)
        for x in r.execute("SELECT task_id, msg_id, state FROM wait_returns"):
            rets[x["task_id"]][x["msg_id"]] = x["state"]
        for tid, rec in files.items():
            x = db.get(tid)
            f_rets = {m: (e.get("state") or "pending") for m, e in (rec.get("returns") or {}).items()}
            if x is None:
                out.append(("attese", tid, None, None, {"state": rec.get("state")}))
            elif x["state"] != rec.get("state") or rets.get(tid, {}) != f_rets:
                out.append(("attese", tid, None, {"state": x["state"], "returns": rets.get(tid, {})},
                            {"state": rec.get("state"), "returns": f_rets}))
        for tid, x in db.items():
            if tid not in files and not (wroot / x["owner_agent"] / f"@{x['owner_slug']}" / f"{tid}.json").exists():
                out.append(("attese", tid, None, {"state": x["state"]}, None))
        return out

    def _cmp_sessioni(self):
        out = []
        host = self.c.amap.host_self() or ""
        for agent in self.c.amap.agents():
            reg = {e["slug"]: e for e in self.c.files.registry_sessions(agent)
                   if not e.get("host") or e.get("host") == host}
            vive = {s.slug: s for s in self.c.vivo.vive(agent)}
            for slug in sorted(set(reg) | set(vive)):
                r_viva = slug in reg and not reg[slug].get("stale")
                s = vive.get(slug)
                if r_viva == (s is not None):
                    continue
                attesa = "regola_unica:viva_non_nel_registro" if s else "regola_unica:registro_senza_sessione"
                out.append(("sessioni", f"{agent}/{slug}", attesa,
                            s.as_dict() if s else {"viva": False},
                            {"registro": {k: reg[slug].get(k) for k in ("last_seen", "pid", "stale")}} if slug in reg
                            else {"registro": None}))
        return out

    def _cmp_sveglia(self):
        """Ultime righe di sveglia.log contro le decisioni ombra degli stessi messaggi."""
        log = self.home / "logs" / "sveglia.log"
        if not log.is_file():
            return []
        r = self.c.store.read()
        off_row = r.execute("SELECT value FROM meta WHERE key='sveglia_log_offset'").fetchone()
        size = log.stat().st_size
        if off_row is None:
            # primo confronto: lo storico precede le decisioni ombra, si parte dalla fine del log
            self.c._write(lambda: self.c._conn().execute(
                "INSERT OR REPLACE INTO meta (key, value) VALUES ('sveglia_log_offset', ?)", (str(size),)))
            return []
        off = int(off_row["value"])
        if size < off:
            off = 0  # log ruotato
        with log.open("rb") as fh:
            fh.seek(off)
            chunk = fh.read()
        righe = chunk.decode("utf-8", "replace").splitlines()
        if chunk and not chunk.endswith(b"\n"):
            chunk = chunk[: chunk.rfind(b"\n") + 1]
            righe = chunk.decode("utf-8", "replace").splitlines()
        nuovo_off = off + len(chunk)
        oggi: dict[tuple[str, str], tuple[str, str]] = {}
        for line in righe:
            if m := RE_SVEGLIA.match(line):
                addr = m.group(2)
                for it in m.group(4).split(","):
                    mid = it.strip().rpartition(":")[2]
                    if mid.startswith("msg-"):
                        oggi[(mid, addr)] = ("sveglia", m.group(3))
            elif m := RE_RIMANDATA.match(line):
                oggi[(m.group(4), m.group(3))] = ("rimandata", m.group(5).strip())
        out = []
        for (mid, addr), (cosa, dettaglio) in oggi.items():
            agent, _, slug = addr.partition("/")
            noi = r.execute("SELECT decisione, motivo, regola FROM wake_decisions WHERE msg_id=? AND agent=? "
                            "AND slug=? ORDER BY id DESC LIMIT 1", (mid, agent, slug)).fetchone()
            nostra = noi["decisione"] if noi else "nessuna_decisione"
            if (cosa == "sveglia") == (nostra == "sveglia"):
                continue
            categoria = dettaglio if cosa == "rimandata" else f"oggi_sveglia_noi_{nostra}"
            out.append(("sveglia", f"{addr} {mid}", f"sveglia:{categoria.split(':')[0]}",
                        dict(noi) if noi else None, {"oggi": cosa, "dettaglio": dettaglio}))

        def salva():
            self.c._conn().execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('sveglia_log_offset', ?)",
                                   (str(nuovo_off),))
        self.c._write(salva)
        return out

    def _cmp_stop(self):
        out = []
        dest = self.destinazioni()
        for s in self.c.vivo.vive():
            oggi = self.c.files.count_inbox(s.agent, s.slug)
            noi = [m for m, regola, _ in dest.get((s.agent, s.slug), []) if regole.blocca_stop(regola)]
            if (oggi > 0) != bool(noi):
                out.append(("stop", f"{s.agent}/{s.slug}", "stop:non_azionabili" if oggi else "stop:azionabile_altrove",
                            {"bloccano": noi}, {"in_casella": oggi}))
        return out

    # ---------- report ----------

    def report(self, ore: float = 48) -> dict:
        r = self.c.store.read()
        da = iso(now() - timedelta(hours=ore))
        rows = r.execute("SELECT area, chiave, attesa, sqlite, file, at FROM shadow_diffs WHERE at>=? ORDER BY id",
                         (da,)).fetchall()
        non_attese: dict[str, list] = defaultdict(list)
        attese: Counter = Counter()
        esempi: dict[str, list] = defaultdict(list)
        for x in rows:
            if x["attesa"] is None:
                non_attese[x["area"]].append({"chiave": x["chiave"], "at": x["at"],
                                              "sqlite": json.loads(x["sqlite"]), "file": json.loads(x["file"])})
            else:
                k = f"{x['area']}/{x['attesa']}"
                attese[k] += 1
                if len(esempi[k]) < 3:
                    esempi[k].append(x["chiave"])
        sveglie = Counter()
        for x in r.execute("SELECT regola, decisione, COALESCE(motivo, '') AS motivo FROM wake_decisions WHERE at>=?",
                           (da,)):
            sveglie[f"{x['regola']}:{x['decisione']}" + (f":{x['motivo']}" if x["motivo"] else "")] += 1
        senza_task = r.execute(
            "SELECT COUNT(*) FROM messages WHERE ts>=? AND task_id IS NULL AND type IN ('response','question')",
            (da,)).fetchone()[0]
        prima = r.execute("SELECT MIN(at) FROM shadow_diffs").fetchone()[0]
        copertura = age_s(prima) / 3600 if prima else 0
        passa = not non_attese.get("messaggi") and not non_attese.get("attese") and copertura >= 48
        return {"finestra_ore": ore, "copertura_ore": round(copertura, 1), "passa": passa,
                "non_attese": {k: {"n": len(v), "esempi": v[:5]} for k, v in non_attese.items()},
                "attese": [{"area_regola": k, "n": n, "esempi": esempi[k]} for k, n in attese.most_common()],
                "decisioni_sveglia": dict(sveglie.most_common()),
                "ritorni_senza_task_id": senza_task}

    # ---------- operazioni sul socket ----------

    def registra(self) -> None:
        ombra = self

        def op_report(central, caller, a, warnings):
            return ombra.report(float(a.get("ore") or 48))

        def op_ingest(central, caller, a, warnings):
            return ombra.importa(completo=bool(a.get("completo")))

        def op_confronta(central, caller, a, warnings):
            return ombra.confronta()

        def op_sveglia(central, caller, a, warnings):
            return [d.__dict__ for d in ombra.giro_sveglia()]

        self.c.OPS = {**self.c.OPS, "ombra.report": op_report, "ombra.ingest_now": op_ingest,
                      "ombra.confronta": op_confronta, "ombra.sveglia": op_sveglia}
