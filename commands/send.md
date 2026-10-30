---
description: Manda un messaggio a un altro agente. Argomenti - "<a> <intento>" (es. "kai bug-fix"). L'indirizzo e' agente, agente/slug o agente@host/slug.
---

Manda un messaggio nuovo a `$1` con intento `$2`.

1. **Indirizzo**. `kai` (la sessione viva dell'agente, vedi sotto), `kai/crewmoor` (una sessione), `kai@mini/crewmoor` (un'altra macchina). Se non sei sicuro di chi è vivo, chiama prima `bus_status`. Un agente nudo va all'unica sessione viva; se ce ne sono più di una, alla sua sessione di casa; altrimenti alla casella condivisa.
2. **Tipo**. `request` (chiedi qualcosa), `brief` (passi un incarico), `handoff` (passi il lavoro), `event` (notizia). Per le risposte non usare questo comando: usa `/reply`.
3. **`kind` (obbligatorio per request, brief e handoff)**:
   - `delega`: rivuoi l'esito. Il bus registra l'attesa da solo e restituisce il `task_id`.
   - `comunicazione`: non deve tornare niente. Solo con `brief` o `handoff`: una `request` è sempre una `delega` e il bus rifiuta una `request` comunicazione.
   Se le parole di Mario non chiariscono quale dei due, chiedi. Niente default.
4. **Contenuto**: `summary` (una riga), `details` (cosa fare, dove, criteri di accettazione), `context_refs` per il materiale pesante invece di incollarlo, `response_by` se c'è una scadenza, `priority` (`urgent` solo se blocca, `high` entro 24 ore, `normal` di default, `low` senza fretta). Niente segreti nei messaggi.
5. **Anteprima, poi conferma**: mostra in poche righe a chi, tipo, `kind`, priorità, oggetto, scadenza, e invia solo dopo il sì di Mario.
6. **Invio**: `bus_send(to, intent, summary, message_type, kind, priority, details, context_refs, thread_id, response_by)`.

Conferma con l'id del messaggio e, per una delega, il `task_id`. Se la risposta dice `queued=true` l'host è irraggiungibile e il messaggio parte alla prossima raccolta: dillo e non ritentare. Un avviso "nessuna sessione viva" vuol dire che il messaggio è stato consegnato ma nessuno lo legge finché quella sessione non parte.
