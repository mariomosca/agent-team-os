---
description: Delega un compito a un altro agente e aspetta l'esito. Argomenti - "<a> <intento>". Come /send con kind=delega, tra due agenti qualsiasi.
---

Delega un compito a `$1` con intento `$2` e aspetta l'esito. `$1` è un indirizzo del bus: `kai`, `kai/slug`, `kai@mini/slug` (vedi `/send`). Nessun ruolo fisso: ogni agente può delegare a ogni altro.

Cosa fa una delega:

- l'attesa si registra sulla TUA sessione al momento dell'invio, con un `task_id` che viaggia sul messaggio;
- ogni ritorno sul `task_id` (esito, domanda, aggiornamento, anche un messaggio nuovo) sveglia questa sessione quando è ferma; mentre lavori i ritorni si accodano e arrivano insieme alla sveglia dopo;
- ogni ritorno si consuma con un esito esplicito: `bus_close` (`chiuso` quando hai finito, `riarma` se aspetti ancora) oppure `bus_reply` per rispondere a una domanda.

Passi:

1. **Contenuto**: cosa fare, dove (`project_path` nei dettagli), criteri di accettazione, `context_refs`. Chiedi una scadenza (`response_by`, opzionale): scaduta senza esito, l'attesa decade con un avviso in casella.
2. **Anteprima, poi conferma**: a chi, intento, priorità, oggetto, scadenza. Invia solo dopo il sì di Mario.
3. **Invio**: `bus_send(to=$1, intent=$2, summary, message_type='request', kind='delega', details, context_refs, response_by)`. Il risultato porta il `task_id`.
4. Conferma con id e `task_id` e di' a Mario che la sessione può andare a riposo: l'esito la sveglia.

**Delega tramite un pannello di Onda** (`onda_agent_prompt` invece del bus): comincia il prompt con il marcatore `[delega]`. L'hook del prompt registra l'attesa da solo e aggiunge la riga con il `task_id`. Se scrivi il prompt senza il marcatore, l'attesa non esiste e l'esito non sveglia nessuno.
