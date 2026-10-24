"""Migrazione dello storico in SQLite (BR24-F5.1) ed esportazione inversa per il ritorno.

``migra``: con il daemon fermo, tutto lo stato a file entra in SQLite com'è in quel
momento (caselle con .read/.done, attese, thread nei due formati). È idempotente: si può
rifare, e a ogni giro i file vincono sul database.

``esporta``: l'inverso, per tornare al bus a file dopo la prova. Ogni messaggio e ogni
attesa di SQLite viene scritto nel formato di oggi dove i lettori di oggi lo cercano;
un messaggio che ha cambiato stato si sposta. Nessun file viene cancellato se non per
spostarlo: quello che SQLite non conosce (pulito o mai importato) resta com'è.
"""

from __future__ import annotations

import json
from pathlib import Path

from .files import atomic_write, location_of, read_json


def migra(central, ombra) -> dict:
    res = ombra.importa(completo=True)
    legacy = 0
    noti = {r[0] for r in central.store.read().execute("SELECT id FROM messages")}
    righe: list[tuple[str, str, str]] = []
    tdir = central.home / "threads"
    for f in sorted(tdir.glob("*.jsonl")) if tdir.is_dir() else []:
        for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                e = json.loads(line)
            except ValueError:
                continue
            if isinstance(e, dict) and e.get("id") and e["id"] not in noti:
                righe.append((f.stem, e["id"], json.dumps(e, ensure_ascii=False)))
    for f in sorted(tdir.glob("*.json")) if tdir.is_dir() else []:
        doc = read_json(f) or {}
        for m in doc.get("messages") or []:
            if isinstance(m, dict) and m.get("id") and m["id"] not in noti:
                e = {k: m.get(k) for k in ("id", "from", "to", "type", "intent", "ts") if m.get(k)}
                righe.append((doc.get("id") or f.stem, m["id"], json.dumps(e, ensure_ascii=False)))

    def scrivi():
        c = central._conn()
        for r in righe:
            c.execute("INSERT OR IGNORE INTO thread_legacy (thread_id, id, entry) VALUES (?,?,?)", r)
    central._write(scrivi)
    legacy = len(righe)
    r = central.store.read()
    return {"messaggi": r.execute("SELECT COUNT(*) FROM messages").fetchone()[0],
            "pending": r.execute("SELECT COUNT(*) FROM messages WHERE state='pending'").fetchone()[0],
            "attese": r.execute("SELECT COUNT(*) FROM waits").fetchone()[0],
            "attese_aperte": r.execute("SELECT COUNT(*) FROM waits WHERE state='expected'").fetchone()[0],
            "righe_thread_legacy": legacy, "importati_ora": res}


def _dove(home: Path, agent: str, slug: str | None, state: str, mid: str) -> Path:
    d = home / "inboxes" / agent
    if slug:
        d = d / f"@{slug}"
    if state in ("read", "done"):
        d = d / f".{state}"
    return d / f"{mid}.json"


def esporta(central) -> dict:
    home = central.home
    host = central.amap.host_self() or ""
    ovunque: dict[str, list[Path]] = {}
    inbox = home / "inboxes"
    for p in inbox.rglob("msg-*.json") if inbox.is_dir() else []:
        if location_of(p, home):
            ovunque.setdefault(p.stem, []).append(p)
    scritti = spostati = attese = 0
    for r in central.store.read().execute(
            "SELECT id, to_agent, to_slug, state, raw FROM messages WHERE to_host=? AND delivery='local'", (host,)):
        dest = _dove(home, r["to_agent"], r["to_slug"], r["state"], r["id"])
        rec = json.loads(r["raw"])
        altri = [p for p in ovunque.get(r["id"], []) if p != dest]
        if dest.exists() and not altri and (read_json(dest) or {}) == rec:
            continue
        if r["to_slug"]:
            for sub in (".read", ".done"):
                (dest.parent.parent if dest.parent.name.startswith(".") else dest.parent).joinpath(sub).mkdir(
                    parents=True, exist_ok=True)
        atomic_write(dest, rec)
        for p in altri:
            p.unlink(missing_ok=True)
        if altri:
            spostati += 1
        else:
            scritti += 1
        # il thread di oggi (jsonl) per i messaggi nati in SQLite
        tf = home / "threads" / f"{rec.get('thread_id')}.jsonl"
        if rec.get("thread_id") and not ovunque.get(r["id"]):
            tf.parent.mkdir(parents=True, exist_ok=True)
            e = {k: rec[k] for k in ("id", "from", "to", "type", "intent", "ts", "in_reply_to", "task_id")
                 if rec.get(k) is not None}
            e["summary"] = (rec.get("payload") or {}).get("summary", "") if isinstance(rec.get("payload"), dict) else ""
            with tf.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(e, ensure_ascii=False) + "\n")
    for r in central.store.read().execute("SELECT owner_agent, owner_slug, raw FROM waits"):
        rec = json.loads(r["raw"])
        f = home / "waits" / r["owner_agent"] / f"@{r['owner_slug']}" / f"{rec['task_id']}.json"
        if (read_json(f) or {}) != rec:
            atomic_write(f, rec)
            attese += 1
    out = {"messaggi_scritti": scritti, "messaggi_spostati": spostati, "attese_scritte": attese}
    central.log("info", "esporta", **out)
    return out

