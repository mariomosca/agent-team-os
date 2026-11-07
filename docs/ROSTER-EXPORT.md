# Agenti: come si cambiano adesso (RS07-F4.1)

Dal 7 novembre 2026 il registro degli agenti è **Roster** (https://rosterhq.io). Questi file sono **generati**, non si
modificano a mano:

| File | Chi lo legge |
|------|--------------|
| `~/.agent-team-os/AGENT_MAP.json` | centralino, statusline, voice-notify, Onda, `agent-detect.sh`, `mcp_sync.py` |
| `~/.agent-team-os/personas/<agente>.md` | hook `agent-team-os-load.sh` (persona iniettata a inizio sessione) |
| `~/.agent-team-os/avatars/{local,default}/*.png` | Onda |
| `~/.lighthouse/agenti.json` | Lighthouse |

Accanto a ogni gruppo di file c'è `.roster-export.json`: per ogni file l'ETag di Roster (`<versione>.<12 hex dello
SHA-256 del file>`), quando è stato controllato e scritto, da quale URL.

## Cambiare un agente

1. Modificalo in Roster: dal portale o dal MCP `roster`.
2. Il LaunchAgent `com.mindfullabai.roster.export` rigenera i file ogni 10 minuti. Subito: `roster export all`
   (o `scripts/roster-export.sh`).

Una modifica a mano al file viene sovrascritta al giro dopo.

## Come gira

- CLI `roster` in `~/.local/bin/roster` (bundle in `~/.local/share/roster/`, dal repo `roster`, `npm run build:cli`;
  non è pubblicato su npm). Token del device nel portachiavi, configurazione in `~/.roster/`.
- `scripts/install-roster-export.sh` installa il LaunchAgent (`--remove` lo toglie); `scripts/roster-export.sh` è
  quello che gira, log in `~/Library/Logs/roster-export.log`.
- **Fail-open**: il CLI scrive un file solo dopo una risposta 200 dell'API, in modo atomico. Con Roster giù o il
  token scaduto il giro fallisce nel log e i file restano quelli dell'ultimo giro buono.
- **Non esportate**: le persona del workspace Brandart (leo, nico, teo, nora, otto) escono dal registro con 403
  finché manca il DPA (`presence_reduced`): restano scritte a mano, il CLI non le tocca. Un agente senza testo di
  persona (elio) non produce nessun file. `personas/proposte/` non è toccata.

## Pre-commit

`scripts/git-hooks/pre-commit` (attivo con `scripts/install-git-hooks.sh`, che imposta `core.hooksPath`) rifiuta il
commit di `AGENT_MAP.json`, `agenti.json` o `personas/*.md` se il `.roster-export.json` nella stessa cartella manca o
non corrisponde al contenuto (hash del file ≠ hash registrato). `examples/` e le persona a mano elencate in
`HAND_MANAGED` (oggi `personas/teo.md`) non sono controllate.

## Tornare indietro

`scripts/install-roster-export.sh --remove`: i file restano e si modificano a mano. Copie dei file di prima del primo
giro: `~/.agent-team-os/backups/roster-f41-20261107/`.
