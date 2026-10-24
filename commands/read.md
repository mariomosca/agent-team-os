---
description: Leggi un messaggio per id, risolvi i context_refs e consumalo con un esito (chiuso, riarma, risposta, letto).
---

Leggi il messaggio `$ARG` (id completo o prefisso).

1. Chiama `bus_read(message_id=...)`. Il risultato porta il messaggio intero, l'indirizzo a cui rispondere (`reply_to`) e un suggerimento sul task (`_task_hint`): "hai delegato" se il messaggio è un ritorno di una tua delega, "stai eseguendo" se è la delega che ti hanno dato.
2. Risolvi i `context_refs` che servono al lavoro: `file://` con Read, `wiki://` e `graphiti://` con le tool dedicate, `gh://` con la tool GitHub. Non risolvere quelli che non servono.
3. Mostra il messaggio: mittente, tipo e `kind`, priorità, oggetto, dettagli, scadenza, riferimenti.
4. Consuma il messaggio, sempre con un esito esplicito:

| Situazione | Azione |
|---|---|
| Ho risposto o devo rispondere | `bus_reply(message_id, summary, message_type='response')`: archivia il messaggio con esito `risposto` |
| Ho una domanda per chi mi ha delegato | `bus_reply(message_id, summary, message_type='question')` |
| Ritorno di una mia delega, lavoro finito | `bus_close(message_id, esito='chiuso')`: chiude anche l'attesa |
| Ritorno intermedio, aspetto ancora | `bus_close(message_id, esito='riarma')` |
| Comunicazione, evento, solo da sapere | `bus_close(message_id, esito='letto')` |

Un messaggio senza esito resta in casella e tiene aperto lo Stop se richiede un'azione. Non chiuderlo "per toglierlo di mezzo": se il lavoro non è finito, `riarma` o rispondi.
