# WT30-F4.1 · Ciclo di vita dei messaggi: niente messaggi appesi

Kai, 30/10/2026. Disegno per lo stream S4 (WT30-F5.4), approvato nel giro WT30 (Mario, 08:28). Codice in
`centralino/`, test in `centralino/tests/`, doc in `CENTRALINO.md` (§25). Stesso principio di DC29: a eventi,
niente giri periodici nuovi (il giro della sveglia esiste già e resta il posto dove si finalizza).

## Il caso

Il 28/10 Leo ha mandato a `kai` (nudo, quindi casella condivisa) due `request` con `kind=comunicazione`
(mcp-config). Nessuna sessione di Kai le ha viste per 46 ore: le comunicazioni non svegliano, non bloccano lo
Stop, e la casella condivisa non entra nell'avviso "fermi senza canale" perché Kai aveva sessioni con canale.
Il 30/10 alle 04:42 la sveglia ha lanciato un allarme urgente falso: la delega t-20261028-48yr0s di Leo a `nico`
nudo era stata eseguita da nico/brandart-agentic-platform-feat-fbk26 (domanda alle 15:00, esito alle 15:07 del
28/10, con `bus_send` e non `bus_reply`), ma la copia nella casella condivisa era ancora `pending`.

## Le quattro regole

### R1. Una richiesta di lavoro è sempre una delega

`dbbus.check_kind`: `message_type='request'` con `kind='comunicazione'` → `BusError("a request asks for work: send
it with kind='delega' (the bus registers the wait and wakes you on every return); for information only send a
brief with kind='comunicazione'", code "kind_required")`. `brief` e `handoff` restano con entrambi i kind. Aggiornare
`KIND_HELP`, la descrizione di `bus_send` nel MCP agent-bus, `docs/PROTOCOL-v3.md:55`, `~/.claude/commands/send.md:11`,
`~/.claude/skills/agent-team-os/SKILL.md:50`, e il banco (`sim.py:585` manda una request comunicazione: diventa un
caso di rifiuto).

### R2. La copia nella casella condivisa si consuma quando il lavoro è preso

In `DbBus._registra_ritorno` (chiamata da `accogli` per ogni ritorno con `task_id`), dopo aver registrato il
ritorno sull'attesa:

- `response` con `task_id` dall'agente esecutore X (da `record["from"]`): ogni messaggio `pending` con
  `kind='delega'` e quel `task_id` indirizzato a X (qualunque `to_slug`, condivisa compresa) passa a `done` con
  `handled = {esito: 'gestito', at, by: <from_session>, via: 'ritorno'}`.
- `question` con `task_id` da X: solo le copie con `to_slug IS NULL` (la casella condivisa): la sessione che
  chiede ha preso la delega; la copia indirizzata a lei resta finché non manda l'esito.

In `close_wait` e in `handle(esito='chiuso')` (attesa chiusa dal delegante): le copie `pending` della delega
indirizzate all'agente esecutore passano a `done` con `handled.esito='attesa_chiusa'`. Lo stesso in
`chiudi_gemelle`.

Effetto: `_bloccati` della sveglia non le vede più, niente allarme falso.

### R3. I messaggi di una sessione chiusa

`DbBus.fine_sessione(agent, slug)`, chiamata da `HookOps.op_end` e da `Deleghe._sessione` quando chiude lei la
sessione (`agent.close` riuscito; il SessionEnd di Claude può non arrivare dopo un kill del PTY). Per ogni
messaggio `pending` con `to_agent=agent AND to_slug=slug` su questa macchina:

1. **Deleghe non ancora prese** (`kind='delega'`, nessun ritorno della sessione su quel task) e **ritorni**
   (`response`/`question` con `task_id`, esito/domanda per un'attesa di quella sessione): passano alla **casella
   condivisa** subito (`UPDATE messages SET to_slug=NULL`, `raw.to` invariato, log `riassegnato`). Un'altra
   sessione dell'agente li prende; per i ritorni, `regole.destinatari` deve mandare alla sessione viva con
   canale quando la proprietaria dell'attesa non è più viva (oggi torna `[owner_slug]` e il messaggio resta muto:
   il fallback è `list(vive)` se `owner_slug not in vive`). Coerente con §6: la proprietà passa a un'altra sessione
   dello stesso agente quando chi ha delegato non è più viva.
