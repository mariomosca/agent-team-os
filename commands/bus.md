---
description: Agent Team OS, stato del bus - quali sessioni sono vive, su quale macchina, quanti messaggi hanno in coda.
---

Mostra lo stato del bus.

Chiama la tool MCP `bus_status` (senza argomenti: l'intero team; con `agent` solo un agente).
Nessun comando Bash.

Presenta il risultato in forma compatta:

- per ogni agente, le sessioni vive nella forma `agente/slug` con il numero di messaggi in coda;
- l'host di ogni sessione (laptop, mini) e, per una sessione viva solo per heartbeat, la nota "canale: nessuno" (non si può svegliare);
- gli host non raggiungibili, con l'età dell'ultimo stato noto.

Se la tool risponde che il centralino non risponde, dillo così com'è e non ripiegare su altro: il bus non è disponibile finché il centralino non riparte.
