# Centralino: contratto (BR24-F3.1)

Stato: **approvato da Alita** il 24/10 alle 20:23; passo 1 implementato in `centralino/` (vedi §10-§12).
Fonti: `~/work-hub/brainstorming/bus-redesign/PROPOSTA.md` (architettura e regole approvate da Mario
il 24/10) e `INVENTARIO.md` (stato di oggi, citato come `INV §n`).

Il centralino è un daemon locale, **unico scrittore** dello stato del bus. Nel passo 1 (BR24-F3) nasce
**in modalità ombra**: sta dietro le stesse 9 tool del MCP, scrive SQLite **e** i file di oggi, e i
lettori di oggi (sveglia, hook Stop, Orbit, bus-band) continuano a leggere i file senza accorgersene.
Le regole nuove (sessione viva, sveglia, Stop) nel passo 1 **si calcolano ma non si applicano**: si
confrontano con quello che fa il sistema di oggi. Si applicano dal passo 2 (F4).

---

## 1. Dove sta e come gira

| Cosa | Valore |
|---|---|
| Codice | `agent-team-os/centralino/` (progetto uv, Python 3.12, solo stdlib + nessuna dipendenza esterna per il daemon) |
| Database | `~/.agent-team-os/centralino.db` (SQLite, `journal_mode=WAL`, `synchronous=NORMAL`, `foreign_keys=ON`) |
| Socket | `~/.agent-team-os/centralino.sock` (Unix domain socket, permessi `0600`, stesso utente verificato con `getpeereid`) |
| Log | `~/.agent-team-os/logs/centralino.jsonl`, una riga JSON per evento (`ts, livello, op, caller, esito, ms, dettagli`) |
| Servizio | launchd `com.agent-team-os.centralino` (`KeepAlive`, `RunAtLoad`). Il plist è nel repo ma **non viene caricato** senza l'ok di Alita |
| CLI | `centralino serve`, `centralino status`, `centralino ombra report [--ore 48]`, `centralino sim` (banco di prova) |
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
| `read` | `message_id, archive` | messaggio intero + `_reply_to`, `_task_hint`, `_location` | |
| `thread` | `thread_id` | `{thread_id, count, entries[]}` | unisce i due formati di oggi (`.json` e `.jsonl`) |
| `waits` | `include_closed, all_sessions` | come `bus_waits` di oggi | |
| `status` | `agent?` | come `bus_status` di oggi | sessioni vive con la regola unica (§5) |

### 3.3 Eventi di sessione

| op | args | Chi lo manda nel passo 1 |
|---|---|---|
| `session.heartbeat` | `—` (basta `caller`) | il MCP, ogni 60 s finché il suo processo vive (vedi §5, domanda 3) |
| `session.event` | `event (start\|prompt\|stop\|end), source` | nessuno nel passo 1; dal passo 2 gli hook al posto di `ab_update_session_registry` |

### 3.4 Operazioni di servizio

`admin.ping` (versione, schema, uptime, coda di scrittura), `admin.stats`,
`ombra.report {ore}` (§7), `ombra.ingest_now` (forza un giro di import dai file, per i test).

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
  raw               TEXT NOT NULL
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
  il 24/10, INV §5.5.11) dà `nessun_canale` finché Mario non decide BR24-F1.5.

Nel passo 1 questa regola alimenta `status`, l'avviso "no live session" di `send` e le decisioni ombra
della sveglia. Il registro `registry/` continua a essere scritto dagli hook come oggi.

---

## 6. Tabella di sveglia e blocco dello Stop