2. **Tutto il resto** (comunicazioni, event, cc-notify, response senza task o su attesa chiusa): dopo una grazia di
   `NON_LETTO_S` (default 600 s, `AB_NON_LETTO_S` per i test), se nessuna sessione con quello slug è tornata viva,
   passano a `done` con `handled.esito='non_letto'`, e il mittente riceve **un** `event` `non-letto` (priorità
   `low`, regola "nessuna": non sveglia, non blocca) per sessione chiusa, con gli id e gli intent. La grazia serve a
   `claude --resume` e alle riaperture nella stessa cartella (stesso slug): la finalizzazione sta nel giro della
   sveglia (`Sveglia.avvisi_non_letti`), l'evento che la innesca è il SessionEnd (`sessions.ended_at`). Mai per i
   messaggi di origine `centralino` e mai verso lo stesso agente/slug. Mai un rimbalzo di un rimbalzo (intent
   `non-letto` escluso).

### R4. Richieste ferme da più di un giorno, qualunque sia il kind

`Sveglia.avvisi_fermi_un_giorno`, nel `giro`: messaggi `pending` di tipo `request`, `brief` o `handoff` (ogni kind,
anche `comunicazione`), più vecchi di `FERMO_GIORNO_S` (86400), di questa macchina, non già segnalati (`meta`
`fermo1g:<id>`), raggruppati per (destinatario agente/slug o condivisa, mittente): **un** messaggio
`richieste-ferme-da-un-giorno`, `priority='urgent'`, alla sessione dell'hub con canale (casa se possibile), oppure
notifica di sistema se l'hub non ha canale; e una copia `event` (priorità `normal`) al mittente, così chi ha chiesto
sa che nessuno ha letto. Una volta per messaggio.

## Dove

| File | Cosa |
|---|---|
| `centralino/src/centralino/dbbus.py` | R1 `check_kind`; R2 in `_registra_ritorno`, `close_wait`, `handle`, `chiudi_gemelle`; R3 `fine_sessione`, `rimbalza_non_letti(adesso)` |
| `centralino/src/centralino/regole.py` | R3 fallback in `destinatari` |
| `centralino/src/centralino/hookops.py` | `op_end` → `bus.fine_sessione` |
| `centralino/src/centralino/deleghe.py` | `_sessione` dopo `chiusa` → `bus.fine_sessione` |
| `centralino/src/centralino/sveglia.py` | `avvisi_non_letti`, `avvisi_fermi_un_giorno` nel `giro`; costanti `NON_LETTO_S`, `FERMO_GIORNO_S`, intent |
| `centralino/src/centralino/sim.py` | scenari: `caso_wt30_request_comunicazione_rifiutata`, `caso_wt30_copia_condivisa_consumata`, `caso_wt30_sessione_chiusa_riassegna_e_rimbalza`, `caso_wt30_richiesta_ferma_un_giorno` |
| `centralino/tests/test_messaggi.py` | gli stessi quattro casi con il Daemon vero (pattern di `test_server.py`), grazia a 0 via env |
| `docs/CENTRALINO.md` §25, `docs/PROTOCOL-v3.md` | regole |
| MCP agent-bus (`~/Projects/07-Tooling/mcp/agent-bus-mcp`), `~/.claude/commands/send.md`, `~/.claude/skills/agent-team-os/SKILL.md` | testo di `kind` per `request` |

Niente schema nuovo: `messages.handled` (nel `raw`), `sessions.ended_at`, `meta` bastano. Se serve un indice,
`messages(task_id, state)` c'è già per `task_id`.

## Verifica

`uv run pytest` verde; banco `centralino sim`; poi `./install.sh --register-hooks` e
`launchctl kickstart -k gui/$(id -u)/com.agent-team-os.centralino`; infine, dal vivo: una `request` con
`kind=comunicazione` rifiutata; una delega a `kai` nudo risolta da una sessione con `bus_send response` sparisce
dalla condivisa (`bus_inbox all_sessions`). Il mini riceve il codice al prossimo `git pull` del suo clone (cron).

## Insieme a S4 (WT30-F5.4)

- Socket Onda per sessione: `hook.start` riceve `ONDA_SOCKET` dal caller (l'hook lo ha in ambiente): colonna
  `sessions.onda_socket` (schema v7, backup prima della migrazione come in `_backup_prima_di_migrare`);
  `canale.via_onda` e `deleghe._sessione`/`_retire` usano il socket della sessione quando c'è, altrimenti il
  default. Serve alla prova di WT30 su `npm run dev` (F0, F6.1) e a chi ha due Onda.
- `schema/onda-sessions.schema.json`: `worktreePath`, `branch` facoltativi (contratto Onda WT30).
- bus-band (`mods/bus-band/hooks/team.ts:192`): richiesta di mount `{action:'mount', workspaceId, terminalId?}`.
- `CENTRALINO.md` §24: "il workspace" → "il worktree e i suoi pannelli; il workspace del repo resta".
