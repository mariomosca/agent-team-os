# Spegnimento del bus · inventario e sequenza

> CM02-F4, preparato da Kai mercoledì 11 novembre 2026 sera (delega di Alita t-20261111-daccij), in **sola lettura**:
> niente è stato fermato, modificato o disinstallato. Fotografia delle 22:40 circa su laptop e mini.
> Il passaggio vero e proprio (interruttore, plugin, hook, istruzioni) ha già il suo copione:
> `docs/prove/f3.6-copione.md` nel repo Crewmoor (ramo develop). Questo documento non lo ripete: elenca **tutto ciò che
> dipende dal bus**, chi lo usa, cosa lo sostituisce, cosa manca e in che ordine si spegne. La sequenza di sabato in
> fondo integra il copione con quello che lì manca.
> Nessun valore segreto qui. Per Brandart (Leo, Nico, Teo) solo l'esistenza delle dipendenze, non il contenuto.

## 0. Il bus oggi, in numeri

| Cosa | Laptop | Mini |
|---|---|---|
| Centralino | launchd `com.agent-team-os.centralino` (`uv run … agent-team-os/centralino serve`, dal checkout `main`), vivo | cron ogni minuto che lo rilancia se il pid è morto, vivo; più il **custode** alle 07:00 (`agent-team-os-custode.sh`) |
| Database | `~/.agent-team-os/centralino.db` 16 MB, log 39 MB | `~/.agent-team-os/centralino.db` 2 MB |
| Interruttore `~/.config/crewmoor/canale` | assente (= `bus`) | assente (= `bus`) |
| `~/.config/crewmoor/canale.json` (workspace Brandart, alias) | **assente** | **assente** |
| Relay Crewmoor | launchd `com.crewmoor.relay`, 0.2.1, vivo | vivo, 0.2.1, ma **due plist**: LaunchAgent (`/opt/homebrew/bin/node`) e LaunchDaemon (node del **Cellar** 26.13.0_2, è quello che gira) |

Traffico degli ultimi 7 giorni (messaggi per mittente): leo 413, alita 383, kai 351, nico 197, **teo 168** (dal mini, 167
a Leo), **remo 33** (Codex, via centralino), più i mittenti di servizio: `alita/sveglia` 21, `leo/nora` 47, `leo/otto`
14, `alita/cloud` 12, `alita/notti` 9, `alita/remarkable` 4, `alita/settimanale-auto` 2, `leo/docusign-invio` 2,
`leo/deck-redflag` 2.

Attese (`waits`) non chiuse: **80** (alita 52, leo 28), di cui **66 ferme da più di 72 ore** (alita 49, leo 17) e 14
attive. Tabella `deleghe` (DC29): **212 righe `open`**, 33 `kept`. Sessioni vive alle 22:38: 9 sul laptop (Alita, Leo,
quattro Kai, tre sessioni `onda-screenshot`); sul mini 10 sessioni tmux (Onda e Teo).

## 1. Inventario: cosa dipende dal bus

Colonne: **Sostituto** = cosa lo fa in Crewmoor (o altrove); **Manca** = cosa non è pronto oggi; **Ordine** = passo della
sequenza di sabato (§4) in cui si spegne o si cambia. «Interruttore» = già pronto dietro `~/.config/crewmoor/canale`.

### 1.1 Processi e servizi

