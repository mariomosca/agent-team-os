# Il Mac mini come device di Roster (RS07-F4.3)

> **Ora si fa con `roster setup` (RS07-F4.2b, 7/11/2026).** Pacchetto `@mindfullabai/roster` (`npm install -g` dal `.tgz` della
> release di Roster), poi `roster setup --config-dir ~/.config/claude-mini` (la scelta resta in `config.json`) e `roster doctor`:
> stessi passi del laptop (device, token, hook, cron, voce MCP con `headersHelper`). Il CLI sta in `~/.npm-global/bin/roster`
> (`~/.local/bin/roster` è un symlink). Lo script di export è lo stesso del laptop (`personas` e `AGENT_MAP.json` col controllo
> `"host": "mini"` e il `.bak` giornaliero di F4.3b, log in `~/.agent-team-os/logs/roster-export.log`); `scripts/roster-export-mini.sh`
> di questo repo non serve più. Il bundle a mano e gli script vecchi sono in `~/.roster/backups/old-bundle/`. La voce MCP è ora
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
| Export | cron ogni 10 minuti, `~/.local/share/roster/roster-export.sh` (sorgente: `scripts/roster-export-mini.sh`) | persona e `AGENT_MAP.json`; log in `~/.agent-team-os/logs/roster-export.log` |
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
leo, che sul mini non ha token: per le sessioni interattive in quelle cartelle il server non si connette. Otto e Nora ora hanno cartelle e token (F4.3c).

## Teo, Otto e Nora

Teo: `claudeConfigDir: ~/.config/claude-mini` nei config del runner, gli hook delle settings utente scattano (sessione interattiva o `claude -p` senza opzioni).
Otto e Nora: `CLAUDE_CONFIG_DIR=~/.config/claude-mini` da plist Lighthouse (`com.lighthouse.otto.*`) e dal cron del calendario di Nora (`avvia.sh`).
Cartelle in Roster (workspace brandart): otto `~/Projects/00-Brandart-PM-OS/contratti`, nora `~/Projects/00-Brandart-PM-OS/segretaria`, `prefix`, `is_home`.
Token: `ROSTER_PRODUCTION_TOKEN_{OTTO,NORA}_MINI_MINI` in `secrets.env` del mini (Nora coniato il 7/11 con `roster setup --agents otto,nora --config-dir ~/.config/claude-mini`).

**Perché gli hook non bastano (F4.3c).** I `run.sh` di Otto (`contratti/`) e di Nora (`segretaria/`) lanciano `claude -p ... --setting-sources project,local`: le
settings utente non vengono caricate, e gli hook di Roster stanno proprio lì. Risultato: giri veri, nessuna sessione su Roster. La presenza è quindi data da
`roster wrap`, messo in testa a ciascun `run.sh` (dopo la scelta di `CLAUDE_CONFIG_DIR`, prima del lock):

```
if [ -z "${ROSTER_WRAPPED:-}" ] && command -v roster >/dev/null 2>&1; then
  export ROSTER_WRAPPED=1
  _SELF="$(cd "$(dirname "$0")" && pwd -P)/$(basename "$0")"
  cd "$(dirname "$_SELF")" && exec roster wrap --client lighthouse --label "${1:-giro}" -- /bin/bash "$_SELF" "$@"
fi
```

- Il wrap parte **dalla cartella dell'agente**: la cwd decide il token (il vault `00-Brandart-PM-OS` da solo risolverebbe a leo, senza token sul mini).
- Apre la sessione (`client: lighthouse`, `reason: wrap`), un `beat` ogni 5 minuti, `end` all'uscita con l'exit del run; inoltra TERM/INT/HUP.
- Etichetta: il modo del run (`giro`, `scadenze`, `chiudi`, `calendario`, `bozze`...). Fail-open: senza `roster`, senza token o con Roster giù il run parte uguale.
- Limiti: niente `usage`/costo (il wrap non lo manda; il costo dei giri sta nei log del run) e una sessione per processo `run.sh`, quindi anche i no-op (es. `chiudi` senza risposte) aprono e chiudono una sessione di un secondo.
- Dove: repo di Leo `brandartit/brandart-pm-os`, ramo `feat/rs07-f43c-roster-wrap` (4fa5d91, da `main`). Le stesse 10 righe per file sono applicate anche alle copie di lavoro del
  laptop e del mini (che hanno modifiche di Leo non committate, e sul mini `segretaria/run.sh` è più vecchio di quello del laptop: non si copia un file sull'altro).

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

F4.3c, 7 novembre 2026, 20:46 CET: `contratti/run.sh chiudi` (Otto) e `segretaria/run.sh <modo sconosciuto>` (Nora) dal mini con l'env del cron
(`CLAUDE_CONFIG_DIR=~/.config/claude-mini`, `PORTINERIA_HOST=mini`): niente modello, niente scritture nel vault. `GET /api/v1/sessions?live=false&device=mini`:
`as-24a5195979c5` (otto, client `lighthouse`, chiusa) e `as-619150870a02` (nora, client `lighthouse`, chiusa). Vivi: `roster wrap -- sleep 12` dalle due cartelle,
`live=true` → `as-8c84c16121f8` (otto) e `as-f803cd2bebc2` (nora), stato `open`; dopo 12 secondi `live=true` vuoto.
Il primo giro vero (cron di Nora 7:45 lun-ven, Otto 9/13/17) passa dallo stesso involucro: da guardare lunedì 9/11.

## Ritorno

Togliere gli hook: `roster hook install --config-dir ~/.config/claude-mini --uninstall` (o ripristinare `settings.json.bak-20261107-rs07-f43`),
togliere la riga `roster export` dal crontab. I file di personas restano e si modificano a mano.

## Da fare dopo

- Giro vero di Otto e Nora lunedì 9/11 visto su Roster; merge del ramo `feat/rs07-f43c-roster-wrap` in `brandartit/brandart-pm-os` (Leo).
- `mcp_sync --host mini`: farlo conoscere `roster` e `agents-crew` (oggi la config globale e i token del mini restano a mano; `hosts:` non ammette `${VAR}`).
- Installare sul laptop il `mcp-secrets.py` nuovo con `mcp_sync --apply` (oggi è solo nel repo e sul mini).
