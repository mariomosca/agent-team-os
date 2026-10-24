# Prova del bus v3 (BR24), per Mario

Circa 15 minuti, due sessioni vere in Onda: Alita (work-hub) e una sessione di Kai in una cartella di prova.
Se un passo non torna, ci si ferma e si torna indietro con **un comando** (in fondo).

## 0. Prima di cominciare

- Onda aperta. Le sessioni che non vuoi coinvolgere possono restare aperte: fino a quando non le riapri usano il bus vecchio, e dopo il passaggio vedono soltanto i messaggi vecchi.
- Una cartella di prova per Kai, per esempio `~/Projects/02-Experiments/prova-bus` (`mkdir -p`).

## 1. Passaggio (2 minuti)

```bash
~/Projects/01-Building/agent-team-os-feat-br24-centralino/centralino/passaggio.sh
```

Rispondi `s`. Deve finire con `== 9/9 fatto` e il percorso del backup. Orbit si chiude e si riapre da sola, con la build del branch.

## 2. Due sessioni nuove

Chiudi la sessione di Alita e riaprila in Onda su `~/work-hub`. Apri Kai in Onda sulla cartella di prova.

**Cosa controllare:** il banner all'avvio dice `You are Alita (alita@laptop/work-hub)` (per Kai, lo slug della cartella) e finisce con la riga `Bus: bus_inbox, bus_read, bus_send, bus_reply, bus_close, bus_status`.

## 3. Le sessioni sono vive

Ad Alita: *"bus_status di kai"*.

**Cosa controllare:** la sessione di prova di Kai compare con `viva per onda, canale: onda`.

## 4. Delega, domanda, risposta, esito (il cuore della prova)

1. Ad Alita: *"delega a kai/prova-bus, con kind delega: scrivi un file NOTE.txt con la data di oggi, ma prima chiedimi che titolo mettere"*.
   **Controlla:** entro ~10 secondi nel pannello di Kai compare da solo un prompt `Nuova delega t-… da alita/work-hub …`.
2. Kai fa la domanda (con `bus_reply`, `message_type='question'`).
   **Controlla:** se Alita è ferma, entro ~10 secondi le arriva il prompt `Domanda sul task …`. Se Alita sta lavorando, il prompt arriva a fine turno, non prima.
3. Ad Alita: *"rispondi: il titolo è Prova BR24"*.
   **Controlla:** Kai si sveglia da solo con `Risposta sul task …`, scrive il file e manda l'esito.
4. **Controlla:** Alita si sveglia con `Ritorno sul task …`. Chiedile di chiudere la delega (`bus_close`, `chiuso`), poi *"bus_inbox"*: la delega non deve più comparire fra le attese aperte.

## 5. Lo Stop

1. Ad Alita: *"manda a kai/prova-bus una delega da fare dopo: rileggi NOTE.txt e dimmi quante righe ha"*, mentre a Kai chiedi qualcos'altro (così è al lavoro).
   **Controlla:** finito quel turno, Kai **non** si ferma: lo Stop gli dice che c'è un messaggio che chiede un'azione, e lui lo gestisce.
2. Ad Alita: *"manda a kai/prova-bus una comunicazione, kind comunicazione: niente da fare, è solo un saluto"*.
   **Controlla:** Kai non viene svegliato e il suo Stop non si blocca per quel messaggio.

## 6. Orbit e la banda

- In Orbit apri l'Ufficio. **Controlla:** compaiono le due sessioni e i messaggi della prova, con la delega di Alita chiusa.
- Nelle due sessioni la banda del bus mostra la squadra e le attese.

## 7. Com'è andata

- **Tutto ok:** di' ad Alita "prova BR24 ok". Si tolgono il codice vecchio e il bus a file (BR24-F5.5) e i branch vanno su `main`.
- **Qualcosa non va:** annota quale passo e cosa hai visto, poi torna indietro:

```bash
~/Projects/01-Building/agent-team-os-feat-br24-centralino/centralino/ritorno.sh
```

Il ritorno prende circa 10 secondi:
- i messaggi e le attese nati durante la prova tornano nei file, quindi non si perde niente;
- tornano hook, comandi, skill, MCP e band, e Orbit 0.13.1;
- si riaccendono la sveglia a file e la raccolta del mini.

Poi chiudi e riapri le sessioni.

## Se serve guardare dentro

| Cosa | Come |
|---|---|
| Il centralino risponde? | `cd ~/Projects/01-Building/agent-team-os-feat-br24-centralino/centralino && uv run centralino status` |
| Cosa ha deciso la sveglia | `~/.agent-team-os/logs/centralino.jsonl` (righe `sveglia`, `op_rifiutata`, `op_errore`) |
| Errori del daemon | `~/.agent-team-os/logs/centralino-errori.log` |
| Backup del passaggio | `~/.agent-team-os-backup/ultimo` |

Nota: una sessione fuori da Onda (per esempio `claude attach`) è viva ma senza canale. Riceve i messaggi ma il bus non può svegliarla: dopo 5 minuti arriva un avviso ad Alita. È la decisione F1.5: la sessione di Alita sta in Onda.