| Cosa | Dove | Chi lo usa | Sostituto | Manca | Ordine |
|---|---|---|---|---|---|
| Centralino laptop | `~/Library/LaunchAgents/com.agent-team-os.centralino.plist` | tutte le sessioni del laptop, hook, script | servizio Crewmoor production + relay del laptop | niente per lo stop; il plist resta fino a F3.7 | S3 (bootout), plist via in F4.1 dopo F3.7 |
| Centralino mini | crontab del mini, riga `* * * * *` che rilancia `centralino serve` | sessioni del mini (Nico, Leo, Teo), Nora, Otto | relay del mini | niente | S3 (riga commentata, kill per pid) |
| Custode del mini | crontab del mini, `0 7 * * *` `agent-team-os-custode.sh` | pulizia dell'host remoto del bus | niente (non serve) | — | S3 (riga commentata insieme al centralino) |
| Raccolta mini (laptop↔mini) | dentro il centralino (`remoto.py`), log `raccolta-mini.log` | messaggi cross-macchina | relay su entrambe le macchine, instradamento dal servizio | — | si ferma col centralino |
| Sveglia del centralino | `centralino/src/centralino/sveglia.py` (mittente `alita/sveglia`) | avvisi di messaggi fermi | tabella di sveglia del servizio + avviso dopo 24 h (contratto §attese) | da vedere nel giro di controllo | si ferma col centralino |
| Relay Crewmoor mini | `~/Library/LaunchAgents/com.crewmoor.relay.plist` **e** `/Library/LaunchDaemons/com.crewmoor.relay.plist` | sostituto del centralino sul mini | — | un solo plist, node dal PATH (come F3.6b sul laptop) | **prima di sabato** |

### 1.2 MCP

| Cosa | Dove | Chi lo usa | Sostituto | Manca | Ordine |
|---|---|---|---|---|---|
| Server `agent-bus` (base) | `mcp-config/servers.yaml` → generato in `~/.claude.json`, `~/.claude-a/.claude.json`, `~/.config/claude-lighthouse/.claude.json` (laptop); `~/.config/claude-mini/.claude.json` (mini) | tutti gli agenti (`bus_*`) | plugin Crewmoor (`crewmoor_send/reply/close/inbox/read/status`) | ramo `feat/cm02-f36-agent-bus-archiviato` (b56ea24) pronto in `~/Projects/07-Tooling/mcp-config-wt-cm02`, **non pushato**, già sopra `main` (c8f50d3) | S8 |
| Repo `agent-bus-mcp` (pubblico, npm) | `~/Projects/07-Tooling/mcp/agent-bus-mcp`, GitHub pubblico | — dopo S8 nessuno | — | deprecazione npm e archivio GitHub | F4.2, dopo F3.7 |

### 1.3 Hook di Claude Code

Laptop: `~/.claude/settings.json` (`~/.claude-a/settings.json` è un collegamento allo stesso file). Mini:
`~/.config/claude-mini/settings.json`. `hook-passaggio.py applica` (prova a secco rifatta stasera) toglie i 5 del bus e
mette i 4 delle deleghe.

| Hook | Evento | Chi lo usa | Sostituto | Manca | Ordine |
|---|---|---|---|---|---|
| `agent-team-os-load.sh` → `scripts/agent-team-os-centralino.py start` | SessionStart | identità, inbox, battito | `roster-identity.sh --with-presence` (già attivo, F3.3b) + blocco Crewmoor del plugin | — | S7 |
| `agent-team-os-urgent.sh` | UserPromptSubmit | urgenti a metà turno | plugin Crewmoor (`urgent` arriva tra due azioni) | — | S7 |
| `agent-team-os-stop.sh` | Stop | blocco finché i messaggi azionabili non sono gestiti | Stop del plugin (`blockStop`, filtro `actionable`, tetto 3) | da provare nel giro | S7 |
| `agent-team-os-end.sh` | SessionEnd | fine sessione nel centralino | plugin + `roster-presence.sh end` | — | S7 |
| `agent-team-os-onda-prompt.py` | PreToolUse `onda_agent_prompt` | marcatori `[delega]` / `[comunicazione]` che aprono un'attesa | **nessuno** (vedi §2) | sì | S7 |
| `agent-team-os-onda-spawn.py pre/post` | Pre/PostToolUse `onda_agent_spawn` | legame spawn↔delega DC29 | stesso hook, con l'interruttore passa a `deleghe-crewmoor.py` | **PR agent-team-os #12 non unita**: senza, `python -m centralino.deleghe_crewmoor` non esiste in `main` | resta; PR prima di sabato |
| `deleghe-crewmoor.py` (delega, chiusa, start, fine) | PostToolUse `crewmoor_send`/`crewmoor_close`, SessionStart, SessionEnd | deleghe DC29 senza centralino | — | gira con `uv run --project agent-team-os/centralino`: **dipende dal repo che F4.2 vuole archiviare** | messo in S7; spostarlo prima di F4.2 |
| `agent-team-os-mobile.py` | 4 eventi | modalità mobile (MM31) | — non dipende dal bus, ha solo il nome | — | resta |
| `onda-agent-event.mjs`, `voice-notify/*` | vari | Onda, voce | leggono `~/.agent-team-os/AGENT_MAP.json`, non il centralino | — | restano |
| Mini: stessi 5 hook del bus | `~/.config/claude-mini/settings.json` | sessioni del mini | plugin sul mini | **`sync-mini.sh` (launchd `dev.mariomosca.sync-mini`, ogni 15 min) ri-aggiunge lo Stop del bus** via `scripts/sync-mini-settings.py` riga 20 | togliere dal sync **prima** di S7 |

