# Contratto — Onda Agent Team nativo

> Stato: **bozza per review**, 10 set 2026, Kai.
> Piano: `~/work-hub/plans/PLAN-ONDA-AGENT-TEAM-NATIVE.md` · Brief: `BRIEF-KAI-onda-agent-team.md`
> Questo file è la fonte di verità per gli stream A/B/C/D. Se il codice divergerà da qui, vince questo file finché non viene modificato esplicitamente.

Quattro schema, uno per ogni superficie condivisa fra gli stream:

| Schema | Chi lo produce | Chi lo consuma |
|---|---|---|
| `agent-event.schema.json` | hook (stream A) | `agent-event-manager.ts` (A), finestra Agentic (C), onda-mcp (A) |
| `workspace-agent.schema.json` | installer + UI (stream B) | finestra Agentic (C), sidebar, AgentPeek |
| `settings-hook-entry.schema.json` | installer (stream B) | solo l'installer |
| `team-manifest.schema.json` | autore del pack (stream D) | agent-store installer (D) |

---

## 1. Discriminante di presenza: `ONDA_SOCKET`, non `ONDA_TERMINAL`

**Il PLAN §1.2 sbaglia su questo punto e va corretto prima di scrivere l'hook.**

Verificato in `onda-electron/src/main/pty/terminal-manager.ts:210-228`:

```
finalEnv.ONDA_SOCKET       = getSocketPath();   // riga 225 — SEMPRE
finalEnv.ONDA_PANE_ID      = options.paneId;    // riga 226 — sempre (se presente)
finalEnv.ONDA_TAB_ID       = options.tabId;     // riga 227 — sempre (se presente)
finalEnv.ONDA_WORKSPACE_ID = options.workspaceId; // riga 228 — sempre (se presente)

if (shell.includes('zsh')) {
  const zdotdir = getZdotdir();
  if (zdotdir) {
    finalEnv.ONDA_TERMINAL = '1';               // riga 217 — SOLO zsh + zdotdir risolto
  }
}
```

`ONDA_TERMINAL` è annidato in due condizioni: shell zsh **e** `getZdotdir()` che risolve. Con bash/fish, o con zdotdir mancante, non esiste.

**Decisioni:**

1. L'hook usa `process.env.ONDA_SOCKET` come test di presenza del driver. Se manca → nessun evento, exit 0, la logica Agent Team OS procede normalmente.
2. `ONDA_TERMINAL` resta quello che è oggi: marker di *shell integration* (OSC 7), non di presenza Onda. Non lo si usa per la correlazione.
3. **Bug collaterale da correggere in stream A**: `onda-mcp/src/index.ts:38` fa `isOnda: process.env.ONDA_TERMINAL === '1'` → falso negativo fuori da zsh. Passa a `ONDA_SOCKET`.
4. **Manca `ONDA_TERMINAL_ID`.** Lo schema evento richiede `onda.terminalId` come chiave di correlazione autoritativa, ma il PTY oggi non lo inietta (inietta pane/tab/workspace, non il terminal id). Stream A deve aggiungere `finalEnv.ONDA_TERMINAL_ID = this.id` in `terminal-manager.ts`, fuori dal ramo zsh, accanto a `ONDA_SOCKET`. **Nome nuovo, non riusare `ONDA_TERMINAL`**: ha già un significato e cambiarlo romperebbe onda-mcp.

## 2. `onda.windowId` non esiste nell'env — si deriva

Il PLAN §4 mette `windowId` nell'evento, ma il PTY non lo inietta e non può: un pane può essere trasferito fra finestre a PTY vivo (`tiledMosaicStore`, transfer atomico cross-window), quindi un `windowId` catturato allo spawn sarebbe stantio.

**Decisione**: `windowId` **non è** un campo dell'evento. Il driver lo risolve da `paneId` al momento della riduzione a stato, dove conosce la topologia corrente. Rimosso da `agent-event.schema.json`. Stream C prende la finestra dal proprio store, non dall'evento.

## 3. Modello di fiducia sul socket — nessun token

**Deciso da Mario, 10 set 2026.**

