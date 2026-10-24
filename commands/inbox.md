---
description: Elenca i messaggi in coda della sessione corrente, più le deleghe che aspettano un ritorno. Con --all tutte le sessioni dell'agente.
---

Mostra la casella della sessione corrente (la sessione la decide la cartella di lavoro).

Chiama `bus_inbox`:

- senza argomenti: i messaggi pending di questa sessione e della casella condivisa dell'agente;
- con `--all` nell'argomento: `all_sessions=true`, tutte le sessioni dell'agente;
- con `--unread`: `unread_only=true`.

Il risultato ha due parti: `messages` (i messaggi) e `mie_attese` (le deleghe che hai aperto, con i ritorni non ancora gestiti).

Presenta una riga per messaggio (id corto, mittente, tipo, `kind`, priorità, oggetto) e sotto le attese aperte con il loro `task_id`. Segnala per primi gli `urgent` e i ritorni di una delega tua.

Per aprire un messaggio usa `/read <id>`. Non consumare niente da qui: un messaggio esce dalla casella solo con `bus_close` o `bus_reply`.