### 1.4 Comandi, skill, istruzioni, memorie

| Cosa | Dove | Sostituto | Manca | Ordine |
|---|---|---|---|---|
| Comandi `/bus /inbox /read /send /reply /handoff /thread /delegate` | `~/.claude/commands/*.md` | riscritti in `~/work-hub/plans/crewmoor-passaggio/istruzioni/home/` | — | S9 |
| Altri comandi che citano il bus | `/mvd`, `/rientro`, `/mobile`, `/speak` | diff nel manifesto per `mvd` e `rientro`; `mobile` e `speak` comparsi dopo il 3/11 | classificare | S9 |
| Skill | `agent-team-os`, `notti-sviluppo`, `onda-mcp-usage`, `claude-effort-tracker`, `brandart-pm-obsidian`, `remote-dev-mini`, `tts-cli` | in manifesto (tranne `tts-cli`, nuova) | classificare `tts-cli` | S9 |
| Moduli globali | `~/.claude/CLAUDE/agent-team-os.md`, `agent-team-economics.md`, `development-team-mode.md`, `mcp-guidelines.md`, `github.md`, `voice-mode.md`, root `CLAUDE.md` | in manifesto (`github.md` e `voice-mode.md` da classificare: probabilmente racconto/falso positivo) | — | S9 |
| Persona file | `~/.agent-team-os/personas/{kai,vera,teo}.md` | diff in manifesto | — | S9 |
| CLAUDE.md dei workspace agente e memorie | `work-hub`, `00-Growth-OS`, `00-Life-OS`, memorie dei due account; **Brandart**: `00-Brandart-PM-OS` (memoria di Leo, 13 file con riferimenti) e progetti `06-Brandart` | `applica.sh --gruppi globale,personale,life,brandart`: **83 voci, errori 0** (prova di stasera) | la scansione di stasera trova 201 file con riferimenti; ~90 non sono nell'INDICE: quasi tutti racconti (`~/.alita-memoria/analisi`, `piani/*.md`, `project_*`), alcuni `feedback_*` di Alita e di Leo con «How to apply» da guardare | S9; scansione da rifare venerdì |

### 1.5 Script, timer e servizi che scrivono o leggono il bus