- Nessun token nell'evento. Il socket è un unix socket con permessi utente: chi può scriverci è già dentro la macchina, e un token nell'env sarebbe leggibile dallo stesso processo che dovrebbe autenticare.
- **`agent-event-manager.ts` scarta ogni evento il cui `onda.terminalId` non risolve a un terminale vivo.** È l'unico controllo, costa una lookup nella mappa dei terminali e chiude il buco che conta: iniettare stato falso in una card. Un evento scartato non è un errore: nessun log rumoroso, nessuna risposta al mittente.
- Corollario: `terminalId` è obbligatorio nello schema. Un evento senza terminalId non è correlabile e viene scartato prima di ogni altra validazione.

Asimmetria da tenere a mente: `TerminalTapManager` è in **lettura** da onda-mcp, `agent-event-manager` accetta **scritture**. Il controllo sopra è ciò che rende la seconda accettabile senza auth.

## 4. Nomi dei campi: si seguono AGENT_MAP, non il PLAN

Il PLAN §4 propone `WorkspaceAgent { display, god, persona }`. La mappa reale usa altri nomi. Verificato in `~/.agent-team-os/AGENT_MAP.json`:

| PLAN §4 | AGENT_MAP reale | Contratto |
|---|---|---|
| `display` | `display_name` | **`display_name`** |
| `god` | `is_god` | **`is_god`** |
| `persona` | `persona_file` | `persona` nel *pack*, `persona_file` nella *mappa* |
| `avatar` | `avatar_path` + `avatar_default_path` | **`avatar_path`** (catena risolta) |

Motivo: `WorkspaceAgent` è una **copia denormalizzata** di una entry della mappa. Se i nomi coincidono, la copia è un sottoinsieme e il codice di sync è una projection; se divergono, ogni stream si scrive il suo mapper e sbagliano in modo diverso.

Campi della mappa reale non presenti nel PLAN e che vanno preservati: `capabilities[]`, `accepts_intents[]`, `workspace_root`, `inline_delegation_allowed`, `task_provider`.

## 5. AGENT_MAP resta la fonte di verità

- L'hook è l'**unico scrittore** di `registry/`. Il driver legge.
- Il driver può scrivere `AGENT_MAP.json` **solo** su azione utente confermata (assegnazione workspace, merge di un pack), sempre con backup e diff. Mai al boot, mai in automatico.
- `Workspace.agent` è cache: su divergenza vince la mappa.

## 6. Privacy dell'evento

Invariante, non negoziabile: **mai contenuto di file, mai testo del prompt.**

- `tool.name` sì, input del tool no.
- `tool.target`: path **relativo** a `session.cwd`. Per `Bash` è `null` — non la stringa del comando, che è testo utente.
- `inbox.from[]`: solo id degli agenti, mai il corpo dei messaggi.
- `session.cwd` è assoluto ed è l'unico path assoluto ammesso.

## 7. Prestazioni dell'hook

- Write fire-and-forget, timeout **200 ms**, `exit 0` **sempre** — anche su socket assente, refused, o JSON malformato.
- Zero dipendenze esterne: nessun `jq`, nessun `curl`. Solo `node:net` dal node di Claude Code.
- Un hook che rallenta Claude Code è un bug che uccide la feature: il tempo dell'hook va misurato nei test dello stream A, non stimato.

---

## 8. Reperto fuori scope: bug attivo nel registry

Trovato mentre verificavo le strutture. **Non è parte dei 4 stream, ma va deciso perché lo stream A tocca esattamente questo codice.**

Stato attuale sul disco:

```
alita.json   0 byte   (azzerato 10 set 22:57)
kai.json     0 byte   (azzerato 10 set 22:59 — da questa sessione)
nico.json    0 byte   (azzerato 10 set 18:09)
vera.json    0 byte   (azzerato  7 set 16:48)
leo.json   190 byte   valido
lifeos.json 116 byte  valido
```

Quattro registry su sei sono vuoti. Causa in `scripts/agent-team-os-lib.sh:419` (`ab_update_registry`):

```bash
[[ -f "$f" ]] || return 0          # passa: il file esiste, è solo vuoto
jq ... "$f" > "$tmp" && mv "$tmp" "$f"
```

