---
description: Mostra la storia completa di un thread. Argomento - "<thread-id>" (senza argomento, i thread recenti dalla casella).
---

Mostra il thread `$1`.

- Con argomento: `bus_read(thread_id=$1)`. Restituisce i messaggi del thread in ordine di tempo.
- Senza argomento: `bus_inbox(all_sessions=true)` e raggruppa per `thread_id` i messaggi che trovi, mostrando quelli con più attività.

Presenta il thread come una cronologia: ora, da chi a chi, tipo e `kind`, oggetto, stato (in casella, letto, risposto, chiuso). Se il thread appartiene a una delega, indica il `task_id` e se l'attesa è ancora aperta.

Sola lettura: non consuma niente.
