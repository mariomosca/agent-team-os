---
description: Modalità mobile della sessione (Mario segue dal telefono) - on, off, status. Argomenti - "on [Nh]", "off", "status".
argument-hint: on [Nh] | off | status
---

L'interruttore lo gira l'hook `agent-team-os-mobile.py` leggendo questo prompt (`/mobile $ARGUMENTS`), prima che tu lo veda: tu non devi fare niente sullo stato.

Nel contesto trovi una nota `[mobile]` con l'esito (attivata, spenta, stato) e, se la modalità è accesa, le regole `[modalità mobile ON ...]`.

Rispondi con UNA riga di conferma: stato, scadenza e, se la nota la riporta, l'avvertenza sul coperchio del Mac. Niente altro.

Se nel contesto non trovi nessuna nota `[mobile]`, l'hook non è installato: dillo e indica `./install.sh --register-hooks` nel repo agent-team-os. Come ripiego manuale: `/usr/bin/python3 ~/.claude/hooks/agent-team-os-mobile.py on|off|status` (usa `CLAUDE_CODE_SESSION_ID`).
