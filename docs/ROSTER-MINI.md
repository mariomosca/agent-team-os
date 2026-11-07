# Il Mac mini come device di Roster (RS07-F4.3)

> **Ora si fa con `roster setup` (RS07-F4.2b, 7/11/2026).** Pacchetto `@mindfullabai/roster` (`npm install -g` dal `.tgz` della
> release di Roster), poi `roster setup --config-dir ~/.config/claude-mini` (la scelta resta in `config.json`) e `roster doctor`:
> stessi passi del laptop (device, token, hook, voce MCP con `headersHelper`). Il CLI sta in `~/.npm-global/bin/roster`
> (`~/.local/bin/roster` è un symlink). **Dal F4.10 (7/11 sera) il cron di export non c'è più**: l'agent-map (con `"host": "mini"` per Teo, che `doctor` controlla) sta in
> `~/.roster/cache/agent-map.json`, rinfrescata dall'hook di sessione; `scripts/roster-export-mini.sh` è stato tolto da questo repo. Il bundle a mano e gli script vecchi sono in `~/.roster/backups/old-bundle/`. La voce MCP è ora
> `roster mcp-headers` (stessa regola della cwd del processo padre del fix di `mcp-config`). La descrizione sotto resta come storia.

Stato al 7 novembre 2026 (F4.3 e F4.3b). Parte mini di `roster/docs/MIGRAZIONE.md` §2. Il laptop è RS07-F4.2.

## Cosa c'è sul mini

| Cosa | Dove | Note |
|---|---|---|
| CLI `roster` 0.1.0 | `~/.local/share/roster/roster.mjs`, symlink `~/.local/bin/roster` | stesso bundle del laptop (SHA-256 `f820e6ff206e…`, da develop 9c5c4ca), copiato con scp il 7/11 (F4.3b); il precedente è `roster.mjs.bak-20261107-f43b`. Dopo ogni ricompilazione sul laptop: scp e confronto dello SHA |
| Hook | `~/.local/share/hooks/` (sorgente per `roster hook install`), installati in `~/.roster/hooks/` | `roster-identity.sh --with-presence` su SessionStart, `roster-presence.sh` sugli altri eventi |
| Device | `dv-mini`, slug `mini`, kind `mini`, workspace brandart e personale | `roster login` (device flow), token device fino al 2027-05-06 |
| Token device | `~/.roster/credentials.json` (600) | niente portachiavi: il mini non ha login grafico, `store: "file"` in `~/.roster/config.json` |
| Token agente | `~/.config/agenti/secrets.env` del mini | `ROSTER_PRODUCTION_TOKEN_{NICO,TEO,OTTO}_MINI_MINI`, mai stampati |
| Agent-map | `~/.roster/cache/agent-map.json` (dal F4.10, niente cron) | rinfrescata da `roster cache refresh` (hook di sessione); la leggono centralino, custode, `mcp-secrets.py` |
| MCP `roster` | `~/.config/claude-mini/.claude.json` (a mano) + `scripts/mcp-secrets.map.json` | `headersHelper` con `{AGENT}_{DEVICE}_{CONFIG}` = `NICO/TEO_MINI_MINI`, vedi sotto |
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

**`AGENT_MAP.json` è nel cron dal 7/11 (F4.3b).** Con il fix F4.1b (Roster 1.0.3, `host` = slug del home_device) l'export ha `teo.host = "mini"`,
che il centralino usa per instradare i messaggi a Teo sul mini. Ogni giro esporta in una cartella temporanea e sostituisce il file solo se
l'export è riuscito e contiene `"host": "mini"`; la prima volta del giorno salva il precedente come `AGENT_MAP.json.bak-AAAAMMGG`.
Il diff del primo giro rispetto al file del 3/11 era solo voci vocali più nuove (alita, kai, leo), `gemini_voice` di kai e campi in più in `hosts`.

## MCP `roster` sul mini

`mcp_sync.py --host mini` non è adatto: sul mini rifiuta le voci con `${VAR}` e la config globale è tenuta a mano (e il suo dry-run
toglierebbe `agents-crew` dal `.mcp.json` di Nico, che non è in `hosts:`: non applicarlo senza prima sistemare quella voce). Quindi, come per
`orbit` e `github-brandart` sul mini:

