# Centralino: contratto (BR24-F3.1)

Stato: **approvato da Alita** il 24/10 alle 20:23. Dopo la rimozione F5.5 (§15) il centralino ha un solo
modo, quello attivo (SQLite unica fonte); la modalità ombra di §7 e §12 è storia e non esiste più nel codice.
Fonti: `~/work-hub/brainstorming/bus-redesign/PROPOSTA.md` (architettura e regole approvate da Mario
il 24/10) e `INVENTARIO.md` (stato di oggi, citato come `INV §n`).

Il centralino è un daemon locale, **unico scrittore** dello stato del bus. Nel passo 1 (BR24-F3) nacque
**in modalità ombra** (SQLite più i file di oggi, regole nuove calcolate ma non applicate); dal passo 2
(F4-F5) le applica e SQLite è l'unica fonte. Dopo F5.5 il codice dell'ombra e dei file di casella è tolto.

---

## 1. Dove sta e come gira

| Cosa | Valore |
|---|---|
| Codice | `agent-team-os/centralino/` (progetto uv, Python 3.12, solo stdlib + nessuna dipendenza esterna per il daemon) |
| Database | `~/.agent-team-os/centralino.db` (SQLite, `journal_mode=WAL`, `synchronous=NORMAL`, `foreign_keys=ON`) |
| Socket | `~/.agent-team-os/centralino.sock` (Unix domain socket, permessi `0600`, stesso utente verificato con `getpeereid`) |
| Log | `~/.agent-team-os/logs/centralino.jsonl`, una riga JSON per evento (`ts, livello, op, caller, esito, ms, dettagli`) |
| Servizio | launchd `com.agent-team-os.centralino` (`KeepAlive`, `RunAtLoad`). Il plist è nel repo ma **non viene caricato** senza l'ok di Alita |
| CLI | `centralino serve`, `centralino status`, `centralino sim` (banco di prova) |
| Radice | `AB_HOME` (default `~/.agent-team-os`): i test girano su una radice temporanea, mai su quella vera |

Un solo processo scrive SQLite: tutte le scritture passano da un'unica coda servita da un thread, con
`BEGIN IMMEDIATE` per operazione. Le letture usano connessioni separate (WAL lo permette).

---

## 2. Un solo modulo per indirizzi e identità (`centralino/indirizzi.py`)

Sostituisce le 5 implementazioni di oggi (INV §5.1). Il MCP **non** risolve più niente: manda al
centralino i dati grezzi del chiamante e riceve l'identità risolta.

- **Agente da cwd**: regola di `AGENT_MAP.rules` con il prefisso **più lungo**, confrontato sul confine
  di cartella (`/a/work-hub` combacia con `/a/work-hub` e `/a/work-hub/x`, non con `/a/work-hub-x`:
  oggi `bus.py:185` lo fa combaciare). Si prova sia il path com'è sia quello risolto (symlink).
  `AB_AGENT` vince su tutto.
- **Slug da cwd**: `AB_SESSION_SLUG`, altrimenti basename del path risolto, minuscolo, caratteri non
  alfanumerici piegati in `-` (identico a `slug_for_path` di oggi, così le caselle `@slug` restano le stesse).
- **Indirizzo**: `agente`, `agente/slug`, `agente@host`, `agente@host/slug`, `agente/slug@host`. Host
  assente: `agents.<a>.host`, poi l'host hub, poi locale (come `resolve_host` di oggi).
- **Host di questa macchina**: file `HOST`, altrimenti l'host con `hub: true`.
- `AGENT_MAP.json` si rilegge quando cambia `mtime`; un file malformato lascia in uso l'ultima versione buona e scrive un errore nel log.

Gli hook bash e `lib.sh` nel passo 1 restano come sono (la lib prende la prima regola, INV §3.a): il
passo 0 la allinea, il passo 2 la sostituisce con eventi al centralino.

---

## 3. API sul socket

### 3.1 Protocollo

JSON per riga (`\n`), una richiesta e una risposta per connessione. Massimo 1 MB per richiesta.

```json
{"v": 1, "id": "r-<rand>", "op": "send",
 "caller": {"cwd": "/Users/.../orbit", "agent": null, "slug": null,
            "pid": 6096, "claude_session_id": "…", "onda_terminal_id": "pane-…", "tmux_pane": null},
 "args": { … }}
```

`caller.agent`/`caller.slug` portano `AB_AGENT`/`AB_SESSION_SLUG` se impostati, altrimenti `null`:
li risolve il centralino (§2). Le operazioni di servizio (`ombra.*`, `admin.*`) non hanno `caller`.

Risposta: `{"v":1, "id":"r-…", "ok":true, "result":{…}, "warnings":[…]}` oppure
`{"v":1, "id":"r-…", "ok":false, "error":{"code":"…", "message":"…"}}`.

