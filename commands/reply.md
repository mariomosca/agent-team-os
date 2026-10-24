---
description: Rispondi a un messaggio nel suo thread. Argomenti - "<id> <tipo>" (tipo=response o question).
---

Rispondi al messaggio `$1` con tipo `$2` (`response` di default, oppure `question`).

1. Se non l'hai già letto, `bus_read(message_id=$1)`: serve per capire a cosa rispondi e se porta un `task_id`.
2. Componi la risposta: `summary` in una riga, `details` solo se servono. Una `response` che dichiara il lavoro finito deve avere un risultato verificabile (file, commit, link), non un "fatto" a vuoto.
3. Invia con `bus_reply(message_id=$1, summary, message_type, details)`.

Cosa fa il bus per te:

- la risposta va all'indirizzo `reply_to` del messaggio, sulla sessione e sulla macchina giuste;
- eredita thread e `task_id` dal messaggio: non passarli a mano;
- archivia il messaggio a cui rispondi con esito `risposto`.

Quindi, se sei l'esecutore di una delega: l'esito è `bus_reply` con `message_type='response'` sulla delega, la domanda al delegante è `bus_reply` con `message_type='question'`. Se sei il delegante e ricevi una domanda: `bus_reply` con la risposta, che sveglia l'esecutore. Per chiudere la delega quando arriva l'esito finale usa `/read` e `bus_close(esito='chiuso')`, non una risposta.