- voce `roster` (http `https://rosterhq.io/mcp`, `headersHelper: ~/.config/claude-mini/scripts/mcp-headers.sh roster`) in `~/.config/claude-mini/.claude.json`;
- voce `roster` in `~/.config/claude-mini/scripts/mcp-secrets.map.json`: `Bearer ${ROSTER_PRODUCTION_TOKEN_{AGENT}_{DEVICE}_{CONFIG}}`;
- `mcp-secrets.py` sostituito con quello di `mcp-config` (placeholder `{AGENT}/{DEVICE}/{CONFIG}`). Backup `*.bak-20261107-f43b` accanto.

**Gotcha**: su questa versione di Claude Code l'helper parte con cwd = `~/.config/claude-mini`, non con la cartella della sessione. Senza agente il
header manca, il server risponde 401 e Claude ripiega sul portachiavi: errore `CREDENTIAL_STORE_UNAVAILABLE` (sul mini il portachiavi è bloccato), che
inganna. Il fix è in `mcp-config` (37cef4e): se il cwd non corrisponde a nessuna regola si usa il cwd del processo padre (`lsof`), il fallback della
mappa solo dopo. Python di sistema 3.9: niente `str | None` non quotato.

Agenti: Nico (`06-Brandart/`) e Teo (`06-Brandart/teo`) si connettono. Le sessioni nella cartella di Leo (`00-Brandart-PM-OS`: Nora, Otto) risolvono a
leo, che sul mini non ha token: il server non si connette (voluto) e non c'è presenza, finché Otto e Nora non hanno le loro cartelle in Roster.

## Teo e Otto

Nessuna modifica ai loro avvii: sono già sulla config del mini. Teo: `claudeConfigDir: ~/.config/claude-mini` nei config del runner. Otto e Nora:
plist Lighthouse (`com.lighthouse.otto.*`, `CLAUDE_CONFIG_DIR=/Users/mariomosca/.config/claude-mini`) e cron di Nora con la stessa variabile.
Gli hook di Roster sono nelle `settings.json` di quella config, quindi si attivano.

**Da impostare in Roster (workspace brandart, serve `registry:write`: l'agente kai ha solo il workspace personale e non vede Teo né Otto):**
`agent_folders_set otto` con `{device: "mini", path: "~/Projects/00-Brandart-PM-OS/contratti", match: "prefix", is_home: true}`; stessa cosa per Nora
(`~/Projects/00-Brandart-PM-OS/segretaria`). Dopo, il token `…OTTO_MINI_MINI` (già nel `secrets.env`) risolve e Otto manda la presenza. Teo ha già la sua
regola nell'`AGENT_MAP` esportato (`~/Projects/06-Brandart/teo`).

## Verifica (DoD)

7 novembre 2026, 17:29 CET: `claude` in tmux su `~/Projects/06-Brandart/audit-plan` con `CLAUDE_CONFIG_DIR=~/.config/claude-mini`,
un prompt breve, poi `/exit`.

- `GET /api/v1/sessions?live=true` con il token di nico: `as-7625ac83fc5d`, agent `nico`, device `mini`, client `claude-code`,
  stato `waiting` (turn_end).
- Dopo `/exit`: `live=true` vuoto, `GET /api/v1/sessions/as-7625ac83fc5d` → `closed`, `end_reason: prompt_input_exit`.

F4.3b, 7 novembre 2026, 17:5x CET: stessa prova per Teo, `claude --model haiku` in tmux su `~/Projects/06-Brandart/teo`, un prompt, `/exit`.
`GET /api/v1/sessions?live=true` con il token di teo: `as-51ec34c6be65`, agent `teo`, device `mini`, client `claude-code`, stato `waiting`; dopo `/exit` vuoto.
`claude -p` con `mcp__roster__whoami` da `teo/` e da `06-Brandart/audit-plan`: risponde Teo e Nico, device mini.

Non verificato a occhio su https://rosterhq.io/team (serve il login di Mario): lo stato viene dall'API.
Un giro vero di Otto non c'è stato (cron/plist non in finestra): resta da vedere dopo che ha le cartelle.

## Ritorno

Togliere gli hook: `roster hook install --config-dir ~/.config/claude-mini --uninstall` (o ripristinare `settings.json.bak-20261107-rs07-f43`),
togliere la riga `roster export` dal crontab. I file di personas restano e si modificano a mano.

## Da fare dopo

- Cartelle di Otto e Nora in Roster (sopra, Alita): poi un loro giro vero visto come vivo sul mini.
- `mcp_sync --host mini`: farlo conoscere `roster` e `agents-crew` (oggi la config globale e i token del mini restano a mano; `hosts:` non ammette `${VAR}`).
- Installare sul laptop il `mcp-secrets.py` nuovo con `mcp_sync --apply` (oggi è solo nel repo e sul mini).