Codici d'errore: `bad_request` (argomenti), `unknown_agent`, `no_identity` (cwd non mappato),
`kind_required`, `not_found` (messaggio o attesa), `not_yours` (attesa di un'altra sessione),
`already_filed`, `conflict`, `remote_failed`, `internal`. Il messaggio è in italiano, pensato per essere
mostrato così com'è al modello.

### 3.2 Operazioni degli agenti

| op | args | result | Note |
|---|---|---|---|
| `send` | `to, type, intent, payload, kind?, priority, thread_id?, in_reply_to?, task_id?, context_refs?, requires_response, response_by?` | record del messaggio `{id, to, slug, host, thread_id, task_id?, queued?, path}` | regole di `bus.send` di oggi (kind obbligatorio per request/brief/handoff, `task_id` ereditato da `in_reply_to`); con `kind=delega` crea l'attesa nella stessa transazione |
| `reply` | `in_reply_to, type (response\|question), summary, details?` | come `send` | `task_id` preso **solo** dal messaggio a cui risponde; indirizzo = `reply_to` del messaggio. Nel passo 1 è un alias di `send` con `in_reply_to`: le tool non lo espongono ancora |
| `expect` | `description, delegated_to?, deadline?, task_id?` | `{task_id, deadline, prompt_line}` | attesa senza messaggio (deleghe via Onda) |
| `handle` | `message_id, esito (chiuso\|riarma\|risposto\|letto), reply?, task_id?` | come `bus.handle` di oggi + `remaining` | |
| `close` | `task_id, esito (chiuso\|annullato), note?` | `{task_id, state, closed_at}` | **nuova**: chiude un'attesa della propria sessione anche senza messaggi pending (caso iv, INV §7). La espone la tool che il passo 0 introduce per F2.3 |
| `archive` | `message_id, state (done\|read)` | `{archived, state, path, remaining}` | |
| `inbox` | `all_sessions, unread_only` | `{agent, slug, count, messages[]}` | stesse righe di `_summarize` di oggi |
| `read` | `message_id, archive` | messaggio intero + `_reply_to`, `_task_hint`, `_location` | la prima lettura di una delega da parte di chi la esegue ne fissa `letto_at` (§18) |
| `thread` | `thread_id` | `{thread_id, count, entries[]}` | unisce i due formati di oggi (`.json` e `.jsonl`) |
| `waits` | `include_closed, all_sessions` | come `bus_waits` di oggi | |
| `status` | `agent?` | come `bus_status` di oggi | sessioni vive con la regola unica (§5) |

### 3.3 Eventi di sessione

| op | args | Chi lo manda nel passo 1 |
|---|---|---|
| `session.heartbeat` | `—` (basta `caller`) | il MCP, ogni 60 s finché il suo processo vive (vedi §5, domanda 3) |
| `session.event` | `event (start\|prompt\|stop\|end), source` | nessuno nel passo 1; dal passo 2 gli hook al posto di `ab_update_session_registry` |

### 3.4 Operazioni di servizio

`admin.ping` (versione, schema, uptime, coda di scrittura), `admin.stats`. Le operazioni `ombra.*`
(report, import, confronto) sono tolte con F5.5.

---

## 4. Schema SQLite

```sql
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS messages (
  id                TEXT PRIMARY KEY,
  ts                TEXT NOT NULL,
  from_agent        TEXT NOT NULL,
  from_slug         TEXT,
  from_host         TEXT,
  to_agent          TEXT NOT NULL,
  to_slug           TEXT,
  to_host           TEXT NOT NULL,
  type              TEXT NOT NULL,
  kind              TEXT,
  intent            TEXT,
  priority          TEXT NOT NULL DEFAULT 'normal',
  thread_id         TEXT NOT NULL,
  in_reply_to       TEXT,
  task_id           TEXT,
  requires_response INTEGER NOT NULL DEFAULT 0,
  response_by       TEXT,
  state             TEXT NOT NULL,
  esito             TEXT,
  handled_by        TEXT,
  handled_at        TEXT,
  delivery          TEXT NOT NULL,
  origin            TEXT NOT NULL,
  path              TEXT,
  raw               TEXT NOT NULL,
  letto_at          TEXT,   -- v4 (BR24-F6.6, §18)
  letto_da          TEXT    -- v4
);
CREATE INDEX IF NOT EXISTS messages_inbox  ON messages(to_agent, to_slug, state);
CREATE INDEX IF NOT EXISTS messages_task   ON messages(task_id);
CREATE INDEX IF NOT EXISTS messages_thread ON messages(thread_id, ts);

CREATE TABLE IF NOT EXISTS waits (
  task_id          TEXT PRIMARY KEY,
  owner_agent      TEXT NOT NULL,
  owner_slug       TEXT NOT NULL,
  owner_host       TEXT,
  description      TEXT,
  delegated_to     TEXT,
  executor_session TEXT,
  delegation_msg   TEXT,
  thread_id        TEXT,
  created_at       TEXT NOT NULL,
  deadline         TEXT,
  state            TEXT NOT NULL,
  last_activity    TEXT,
  closed_at        TEXT,
  closed_by        TEXT,
  close_reason     TEXT,
  target           TEXT,
  origin           TEXT NOT NULL,
  raw              TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS waits_owner ON waits(owner_agent, owner_slug, state);

CREATE TABLE IF NOT EXISTS wait_returns (
  task_id   TEXT NOT NULL,
  msg_id    TEXT NOT NULL,
  from_addr TEXT,
  at        TEXT,
  type      TEXT,
  state     TEXT NOT NULL,
  esito     TEXT,
  PRIMARY KEY (task_id, msg_id)
);

CREATE TABLE IF NOT EXISTS sessions (
  agent             TEXT NOT NULL,
  slug              TEXT NOT NULL,
  host              TEXT NOT NULL,
  workspace_path    TEXT,
  claude_session_id TEXT,
  onda_terminal_id  TEXT,
  tmux_pane         TEXT,
  pid               INTEGER,
  started_at        TEXT,
  last_heartbeat    TEXT,
  last_event        TEXT,
  ended_at          TEXT,
  PRIMARY KEY (agent, slug, host)
);

CREATE TABLE IF NOT EXISTS wake_decisions (
  id        INTEGER PRIMARY KEY,
  at        TEXT NOT NULL,
  giro      INTEGER NOT NULL,
  agent     TEXT NOT NULL,
  slug      TEXT NOT NULL,
  msg_id    TEXT NOT NULL,
  task_id   TEXT,
  regola    TEXT NOT NULL,
  decisione TEXT NOT NULL,
  motivo    TEXT,
  canale    TEXT
);
CREATE INDEX IF NOT EXISTS wake_msg ON wake_decisions(msg_id, agent, slug, id);
CREATE INDEX IF NOT EXISTS wake_at  ON wake_decisions(agent, slug, decisione, at);

CREATE TABLE IF NOT EXISTS remote_queue (
  msg_id     TEXT PRIMARY KEY,
  host       TEXT NOT NULL,
  queued_at  TEXT NOT NULL,
  attempts   INTEGER NOT NULL DEFAULT 0,
  last_error TEXT
);

CREATE TABLE IF NOT EXISTS shadow_diffs (
  id     INTEGER PRIMARY KEY,
  at     TEXT NOT NULL,
  giro   INTEGER NOT NULL,
  area   TEXT NOT NULL,
  chiave TEXT NOT NULL,
  attesa TEXT,
  sqlite TEXT,
  file   TEXT
);
CREATE INDEX IF NOT EXISTS diffs_at ON shadow_diffs(at, area);
```

Lo stato di Onda delle sessioni (`onda_state`, pannello) non si copia in tabella: si legge dal vivo da
`sessions.json` a ogni domanda (§5). `wake_decisions.giro` raggruppa le decisioni di uno stesso giro
(accorpamento). I file di oggi restano il formato di scambio: `raw` conserva il record v1.0 così com'è (campi
opzionali compresi: `wake_chain`, `task_link`, `context`, `handled`), e il file in casella si scrive da lì.

---

## 5. Regola unica di sessione viva

Una sessione `(agente, slug, host)` è **viva** se vale almeno una delle due:

1. **Onda la dà viva**: `onda/sessions.json` valido (`v == 1`, `ondaPid` vivo, `writtenAt` più recente
   di 60 s) e contiene una voce con quell'`agent`+`slug` (o quel `claude_session_id`) e `state != "exited"`.
2. **Heartbeat negli ultimi 120 s** (`sessions.last_heartbeat`).

