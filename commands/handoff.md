---
description: Passa il lavoro in corso a un altro agente con un riassunto dello stato. Argomenti - "<a> [intento]".
---

Passa il lavoro corrente a `$1`, con intento opzionale `$2` (default `handoff`). `$1` è un indirizzo del bus: `kai`, `kai/slug`, `kai@mini/slug` (vedi `/send`).

1. **Raccogli lo stato** dalla sessione, senza chiedere a Mario quello che già sai: cosa è fatto, cosa resta (`next_steps`, espliciti e in ordine), blocchi, file toccati, stato dei test. Il materiale pesante va in `context_refs`, non nel testo.
2. **Scegli il `kind`**:
   - `delega` se rivuoi un esito (l'altro agente porta a termine e ti ritorna qualcosa);
   - `comunicazione` se passi il testimone e basta.
   Se non è chiaro, chiedi.
3. **Anteprima, poi conferma**: a chi, `kind`, riassunto dello stato in poche righe, prossimi passi. Invia solo dopo il sì di Mario.
4. **Invio**: `bus_send(to=$1, intent, summary, message_type='handoff', kind, details, context_refs)`, con `details` che contiene lo stato e i prossimi passi.

Conferma con l'id del messaggio e, per `delega`, il `task_id`. Il destinatario riceve il messaggio e, se è fermo, viene svegliato.