| Cosa | Dove / quando | Chi lo usa | Sostituto | Manca | Ordine |
|---|---|---|---|---|---|
| Rito delle notti | launchd `dev.mariomosca.notti.sera` 21:15, `.mattina` 06:00 → `work-hub/tools/notti/rito.sh` → `bus-manda.sh` | Alita, Leo | interruttore (`crewmoor send` da `notti`) | verificare che `brief urgent notice` svegli Alita (riga `urgent`) | S5 |
| Sweep dei piani | launchd `dev.mariomosca.sweep-piani` lun 07:35 → `bus-manda.sh` | Alita, Leo | interruttore (`sweep-piani`) | — | S5 |
| Taccuini reMarkable | launchd `dev.mariomosca.taccuini` 23:00 → `taccuini.py` `avvisa_bus` | Alita | interruttore (`taccuini`) | — | S5 |
| **Settimanale, chiusura** | launchd `dev.mariomosca.settimanale.chiusura` **sabato 22:30** → `redazione/strumenti/chiusura.sh` | Alita (`alita/settimanale-auto`) | **nessuno**: chiama `centralino.client.call("send")` direttamente con `uv run --project agent-team-os/centralino`, **fuori dall'interruttore** | portarlo su `bus-manda.sh` / `canale-agenti.py manda` | **prima di sabato** (gira la sera stessa del passaggio) |
| Settimanale, taccuino | launchd `dev.mariomosca.settimanale.taccuino` 22:45 → `taccuino.py` | conta le deleghe | legge `centralino.db` in sola lettura: dopo lo stop resta fermo a sabato | leggere da Crewmoor (passo da aprire) | non blocca |
| alita-memoria-sync | launchd `dev.mariomosca.alita-memoria-sync` ogni 30 s + hook SessionStart | alita-cloud | interruttore (`alita-cloud`); `stato_messaggi_centralino` legge `centralino.db` | cloud fuori (Mario 8/11) | S5; la lettura del db diventa inerte |
| Nora, Otto (Brandart) | `00-Brandart-PM-OS/{segretaria,contratti}/run.sh`, laptop e mini (cron mini 07:45, LaunchAgents `com.lighthouse.otto.*` sul mini, `com.brandart.otto-sync` sul laptop) | Leo | interruttore (eventi a `@leo` da `nora`/`otto`) | workspace Brandart in `canale.json` (copione passo 1) | S5 |
| **Teo** (Brandart, mini) | sessione tmux `ops-t-teo-*` sul mini, 167 messaggi a Leo in 7 giorni | Leo | **non nel copione** | percorso di Teo su Crewmoor da confermare con Leo/Nico | **prima di sabato** |
| `leo/docusign-invio`, `leo/deck-redflag` | mittenti di servizio Brandart (2+2 messaggi in 7 giorni) | Leo | non nel copione | Leo/Nico confermano chi li manda e come | prima di sabato |
| **Remo** (Codex) | `remo/02-experiments`, `remo/barra-calendario`, 33 messaggi in 7 giorni via centralino | Alita, Leo | adattatore Codex nel relay (CM02-F3.15, in corso) | se F3.15 non è in production sabato, Remo resta senza canale | decisione prima di sabato |
| voice-notify | `~/.claude/hooks/voice-notify/` | Onda | inerte dal 10/11; legge `AGENT_MAP.json` | — | resta |
| `sync-mini.sh` | launchd `dev.mariomosca.sync-mini` ogni 15 min | config del mini | — | rimette lo Stop del bus (vedi §1.3) | prima di S7 |

### 1.6 Superfici e altri servizi

| Cosa | Dove | Dipendenza | Sostituto | Manca | Ordine |
|---|---|---|---|---|---|
| Mod `bus-band` di Onda | `~/.claude/mods/bus-band/hooks/register.tsx` | legge il centralino (`centralino.sock`) | interruttore: tace | rimozione del mod | S5; via in F4.2 |
| Onda (app) | `onda-electron/src`: `AGENT_MAP.json`, `avatars`, `registry`, `threads`, `onda/sessions.json` sotto `~/.agent-team-os` | legge **la cartella** `~/.agent-team-os`, non il centralino | Roster nel tempo | — | **la cartella `~/.agent-team-os` non si cancella**: si archiviano solo `centralino.*` |
| Roster | `~/.roster/cache/agent-map.json`, `roster-export` verso `AGENT_MAP.json` | nessuna sul centralino | — | — | resta |
| Orbit | tabella `step_delegations` (`bus_task_id`), vista Deleghe, agente di servizio `centralino` nel web | il centralino scriveva `delega_set` su 127.0.0.1:7331, **rotto dall'8/11** | `deleghe-crewmoor.py` su Orbit online (PR #12); costi e indirizzi per passo da OA06 | PR #12 | dopo S7 tornano spawn e chiusure |
| Lighthouse | `07-Tooling/apps/lighthouse`: solo `voice-orb.ts` cita `.agent-team-os` (voci) | nessuna sul centralino; la config `~/.config/claude-lighthouse/.claude.json` ha `agent-bus` | tolto da S8 | — | S8 |
| alita-cloud | `01-Building/alita-mobile/cloud` | casella e centralino via `alita-memoria-sync` | interruttore | fuori per decisione di Mario | — |
| Repo agent-team-os | checkout `main` (il daemon gira da qui), 3 worktree vivi, PR aperte #1, #11, #12 | il codice del centralino e di `deleghe_crewmoor` | — | `deleghe_crewmoor` va spostato fuori prima dell'archivio | F4.2 dopo F3.7 |
| Sito Pages agent-team-os | GitHub Pages (`docs/index.html`) | pubblico | — | avviso di deprecazione | F4.2 |

