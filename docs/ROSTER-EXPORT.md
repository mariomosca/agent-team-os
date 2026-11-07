# Agenti: come si cambiano adesso (RS07-F4.1, riscritto in F4.10)

Dal 7 novembre 2026 il registro degli agenti è **Roster** (https://rosterhq.io). Dal 7/11 sera (F4.10) non c'è più nessun
file `AGENT_MAP.json` da tenere: i lettori aprono la **cache del CLI Roster**.

| Cosa | Dove sta | Chi lo legge |
|------|----------|--------------|
| agent-map (regole di cartella, `hosts`, `cc_awareness`, schede degli agenti) | `~/.roster/cache/agent-map.json` (stesso formato del vecchio `AGENT_MAP.json`) | centralino (`indirizzi.py`), statusline e `agent-detect.sh`, voice-notify, hook `onda-agent-event.mjs`, bus-band, `mcp-secrets.py`, `alita-memoria-sync.py`, custode del mini, `install.sh` |
| persona | `~/.roster/cache/personas/<agente>.md` | hook `roster-identity.sh` a inizio sessione |
| schede, cartelle, voce | `~/.roster/cache/agents.json` | hook `roster-identity.sh`, voice-notify |
| avatar | `~/.agent-team-os/avatars/{local,default}/*.png` | Onda |
| `~/.lighthouse/agenti.json` | scritto da `roster cache refresh` quando il registro cambia | Lighthouse (solo sul laptop) |

## Cambiare un agente

1. Modificalo in Roster: dal portale o dal MCP `roster`.
2. La cache si rinfresca da sola: l'hook di sessione lancia `roster cache refresh --if-older 600` (If-None-Match, un
   giro a vuoto costa un 304). Subito: `roster cache refresh --force`.

## Regola dei lettori

Un unico ordine, uguale in tutti i lettori: `AB_AGENT_MAP` (un file preciso) → la cache di Roster
(`$ROSTER_HOME` o `~/.roster`, `cache/agent-map.json`) se esiste → il file a mano `~/.agent-team-os/AGENT_MAP.json`
(per chi installa il bus senza Roster; `install.sh` ne crea uno di partenza solo se non c'è né la cache né il file).
**Fail-open**: cache assente = nessuna mappa, comportamento di un file mancante; cache vecchia (Roster giù, token scaduto)
= l'ultima copia buona; file malformato = il centralino tiene l'ultima versione buona e scrive l'errore nel log. Mai un crash.
`roster doctor` segnala una copia non controllata da più di 24 ore.

## Cosa non c'è più

LaunchAgent `com.mindfullabai.roster.export` e cron del mini, `roster-export.sh`, il file `~/.agent-team-os/AGENT_MAP.json`
(copia in `~/.roster/backups/`), il pre-commit che lo proteggeva e `.roster-export.json` accanto ai file generati.
Non esportate: le persona del workspace Brandart restano scritte a mano (il server le nega finché manca il DPA).

## Tornare indietro

Rimettere un `AGENT_MAP.json` a mano in `~/.agent-team-os/` e togliere (o rinominare) `~/.roster/cache/agent-map.json`: i lettori
ripiegano sul file. La copia di prima dell'ultimo giro è in `~/.roster/backups/`.
