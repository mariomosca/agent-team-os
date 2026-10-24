# Centralino del bus (BR24-F3)

Daemon unico scrittore dello stato del bus di Agent Team OS. Contratto: [`../docs/CENTRALINO.md`](../docs/CENTRALINO.md).
Passo 1 = **modalità ombra**: scrive SQLite e i file di oggi, calcola sveglie e Stop con le regole nuove
senza applicarle, confronta con il sistema di oggi.

```bash
uv sync
uv run pytest -q                 # test unitari, socket, concorrenza, scenari
uv run centralino sim            # banco di prova: i casi del 24/10 come regressione
uv run centralino --home /tmp/bus-copia serve      # daemon in primo piano su una radice di prova
uv run centralino --home /tmp/bus-copia ombra report --ore 48
```

| Modulo | Cosa |
|---|---|
| `indirizzi.py` | unico modulo per agente da cwd (regola più lunga, confine di cartella), slug, indirizzi, host |
| `db.py` | schema SQLite, transazione unica di scrittura |
| `files.py` | i file di oggi (porting di `agent_bus_mcp/bus.py`), ogni scrittura notificata a SQLite |
| `vivo.py` | regola unica di sessione viva (Onda o heartbeat 120 s) |
| `regole.py` | tabella di sveglia e blocco dello Stop, funzioni pure |
| `ops.py` | le operazioni del socket, stesse risposte delle 9 tool |
| `ombra.py` | import dei file scritti da altri, sveglia in ombra, confronto, report |
| `server.py` | socket Unix 0600, thread per connessione, giri periodici |
| `sim.py` | banco di prova a simulazione |

Niente installazione: il plist in `launchd/` è un modello con segnaposto e non va caricato senza l'ok di Alita.