Altrimenti è **chiusa**. Nient'altro: niente TTL di 240 minuti, niente pid del registro, niente `last_seen`.

- `sessions.json` si legge **così com'è** (contratto `v:1` di Onda), a ogni cambio di `mtime` e
  comunque ogni 5 s. Il centralino non chiede nulla a Onda e non scrive in `onda/`.
- Sessioni di un altro host: vive se lo dice l'ultimo stato raccolto da quell'host (oggi
  `hosts/<h>/status.json`), con l'età dello stato riportata in `status`.
- **Viva non vuol dire raggiungibile**: il canale di sveglia è Onda (pannello in `sessions.json`),
  oppure tmux se l'attesa ha un `tmux_pane`. Una sessione viva senza canale (Alita in `claude attach`
  il 24/10, INV §5.5.11) dà `nessun_canale` per i messaggi indirizzati a lei, e dal 26/10 non riceve
  quelli della casella condivisa né quelli a un agente nudo (§22).

Nel passo 1 questa regola alimenta `status`, l'avviso "no live session" di `send` e le decisioni ombra
della sveglia. Il registro `registry/` continua a essere scritto dagli hook come oggi.

---

## 6. Tabella di sveglia e blocco dello Stop

Calcolata per ogni messaggio pending destinato a una sessione `S` (o alla casella condivisa: in quel
caso a ogni sessione viva **con un canale** dell'agente, §22). "S ferma" = Onda dice `idle` e senza testo nella
casella (`hasText == false`) da almeno 5 s.

| # | Messaggio | Riconoscimento | S ferma | S al lavoro | Blocca lo Stop |
|---|---|---|---|---|---|
| 1 | esito di una delega | `type=response`, `task_id` di un'attesa `expected` di **S** | sveglia **sempre**, esente dal tetto | coda, a fine turno | sì |
| 2 | domanda su una delega | `type=question`, `task_id` di un'attesa `expected` di S | sveglia | coda | sì |
| 3 | risposta all'esecutore | `type=response` con `task_id`: attesa `expected` di un'altra sessione, oppure nessuna attesa su questa macchina (il delegante risponde all'esecutore) | sveglia | coda | sì |
| 4 | delega nuova | request/brief/handoff con `kind=delega` (o vecchio, senza `kind`, con `requires_response`) | sveglia | coda | sì |
| 4b | da agganciare | `task_link.by = da_agganciare` (nel passo 1 lo scrive ancora `wake.py`) | sveglia | coda | sì |
| 5 | urgente | `priority=urgent`, non nelle righe sopra | sveglia, max 1 ogni 15 min per coppia **di sessioni** | coda | sì |
| 5b | domanda libera | `type=question` fuori da una delega aperta di S | niente | niente | sì |
| 6 | tutto il resto | comunicazione, event, cc-notify, report, response senza `task_id` o su attesa chiusa | niente | niente | **no** |

Le righe 3, 4, 4b e 5b e la colonna dello Stop seguono `ab_msg_actionable` della lib dopo il passo 0
(BR24-F2.7), così nel confronto ombra restano solo le differenze vere.

- **Un messaggio senza `task_id` non è mai un ritorno**: niente aggancio indovinato. I messaggi del
  cloud senza `task_id` cadono nella riga 6 e si contano a parte (`senza_task`) per decidere più avanti
  se serve Haiku (decisione 2 di Mario).
- **Un solo tetto**: 10 sveglie all'ora per sessione; la riga 1 non conta e non è mai trattenuta.
- Più messaggi per la stessa sessione nello stesso giro diventano **una** sveglia (come `batch_text` oggi).
- La proprietà di un'attesa è **per sessione** (`handle`, `_task_hint` di `read`), e passa a un'altra
  sessione dello stesso agente solo se quella che ha delegato non è più viva per la regola unica.
  La chiusura per `task_id` (`bus_gestisci` senza `message_id`) è aperta a ogni sessione dell'agente,
  come nel passo 0 (INV §5.5.1, BR24-F2.3 e F2.6).
- Scadenza: `deadline` superata, oppure 72 h senza attività, **anche con ritorni già gestiti**; la
  chiusura toglie ogni `pending_wake` (INV §5.5.3, §5.5.9).

Nel passo 1 la tabella **non sveglia nessuno**: scrive la decisione in `wake_decisions`. La sveglia
vera resta `wake.py`, che il centralino continua a lanciare dopo ogni `send` non-delega e dopo
`handle risposto`, esattamente come fa oggi il MCP (`_kick_wake`).

---

## 7. Modalità ombra (storico, tolta con F5.5)

Nel passo 1 i file restavano la fonte e SQLite li specchiava: un import ogni 5 s dei file scritti da
altri, un confronto ogni 10 minuti in `shadow_diffs` (messaggi, attese, sessioni, sveglia, stop) con le
differenze attese marcate, e `centralino ombra report --ore 48` come criterio di uscita (0 differenze non
attese su messaggi e attese per 48 h). Servì a misurare che le regole nuove facessero meglio di quelle di
oggi. Dopo il passaggio non serve più: `ombra.py`, `files.py`, `migra.py` e il modo in `config.py` sono
rimossi, la tabella `shadow_diffs` e le decisioni solo decise (`modo='ombra'`) spariscono con lo schema v3
al primo avvio. Per il testo completo vedi la storia del branch `feat/br24-centralino`.

---

## 8. Compatibilità delle 9 tool

Il MCP diventa un client sottile: costruisce `caller` dall'ambiente (`AB_CWD`/`CLAUDE_PROJECT_DIR`/cwd,
`AB_AGENT`, `AB_SESSION_SLUG`, `ONDA_TERMINAL_ID`, `CLAUDE_CODE_SESSION_ID`, `TMUX_PANE`, pid del
processo padre), chiama l'operazione e restituisce `result` così com'è. Firme, nomi dei parametri,
docstring e `instructions` restano quelli di oggi.

| Tool | op | Cambia qualcosa per l'agente? |
|---|---|---|
| `bus_status` | `status` | sessioni vive con la regola unica (§5): sparisce il falso "non viva" (caso ii) |
| `bus_inbox` | `inbox` | no |
| `bus_read` | `read` | `_task_hint` "hai delegato" solo alla sessione proprietaria |
| `bus_send` | `send` | l'avviso "no live session" usa la regola unica |
| `bus_archive` | `archive` | no |
| `bus_thread` | `thread` | no |
| `bus_expect` | `expect` | no |
| `bus_waits` | `waits` | no |
| `bus_gestisci` | `handle` (e `close`, se il passo 0 lo espone qui) | no, oltre a quanto porta il passo 0 |

**Centralino giù**: la tool risponde con un errore chiaro ("il centralino del bus non risponde su
<socket>: …"), nessun ripiego silenzioso sui file (F3.3).

**Interruttore**: il MCP usa il centralino solo con `AB_CENTRALINO=1` nell'ambiente (impostato in
`servers.yaml`); senza, gira il codice di oggi. È il rollback del passo 1: si toglie la variabile e le
sessioni nuove tornano al percorso a file. Si toglie nel passo 3.

**Allineamento col passo 0**: il centralino implementa le regole **dopo** le correzioni di BR24-F2
(esito fuori dai tetti, `close`, aggancio solo nella stessa sessione e mai per le comunicazioni, Stop
solo su messaggi azionabili, scadenza vera). Se il passo 0 aggiunge parametri alle tool, il centralino
li riceve come argomenti in più della stessa operazione.

---

## 9. Banco di prova (F3.5)

`centralino sim` gira su un `AB_HOME` temporaneo con sessioni finte (finto `sessions.json` scritto dal
test, heartbeat simulati, orologio iniettabile) e verifica stato SQLite, file e decisioni di sveglia.
Casi minimi, quelli del 24/10 come regressione:

1. **irjocy**: 3 domande + 1 risposta intermedia nell'ora, poi l'esito finale → sveglia subito (riga 1).
2. **orbit non viva**: sessione viva per Onda, assente dal registro → `status` la dà viva, nessun avviso.
3. **xn9lv0**: comunicazioni di `alita/work-hub` e messaggi del cloud senza `task_id` verso `kai` →
   nessun aggancio, nessuna sveglia, non bloccano lo Stop; l'attesa scaduta con ritorni gestiti scade.
4. **attese non chiudibili**: ritorni archiviati con `archive`, poi `close(task_id)` → `closed`.
5. Delega con domanda, risposta `risposto`, esito, `chiuso`; sessione che muore a metà (heartbeat che
   si ferma, voce `exited` in Onda); host mini irraggiungibile (consegna in coda, `queued=true`);
   due sessioni dello stesso agente con un'attesa ciascuna (proprietà per sessione).
6. Ombra: un messaggio scritto dalla lib e una riscrittura di `wake.py` vengono importati senza differenze non attese.

---

## 10. Note di implementazione (passo 1)

- **Identità di un messaggio** = nome del file (`msg-….json`), come per le tool di oggi. Qualche file
  vecchio ha un `id` interno diverso (due del 13 settembre): in SQLite vale il nome del file.
- Lo **stato delle attese** si conserva com'è nei file (anche `woken`/`seen`/`blocked` della v2.6): conta
  aperta solo `expected`.
- I record vecchi con un campo di tipo sbagliato (es. `context` stringa) si importano con quel campo
  vuoto; un record illeggibile si salta e finisce nel log, senza fermare il giro.
- **Scadenze delle attese**: nel passo 1 restano a `wake.py` (passo 0). Il centralino le prende dal F4.1.
- Il primo confronto della sveglia parte dalla fine di `sveglia.log`: lo storico precede le decisioni ombra.
- La **raccolta dagli altri host** (`inbox`, `status`) gira fuori dal lock di scrittura; i messaggi tirati
  giù si registrano dopo in una transazione breve (e comunque l'import li vede entro 5 s).
- **Limite noto**: la consegna SSH di un `send` verso il mini avviene ancora dentro la transazione
  (fino a ~5 s con il mini irraggiungibile, poi coda). Si toglie nel F4.4 (coda prima, consegna dopo).
- Lock comuni con il sistema di oggi: `locks/waits.flock` (flock, attese), `locks/<agente>.lock` (caselle),
  `locks/collect-<host>.lock` (raccolta).

## 11. Decisioni (review di Alita, 24/10 20:23)

1. **Interruttore `AB_CENTRALINO`**: sì, **spento di default**. Si accende prima su una sola sessione
   di prova, poi su tutte, mai senza l'ok di Alita.
2. **Agente nudo** (dal passo 2; nel passo 1 resta la casella condivisa di oggi): l'unica sessione viva
   con un canale se ce n'è una sola; altrimenti la sessione di casa (`workspace_root`) se ha un canale;
   altrimenti la casella condivisa (§22).
3. **Heartbeat del MCP ogni 60 s**: sì. In `status` una sessione viva solo per heartbeat e senza
   pannello si legge chiaramente: `"viva per heartbeat, canale: nessuno"`.
4. **Esito**: ogni `response` con il `task_id` di un'attesa `expected` sveglia senza tetto, **con
   l'accorpamento** (più messaggi per la stessa sessione nello stesso giro = una sola sveglia), così un
   ritorno intermedio non diventa una raffica.
5. **Codice**: centralino in `agent-team-os/centralino/`, client sottile in `agent-bus-mcp`, tutti e due
   sul branch `feat/br24-centralino`.

In più: il passo 0 (`fix/br24-passo0`, in parallelo) cambia `wake.py` e `bus.py`. Il confronto ombra si
misura contro il comportamento **dopo** il passo 0: rebase su `main` quando è unito, e differenze
attese di §7.3 allineate a quel comportamento. Il doppio `expect` dell'hook onda-prompt è nel passo 0.

## 12. Accensione della modalità ombra (storico)

Fatta e superata: il passaggio al modo attivo l'ha sostituita. Con F5.5 non esiste più un modo da
accendere o da cui tornare indietro: il centralino parte sempre attivo (SQLite fonte, sveglia, raccolta,
pulizia).

## 13. Bus v3 dopo il passaggio (F4-F5)

Modo attivo (`centralino.json`: `fonte: sqlite`, `sveglia: attiva`, `raccolta: true`): SQLite è la fonte
unica, il centralino sveglia con la tabella di §6, raccoglie dal mini e riceve dal cloud. I file di
oggi restano come archivio in sola lettura; l'unico file che il centralino continua a scrivere è
`registry/<agente>.d/<slug>.json`, che Onda legge per risalire allo slug dal pid (Onda non si tocca).

### 13.1 MCP: sei azioni

| Tool | Parametri | Cosa fa |
|---|---|---|
| `bus_send` | `to, intent, summary, message_type (request\|brief\|handoff\|event), kind (delega\|comunicazione, obbligatorio per request/brief/handoff), priority, details, context_refs, thread_id, response_by` | messaggio nuovo; `kind=delega` registra l'attesa e restituisce il `task_id` |
| `bus_reply` | `message_id, summary, message_type (response\|question, default response), details` | risponde a un messaggio: va al suo `reply_to`, eredita thread e `task_id`, e archivia il messaggio con esito `risposto`. L'esito di una delega è `bus_reply` sulla delega; una domanda è `bus_reply(message_type='question')` |
| `bus_close` | `message_id` **oppure** `task_id`, `esito (chiuso\|riarma\|letto, default chiuso)` | consuma un messaggio (`chiuso`: fatto, e chiude l'attesa se è un ritorno della tua delega; `riarma`: continui ad aspettare; `letto`: solo letto). Con solo `task_id`: chiude l'attesa di quella delega |
| `bus_inbox` | `all_sessions, unread_only` | messaggi pending della sessione + `mie_attese` (deleghe aperte con i ritorni non gestiti) |
| `bus_read` | `message_id` **oppure** `thread_id` | il messaggio intero con il suggerimento sul task, oppure il thread in ordine |
| `bus_status` | `agent` | sessioni vive (regola unica) e host |

Alias fino al **31 ottobre 2026**, con descrizione "alias, usa X": `bus_gestisci` (→ `bus_close`, o
`bus_reply` con `risposto`), `bus_archive` (→ `bus_close`), `bus_waits` (→ `bus_inbox.mie_attese`),
`bus_expect` (deleghe via Onda: le registra l'hook onda-prompt), `bus_thread` (→ `bus_read(thread_id)`).

### 13.2 Hook

Gli hook diventano sottili e chiamano `~/.claude/scripts/agent-team-os-centralino.py` (stdlib, `-I`):

| Hook | Chiamata | Effetto |
|---|---|---|
| SessionStart | `evento start` | sessione registrata (pid, sessione Claude, pannello), banner con gli ultimi messaggi, persona |
| UserPromptSubmit | `evento prompt` | heartbeat + avviso per urgent/high (massimo 3) |
| Stop | `stop` | blocca solo con messaggi della tabella §6 colonna Stop, massimo 3 blocchi di fila |
| SessionEnd | `evento end` | sessione chiusa |
| PreToolUse onda_agent_prompt | (onda-prompt.py) | l'attesa si registra con `expect` sul centralino |

Se il centralino non risponde, gli hook non bloccano nulla e non stampano errori nel prompt.

### 13.3 Comandi slash

`/bus`, `/inbox`, `/read`, `/send`, `/reply`, `/handoff`, `/thread`, `/delegate` diventano istruzioni al
modello per usare le tool MCP (nessun Bash). `/god` sparisce.

### 13.4 Cosa resta per il mini

Superato da BR24-F5.7: anche il mini ha il suo centralino (§17).

## 14. Tetto sugli esiti: nessuno, c'è l'accorpamento

Nel centralino un esito (`response` sul `task_id` di un'attesa aperta della sessione) non ha tetto.
Il passo 0 aveva messo in `wake.py` un tetto di 4 esiti all'ora per task (`RESPONSE_MAX_PER_HOUR`),
contro un botta e risposta a vuoto fra due sessioni. Nel centralino lo stesso rischio lo copre
l'accorpamento di §6: una sessione al lavoro non si sveglia, e tutto quello che le arriva nel frattempo
parte in **una** sveglia a fine turno. Un botta e risposta quindi non va oltre una sveglia per turno del
destinatario, che è il ritmo del lavoro vero. Dato: nei 7 giorni misurati (INVENTARIO §6.2) c'è un solo
episodio di tetto su un task (`irjocy`), ed era l'esito finale legittimo che il tetto ha trattenuto. Il
tetto orario per sessione (10 all'ora, esiti esclusi) resta come rete di sicurezza contro un difetto,
non come regola del lavoro normale. `wake.py` col suo tetto si spegne al passaggio.

## 15. Dopo la prova: la rimozione (BR24-F5.5)

Obiettivo di Alita: sotto le ~4.500 righe e un solo archivio, una volta confermata la prova. Sul branch
oggi ci sono due sistemi, perché la modalità ombra li tiene in piedi tutti e due. Dopo la conferma si toglie:

| Via | Righe | Note |
|---|---|---|
| `centralino/files.py` (FileBus) | 973 | il trasporto verso il mini (SSH, coda, raccolta) passa in un `remoto.py` di ~250 righe |
| `centralino/ombra.py` | 441 | la tabella di sveglia (`_attese`, `destinazioni`, `giro_sveglia`) passa in `sveglia.py` (~120 righe) |
| `centralino/migra.py` + modo in `config.py` | ~170 | il modo diventa uno solo |
| `ops._Specchio`, i rami del modo file | ~80 | |
| MCP: `bus.py` e i rami a file di `server.py` | ~1.500 | il server MCP resta un client da ~250 righe |
| hook a file (`hooks/*.sh`, onda-prompt v2) | ~430 | restano gli hook v3 |

Stima dopo la rimozione, laptop: centralino ~3.100 righe, MCP ~370, hook v3 ~250, in totale **~3.700**.
`sim.py` (il banco di prova) resta come strumento di test e non entra nel conto.

**Il nodo è il mini.** Oggi il mini gira sul bus a file: `lib.sh` (1.600 righe), `wake.py` (2.000, con
tmux), `collect.sh`, `custode.sh`. Se il mini resta a file, quei 3.700 righe restano nel repo e
l'obiettivo non si raggiunge. La proposta è che anche il mini abbia il suo centralino (stesso codice,
canale tmux, nessuna raccolta: la fa il laptop). Il laptop gli consegna con `ssh mini … centralino consegna`
invece di scrivere file nella sua casella, e raccoglie la sua coda `outbox/laptop` come oggi. Così
`lib.sh` scende a quello che serve al custode, e `wake.py` sparisce. Va deciso prima di applicare F5.5.

**Misurato sul branch `feat/br24-f55-rimozione`** (24/10/2026, laptop, `sim.py` escluso come da conto):
centralino 3.275 righe (era 4.492), hook v3 236, MCP 391 (era 1.767 con `bus.py` e i rami a file di
`server.py`), in totale **3.902** contro le ~6.500 di prima e le ~3.700 stimate. Lo scarto di ~200 righe dalla
stima è tutto nel centralino: `remoto.py` è di 270 righe (stima 250), `sveglia.py` di 337 (stima 120 di
aggiunta, ne ha assorbite 89) e `dbbus.py` di 590 perché porta con sé l'invio e le regole di validazione che
erano in `files.py`. `sim.py` (banco di prova) resta di 574 righe. Non rimossi, perché il mini ne ha ancora
bisogno: `lib.sh`, `wake.py`, `collect.sh`, `custode.sh`, `hooks/*.sh` e i loro test. `install.sh` installa
gli hook v3 di default; `--file-bus` installa quelli a file, per il mini.

## 16. Incidente del 24/10, 21:33: il daemon in ombra si blocca alla chiusura

**Cosa.** Il daemon in ombra sul bus vero (pid 23545) dalle 21:33:26 non ha più risposto sul socket.

**Causa, dimostrata.** Gli stack Python li ho presi con `lldb` e `faulthandler.dump_traceback` nel processo bloccato.
- Il main thread, che accetta le connessioni, era nella `select` quando è arrivato un SIGTERM. Il gestore, `ferma()`, ha chiamato `stop.set()`: prende il lock (non rientrante) della `Condition` dell'Event e dentro chiama `notify_all()`.
- Proprio lì è arrivato un secondo SIGTERM. Il gestore è ripartito annidato sullo stesso `stop.set()` e si è fermato sul lock tenuto dal suo stesso thread.
- I tre thread dei giri, svegliati da `notify_all`, sono rimasti in `_acquire_restore` su quel lock.

**Da dove i segnali.** Alle 21:33:26 un `pkill -f "centralino serve"`, lanciato per fermare un daemon di prova su una radice temporanea, ha colpito anche il daemon vero e il suo `uv run`, che inoltra il segnale al figlio: due SIGTERM quasi insieme. Il terzo segnale, alle 21:36:19, era un riavvio.

**Le prove.**
- L'ultima scrittura dei giri nel WAL è delle 21:33:21.
- Il confronto delle 21:34:21 non c'è.
- Nello stack ci sono tre `ferma()` annidati.

**Correzione** (`d69f34b`, poi `839530c` sul branch di F5.5).
- `ferma()` alza solo un flag. Il server gira con un ciclo `handle_request` che lo controlla ogni 0,5 s.
- Un watchdog pinga il socket e controlla che i giri avanzino. Dopo due controlli falliti scrive nel log degli errori gli stack di tutti i thread ed esce, e launchd riavvia.
- `kill -USR1 <pid>` scrive gli stack senza root.
- Negli script lo stop è SIGTERM, poi `kill -9` dopo 6 s. Il passaggio fallisce se `status` non risponde entro 10 s.
- Test di regressione in `tests/test_vita.py`:
  - il gestore di segnale non prende lock (lo stesso test, sul `ferma()` vecchio, vede `set`, `lock.acquire` e `start`);
  - quattro SIGTERM a raffica con richieste e giri in corso, ripetuti cinque volte;
  - il watchdog che esce, sia con il socket muto sia con un giro fermo.

**Regola.** Il centralino si ferma col pid di `centralino.pid` (o con launchd), mai con `pkill -f centralino`: prende anche il daemon vero.

## 17. Il centralino sul mini (BR24-F5.7, 24/10/2026)

Il mini ha il suo centralino, stesso codice e stesso schema. Niente più bus a file.

**Avvio.** Il mini non ha login grafico (`launchctl print gui/<uid>` fallisce via SSH), quindi niente
LaunchAgent: `install.sh --daemon` scrive una riga di cron che ogni minuto avvia `centralino serve` se il
pid di `centralino.pid` non è vivo. `serve` stesso rifiuta di partire se un centralino risponde già sul
socket. Il watchdog (§16) esce se il daemon si blocca, e cron lo riavvia entro un minuto. Sul laptop resta
launchd. Il controllo del pid vuole un pid vero: `kill -0 0` colpirebbe il gruppo della shell di cron e
riuscirebbe sempre (la prima versione della riga aveva questo difetto, coperto ora da test).

**Fra i due centralini.**

| Verso | Come |
|---|---|
| laptop → mini | `ssh mini '<hosts.mini.centralino> consegna'` col messaggio su stdin; l'op `consegna` lo accoglie con le regole di tutti. Mini giù: coda in `outbox/mini/` del laptop e `remote_queue`, la raccolta lo riprova |
| mini → laptop | il mini non apre SSH: il messaggio resta in `outbox/laptop/` del mini e la raccolta del laptop lo tira giù (tar) e lo cancella là dopo averlo scritto |
| stato del mini | il laptop legge `registry/*.d/*.json` (li scrive il centralino del mini per Onda) e `<centralino> pendenti`, che legge SQLite in sola lettura |

`hosts.mini.centralino` in AGENT_MAP del laptop è il comando completo
(`/opt/homebrew/bin/uv run --frozen --project ~/Projects/01-Building/agent-team-os/centralino centralino`). Un
host con `ssh` ma senza `centralino` non riceve: il messaggio resta in coda e l'errore va nel log.

**Sveglia sul mini.** Il canale è tmux: il pannello lo dichiara l'hook (`TMUX_PANE`). Fino a F5.7 una
sessione su tmux non risultava mai `ferma` (lo stato veniva solo da Onda) e quindi non si sarebbe mai
svegliata: ora per il canale tmux decide `via_tmux` leggendo lo schermo (occupata, testo nella casella o una
domanda aperta: rinvio). Test in `tests/test_tmux.py`. Lo schermo si legge con `capture-pane -e`: le celle in SGR 2
(dim) non contano come testo, perché Claude Code disegna così il suggerimento del prompt e il placeholder
`Try "…"` (misurato sul mini il 29/10: t-20261029-gsmn19 e audit-plan-fe del 27/10 rinviati per `has_text`
con nella casella solo il suggerimento grigio). Un rinvio del canale (`tmux:*`, `onda:*`) si riprova a ogni
giro; in `wake_decisions` si riscrive identico ogni 10 minuti (`RIPROVA_LOG_S`), così si vede che la
sveglia è ancora in prova. Dopo 30 minuti di rinvio continuo per testo nella casella (`tmux:has_text` o
`onda:has_text`, `TESTO_AVVISO_S`) il centralino manda un evento `sveglia-rinviata-testo` a chi ha delegato
(il `from_session` del messaggio fermo, anche sull'altra macchina), una volta per attesa (chiave `testo:<task_id>`
in `meta`). È un evento: riga 6, non sveglia e non blocca lo Stop; ad Alita non va.

**Migrazione del 24/10.** Backup del mini in `~/.agent-team-os-backup/f57-20261024-224050`. I 22 messaggi
pending (2 prove di kai del 14/10, 20 eventi `otto-giro` di leo) sono entrati nel centralino del mini con
`consegna` (origine `migrazione-f57`); `inboxes/` e `waits/` (4 attese di prova del 17/10) restano come
`inboxes.pre-f57` e `waits.pre-f57`. Tolte dal crontab la sveglia (`wake.py tick`) e la pulizia `--registro`
del custode; il custode delle 7:00 resta, senza lib.

**Da sapere.** `00-Brandart-PM-OS/contratti/run.sh` (il giro di Otto, sul mini) scrive i suoi eventi come file
in `inboxes/leo/@00-brandart-pm-os/`: dopo F5.7 quei file non li legge nessuno. Va passato a
`centralino consegna` (vault di Leo).

**Conteggio** (righe di codice, `sim.py` escluso; comando: `git show <ref>:<file> | wc -l` sui file del
centralino, degli hook v3, del MCP e di `lib.sh`/`wake.py`/`collect.sh` più gli hook a file):

| | centralino | hook v3 | MCP | bus a file | totale |
|---|---:|---:|---:|---:|---:|
| `feat/br24-centralino` (ombra) | 4.593 | 236 | 1.892 | 3.990 | 10.711 |
| dopo F5.5, laptop | 3.388 | 236 | 391 | 3.990 | 8.005 |
| dopo F5.7 | 3.425 | 260 | 391 | 0 | **4.076** |

## 18. Chi ha la palla: delega letta e `stato_palla` (BR24-F6.6, 25/10/2026)

La banda diceva "Kai lavora" anche quando la sessione esecutrice lavorava su altro e la delega non
l'aveva ancora aperta, e calcolava da sola lo stato di ogni delega. Ora il centralino registra la
lettura e calcola lo stato, per la banda e per l'Ufficio di Orbit.

**Lettura (schema v4).** `messages.letto_at`, `messages.letto_da`, aggiunte da `MIGRAZIONI`. Prima di
migrare un database di schema più vecchio, `Store` ne fa una copia accanto con la backup API di SQLite
(`centralino.db.bak-v3-<ora>`), una volta sola. Ogni messaggio registra la sua prima lettura (`bus_read` della
sessione destinataria, sveglia consegnata); una delega (`kind=delega`) conta come letta alla prima di:

- `read` (`bus_read`) della sessione destinataria, o di una sessione dell'agente se è andata alla casella
  comune; non conta la lettura del delegante né di un'altra sessione dello stesso agente;
- un ritorno dell'esecutore sul task (esito, domanda, qualunque messaggio col task_id): `letto_da` è chi
  lo manda, anche da un'altra macchina (`kai@mini/slug`);
- una sveglia consegnata nella casella del prompt della sessione esecutrice (decisione `sveglia`).

È solo un registro: la sveglia non lo guarda. Alla migrazione le deleghe già esistenti valgono come
lette alla prima sveglia consegnata, se c'è, altrimenti all'ora della migrazione (`letto_da` vuoto):
meglio "ci lavora" come prima che un "in coda" falso. Il prompt dell'utente nella sessione esecutrice
non conta (non è un segnale che il centralino veda).

**Vista.** Ogni attesa porta, accanto a `raw`:

| Campo | Cosa |
|---|---|
| `letto_at`, `letto_da` | quando e chi ha letto la delega; `null` finché non è letta |
| `stato_palla` | `domanda`, `esito`, `scaduta`, `mario`, `consegnato`, `fermo`, `chiusa`, `coda`, `lavora` |
| `riga.glifo`, `riga.parola` | lo stato in glifo e parola (mai il solo colore): `?` domanda per te, `✉` esito da leggere, `!` scaduta, `●` tocca a Mario, `✓` Kai ha consegnato, `◌` Kai fermo, `○` Kai non aperto (`qui · vivo in <slug>` `+N`), `⧗` in coda: Kai occupato, `◐` Kai ci lavora |
| `riga.ordine` | posizione dello stato, prima ciò che chiede qualcosa a chi ha delegato |
| `riga.codice`, `riga.piano` | codice del piano dall'intent o dal summary (`BR24-F6.6`), e il piano |
| `riga.titolo` | titolo del passo (o della fase) in Orbit, prima clausola; senza Orbit la prima frase del summary |
| `riga.esecutore`, `riga.vivo_in` | la sessione che esegue (dal ritorno, da chi ha letto, o l'indirizzo della delega) e, se non è viva, le cartelle dove l'agente è vivo: `riga.parola` le dice già (`qui · vivo in …`), chi disegna non le aggiunge una seconda volta |
| `riga.da`, `riga.leggi` | età della delega; `/read <id>` della domanda aperta, del primo esito non gestito o della delega |
| `riga.allerta`, `riga.nota` | da guardare subito; il tetto che trattiene una sveglia, in parola |

Un'attesa chiusa (la vista dà anche quelle dell'ultimo giorno) ha `stato_palla` e `riga` a `null`.

Regole (`centralino/palla.py`, test in `tests/test_palla.py`): scaduta, poi domanda aperta, poi esito
non gestito, poi `mario` se l'ultimo ritorno è riarmato e in Orbit il passo è di Mario (o Mario ha un
passo aperto nella fase) e il delegante non ha più scritto sul task dopo quel ritorno, poi sessione
esecutrice viva al lavoro (`coda` se la delega non è letta,
`lavora` se sì), poi `consegnato` se riarmata, `fermo` se viva e ferma, altrimenti `chiusa`. Un'attesa
senza messaggio di delega (registrata da `bus_expect`) non sa se è letta e resta a `lavora`. **Ultima
mossa** (F6.8): se dopo l'ultimo ritorno il delegante ha scritto sul task (risposta a un esito riarmato o a
una domanda), la palla torna a chi esegue anche con un passo di Mario aperto in Orbit: `coda` finché quel
messaggio non è letto (o consumato), poi `lavora`; `fermo` o `chiusa` se la sessione è ferma o non c'è. Orbit si
legge in sola lettura da `~/Library/Application Support/Orbit/orbit.db` (`AB_ORBIT_DB` per cambiarlo),
una query per tutti i codici, tenuta un minuto; sul mini non c'è e il titolo viene dal summary. Una
delega a un'altra macchina diventa letta solo al primo ritorno: le sessioni dell'altra macchina non sono
fra le vive di questa, quindi la riga dice `chiusa` finché non torna qualcosa.

**Messaggi per chi li riceve (BR24-F6.8).** Ogni messaggio pending della vista porta anche `stato_msg` e
`riga`, lo stato per la sessione destinataria: `risposta` (risposta a un suo messaggio: a una domanda, a un esito),
`nuova` (delega non letta), `comunicazione` (tutto ciò che non è una delega né una risposta a lei),
`lavori` (delega letta), `domanda` (delega su cui la sessione ha una domanda senza risposta), `ritorno`
(ritorno su una delega che la sessione ha dato: sta fra le sue attese, `riga` è `null`). `riga`: glifo
(`↩ ✉ ≡ ◐ ?`), parola, ordine, codice, piano, titolo (Orbit, altrimenti la prima frase del summary),
mittente, da, leggi (`/read <id>`), priorita (`urgente`, `alta`, altrimenti `null`). La banda li disegna
nella sezione "Da fare".

## 19. Sessioni fantasma dei processi bg-spare (BR24-F6.13, 25/10/2026)

Il daemon di Claude Code tiene pronti processi di riserva (`claude bg-spare --bg-spare <sock>`, figli di
`claude bg-pty-host`) con la cwd di una cartella già usata. Caricano il MCP agent-bus, che batte ogni 60 s
anche se nessuno ha mai usato quel processo. Il 25/10 kai/onda-electron, chiusa da Alita alle 08:40, risultava
ancora viva "per heartbeat, canale nessuno" e la banda la segnalava come "senza canale di sveglia". In più
l'heartbeat dopo l'evento `end` azzerava `ended_at` e la sessione tornava viva.

- Un heartbeat senza evento non riapre una sessione chiusa (`ended_at` impostato o `last_event='end'`): la
  riapre solo un evento di sessione (`start`, `prompt`, `stop`) dagli hook.
- Viva per heartbeat solo con un evento di sessione alle spalle (`last_event` non nullo e diverso da `end`):
  un processo che carica il MCP senza che sia partito nessun hook non è una sessione. Quando il processo di
  riserva viene preso per una sessione vera, l'hook di SessionStart la registra.
- Viva per Onda resta com'è (il pannello è la prova). Il processo bg-spare non si tocca: è del daemon di
  Claude Code. Scenario `caso_bg_spare` nel banco di prova.

## 20. Slug senza maiuscole (BR24-F6.12, 25/10/2026)

Lo slug di una sessione viene dalla cartella in minuscolo (`~/Projects/01-Building/Orbit` → `orbit`), ma un
indirizzo scritto a mano come `kai/Orbit` apriva una casella `Orbit` che nessuna sessione legge: il 24 e il
25/10 due messaggi per Kai di Orbit sono finiti lì.

- `indirizzi.norm_slug`: minuscolo, il resto piegato in `-` come per la cwd. Lo usano `parse_addr` (invio:
  `kai/Orbit`, `KAI/ORBIT` → `kai/orbit`; anche l'agente va in minuscolo), `identify` (registrazione e
  lettura, compreso `AB_SESSION_SLUG`) e la vista di Onda (`OndaView.entries`).
- Schema v5: gli slug già scritti passano in minuscolo (`messages.to_slug`/`from_slug`, `waits.owner_slug`,
  `wake_decisions.slug`, `sessions.slug`); due sessioni che diventano la stessa lasciano la più recente.
  Backup accanto prima di migrare (`centralino.db.bak-v4-<ora>`).

## 21. Tetto dello Stop per messaggio (BR24-F6.14, 25/10/2026)

Il contatore dei blocchi era per sessione (`sessions.drain_blocks`) e tornava a zero dopo il terzo: ogni
turno nuovo, anche nato da una sveglia o dalla notifica di un task in background, ne concedeva altri tre.
Nella prova del 25/10 (sessione kai/agent-team-os-prova-f66) una sola delega lasciata apposta senza risposta
ha dato quattro giri da tre blocchi.

- Tabella `stop_blocks` (agent, slug, host, msg_id, n, at): ogni Stop che blocca conta +1 su ciascun
  messaggio che lo trattiene. Dopo `cap` blocchi (3) quel messaggio non trattiene più lo Stop, in nessun
  turno, finché sul suo task non arriva un messaggio nuovo (ts dopo l'ultimo blocco): allora riparte da zero.
- Un messaggio nuovo che chiede un'azione blocca con il suo contatore; lo Stop mostra solo i messaggi che
  contano ancora. `sessions.drain_blocks` resta nello schema ma non si usa più.
- La pulizia toglie i blocchi dei messaggi già consumati. Scenario `att_stop` nel banco di prova.

## 22. Niente consegne a sessioni senza canale (AM26-F2.2, 26/10/2026)

**Il caso.** Fra le 17:44 e le 18:16 alita/cloud ha mandato a `kai` (nudo) quattro richieste di Mario
per Alita Mobile. Sono entrate nella casella condivisa di Kai, giusto; ma l'unica sessione viva di Kai
era kai/lighthouse, viva per battito e senza pannello. La sveglia le ha assegnate a lei (`coda`,
`nessun_canale`) e ad Alita sono arrivati quattro eventi "sveglia senza canale", non azionabili: nessuno
li ha letti per quattro ore, finché Mario non ha aperto kai/alita-mobile. Con `bus_send` a `kai` nudo
andava anche peggio: `slug_nudo` metteva il messaggio direttamente nella casella di lighthouse, e una
sessione aperta dopo non lo vedeva.

**Regole.**

- Una sessione viva senza canale (né Onda né tmux) resta viva per il battito, ma non entra nella scelta:
  `slug_nudo` e i destinatari della casella condivisa guardano solo `Vivacita.slug_con_canale`. Il
  messaggio resta nella casella condivisa e lo riceve la prima sessione con canale che si apre (sveglia
  al primo giro, e lo vede in `bus_inbox`). Lo Stop di una sessione senza canale non si blocca per i
  messaggi della casella condivisa. Un messaggio indirizzato esplicitamente a quella sessione resta suo
  (`coda`, `nessun_canale`) come prima.
- **Avviso all'hub.** Un messaggio che chiede un'azione (regole che svegliano, §6), fermo da più di
  10 minuti (`FERMO_S`) e senza nessuna sessione con canale che lo possa ricevere (stanza chiusa o muta,
  oppure nessuna sessione con canale per la casella condivisa), entra in un gruppo per destinatario
  (agente, stanza o casella condivisa) e mittente. Per ogni gruppo arriva **un** messaggio
  `messaggi-fermi-senza-canale`, priorità `urgent`, alla sessione dell'hub con canale (casa se possibile):
  "4 messaggi per kai da alita/cloud@cloud fermi da 10 min: nessuna sessione di kai con canale (vive senza
  canale: lighthouse). Intent: …". È azionabile: sveglia Alita e ne tiene lo Stop finché non lo chiude.
  La chiave in `meta` è il gruppo più il suo messaggio più vecchio: lo stesso gruppo non riavvisa, un gruppo
  nuovo sì.
- Se l'hub non ha un canale, o il gruppo è per l'hub stesso, al posto del messaggio una notifica di sistema
  (`osascript`), una per gruppo.
- Sostituisce gli eventi `sveglia-senza-canale` (uno per messaggio, priorità `high`, dopo 5 minuti).

Regressioni: `caso_am26_cloud_a_kai_nudo` e `caso_am26_tmux_e_hub_muto` nel banco (tmux conta come canale,
per il mini), `att_senza_canale`, `tests/test_f55e.py`.

