# Agent Team OS: protocollo v3

Questo è il protocollo autorevole del bus dopo il passaggio al centralino (piano BR24, 24 ottobre 2026).
Sostituisce `PLAN-AGENT-TEAM-OS.md` per tutto ciò che riguarda messaggi, deleghe, sveglia e Stop.
Il dettaglio di implementazione (API sul socket, schema SQL, modalità ombra) sta in
[CENTRALINO.md](./CENTRALINO.md); qui c'è il contratto per chi usa il bus e per chi lo mantiene.

## 1. Componenti

| Componente | Ruolo |
|---|---|
| **Centralino** (`centralino/`, launchd `com.agent-team-os.centralino`) | daemon locale, unico scrittore dello stato. Riceve richieste su un socket Unix `0600`, scrive SQLite, sveglia le sessioni, raccoglie dal mini, riceve dal cloud, pulisce |
| **MCP `agent-bus`** | client sottile: sei tool per gli agenti (`bus_send`, `bus_reply`, `bus_close`, `bus_inbox`, `bus_read`, `bus_status`). Manda al centralino i dati grezzi del chiamante (cwd, pid, pannello) e ne riceve l'identità risolta. Ogni 60 secondi manda un heartbeat |
| **Hook** (SessionStart, UserPromptSubmit, Stop, SessionEnd, PreToolUse su `onda_agent_prompt`) | sottili: chiamano il centralino e non bloccano mai se non risponde |
| **Onda** | il terminale. Scrive `onda/sessions.json` (stato di ogni pannello) e riceve la riga di sveglia (`agent.prompt` con `onlyIfIdle`). Non si tocca |
| **Mac mini** | resta sul bus a file (`lib.sh`, `wake.py`, custode) perché non ha il centralino. Il laptop gli consegna via SSH e raccoglie dal suo `outbox/laptop/` |
| **Cloud** (`alita@cloud`) | senza SSH: i suoi messaggi arrivano con la sync del repo e il centralino li accoglie come messaggi di un altro host |
| **Orbit e bus-band** | leggono la vista globale in sola lettura del centralino (sessioni, attese, messaggi). Non decidono niente |

Cosa non fa il centralino: non chiama modelli sul percorso normale, non scrive in `onda/`, non tocca `settings.json`.

## 2. Stato

La fonte unica è `~/.agent-team-os/centralino.db` (SQLite, WAL), scritta solo dal centralino. Le tabelle: `messages`
(un record per messaggio, con tipo, `kind`, `thread_id`, `task_id`, stato e esito), `waits` e `wait_returns` (le deleghe
aperte per sessione e i ritorni agganciati), `sessions` (agente, slug, host, pid, ultimo heartbeat, fine), `wake_decisions`
(ogni decisione di sveglia, con la regola e il motivo, per misurare), `remote_queue` (consegne verso un host non
raggiungibile) e `meta`. I file di prima (`inboxes/`, `waits/`, `threads/`, `outbox/`) restano come archivio in sola
lettura. L'unico file che il centralino continua a scrivere è `registry/<agente>.d/<slug>.json`, perché Onda risale
allo slug dal pid leggendolo.

## 3. Indirizzi

| Forma | Significato |
|---|---|
| `kai/crewmoor` | la sessione `crewmoor` di Kai (lo slug è il nome della cartella, o `AB_SESSION_SLUG`) |
| `kai@mini/crewmoor` | stessa sessione, su un altro host (anche `kai/crewmoor@mini`) |
| `kai` | l'unica sessione viva di Kai; se sono più di una, la sua sessione di casa (`workspace_root`) se è viva; altrimenti la casella condivisa, visibile a tutte le sue sessioni |
| `kai@mini` | come sopra, sul mini |

L'agente si ricava dalla cwd con la regola di `AGENT_MAP.rules` dal prefisso più lungo, sul confine di cartella
(`/a/work-hub` non combacia con `/a/work-hub-x`); `AB_AGENT` vince. Senza `@host` vale l'host dell'agente
(`agents.<a>.host`), poi l'host hub, poi questa macchina. Un solo modulo (`centralino/indirizzi.py`) fa tutto questo:
nessun altro componente risolve indirizzi. Si risponde sempre con `bus_reply`, che usa il `reply_to` del messaggio.

## 4. Tipi di messaggio e `kind`

Tipi: `request`, `brief`, `handoff`, `event` (con `bus_send`); `response`, `question` (con `bus_reply`).
Campi: id, mittente (agente, slug, host), destinatario, tipo, `kind`, intento, priorità (`urgent`, `high`, `normal`,
`low`), `thread_id`, `in_reply_to`, `task_id`, `response_by`, `payload` (`summary`, `details`), `context_refs`.

