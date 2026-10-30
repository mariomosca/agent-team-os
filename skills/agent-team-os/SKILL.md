---
name: agent-team-os
description: Bus between multiple Claude Code sessions (v3, centralino + MCP agent-bus). Use when the user says "send to <agent>", "tell <agent>", "hand off to <agent>", "check inbox", "reply to brief", "delegate to <agent>", "manda a <agente>", "controlla la casella", or asks "who is active", "list agents", "open threads".
allowed-tools: Read
---

# Agent Team OS v3: come si usa

Il bus fa parlare sessioni Claude Code separate. Dal passaggio al v3 lo gestisce il centralino (un daemon locale, unico scrittore dello stato in SQLite). Tu lo usi solo con le sei tool MCP di `agent-bus`: niente Bash, niente file da scrivere a mano. I comandi slash (`/send`, `/inbox`, ...) sono scorciatoie che dicono al modello quale tool chiamare.

## Quando attivare la skill

- "manda / dì / passa a <agente>", "delega a...", "passa il lavoro a..."
- "controlla la casella", "ho messaggi?", "rispondi al brief di..."
- "chi è attivo sul bus?", "quali thread sono aperti?"

Non attivarla per i sub-agent locali (Explore, Plan, code-reviewer): quelli sono la tool `Agent`, non il bus. Né per la chat normale tra Mario e te nella stessa sessione.

## Le sei azioni

| Tool | Cosa fa |
|---|---|
| `bus_send` | messaggio nuovo. Con `kind='delega'` registra l'attesa e restituisce il `task_id` |
| `bus_reply` | risponde a un messaggio (`response` o `question`): va a chi ha scritto, eredita thread e `task_id`, archivia il messaggio con esito `risposto` (non una `question` di chi esegue: la delega resta in casella per l'esito) |
| `bus_close` | consuma un messaggio (`chiuso`, `riarma`, `letto`) oppure, con solo `task_id`, chiude l'attesa di una delega |
| `bus_inbox` | i messaggi pending di questa sessione e `mie_attese` (le tue deleghe aperte, con i ritorni non gestiti) |
| `bus_read` | un messaggio intero con il suggerimento sul task, oppure un thread intero (`thread_id`) |
| `bus_status` | sessioni vive e host |

Alias fino al 31 ottobre 2026 (la descrizione dice "alias, usa X"): `bus_gestisci` (usa `bus_close`, o `bus_reply` per `risposto`), `bus_archive` (`bus_close`), `bus_waits` (`bus_inbox`, campo `mie_attese`), `bus_thread` (`bus_read(thread_id)`), `bus_expect` (solo per le deleghe via Onda, vedi sotto). Nel lavoro nuovo usa i nomi nuovi.

## Indirizzi e sessioni

Un agente può avere più sessioni vive, una per cartella di lavoro. Ognuna ha una casella propria.

- `kai/crewmoor`: la sessione `crewmoor` di Kai. Lo slug è il nome della cartella, o `AB_SESSION_SLUG`.
- `kai@mini/crewmoor`: un'altra macchina. Senza `@host` vale l'host dell'agente, o questa macchina.
- `kai` nudo: l'unica sessione viva di Kai; se sono più di una, la sua sessione di casa (la cartella `workspace_root`) se è viva; altrimenti la casella condivisa, che le sessioni di Kai vedono tutte.
- Per rispondere usa `bus_reply`: l'indirizzo giusto (sessione e macchina) lo mette il bus. Non scrivere a mano il campo `from`.
- Un host irraggiungibile non fa fallire l'invio: `queued=true`, il messaggio parte alla prossima raccolta. Dillo a Mario, non ritentare.
- Se non sai chi è vivo, `bus_status`. Indirizzare uno slug non vivo consegna comunque, ma nessuno lo legge finché quella sessione non parte.

## Tipo e `kind`

`message_type`: `request`, `brief`, `handoff`, `event` per `bus_send`; `response` e `question` per `bus_reply`.

Regola del `kind`: ogni `request`, `brief` e `handoff` dichiara cosa vuole, e senza `kind` il bus rifiuta l'invio.

- `delega`: rivuoi l'esito. Il bus registra l'attesa sulla tua sessione e restituisce il `task_id`.
- `comunicazione`: non deve tornare niente, nessuno viene svegliato. Solo per `brief` e `handoff`: una `request` chiede lavoro ed è sempre una `delega`; il bus rifiuta una `request` con `kind='comunicazione'`.

Niente default e niente deduzioni dall'intento: se le parole di Mario non chiariscono, chiedi. `response`, `question` ed `event` non hanno `kind`.

Priorità: `urgent` blocca chi aspetta (avviso al prossimo prompt del destinatario), `high` entro 24 ore, `normal` di default, `low` senza fretta. Prima di inviare mostra sempre un'anteprima a Mario (a chi, tipo, `kind`, priorità, oggetto, scadenza) e invia dopo il sì. Il materiale pesante va in `context_refs` (`file://`, `wiki://`, `graphiti://`, `gh://`, `artifact://`), non nel testo. Mai segreti nei messaggi.

## Una delega, dall'inizio alla fine

1. **Delegante**: `bus_send(kind='delega', response_by=...)`. L'attesa nasce con il messaggio, sulla sessione del delegante; il `task_id` viaggia sul messaggio. Poi può andare a riposo.
2. **Esecutore**: la delega lo sveglia se è fermo. La legge con `bus_read` (il suggerimento dice "stai eseguendo").
3. **Domanda**: se gli manca qualcosa, `bus_reply(message_type='question')` sulla delega. Sveglia il delegante e non chiude l'attesa.
4. **Risposta**: il delegante risponde con `bus_reply` alla domanda. Sveglia l'esecutore, che riprende.
5. **Esito**: finito il lavoro, l'esecutore manda `bus_reply(message_type='response')` sulla delega, con un risultato verificabile.
6. **Chiusura**: l'esito sveglia il delegante, che lo legge e lo consuma con `bus_close`: `chiuso` se è davvero finita (chiude anche l'attesa), `riarma` se è un ritorno intermedio e aspetta ancora. Per chiudere una delega senza messaggi in casella: `bus_close(task_id=..., esito='chiuso')`.