## 2. Cosa fa oggi il bus e Crewmoor non sa ancora fare

Verificato su `origin/develop` di Crewmoor (77885bf, contratto v10, `packages/protocol/src/agent-tools.ts`).

1. **Messaggio libero da agente a persona.** `crewmoor_send` accetta come `to` solo agenti. Verso una persona un agente
   ha `ask` (domanda **con opzioni**), la risposta nel thread in cui la persona ha scritto, o un messaggio in un canale.
   Manca «Alita scrive a Mario un messaggio diretto senza domanda».
2. **Marcatori `[delega]` nei prompt di Onda.** L'hook PreToolUse su `onda_agent_prompt` apre un'attesa quando un
   orchestratore scrive un compito direttamente nella TUI di un'altra sessione. Con Crewmoor non c'è un equivalente:
   chi lancia via Onda deve mandare anche un `crewmoor_send` con `mode: delegation`, altrimenti il ritorno non sveglia.
3. **cc automatico ad Alita sui messaggi fra domini.** Il bus lo fa da sé (`cc-notify`, Nadia esclusa). Crewmoor ha il
   `cc` esplicito e il **coordinatore**, che vede thread e deleghe senza copie in casella e senza sveglia. Due punti
   aperti: (a) per vedere Brandart Alita deve essere coordinatore con permesso di lettura sul workspace Brandart; (b) la
   mappa dell'importatore mette `lifeos` nel Personale: Alita coordinatore del Personale vedrebbe i thread di Nadia,
   che oggi è esclusa. Va deciso (workspace Life separato, o eccezione).
4. **Sveglie dei timer verso una sessione non viva.** Il bus consegna alla sessione di casa o alla casella condivisa e
   la sessione dopo la trova. In Crewmoor una delega a una sessione morta da più di 10 minuti torna come
   `session_closed`; per `brief notice` ed `event` da script a un agente senza sessione viva va provato cosa succede
   (resta in Inbox per la prossima sessione?). Gli `event` non svegliano (riga `other`), come nel bus.
5. **Stop che blocca.** Esiste (`actionable`, tetto 3) ma solo dove il plugin è installato; le sessioni headless
   (`claude -p` di Nora, Otto, giri della redazione) ereditano lo Stop dalla config in cui il plugin finisce: serve il
   modo di spegnerlo lì (copione: plugin in `claude-lighthouse` solo se i giri devono vedere gli strumenti).