`kind` è obbligatorio per `request`, `brief` e `handoff`, e senza il bus rifiuta l'invio:

- `delega`: il mittente rivuole l'esito. Crea un'attesa sulla sua sessione, nella stessa transazione del messaggio.
- `comunicazione`: non deve tornare niente.

`response`, `question` ed `event` non hanno `kind`. I record vecchi senza `kind` ma con `requires_response` si
trattano come deleghe.

## 5. Ciclo di vita di una delega

1. `bus_send(kind='delega')`: attesa `expected` sulla sessione del delegante, `task_id` sul messaggio.
2. L'esecutore riceve la delega (sveglia se è fermo) e lavora.
3. Domanda: `bus_reply(message_type='question')` con il `task_id` ereditato. Sveglia il delegante, l'attesa resta aperta.
4. Risposta del delegante: `bus_reply` alla domanda. Sveglia l'esecutore.
5. Esito: `bus_reply(message_type='response')` sulla delega. Sveglia il delegante, fuori da ogni tetto.
6. Chiusura: `bus_close(esito='chiuso')` sul ritorno chiude l'attesa; `riarma` la tiene aperta; `bus_close(task_id=...)`
   chiude un'attesa anche senza messaggi in casella (`chiuso` o `annullato`).

Regole: la proprietà dell'attesa è per sessione e passa a un'altra sessione dello stesso agente solo se quella che
ha delegato non è più viva. Un messaggio senza `task_id` non è mai un ritorno (niente aggancio indovinato). L'attesa
scade alla `response_by` o dopo 72 ore senza attività, anche con ritorni già gestiti, con un avviso in casella; la
chiusura toglie ogni sveglia pendente. Le deleghe via Onda (`onda_agent_prompt`) non passano da `bus_send`: il prompt
comincia con `[delega]` e l'hook registra l'attesa con `expect` sul centralino (nessuna seconda attesa se il prompt
ha già la riga col `task_id`).

## 6. Tabella di sveglia

Una sessione "ferma" è una sessione che Onda dà `idle`, senza testo nella casella di Mario, da almeno 5 secondi.
Al lavoro, il messaggio si accoda e arriva a fine turno. Più messaggi per la stessa sessione nello stesso giro
fanno una sveglia sola. Il canale è Onda (pannello in `sessions.json`), oppure tmux se l'attesa ha un pannello tmux.

| Messaggio | Sveglia | Blocca lo Stop |
|---|---|---|
| esito di una delega (response col `task_id` di un'attesa aperta della sessione) | sempre, esente dal tetto | sì |
| domanda su una delega della sessione | sì | sì |
| risposta all'esecutore (response con `task_id` di un'attesa di un'altra sessione, o senza attesa qui) | sì | sì |
| delega nuova (`kind=delega`) | sì | sì |
| da agganciare (`task_link.by=da_agganciare`, vecchi record) | sì | sì |
| `urgent`, fuori dalle righe sopra | sì, al massimo 1 ogni 15 minuti per coppia di sessioni | sì |
| domanda libera | no | sì |
| tutto il resto (comunicazione, event, notifiche, response senza `task_id` o su attesa chiusa) | no | no |

Un solo tetto: 10 sveglie all'ora per sessione, l'esito non conta. Se la sessione non ha un canale (viva solo per
heartbeat), la decisione è `nessun_canale`, registrata in `wake_decisions` e visibile in `bus_status`.

## 7. Regola dello Stop

Lo Stop blocca la fine del turno solo se in casella c'è almeno un messaggio della colonna "Blocca lo Stop", al
massimo 3 volte di fila (poi lascia chiudere: un contatore per sessione). Un messaggio esce dalla casella solo con
un esito esplicito (`bus_close` o `bus_reply`). Eventi, comunicazioni e notifiche non tengono mai la sessione.

## 8. Sessione viva

Una sessione `(agente, slug, host)` è viva se vale almeno una delle due:

1. Onda la dà viva: `sessions.json` valido (`v=1`, `ondaPid` vivo, `writtenAt` più recente di 60 secondi) con una
   voce per quell'agente e slug (o `claude_session_id`) e `state != exited`;
2. heartbeat negli ultimi 120 secondi.

Altrimenti è chiusa. Niente TTL a ore, niente pid del registro, niente `last_seen`. Viva non vuol dire
raggiungibile: serve anche un canale di sveglia. Per le sessioni di un altro host vale l'ultimo stato raccolto.

## 9. Host

Due macchine: il laptop (con il centralino) e il mini (bus a file). Laptop verso mini: consegna via SSH; se il mini
non risponde, `queued=true` e il messaggio parte alla raccolta successiva (la coda sta in `remote_queue`). Mini
verso laptop: i messaggi aspettano in `outbox/laptop/` del mini finché il centralino li raccoglie. Il cloud non ha
SSH: i suoi messaggi arrivano con la sync e il centralino li accoglie come messaggi di un altro host, senza `task_id`
indovinati. Gli stati dei due host si leggono con `bus_status`, con l'età dell'ultimo stato noto.

## 10. Pulizia

Un giro ogni 6 ore:

- messaggi archiviati e righe di thread oltre 30 giorni: via;
- attese chiuse, scadute o annullate oltre 30 giorni: via, con i loro ritorni;
- decisioni di sveglia oltre 30 giorni: via;
- caselle di sessioni chiuse da più di 24 ore: i messaggi che chiedono un'azione passano alla sessione di casa
  dell'agente (unica sessione viva, poi casa, poi casella condivisa), gli altri si archiviano come letti; la
  sessione esce dal registro.

## 11. Cosa è stato tolto e perché

Tutto ciò che segue non aveva lettori o ne aveva di diretti dall'implementazione, e non risultava in uso (prove in
`INVENTARIO.md` §3.g, §4, §5.4 del lavoro di BR24):