`jq` su input vuoto **esce 0 senza produrre output** → `$tmp` vuoto → `mv` promuove il vuoto. È uno **stato assorbente**: una volta vuoto, ogni SessionStart successivo lo riconferma vuoto. Riprodotto in sandbox: file valido → aggiornato correttamente; file vuoto → resta vuoto, exit 0.

Effetto sulla feature: la barra del team (PLAN §5.1) e la catena avatar leggono `registry.*` come primo anello. Con quattro registry vuoti, quattro agenti su sei risultano perennemente offline e senza avatar. Lo stream A che porta la lib a `.mjs` erediterebbe il bug se lo traduce alla lettera.

**Fix proposto** (5 minuti, dentro lo stream A dato che riscrive questa funzione):
1. Guardia sull'input: se il file è vuoto o non è JSON valido, ricostruire la entry da zero (`{name, active, last_seen, workspace_path, session_started}`) invece di darla in pasto a jq.
2. Guardia sull'output: promuovere `$tmp` **solo se** non vuoto e JSON valido — `[[ -s "$tmp" ]] && jq -e . "$tmp" >/dev/null && mv ...`. Elimina la classe di bug, non l'istanza.
3. Ricostruire ora i quattro file azzerati.

Serve il tuo ok: lo includo nello stream A o lo tengo separato?

---

## 9. Cosa NON è deciso qui

- Palette e layout della finestra Agentic: segue il mockup, non il contratto.
- Nomi dei metodi IPC lato Onda (`agent-team:*`, `agent:events:*`): interni allo stream B/A, non parte del protocollo pubblico.
- Schema DB delle tabelle `agent_packs` su onda-landing: stream D.

## 10. Checklist di congelamento

- [ ] `ONDA_TERMINAL_ID` aggiunto al PTY (stream A) — nome confermato
- [ ] `onda-mcp` passa a `ONDA_SOCKET` per `isOnda`
- [ ] `windowId` derivato, non trasmesso — confermato
- [ ] Nessun token, scarto per terminalId morto — **deciso**
- [ ] Nomi `display_name` / `is_god` allineati alla mappa — confermato
- [ ] Bug registry: dentro stream A o separato? — **da decidere**
- [ ] Versione iniziale del marker: `agent-team@1.0.0`

---

# Onda → mod (bus-band, 14 ott 2026)

> Stato: **congelato**, 14 ott 2026. Brief: `~/work-hub/plans/BRIEF-KAI-bus-band-squadra-14ott.md`.
> Produttore: Onda (`onda-electron`, stream A). Consumatore: il mod `bus-band` (`mods/bus-band`, stream B).
> Schema: `onda-sessions.schema.json`. Indipendente dal contratto "Agent Team nativo" sopra: qui il flusso è Onda → bus, non hook → Onda.

Il dato su chi è fermo lo ha solo Onda (legge il buffer del TUI). Lo scrive su file; il mod lo legge. Un mod non apre socket, quindi la strada inversa (chiedere a Onda di fare qualcosa) passa anch'essa da file.

## 11. `sessions.json` — stato delle sessioni

```
$AB_HOME/onda/sessions.json        (default ~/.agent-team-os/onda/sessions.json)
```

- Scritto da Onda in modo **atomico** (file temporaneo nella stessa cartella + `rename`), a ogni cambiamento di stato, **al massimo una volta al secondo**.
- Onda **non crea** la cartella del bus: se `$AB_HOME` (o `~/.agent-team-os`) non esiste, non scrive nulla. La cartella `onda/` la crea Onda dentro un bus esistente.
- Un lettore non deve mai assumere che il file esista, sia recente o sia valido: se manca, non parsa, o ha `v` ignoto, la sezione "squadra" semplicemente non compare.

```json
{
  "v": 1,
  "writtenAt": "2026-10-14T09:25:00Z",
  "ondaPid": 23715,
  "sessions": [
    {
      "sessionId": "…", "terminalId": "pane-…", "workspaceId": "workspace-…",
      "agent": "kai", "slug": "portineria", "cwd": "/Users/…/portineria",
      "state": "working",
      "stateSince": "2026-10-14T09:12:00Z",
      "lastTool": "Bash", "lastTarget": null,
      "mounted": true, "windowId": "window-…",
      "host": "laptop"
    }
  ]
}
```

