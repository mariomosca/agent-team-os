# Centralino del bus (BR24-F3)

Daemon unico scrittore dello stato del bus di Agent Team OS. Contratto: [`../docs/CENTRALINO.md`](../docs/CENTRALINO.md).
SQLite è l'unica fonte (BR24-F5.5): il centralino tiene caselle, attese, thread e registro delle sessioni,
fa le sveglie, raccoglie dagli altri host e pulisce. Nessun file di casella, attesa o thread.

```bash
uv sync
uv run pytest -q                 # test unitari, socket, concorrenza, scenari
uv run centralino sim            # banco di prova: i casi del 24/10 come regressione
uv run centralino --home /tmp/bus-copia serve      # daemon in primo piano su una radice di prova
uv run centralino --home /tmp/bus-copia status     # ping e contatori del daemon
```

| Modulo | Cosa |
|---|---|
| `indirizzi.py` | unico modulo per agente da cwd (regola più lunga, confine di cartella), slug, indirizzi, host |
| `db.py` | schema SQLite, transazione unica di scrittura |
| `dbbus.py` | caselle, attese, invio e regole di validazione, registro delle sessioni, tutto in SQLite |
| `remoto.py` | solo trasporto verso gli altri host: SSH, coda `outbox/<host>/`, raccolta |
| `vivo.py` | regola unica di sessione viva (Onda o heartbeat 120 s) |
| `regole.py` | tabella di sveglia e blocco dello Stop, funzioni pure |
| `ops.py` | le operazioni del socket, stesse risposte delle 9 tool |
| `sveglia.py` | giro della sveglia: scadenze, tabella di §6 eseguita via Onda o tmux, avvisi |
| `hookops.py` | operazioni degli hook sottili (banner, urgenti, Stop) e `consegna` dal cloud |
| `pulizia.py` | pulizia ogni 6 ore (messaggi vecchi, attese chiuse, sessioni morte) |
| `server.py` | socket Unix 0600, thread per connessione, giri periodici |
| `sim.py` | banco di prova a simulazione |

Niente installazione: il plist in `launchd/` è un modello con segnaposto e non va caricato senza l'ok di Alita.