6. **Storico.** I 30 giorni del bus non entrano nei workspace di production (l'importatore crea solo workspace nuovi):
   decisione aperta del copione.
7. **Agenti non Claude.** Remo (Codex) parla oggi al centralino; l'adattatore Codex del relay è F3.15, in corso.
8. **Teo.** Il canale di Teo verso Leo (il mittente più attivo dopo i tre agenti principali) non è nel copione.
9. **Conteggi e cruscotti** che leggono `centralino.db`: settimanale (`taccuino.py`), stato messaggi di alita-cloud,
   costi per passo in Orbit (OA06). Dopo lo stop restano fermi a sabato.

## 3. Cosa va fatto prima di sabato

Proprietario suggerito tra parentesi.

1. **Unire agent-team-os PR #12** e `git pull` nel checkout `main` (Alita): senza, gli hook delle deleghe con
   l'interruttore su `crewmoor` chiamano un modulo che non c'è. Valutare anche #11 (chiusure al resume).
2. **`chiusura.sh` del settimanale** sull'interruttore (Kai): oggi chiama il centralino direttamente e gira **sabato
   alle 22:30**. Sostituire il blocco `centralino.client.call("send")` con `bus-manda.sh` (stessa firma).
3. **`sync-mini-settings.py`** (Kai): togliere `agent-team-os-stop.sh` dalla lista degli hook che aggiunge al mini,
   altrimenti dopo S7 il sync lo rimette entro 15 minuti. In alternativa: sospendere `dev.mariomosca.sync-mini` per la
   finestra e correggerlo dopo.
4. **Relay del mini** (Kai): un solo plist (oggi LaunchAgent e LaunchDaemon) e node dal PATH, non dal Cellar, come
   F3.6b sul laptop.
5. **Teo, `docusign-invio`, `deck-redflag`** (Leo e Nico): dire come mandano oggi (MCP `agent-bus` o centralino diretto) e
   portarli su `crewmoor send` o sull'interruttore; senza, sabato tacciono verso Leo.
6. **Remo** (Alita): F3.15 in production prima di sabato, oppure Remo resta fuori e lo si dice a Mario.
7. **`canale.json`** su laptop e mini con il workspace Brandart (copione passo 1, serve l'id che crea Mario).
8. **Attese ferme**: 66 oltre le 72 ore (alita 49, leo 17) e 212 righe `deleghe` `open` da chiudere nel bus (Alita e
   Leo), così l'elenco di sabato è corto.
9. **Decisioni** (Mario e Alita): coordinatore e Nadia (§2.3), messaggio libero agente→persona (§2.1), storico (§2.6).
10. **Venerdì**: `scansione.sh --riepilogo` e `applica.sh` in prova (deve restare «errori: 0»), classificare i file nuovi
    (`tts-cli`, `/mobile`, `/speak`, `CLAUDE/github.md`, `CLAUDE/voice-mode.md`, `feedback_*` di Alita e Leo).
11. **mcp-config**: push del ramo `feat/cm02-f36-agent-bus-archiviato` (oggi solo locale nel worktree).

## 4. Sequenza di sabato

Numerazione S1-S14. I passi coincidono con il copione F3.6 dove indicato (C0…C13); quelli in più sono marcati **nuovo**.
**[Mario]** = serve Mario di persona. Ogni passo ha il suo ritorno; il ritorno completo è in fondo.

| # | Passo | Verifica | Ritorno del passo |
|---|---|---|---|
| S1 | (C0) **[Mario]** Via libera, sessioni ferme, `ssh mini true`, `crewmoor relay status` su laptop e mini | relay collegati | — |
| S2 | **nuovo** Fermare i timer che scrivono durante la finestra: `launchctl bootout` di `dev.mariomosca.sync-mini` e `dev.mariomosca.alita-memoria-sync` (i plist restano) | `launchctl list` senza le due voci | `launchctl bootstrap gui/$UID <plist>` |
| S3 | (C2) Centralino fermo, database intatto: laptop `launchctl bootout gui/$UID/com.agent-team-os.centralino`; mini: commentare **le due** righe di crontab (centralino e custode), `kill $(cat ~/.agent-team-os/centralino.pid)`, mai `pkill` | `bus_status` → centralino non raggiungibile, su tutte e due | `launchctl bootstrap …centralino.plist`; righe del crontab di nuovo attive |
| S4 | (C3) Copia coerente: `sqlite3 ~/.agent-team-os/centralino.db ".backup '<archivio>/centralino.db'"` su laptop **e** mini, più `personas`, `avatars`, `threads`, `hosts`, `logs`, in `~/Archivio/bus-2026-11-14/{laptop,mini}` | `select count(*) from waits` sulla copia | — (sola lettura) |
| S5 | (C5) Interruttore: `echo crewmoor > ~/.config/crewmoor/canale` su laptop e mini | `canale-agenti.py canale` → `crewmoor` | `echo bus > …/canale` |
| S6 | (C6) Plugin nelle config vere (laptop `~/.claude`, `~/.claude-a`; mini `~/.config/claude-mini`; `claude-lighthouse` solo se deciso) | `claude plugin list` per config | `crewmoor plugin uninstall …` |
| S7 | (C7) Hook: `hook-passaggio.py applica --esegui` sul laptop (5 tolti, 4 messi) e sul mini con `--settings ~/.config/claude-mini/settings.json` | prova a secco di stasera coerente | `hook-passaggio.py ritorno --esegui` |
| S8 | (C8) MCP: unire il ramo di mcp-config, `mcp_sync.py --apply`, `--verify`; mini con `--check --host mini` | `claude mcp list` senza `agent-bus` in `~/.claude.json`, `~/.claude-a/.claude.json`, `claude-lighthouse`, `claude-mini` | revert di b56ea24, `--apply` |
| S9 | (C9) Istruzioni: `applica.sh --gruppi globale,personale,life,brandart --esegui` | «errori: 0», backup stampato | `ritorno.sh <backup> --esegui` |
| S10 | **nuovo** Timer di S2 di nuovo accesi (con `sync-mini` già corretto, vedi §3.3) | un giro di `sync-mini` non rimette lo Stop del bus sul mini | — |
| S11 | (C10) **[Mario]** Riavvio delle sessioni principali (Alita, Leo, Kai; Nico, Vera, Teo se aperte) | blocco Crewmoor e identità Roster all'avvio | — |
| S12 | (C11) Giro di controllo: delega laptop→mini con ritorno, domanda, riarma; evento da script (`bus-manda.sh … event`); **nuovo**: un `brief urgent notice` come quello del rito delle notti verso `@alita/work-hub`, e un evento verso un agente senza sessione viva (§2.4) | ritorno entro 10 s; `~/.config/crewmoor/deleghe.log` | ritorno completo |
| S13 | (C12) **[Mario]** Vista dal telefono | sessioni di laptop e mini, delega chiusa | — |
| S14 | (C13) Rapporto in `docs/prove/f3.6.md` di Crewmoor, Orbit F3.6 done, partono le due settimane di F3.7 | — | — |

Durata: quella del copione (circa 75 minuti, 30 con Mario) più 5 minuti per S2 e S10.

**Cosa non si fa sabato** (resta per F4.1 e F4.2, dopo la decisione di F3.7 di non tornare indietro): togliere il plist
del centralino e le righe del crontab, archiviare gli hook `agent-team-os-*`, il mod `bus-band`, `agent-bus-mcp` (npm
e GitHub), il repo agent-team-os in `05-Archive` con MANIFEST (dopo aver spostato `deleghe_crewmoor` fuori dal
progetto del centralino), il sito Pages. La cartella `~/.agent-team-os` resta: Onda, Roster e voice-notify ne leggono
`AGENT_MAP.json`, `avatars`, `registry`, `onda/`.

### Ritorno completo

Ordine inverso, circa 20 minuti, come il copione (R1-R8), più: R0 timer di S2 fermi durante il ritorno; dopo R2, sul
mini anche la riga del custode di nuovo attiva. Gli script tornano al centralino da soli con l'interruttore su `bus`,
**tranne** quelli corretti in §3 (che usano già l'interruttore: nessun problema) e il settimanale, che dopo la
correzione passa anch'esso dall'interruttore.

## 5. Come rifare questo inventario

Tutto in sola lettura:

- `launchctl list | grep -iE 'agent-team|notti|sweep|crewmoor|sync-mini|taccuin|settiman'`
- `python3 ~/work-hub/plans/crewmoor-passaggio/canale/hook-passaggio.py applica` (prova)
- `~/work-hub/plans/crewmoor-passaggio/scansione.sh --riepilogo` (non va committato: contiene testo dei file di Mario)
- `~/work-hub/plans/crewmoor-passaggio/istruzioni/applica.sh --gruppi globale,personale,life,brandart` (prova)
- `sqlite3 "file:$HOME/.agent-team-os/centralino.db?mode=ro" "select state,count(*) from waits group by 1"`
- sul mini: `crontab -l`, `pgrep -lf 'centralino serve|crewmoor'`, `tmux ls`, hook in `~/.config/claude-mini/settings.json`