| Campo | Tipo | Note |
|---|---|---|
| `v` | `1` | Versione maggiore dello schema. Lettore: `v` ignoto → scarta il file. |
| `writtenAt` | ISO 8601 UTC | Istante della scrittura. |
| `ondaPid` | intero | Pid del processo Onda che scrive. Il lettore controlla che sia vivo: se no, il file è un residuo di un Onda chiuso male e va ignorato. |
| `sessions[].sessionId` | string | Id di sessione Claude Code. |
| `sessions[].terminalId` | string | Id stabile del terminale (`pane-…`). |
| `sessions[].workspaceId` | string | Workspace che contiene la sessione. Serve per la richiesta `mount`. |
| `sessions[].agent` | string | Agente da `AGENT_MAP` (`kai`, `leo`, …); `null` se la cartella non è mappata. |
| `sessions[].slug` | string | Slug di sessione, stessa regola della lib (basename del cwd, minuscolo, `[a-z0-9._-]`). |
| `sessions[].cwd` | string | Path assoluto. |
| `sessions[].state` | enum | Vedi sotto. |
| `sessions[].stateSince` | ISO 8601 UTC | Da quando la sessione è in questo stato. |
| `sessions[].lastTool` | string \| null | Ultimo tool visto (`Bash`, `Edit`, …). |
| `sessions[].lastTarget` | string \| null | Bersaglio dell'ultimo tool, **relativo** al cwd, mai contenuti (stessa regola di privacy di §6). |
| `sessions[].mounted` | bool | Il workspace è nel mosaico di una finestra aperta. |
| `sessions[].windowId` | string \| null | Finestra che lo ospita; `null` se `mounted` è falso. |
| `sessions[].host` | string | Oggi sempre `laptop`. Esiste per il piano del Mac mini (`plans/PLAN-MAC-MINI-WORKER.md`). |

### Stati

| `state` | Significato |
|---|---|
| `working` | L'agente sta lavorando (segnale di lavoro a schermo, `BUSY_RE` di `submit-detection.ts`). |
| `idle` | Fermo in attesa di input, casella vuota o testo da meno di 60 s. |
| `waiting_permission` | Chiede un permesso: `Notification` del hook, oppure a schermo "Do you want to", "Allow", "trust this folder". |
| `waiting_answer` | Pone una domanda (`AskUserQuestion` a schermo o `Notification`). |
| `pending_input` | La casella del TUI ha testo (riga del prompt non vuota dopo il glifo `❯`/`>`, oppure `[Pasted text #N`) **e** nessun segnale di lavoro da almeno **60 s**. Prima dei 60 s resta `idle`. È il caso "testo scritto e mai inviato". |
| `exited` | Il processo dell'agente è terminato. |

`stateSince` per `pending_input` è l'istante in cui è **iniziato** l'episodio, non quello in cui sono scattati i 60 s: "TESTO FERMO da 12 min" si calcola da lì.

Stati "fermi" (richiedono qualcuno): `pending_input`, `waiting_permission`, `waiting_answer`.

## 12. `requests/` — richieste dal mod a Onda

```
$AB_HOME/onda/requests/req-<ts>-<rand>.json
```

Onda osserva la cartella (`fs.watch` + scansione all'avvio), consuma ogni `req-*.json` e **lo cancella** dopo averlo gestito (anche se la richiesta è invalida o il workspace non esiste: un file non consumato si accumulerebbe).

```json
{ "action": "mount", "workspaceId": "workspace-…" }
```

- Unica azione per ora: `mount`. Monta il workspace nella finestra attiva (o nella prima aperta) e mette a fuoco il pannello dell'agente.
- `<ts>` è epoch in millisecondi, `<rand>` almeno 4 caratteri `[a-z0-9]`. Il mod scrive in modo atomico (tmp + rename) con il nome finale già `req-*.json`; i file temporanei **non** devono iniziare con `req-`.
- Azioni ignote: Onda scarta e cancella.
- Lo schema delle richieste è descritto in `onda-sessions.schema.json` (`$defs/request`).

## 13. Evoluzione

Un campo nuovo e opzionale non cambia `v`. Un campo che cambia significato, o un nuovo valore di `state` che un lettore vecchio non saprebbe trattare, alza `v`. Un lettore deve trattare un `state` sconosciuto come `idle`.