Calcolata per ogni messaggio pending destinato a una sessione `S` (o alla casella condivisa: in quel
caso a ogni sessione viva dell'agente, come oggi). "S ferma" = Onda dice `idle` e senza testo nella
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

## 7. Modalità ombra

### 7.1 Chi è la fonte

Nel passo 1 **i file restano la fonte di verità** per tutti i lettori. SQLite si costruisce da due strade:

1. **Scritture del centralino** (tutto ciò che arriva dal MCP): una transazione SQLite, poi la
   scrittura dei file nel formato di oggi. Se la scrittura dei file fallisce, la transazione si annulla
   e l'agente riceve l'errore: SQLite non può essere avanti ai file.
2. **Import dei file scritti da altri** (lib/comandi slash, `wake.py`, `alita-memoria-sync`, hook
   onda-prompt, raccolta del mini): un giro ogni 5 s su `inboxes/`, `waits/`, `threads/` per `mtime`,
   più un giro completo ogni 10 minuti. Il record importato ha `origin='ingest'`.

### 7.2 File scritti dal centralino (formato di oggi, byte-compatibile con i lettori)

| File | Come oggi lo scrive | Il centralino |
|---|---|---|
| `inboxes/<a>/[@slug/]msg-*.json` | MCP `_deliver` | identico; tmp con nome unico (`.<id>.<pid>.<rand>.tmp`) + `rename`; rispetta il lock `locks/<agente>.lock` della lib |
| spostamenti in `.read/` e `.done/` | `archive_message`, `handle` | identici |
| `threads/<id>.jsonl` | MCP | identico |
| `outbox/<mittente>.jsonl` | MCP `_append_outbox` | identico |
| `waits/<a>/@<s>/<task>.json` | `bus.expect`, `bus.handle` | identico, tmp con nome unico (toglie la collisione di INV §2 punto 1) |
| `registry/<a>.d/<s>.json` campo `waiting_agent` | MCP | identico |
| `outbox/<host>/…` e consegna SSH | MCP `deliver_remote`, `queue_for_host` | identico, stesso lock `locks/collect-<host>.lock` |

### 7.3 Confronto

Un giro ogni 10 minuti (e a richiesta con `ombra.report`) confronta e scrive in `shadow_diffs`:

| Area | Cosa si confronta | Differenza attesa (marcata `atteso`) |
|---|---|---|
| messaggi | presenza, casella, stato pending/read/done, esito | nessuna |
| attese | stato, ritorni e loro stato, `executor_session` | aggancio fatto da `wake.py` su messaggi senza `task_id`; scadenze diverse (§6) |
| sessioni | viva per la regola unica vs registro (TTL 240 min) vs Onda | sì: è il punto (casi ii e iii) |
| sveglia | `wake_decisions` vs `logs/sveglia.log` (sveglia / rimandata / motivo) | `tetto_task`, `tetto_coppia`, `nessun_canale` su sessioni chiuse, sveglie da aggancio |
| stop | "bloccherebbe" per la tabella §6 vs messaggi presenti in casella (regola di oggi) | event, cc-notify, comunicazioni |

`centralino ombra report --ore 48` stampa: differenze **non attese** (devono essere 0 per passare al
passo 2) e differenze attese raggruppate per regola, con esempi. Criterio di uscita dalla modalità ombra:
48 h senza differenze non attese su messaggi e attese.

### 7.4 Cosa NON cambia nel passo 1

Nessun lettore cambia: `wake.py`, hook Stop/urgent/load/end, Orbit, bus-band, `alita-memoria-sync`,
`collect.sh`, Onda. Nessuna modifica a Onda né a `sessions.json`. Il canale di invio della lib
(`/send`, `/reply`, `/handoff`) resta e il centralino lo vede solo tramite l'import.

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
   se ce n'è una sola; altrimenti la sessione di casa (`workspace_root`) se è viva; altrimenti la
   casella condivisa.
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

## 12. Accensione della modalità ombra (da fare solo con l'ok di Alita)

1. Daemon a mano su `~/.agent-team-os`, senza launchd: `uv run --project centralino centralino serve`.
   Solo lettura dei file e scrittura di `centralino.db` e `logs/centralino.jsonl`: nessuna sessione
   cambia comportamento finché nessun MCP ha `AB_CENTRALINO=1`.
2. Una sessione di prova con `AB_CENTRALINO=1` nell'ambiente del suo MCP (non in `servers.yaml`).
3. Dopo qualche ora pulita: `AB_CENTRALINO=1` in `servers.yaml` (Kai, `mcp_sync.py`) e plist launchd.
4. 48 h: `centralino ombra report --ore 48` deve dire `passa: true` (0 differenze non attese su
   messaggi e attese). Rollback in ogni momento: togliere `AB_CENTRALINO` e fermare il daemon.

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

Il mini non ha un centralino: tiene il bus a file (lib, `wake.py` con tmux, custode). Il laptop gli
consegna via SSH e raccoglie dal suo `outbox/laptop/` come oggi, ma dentro il centralino. Per questo
`lib.sh`, `wake.py`, `collect.sh` e `custode.sh` restano nel repo; dal laptop non si usano più.

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
