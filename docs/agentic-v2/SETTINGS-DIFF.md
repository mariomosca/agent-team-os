# Settings diff — attivazione hook drain-on-Stop (GATED)

> ⚠️ **NON applicato automaticamente.** L'attivazione del hook `Stop` modifica `~/.claude/settings.json`,
> che è **condiviso tra i 2 account** di Mario via symlink → un hook rotto impatta TUTTE le sessioni.
> Applicare manualmente solo dopo aver testato il hook isolato. Preferibile via skill `update-config`.

## Cosa aggiungere

In `~/.claude/settings.json`, sezione `hooks`, aggiungere il blocco `Stop`:

```jsonc
{
  "hooks": {
    // ... SessionStart / UserPromptSubmit / SessionEnd già presenti ...
    "Stop": [
      {
        "hooks": [
          { "type": "command", "command": "~/.claude/hooks/agent-team-os-stop.sh" }
        ]
      }
    ]
  }
}
```

## Pre-attivazione: test isolato (obbligatorio)

```bash
# 1. simula un agente con inbox NON vuota e verifica che il hook ritorni block
AB_HOME=~/.agent-team-os ~/.claude/hooks/agent-team-os-stop.sh   # (con un cwd di un agente che ha messaggi)
# atteso: JSON {"decision":"block","reason":"..."} se inbox ha messaggi nuovi

# 2. verifica loop guard: dopo CLAUDE_CODE_STOP_HOOK_BLOCK_CAP (default 3) block consecutivi,
#    deve permettere lo stop (exit 0) per non intrappolare l'agente

# 3. verifica che con inbox vuota ritorni exit 0 (permetti stop) senza output block
```

## Rollback

Rimuovere il blocco `Stop` da `settings.json` e riavviare le sessioni. Nessuno stato persistente da pulire (il cursor.json per-agente è innocuo se il hook non gira).

## Rischi noti

- `~/.claude/` symlinkato tra account `claude` e `claudea` → la modifica vale per entrambi.
- Un hook `Stop` che blocca in loop senza guard intrappola l'agente. Il loop guard (`CLAUDE_CODE_STOP_HOOK_BLOCK_CAP`) mitiga, ma testare prima.
- Applicare a un account alla volta e verificare una sessione di prova prima di propagare.

## PreToolUse su onda_agent_prompt (bus v2.8, BS21-F1.4)

`install.sh --register-hooks` aggiunge (idempotente, backup prima):

```jsonc
"PreToolUse": [
  { "matcher": "mcp__onda__onda_agent_prompt",
    "hooks": [ { "type": "command", "timeout": 10,
                 "command": "/usr/bin/python3 -I ~/.claude/hooks/agent-team-os-onda-prompt.py" } ] }
]
```

Prova isolata: `python3 -I tests/test_onda_prompt.py` (AB_HOME temporaneo). Rollback: togliere la voce
con quel matcher. Un errore dell'hook lascia passare il prompt: il solo effetto visibile possibile è il
rifiuto dei prompt lunghi senza `[delega]` / `[comunicazione]`, voluto.
