# Il Mac mini come device di Roster (RS07-F4.3)

Stato al 7 novembre 2026. Parte mini di `roster/docs/MIGRAZIONE.md` §2. Il laptop è RS07-F4.2.

## Cosa c'è sul mini

| Cosa | Dove | Note |
|---|---|---|
| CLI `roster` 0.1.0 | `~/.local/share/roster/roster.mjs`, symlink `~/.local/bin/roster` | bundle di `npm run build:cli` da `origin/develop` (f5fbbfe), copiato con scp |
| Hook | `~/.local/share/hooks/` (sorgente per `roster hook install`), installati in `~/.roster/hooks/` | `roster-identity.sh --with-presence` su SessionStart, `roster-presence.sh` sugli altri eventi |
| Device | `dv-mini`, slug `mini`, kind `mini`, workspace brandart e personale | `roster login` (device flow), token device fino al 2027-05-06 |
| Token device | `~/.roster/credentials.json` (600) | niente portachiavi: il mini non ha login grafico, `store: "file"` in `~/.roster/config.json` |
| Token agente | `~/.config/agenti/secrets.env` del mini | `ROSTER_PRODUCTION_TOKEN_{NICO,TEO,OTTO}_MINI_MINI`, mai stampati |
| Export | cron ogni 10 minuti, `~/.local/share/roster/roster-export.sh` (sorgente: `scripts/roster-export-mini.sh`) | log in `~/.agent-team-os/logs/roster-export.log` |
| Config Claude del mini | `~/.config/claude-mini/settings.json` | hook di Roster + env `ROSTER_TOKEN_CMD`; backup `settings.json.bak-20261107-rs07-f43` |

Niente LaunchAgent: il mini non ha sessione grafica (gui/501), quindi tutto gira da cron.

## Token e etichetta di configurazione

Il CLI ricava l'etichetta dalla cartella di `CLAUDE_CONFIG_DIR`: per `~/.config/claude-mini` risulta `claude-mini`, ma i token
sono stati chiesti con `--config-label mini`. Il ponte è `ROSTER_TOKEN_CMD` nelle `env` di `settings.json`:

```
/opt/homebrew/bin/node ~/.local/bin/roster token --for-cwd "$ROSTER_CWD" --config-label mini
```

Il hook di presenza usato sul mini è quello del laptop, con la riga che toglie il prefisso `claude-` dall'etichetta mostrata
(non ancora nel repo di Roster: `hooks/roster-presence.sh` di develop non la ha). Se Roster ripubblica gli hook, rimetterla.

Il comando di `config.json` (`cli`) usa `/opt/homebrew/bin/node`, non il percorso del Cellar, che sparisce con `brew cleanup`.

## Export: cosa si scrive e cosa no

Si esportano solo `personas/*.md` in `~/.agent-team-os/personas/` (4 file: alita, kai, lifeos, vera, byte per byte uguali a quelli
del mini al momento dell'attivazione). Le persona di leo, nico, nora, otto e teo il server non le esporta (nessun DPA): i file
già sul mini restano come sono. Lighthouse sul mini non legge `agenti.json`, quindi non si esporta.

**`AGENT_MAP.json` non è nel cron.** `roster export agent-map --device mini` produce un documento che differisce da quello del
mini in tre modi:

1. **toglie `agents.teo.host: "mini"`**, anche se il registro ha `home_device = mini` per teo. Il centralino usa quel campo
   (`indirizzi.py`, `resolve_host`) per mandare i messaggi a teo verso il mini: senza, vanno in coda sul laptop. Blocca l'attivazione.
2. voci di alita, leo e kai più nuove di quelle del mini (giuste), `gemini_voice` per kai.
3. campi in più in `hosts` (`mini.claude_account`, `mini.centralino`, `mini.claude_config`, `cloud`, `laptop.claude_accounts`).

Il passo RS07-F4.1b (ramo `fix/rs07-f41b-export-host` di Roster) lo sistema: `host` = slug del home_device. Il comando è già in
`roster-export.sh` ma commentato: si attiva quando il fix è in develop e il bundle del mini è aggiornato (Alita avvisa).

Da fare lato server di Roster: emettere `host` (slug del `home_device`) per gli agenti con home device. Poi si aggiunge
`export agent-map --device mini --agent-map ~/.agent-team-os/AGENT_MAP.json` a `roster-export.sh`, dopo un confronto con il file attuale.

## Verifica (DoD)

7 novembre 2026, 17:29 CET: `claude` in tmux su `~/Projects/06-Brandart/audit-plan` con `CLAUDE_CONFIG_DIR=~/.config/claude-mini`,
un prompt breve, poi `/exit`.

- `GET /api/v1/sessions?live=true` con il token di nico: `as-7625ac83fc5d`, agent `nico`, device `mini`, client `claude-code`,
  stato `waiting` (turn_end).
- Dopo `/exit`: `live=true` vuoto, `GET /api/v1/sessions/as-7625ac83fc5d` → `closed`, `end_reason: prompt_input_exit`.

Non verificato a occhio su https://rosterhq.io/team (serve il login di Mario): lo stato viene dall'API.

## Ritorno

Togliere gli hook: `roster hook install --config-dir ~/.config/claude-mini --uninstall` (o ripristinare `settings.json.bak-20261107-rs07-f43`),
togliere la riga `roster export` dal crontab. I file di personas restano e si modificano a mano.

## Da fare dopo

- `mcp_sync --host mini` per distribuire i token di Roster (`servers.yaml`, voce `roster`): oggi i token sono nel file dei segreti del mini, non nei `.mcp.json`.
- Sessioni di Teo e Otto (cron/Lighthouse): i loro token ci sono, ma i processi devono avere `CLAUDE_CONFIG_DIR=~/.config/claude-mini` perché gli hook si attivino.