| Tolto | Motivo |
|---|---|
| GOD mode (`/god`, `god-mode/*.on`, GOD-CONTRACT) | il flag non lo leggeva nessun altro codice; il comportamento era solo il testo del contratto; `alita.on` vuoto dal 24 giugno |
| TaskProvider (`taskprovider.sh`) | serviva solo a `/god`; Orbit è l'unico provider, il ramo PMOHub era morto |
| `harvest.sh` | non inserito in nessun flusso |
| filtro `project_path` (v1.2) | 0 messaggi su 384 lo portavano; sostituito dallo slug |
| `routing_rules.deny` | tutte vuote: il controllo non scattava mai |
| registro piatto `registry/<agente>.json` | legacy, poteva dire `active:false` con sessioni vive |
| cursore `lastProcessed` | sempre vuoto; resta solo il contatore dei blocchi dello Stop |
| `.no-wake` | nessuno strumento lo creava |
| TTL di 240 minuti e `last_seen` per la "vita" | sostituiti dalla regola unica del §8 |
| `lib.sh` come secondo canale di invio | l'invio è solo `bus_send`; i comandi slash usano le tool |
| stati di attesa v2.6 (`woken`, `seen`, `blocked`) | conta solo `expected` contro chiusa |
| tre significati di `outbox/`, due formati di thread | un solo modello in SQLite |
| cinque implementazioni degli indirizzi | un solo modulo |

Alias delle vecchie tool (`bus_gestisci`, `bus_archive`, `bus_waits`, `bus_expect`, `bus_thread`) fino al
31 ottobre 2026. `lib.sh`, `wake.py`, `collect.sh` e `custode.sh` restano nel repo per il mini.

## 12. Migrazione e ritorno

Il passaggio è a passi, ciascuno con il bus funzionante: passo 0 (correzioni al sistema di prima), passo 1
(centralino in modalità ombra dietro le stesse tool), passo 2 (lettori e scrittori sul centralino), passo 3 (SQLite
come fonte, codice morto via, pulizia attiva). Accensione e rollback della modalità ombra:
[CENTRALINO.md](./CENTRALINO.md) §12. Il modo attivo è in `~/.agent-team-os/centralino.json`
(`fonte: sqlite`, `sveglia: attiva`, `raccolta: true`).

Gli script del passaggio e del ritorno sono `centralino/passaggio.sh` e `centralino/ritorno.sh` (li scrive Kai,
BR24-F5). Il ritorno rimette il bus a file: serve tenere i file di archivio e ripristinare gli hook della versione
precedente. Finché il ritorno non è stato provato, il passaggio non è definitivo.

## 13. Dove sta il resto

| Cosa | Dove |
|---|---|
| contratto del centralino, API, schema | `docs/CENTRALINO.md` |
| uso per gli agenti | `skills/agent-team-os/SKILL.md`, `commands/*.md` |
| schemi dei messaggi | `schema/` |
| bus a file del mini | `scripts/agent-team-os-lib.sh`, `-wake.py`, `-collect.sh`, `-custode.sh` |