Un messaggio senza `task_id` non è mai il ritorno di una delega: niente aggancio indovinato. Un'attesa scade alla `response_by`, o dopo 72 ore senza attività, con un avviso nella casella di chi aspettava.

**Delega tramite un pannello di Onda** (`onda_agent_prompt` invece del bus): comincia il prompt con `[delega]` (oppure `[comunicazione]`). L'hook registra l'attesa e aggiunge la riga col `task_id`. Un prompt lungo senza marcatore viene rifiutato con la spiegazione. Chi esegue una delega arrivata così non ha un messaggio a cui rispondere: segue la riga, cioè `bus_send(to=..., message_type='response' o 'question', task_id=...)`.

## Quando il bus ti sveglia

Il bus scrive una riga nel prompt della sessione, ma solo se è ferma (Onda dice idle, niente testo nella casella di Mario, da almeno 5 secondi). Se lavora, il messaggio si accoda e arriva a fine turno; più messaggi nello stesso giro fanno una sola sveglia.

| Arriva | Ti sveglia | Blocca lo Stop |
|---|---|---|
| esito di una tua delega | sempre, fuori da ogni tetto | sì |
| domanda su una tua delega | sì | sì |
| risposta del delegante a te esecutore | sì | sì |
| delega nuova (`kind=delega`) | sì | sì |
| messaggio `urgent` | sì, al massimo uno ogni 15 minuti per coppia di sessioni | sì |
| domanda fuori da una delega | no | sì |
| comunicazione, evento, notifica, risposta senza `task_id` o su attesa chiusa | no | no |

Tetto: 10 sveglie all'ora per sessione (l'esito non conta). Una sessione viva ma senza canale di sveglia (nessun pannello Onda) legge `canale: nessuno` in `bus_status`.

## La regola dello Stop

A fine turno il bus ti ferma solo se in casella c'è qualcosa della colonna "Blocca lo Stop", al massimo 3 volte di fila: poi ti lascia chiudere. Non è un errore: leggi (`bus_read`) e consuma ogni messaggio con un esito esplicito, `bus_close` (`chiuso`, `riarma`, `letto`) o `bus_reply`. Non usare `letto` per togliere di mezzo un lavoro non fatto: se aspetti ancora, `riarma`.

## Sessione viva

Una sessione è viva se Onda la dà viva (voce in `sessions.json` non `exited`), oppure se ha mandato un heartbeat negli ultimi 120 secondi. Altrimenti è chiusa. Una casella di sessione chiusa da più di 24 ore si svuota: i messaggi che chiedono un'azione passano alla sessione di casa dell'agente, gli altri si archiviano come letti.

## Anti-pattern

- Payload da più di 10 KB nel testo: usa `context_refs`.
- Segreti o credenziali nei messaggi.
- Invio senza anteprima a Mario.
- Rispondere "fatto" senza un risultato verificabile.
- Cambiare `thread_id` rispondendo: usa `bus_reply`, che lo eredita.
- Un broadcast per un compito specifico: indirizza la sessione.
- Consumare un messaggio con `letto` per liberare lo Stop.
- Scrivere a mano nelle cartelle di `~/.agent-team-os/`: il centralino è l'unico scrittore.

## Dove sta il resto

- Protocollo: `docs/PROTOCOL-v3.md` e `docs/CENTRALINO.md` nel repo `agent-team-os`.
- Anche il Mac mini ha il suo centralino (sveglia via tmux); il bus a file non c'è più. Il custode controlla il mini ogni mattina.
- Comandi: `/bus`, `/inbox`, `/read`, `/send`, `/reply`, `/handoff`, `/thread`, `/delegate`.
