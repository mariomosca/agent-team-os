# Il laptop come device di Roster: motori (RS07-F4.2)

> **Ora si fa con `roster setup` (RS07-F4.2b, 7/11/2026).** Pacchetto `@mindfullabai/roster` (`npm install -g` dal `.tgz` della
> release di Roster, bin in `/opt/homebrew/bin/roster`), poi `roster setup` e `roster doctor`: trova `~/.claude`, `~/.claude-a` e
> `~/.config/claude-lighthouse`, conia i token che mancano, installa gli hook e lascia la voce MCP a `mcp_sync`. **Dal F4.10 (7/11 sera)
> niente export programmato né `AGENT_MAP.json`**: l'agent-map sta in `~/.roster/cache/agent-map.json` (vedi `ROSTER-EXPORT.md`). Sul laptop ha detto «già a posto»
> su tutto tranne il LaunchAgent (plist e script rigenerati). `~/.local/bin/roster` è un symlink al nuovo bin. La descrizione
> sotto resta come storia.

Stato al 7 novembre 2026. Parte laptop di `roster/docs/MIGRAZIONE.md` §2. Il mini è RS07-F4.3 (`ROSTER-MINI.md`),
l'export dei file generati è RS07-F4.1 (`ROSTER-EXPORT.md`).

## Cosa è cambiato all'avvio di una sessione

Prima: l'identità («You are Kai (kai@laptop/slug). Role: …») e la persona le stampava il centralino
(`agent-team-os-load.sh` → `agent-team-os-centralino.py start`), leggendo `AGENT_MAP.json` e `personas/`.

Ora, nelle tre configurazioni Claude Code del laptop (`~/.claude`, `~/.claude-a`, `~/.config/claude-lighthouse`, che
condividono lo stesso `settings.json` via symlink):

| Chi | Cosa stampa |
|---|---|
| `~/.roster/hooks/roster-identity.sh --with-presence` (SessionStart, nuovo) | `=== Roster ===`, `You are Kai (kai@laptop). Role: … · workspace personale · Roster as-…`, la persona dalla cache del CLI (`~/.roster/cache/personas/<handle>.md`), e manda lo `start` di presenza |
| `agent-team-os-load.sh` (centralino, come prima) | `=== Agent Team OS ===`, `Bus: this session is kai@laptop/<slug>. Workspace: …`, casella, deleghe aperte, tool del bus. **Niente più «You are…» e niente persona** |

La riga di Roster ha la forma che Onda riconosce (`session-scan.ts`: `You are Kai (kai@laptop)`); quella vecchia del
centralino, con `/slug`, non la riconosceva.

Il centralino decide da solo: l'hook `start` (`hooks/agent-team-os-centralino.py`) manda `caller.identita = "roster"`
quando `roster-identity.sh` è registrato nello SessionStart della configurazione (`$CLAUDE_CONFIG_DIR/settings.json`) **e**
la cache di Roster c'è (`~/.roster/cache/agents.json`). Altrimenti tutto come prima. Il bus non cambia: protocollo,
registro delle sessioni, sveglia, Stop, bus-band e `bus_status` sono gli stessi.

Una cartella che Roster non conosce (nessun agente con quella cartella) non riceve la riga «You are» da nessuno dei due:
c'è solo l'indirizzo sul bus (che resta l'identità del centralino, `alita` di ripiego). Vale la regola di `CLAUDE.md`.

## Presenza

`roster-presence.sh` su UserPromptSubmit (`prompt`), PostToolUse (`beat`, al più ogni 120 s), Notification
(`waiting`: permesso o inattività), Stop (`stop`, con `usage`), SessionEnd (`end`). Tutto in background tranne lo
`start`. Timeout 5 s in `settings.json` come ROSTER.md §9.1: lo script si ferma da solo a 1,5 s sullo `start`, quindi il
timeout non incide (con 2 s il caso «server irraggiungibile» arrivava a 1,75 s, troppo vicino).

Token: `cli` di `~/.roster/config.json` (`/opt/homebrew/bin/node ~/.local/bin/roster`, non il Cellar) con
`token --for-cwd`: agente dalla cartella (cache del CLI), etichetta da `CLAUDE_CONFIG_DIR` (`it`, `com`, `lighthouse`),
token da `secrets.env`. Non serve `ROSTER_TOKEN_CMD` in `settings.json`: è la strada di ripiego dello script e fa la stessa
cosa (il mini la usa solo per forzare l'etichetta, che con la CLI di develop 9c5c4ca non serve più).

Le sessioni già aperte non sono state riavviate. Claude Code rilegge gli hook da `settings.json`, quindi dagli eventi
successivi anche loro mandano `prompt`/`beat`/`stop`: compaiono su Roster senza lo `start` (senza `label`), e chiuse alla
loro fine. La riga di identità di Roster la vedono solo le sessioni nate dopo.

## Token agente (laptop)

In `~/.config/agenti/secrets.env` (mai stampati) e nel portachiavi, scadenza 2027-05-06:

| Configurazione | Agenti | Variabile |
|---|---|---|
| `.it` (`~/.claude`) | alita, kai, vera, nadia, leo, nico | `ROSTER_PRODUCTION_TOKEN_<AGENTE>_LAPTOP_IT` |
| `.com` (`~/.claude-a`) | alita, kai, vera, nadia, leo, nico | `ROSTER_PRODUCTION_TOKEN_<AGENTE>_LAPTOP_COM` |
| Lighthouse (`~/.config/claude-lighthouse`) | nadia, elio, nora, otto | `ROSTER_PRODUCTION_TOKEN_<AGENTE>_LAPTOP_LIGHTHOUSE` |

Più gli alias `ROSTER_PRODUCTION_TOKEN_LIFEOS_LAPTOP_{IT,COM,LIGHTHOUSE}` (riferimenti a quelli di Nadia: per
`mcp_sync` e l'AGENT_MAP Nadia è `lifeos`). Coniati con `roster token mint <agente> --config-label <it|com|lighthouse> --secrets`.

**Limite noto**: nel registro Nora, Otto ed Elio non hanno cartelle. Nora (`00-Brandart-PM-OS/segretaria`) e Otto
(`…/contratti`) risolvono quindi a Leo, che non ha un token Lighthouse: i loro giri non mandano presenza e l'MCP `roster`
non si collega lì (fail-open, nessun errore nel giro). Si sistema dando le cartelle a Nora e Otto nel portale, con RS07-F4.7.

## MCP `roster`

Voce `roster` in `mcp-config/servers.yaml` (agenti alita, kai, leo, nico, vera, nadia, per workspace come Orbit), URL
`https://rosterhq.io/mcp`, header `Bearer ${ROSTER_PRODUCTION_TOKEN_{AGENT}_{DEVICE}_{CONFIG}}` risolto da
`mcp-headers.sh roster` al collegamento: `{AGENT}` dall'AGENT_MAP, `{DEVICE}` da `~/.roster/config.json`, `{CONFIG}` da
`CLAUDE_CONFIG_DIR`. Il fallback statico nei `.mcp.json` usa `LAPTOP` e `IT`.

## Tempi misurati (7/11, rosterhq.io su Railway)

- per turno: `prompt` 10 ms, `stop` 20 ms (solo il fork in background);
- una volta per sessione: `roster-identity.sh --with-presence` 360-440 ms (Node per `token --for-cwd` più il giro di rete);
- server irraggiungibile: identità dalla cache, nessuno `start`, uscita 0; porta chiusa 0,25 s, indirizzo che non risponde
  1,75 s.

## Verifica (DoD)

7 novembre 2026, 17:35 CET: sessione nuova `claude --model haiku` aperta da Onda in background in
`~/Projects/02-Experiments/roster-prova-f42`, un prompt, poi chiusa.

- Contesto di avvio: `You are Kai (kai@laptop). Role: … · workspace personale · Roster as-e9f6400695f9` e la persona una
  volta sola (dal registro).
- `GET /api/v1/sessions?live=true`: `as-e9f6400695f9`, `kai@laptop`, `config_label: it`, `label: roster-prova-f42`,
  `client: claude-code`, `model: claude-haiku-5-5`, stato `waiting` dopo il turno.
- Alla chiusura: `closed`, `end_reason: other`, `usage` con costo, token e contesto.
- «Lavora su» (`work`) resta vuoto: lo scrive Orbit allo `step_claim` (MIGRAZIONE §5, RS07-F4.6), non gli hook.
- `mcp_sync.py --verify`: `roster` collegato in tutti i workspace, su `.it`, `.com` e `lighthouse`.

## Ritorno

1. `roster hook install --config-dir ~/.claude --uninstall` (il file è uno solo per le tre configurazioni), oppure
   ripristinare `~/.agent-team-os/backups/settings.json.20261107-172344`.
2. Il centralino riprende a stampare identità e persona alla sessione successiva, senza riavvii: la decisione la prende
   l'hook guardando `settings.json`. Per una sola sessione basta `ROSTER_DISABLE=1`.
3. MCP: togliere la voce `roster` da `servers.yaml` e `mcp_sync.py --apply`.
4. I token restano validi fino alla revoca dal portale.

Backup di questo passo in `~/.agent-team-os/backups/`: `secrets.env.20261107-171958`, `settings.json.20261107-172344`,
`settings.json.20261107-*-mcpsync`, `agent-team-os-centralino.py.{.claude,.claude-a}.*`, `roster-cli-20261107-172116/`.
