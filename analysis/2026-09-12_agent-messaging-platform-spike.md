# Spike — "Slack per agenti": esiste già? Cosa costruiamo?

> Autore: Kai · Data: 12 settembre 2026 · Tipo: spike di ricerca (no codice)
> Contesto: Mario vuole un sistema di messaggistica per il suo team di 5 agent Claude Code (Alita, Kai, Vera, Leo, Nico) che sia più veloce, autonomo e monitorabile del bus file-based attuale (`~/.agent-team-os`, spec in `PLAN-AGENT-TEAM-OS.md`). Vuole poterlo guardare come un umano guarda Slack: presenza online, DM, gruppi, thread, e la possibilità che un agente "spento" venga risvegliato da un altro agente.

---

## 0. Sintesi in una tabella

| Domanda | Risposta corta |
|---|---|
| Esiste già "Slack per agenti" pronto all'uso? | No, in forma esattamente richiesta. Esistono tre categorie adiacenti: (a) chat server umani con MCP bolt-on, (b) piattaforme "agent-native workspace" nuove di zecca (2026), (c) estensioni leggere file-based dentro un singolo coding agent. Nessuna è un drop-in perfetto. |
| Il candidato più vicino al bisogno reale | **AgentTeams** (agentscope-ai) — Matrix rooms + Manager/Worker + human-in-the-loop nativo, self-hosted one-command. |
| Il candidato concettualmente più simile al problema di Mario (scala micro, coding agent) | **pi-messenger** — file-based, no daemon, presenza/idle/stuck, reserve dei file, wake-up immediato. È quasi un "AgentTeams in miniatura" per un solo coding agent (pi), non multi-tool. |
| La chat server house più matura per farci un bridge | **Zulip** (o Mattermost) — entrambi hanno bot API mature + MCP server ufficiale/comunitario, thread nativi, presenza, self-hosted gratis. |
| Il protocollo standard di interoperabilità 2026 | **A2A** (Google, ora sotto Linux Foundation/AAIF) ha vinto la guerra dei protocolli — ACP di IBM ci è confluito dentro nell'agosto 2025. MCP resta per tool-calling, non per messaging fra agenti pari. |
| La feature nativa più recente e rilevante | **Claude Code SendMessage/ListAgents** (v2.1.224, agosto 2026) — messaggistica push nativa fra sessioni Claude Code locali, macOS/Linux. Non ha UI, non ha storage persistente visibile, non ha presenza esplicita. |
| Raccomandazione secca | **Opzione C (ibrida)**: tenere i file JSON come storage di verità, aggiungere push nativo via `SendMessage`/hook, e costruire una web UI di sola lettura leggera (Bun/Node + SQLite mirror + WebSocket) invece di migrare tutto su un chat server esterno. Vedi §4. |

---

## 1. Censimento

### 1.A — Prodotti/piattaforme nate per il caso "Slack/Discord per agenti"

| # | Nome | Cos'è | Licenza | Stato/ultima release | Fonte |
|---|---|---|---|---|---|
| 1 | **AgentTeams** (agentscope-ai) | OS collaborativo multi-agente: Manager umano/agente + Worker in stanze Matrix, human-in-the-loop by design | Apache 2.0 | v1.2.3, 22 ago 2026. 5.6k star, 691 fork, 786 commit — attivo | [GitHub](https://github.com/agentscope-ai/AgentTeams) |
| 2 | **AgentSpace** (HKUDS) | Workspace collaborativo umano+agenti: canali, DM, task board, approvazioni, governance | Apache 2.0 | 980 star, 132 fork, 142 commit, sviluppo attivo, lanciato giu 2026 | [GitHub](https://github.com/HKUDS/AgentSpace) |
| 3 | **pi-messenger** | Estensione per l'agente di coding "pi": presenza (active/idle/away/stuck), DM+broadcast, wake-up immediato, file reservation, tutto file-based senza daemon | MIT | 707 star, 60 commit, npm package attivo | [GitHub](https://github.com/nicobailon/pi-messenger) |
| 4 | **Dust** | "Multiplayer AI" enterprise: umani+agenti in workspace condiviso, notifiche, conversazioni condivise | SaaS proprietario (con self-hosted enterprise a richiesta) | Serie B $40M maggio 2026, 3000+ org, 300k+ agent custom — molto vivo ma enterprise-oriented, non pensato per 5 agent personali | [Dust Blog](https://dust.tt/blog/series-b-multiplayer-ai) |
| 5 | **AgentMail** | Non è chat, è "email inbox per agenti" (webhook/websocket, no human in loop by default) — categoria adiacente, non fit diretto | Proprietario/freemium | Attivo, buona ricezione su Product Hunt 2026 (4.8/87 review) | [Product Hunt](https://www.producthunt.com/products/agentmail) |
| 6 | **Slack Agentforce / Slackbot agentic** | Slack stesso è diventato "agentic OS": Slackbot GA da gen 2026, Agentforce agenti come "teammate" nei canali, Command Center per monitoring | SaaS Enterprise/Business+ | GA 13 gen 2026, attivamente esteso tutto il 2026 | [Slack Blog](https://slack.com/blog/news/slack-is-where-agents-work), [Salesforce](https://www.salesforce.com/agentforce/ai-agents/agent2agent-protocol/) |
| 7 | **AionUi** | Workspace desktop locale multi-agente open source, menzionato come alternativa leggera | OSS (dettagli licenza non verificati in profondità) | Citato in digest 2026, non ho verificato repo/commit direttamente — **da controllare prima di fare affidamento** | [startupcorners digest](https://startupcorners.com/digest/devtools-digest-2026-08-06) |
| 8 | **LobeHub (ex LobeChat)** | "Chief Agent Operator": agent groups, scheduling, self-hosted via Docker/Vercel, MCP-compatible skills | OSS + hosted | Attivo, rebrand 2026, grande ecosistema plugin | [GitHub](https://github.com/lobehub/lobehub) |
| 9 | **Chatwoot** | Inbox condivisa multicanale (non pensata per agenti, ma spesso citata come base "Slack-like" self-hosted) | OSS (MIT-like) | Maturo, non agent-native — richiederebbe bridge pesante | [Featurebase](https://www.featurebase.app/blog/open-source-chatbot) |
| 10 | **agentgateway.dev** | Gateway HTTP/gRPC unificato per traffico agent-to-agent + MCP + LLM provider — infra layer, non chat UI | OSS | Attivo 2026 | [agentgateway.dev](https://agentgateway.dev/) |

**Nota onestà**: dei 10 sopra, i primi 3 (AgentTeams, AgentSpace, pi-messenger) sono gli unici che rispondono realmente e specificamente al brief di Mario ("Slack per agenti, umano legge tutto, presenza, DM+gruppi"). Gli altri sono o troppo enterprise/SaaS (Dust, Slack/Agentforce), o categoria adiacente (AgentMail, agentgateway), o chat-generico con agenti aggiunti dopo (Chatwoot, LobeHub).

### 1.B — Protocolli di comunicazione agente-agente

| Protocollo | Chi lo spinge | Stato 2026 | Cosa risolve | Cosa NON risolve per Mario | Fonte |
|---|---|---|---|---|---|
| **A2A (Agent2Agent)** | Google, ora Linux Foundation / Agentic AI Foundation (AAIF) | v1.0 rilasciato gennaio-aprile 2026 (fonti divergono sul mese esatto, concordano su "H1 2026"), 150+ organizzazioni supporter, adozione enterprise in produzione (supply chain, finance, insurance, IT ops), release 0.3 con gRPC e signed Agent Cards | Standard di interoperabilità cross-vendor: agent discovery (Agent Card), task lifecycle, streaming, negoziazione capability | Non è una UI, non è "chat leggibile da umano" — è un protocollo di trasporto/wire-format. Serve costruirci sopra un client per renderlo leggibile | [Linux Foundation](https://www.linuxfoundation.org/press/a2a-protocol-surpasses-150-organizations-lands-in-major-cloud-platforms-and-sees-enterprise-production-use-in-first-year), [Google Cloud Blog](https://cloud.google.com/blog/products/ai-machine-learning/agent2agent-protocol-is-getting-an-upgrade) |
| **ACP (IBM/BeeAI)** | IBM Research + BeeAI | **Morto come standard separato**: confluito in A2A sotto Linux Foundation nell'agosto 2025. BeeAI ora gira su A2A | — | — | [WorkOS](https://workos.com/blog/ibm-agent-communication-protocol-acp) |
| **AGNTCY / Agent Connect Protocol (ACP, nome riusato)** | Cisco Outshift, LangChain, Galileo (collettivo) | Attivo 2026, usato in produzione interna Cisco (JARVIS platform engineer), orchestrato via LangGraph | Layer di collaborazione multi-org "Internet of Agents": auth, config dinamica, output chaining | Stesso problema di A2A: è infrastruttura di rete, non una piattaforma chat con storia/thread leggibili da un umano | [Outshift](https://outshift.cisco.com/blog/ai-ml/building-the-internet-of-agents-introducing-the-agntcy) |
| **Coral Protocol** | Collettivo indipendente, con token $CORAL (elemento crypto/web3) | Coral Server + threaded messaging su base MCP, roadmap Q1 2026 (SDK, Stack Kit) | Modello di trust decentralizzato + micropagamenti fra agenti di organizzazioni diverse | Introduce complessità (identità decentralizzata, pagamenti on-chain) totalmente fuori scope per 5 agent di un solo utente. **Sconsigliato per questo caso** | [arXiv](https://arxiv.org/html/2505.00749) |
| **MCP (Model Context Protocol)** | Anthropic → donato a Agentic AI Foundation (dic 2025) | Standard de facto per tool-calling agente↔strumento. Roadmap agosto 2026 include esplicitamente "agentic messaging primitives" — quindi MCP sta iniziando a guardare al messaging, ma non c'è ancora | Standardizza come un agente chiama uno strumento/dato esterno | **Dove non arriva**: non è un protocollo peer-to-peer fra agenti pari con storia conversazionale, presenza, thread. Serve per far leggere a un agente la mailbox di un chat server (è quello che fanno i "Mattermost MCP server", "Zulip MCP server" ecc. — MCP fa da bridge, non da bus) | [MCP roadmap coverage](https://dev.to/pockit_tools/mcp-vs-a2a-the-complete-guide-to-ai-agent-protocols-in-2026-30li) |

**Sintesi protocolli**: nel 2026 la guerra si è consolidata su un layering chiaro — **MCP per tool access, A2A per coordinamento agent-to-agent, HTTP/gRPC streamable come trasporto**. Ma tutti e tre restano protocolli, non prodotti finiti con un'interfaccia che Mario può aprire e leggere. Costruire un ponte A2A per 5 agent-Claude-Code-locali-su-un-Mac è overkill: A2A è pensato per interoperabilità cross-vendor/cross-org (es. il tuo agente Salesforce che parla con l'agente SAP di un partner), non per Alita che parla con Kai sullo stesso filesystem.

### 1.C — Chat server self-hosted riusabili come "Slack per agenti"

| Server | Bot API | Presenza | Thread | MCP disponibile | Desktop+Web | Note |
|---|---|---|---|---|---|---|
| **Mattermost** | Matura (REST + webhook + Agents nativi) | Sì | Sì | **Nativo da v-recente 2026** (Mattermost Agents + MCP Server ufficiale, "Give Agents Secure Access to Your Workspace") + 3 implementazioni community | Sì, entrambe | Il più "enterprise-pronto" dei tre: ha già il concetto di "Agent" come cittadino di prima classe nell'admin UI | [Mattermost Blog](https://mattermost.com/blog/mattermost-mcp-server/) |
| **Zulip** | Matura, bot @mentionable | Sì | **Sì, il migliore**: thread-per-topic è il design nativo di Zulip, ideale per conversazioni asincrone lunghe (esattamente il caso "brief + risposta ore dopo") | **Sì**, `zulipmcp` ufficiale (windborne/zulip org su GitHub) — bot AI @mentionable o MCP client generico, long-polling per eventi real-time | Sì, entrambe | Il modello a topic di Zulip è probabilmente il migliore fit semantico per "thread" del protocollo attuale di Mario (thread-id ⇄ topic Zulip è mapping quasi 1:1) | [GitHub zulipmcp](https://github.com/zulip/zulipmcp) |
| **Rocket.Chat** | Nativo MCP da v8.8 (2026), 18 tool via terze parti | Sì | Sì | **Sì, nativo**, "AI Center panel", token per-persona (mappa identità reale, non credenziale condivisa) | Sì, entrambe | Design MCP più recente e pulito dei tre (per-user token invece di bot account condiviso) | [Rocket.Chat Blog](https://www.rocket.chat/blog/rocket-chat-8-8-introduces-native-mcp-server-for-ai-agents) |
| **Matrix/Element** | Bot API matura (Matrix Client-Server API), ma nessun MCP "ufficiale" trovato per Matrix stesso — **AgentTeams lo usa come infra sotto**, non è Matrix a offrire MCP nativo | Sì (protocollo, non solo prodotto) | Sì (room-based) | No MCP nativo diretto verificato — bridge fatti da progetti terzi (es. AgentTeams) | Sì (Element Web/Desktop/Mobile), ecosistema multi-client enorme | Interessante perché **decentralizzato** e già scelto da AgentTeams come substrato — ma Mario non ha bisogno di federazione fra server diversi, solo di un bus locale |

**Verdetto categoria 1.C**: Zulip e Mattermost sono i due più pronti "off the shelf" con MCP nativo o quasi-nativo. Rocket.Chat ha il design MCP più moderno ma è il meno maturo dei tre come prodotto per questo caso. Matrix è la scelta di AgentTeams (vedi sopra) proprio perché offre stanze + presenza + client gratis (Element) senza dover scrivere una UI da zero.

### 1.D — Framework multi-agente con UI di conversazione osservabile

| Framework | Copertura "chat fra agenti leggibile da umano" | Stato 2026 | Fonte |
|---|---|---|---|
| **AutoGen Studio** | UI no-code per configurare conversazioni multi-agente — ma il progetto originale **è in maintenance mode** | Sconsigliato per nuovi progetti nel 2026 | [Scrimba](https://scrimba.com/articles/best-ai-agent-frameworks/) |
| **LangGraph Studio/Platform** | Visualizzazione grafo in tempo reale, time-travel debugging, editing dello stato — ottimo per debug di UN agente/grafo, non pensato come "casella di posta persistente multi-utente" | Attivo, LangSmith per osservabilità enterprise | [LangChain resources](https://www.langchain.com/resources/ai-agent-frameworks) |
| **Mastra** | Framework TS con tracing built-in per capire il comportamento agente e diagnosticare fallimenti — di nuovo, osservabilità/debug, non messaging persistente multi-party | Attivo 2026 | [Mastra via LangChain comparison](https://dev.to/pockit_tools/langgraph-vs-crewai-vs-autogen-the-complete-multi-agent-ai-orchestration-guide-for-2026-2d63) |
| **OpenAI Agents SDK (traces)** | Traces per debug, non un'interfaccia chat persistente | Attivo | — |
| **Claude Agent SDK / Claude Code nativo** | **SendMessage/ListAgents** (v2.1.224, agosto 2026): messaggistica push cross-sessione, ma senza storage persistente visibile all'umano né UI dedicata — è "whisper" fra sessioni, non un registro consultabile | Nuovo, solo macOS/Linux, no Windows/Bedrock/Vertex/Foundry | [thepromptshelf.dev](https://thepromptshelf.dev/blog/claude-code-cross-session-messaging-guide-2026/) |

**Verdetto categoria 1.D**: nessuno di questi framework offre quello che Mario vuole ("Slack dove l'umano legge tutto"). Sono tutti ottimizzati per l'agente che sviluppa/orchestrano, non per l'umano che sorveglia una conversazione persistente multi-party. L'eccezione degna di nota è **Claude Code SendMessage** perché è esattamente il layer che Mario già userebbe sotto (le sue 5 sessioni SONO sessioni Claude Code) — ma manca totalmente la parte "Mario legge via web/app".

---

## 2. Matrice di fit contro i requisiti di Mario

Scala: ✅ pieno fit — 🟡 parziale/richiede lavoro — ❌ assente/mal si adatta

| Requisito | Protocollo attuale (file-based) | AgentTeams (Matrix) | AgentSpace | pi-messenger (pattern) | Zulip+MCP | Mattermost+MCP | Claude Code SendMessage nativo |
|---|---|---|---|---|---|---|---|
| Presenza online/offline | ❌ (solo `last_seen` in registry, bug attuale lo rompe pure) | ✅ (Matrix presence) | 🟡 (non esplicita) | ✅ (active/idle/away/stuck) | ✅ | ✅ | ❌ |
| DM + gruppi | ✅ (msg 1:1, no gruppi nativi) | ✅ | ✅ (canali+DM) | 🟡 (DM+broadcast, no gruppi strutturati) | ✅ | ✅ | 🟡 (peer-to-peer, no gruppi) |
| Contesto/thread | ✅ (`thread_id`, buono) | ✅ (room = thread) | ✅ | 🟡 (feed lineare) | ✅✅ (topic-per-thread, il migliore) | ✅ | ❌ (nessuna persistenza di thread) |
| Umano legge tutto | 🟡 (via `/inbox`, `/thread`, terminal-bound) | ✅✅ (Element Web, zero-config, da browser/telefono) | ✅ (web UI dedicata) | 🟡 (overlay dentro il terminale pi) | ✅✅ | ✅✅ | ❌ (nessuna vista umana) |
| Avviabile da agente se spento | ❌ (richiede Mario apra sessione) | 🟡 (Worker sono container, wake-up gestito da orchestratore, non da bot che apre terminale) | 🟡 (daemon remoto, ma via token/CLI) | 🟡 (wake-up immediato SE l'agente destinatario ha già una sessione pi viva — non avvia un processo nuovo) | ❌ (serve comunque un bot/processo già in ascolto) | ❌ (idem) | ❌ (richiede sessione Claude Code già viva) |
| Web + desktop | ❌ (solo terminale + plugin Onda) | ✅ (Element Web/Desktop/Mobile) | ✅ (Next.js web, no desktop) | ❌ (dentro terminale) | ✅ | ✅ | ❌ |
| Integrazione MCP/skill Claude Code | 🟡 (skill `agent-team-os` esiste, custom) | 🟡 (bridge da scrivere) | 🟡 (bridge da scrivere) | N/A (per pi, non Claude) | ✅ (MCP ufficiale, basta collegarlo) | ✅ (MCP ufficiale) | N/A (è già dentro Claude Code) |
| Autonomia (push, non polling) | 🟡 (hook SessionStart/UserPromptSubmit, non vero push) | ✅ (Matrix è push nativo) | ✅ | ✅ (`triggerTurn`) | ✅ | ✅ | ✅✅ (nativo, zero setup) |
| Monitorabilità (log/audit/metriche) | ✅ (outbox JSONL append-only, già buono) | ✅ (Matrix room history) | ✅ (audit trail dichiarato) | 🟡 (log locale, meno strutturato) | ✅ | ✅ | ❌ (nessun log persistente esposto) |
| Costo | Gratis (già pagato/costruito) | Gratis, self-hosted | Gratis (self-hosted) o SaaS hosted | Gratis, MIT | Gratis self-hosted, o cloud a pagamento oltre soglie | Gratis self-hosted (Enterprise a pagamento per feature avanzate) | Incluso nel piano Claude giá esistente |
| Self-hosted vs SaaS | Self-hosted (file locali) | Self-hosted (one-command) | Entrambi | Self-hosted (file locali) | Entrambi | Entrambi | N/A (locale al Mac) |
| Lock-in | Nessuno (è tuo) | Basso (Apache 2.0, ma opinionato su Matrix+container) | Basso (Apache 2.0) | Bassissimo (MIT, piccolo) | Basso (formato Zulip, ma OSS) | Basso | **Alto** (dipende da Anthropic, feature nuova, no SLA su API stabile) |
| Latenza percepita | Alta (serve aprire sessione Mario-mediata) | Bassa (Matrix è real-time) | Bassa-media | Bassa (stesso processo/filesystem) | Bassa | Bassa | Bassissima (in-process) |

**Lettura della matrice**: nessuna colonna ha tutte ✅. Le due colonne più forti sono **AgentTeams** (via Matrix, quasi tutto ✅ tranne il bridge da scrivere per Claude Code + l'avviare-un-agente-spento resta parziale ovunque) e la combinazione **file-based attuale + SendMessage nativo + una web UI** (che è l'opzione C, vedi sotto) perché parte da ✅ già consolidati (audit, thread, costo zero) e aggiunge solo i due buchi veri: push nativo e vista umana web.

**Sul punto "avviabile da agente se spento"**: è il requisito più duro e nessun candidato lo risolve elegantemente. Il motivo strutturale è che un agente Claude Code "spento" non è un processo in ascolto — è l'assenza di un processo. Per farlo risvegliare serve SEMPRE un demone esterno che tenga viva almeno una "porta d'ascolto" (systemd/launchd timer, processo Node long-running, o Onda stesso che tiene panes/terminal come oggetti persistenti anche a sessione chiusa). Questo è indipendente dalla scelta del layer di chat: va risolto una volta, a livello di orchestratore (Onda ha già `onda_agent_spawn`/`onda_terminal_spawn`, è la base giusta), non a livello di protocollo messaggi.

---

## 3. Tre opzioni architetturali

Tutte le stime sono in wall-clock con N istanze Claude Code parallele, mai in giornate-uomo. Assumo che l'esecuzione la fai fare a istanze Kai (o Kai+sub-agent), non a un ipotetico team umano.

### Opzione A — Adottare un chat server OSS esistente (Zulip o Mattermost) + MCP + bridge

**Come funzionerebbe**: Zulip self-hosted (Docker) come "verità" della conversazione. Ogni agente (Alita, Kai, Vera, Leo, Nico) diventa un bot Zulip @mentionable. Il bridge traduce: messaggio nel bus file-based attuale → post Zulip nel topic giusto, e viceversa un mention Zulip → hook che sveglia (o notifica) la sessione Claude Code giusta. Mario apre Zulip da browser o app desktop e vede tutto come team chat vera, con la cronologia completa e ricerca full-text nativa.

**Pro**
- Presenza, thread, ricerca, mobile, notifiche push: tutto già pronto e maturo, zero da costruire su quel fronte.
- MCP ufficiale (`zulipmcp`) toglie la parte più rischiosa (autenticazione, rate limit, formati) da scrivere a mano.
- Se in futuro Mario vuole invitare un umano vero (un collega) nel loop, Zulip lo supporta già.

**Contro / rischi**
- Il bridge bidirezionale file↔Zulip è comunque codice nuovo e stateful: due fonti di verità (i file JSON e il database Zulik) che devono restare sincronizzate — la stessa classe di bug del registry rotto già visto in `CONTRACT.md` (§8, jq stato-assorbente) si può ripresentare qui su scala doppia.
- Overhead operativo: un container Postgres+Zulik in più da mantenere sul Mac (o mini) sempre acceso, backup, upgrade.
- "Avviabile da agente se spento" resta irrisolto: Zulip non avvia processi Claude Code, serve comunque il demone esterno di cui sopra.
- Rischio di over-engineering: Mario ha 5 agent, non 50. La capacità enterprise di Zulip (migliaia di canali, org multiple) è capacità che non userà.

**Stima**: setup Zulik Docker + bot account + MCP server collegato: **2-3 ore wall-clock, 1 istanza Kai**. Bridge bidirezionale file↔Zulip con mapping thread↔topic, dedup, gestione errori: **1-2 giorni wall-clock con 2 istanze Kai in parallelo** (una sul bridge in-bound file→Zulip, una sul bridge out-bound Zulip→file+hook di wake). Totale realistico: **~1.5-2 giorni wall-clock**, non settimane, perché il grosso (chat server) è già scritto da altri.

### Opzione B — Piattaforma propria minimale (Node/Bun + WebSocket + SQLite, web UI, presenza, thread)

**Come funzionerebbe**: un servizio Bun (o Node) locale, mai spento (systemd/launchd), con SQLite come mirror in tempo reale dei file JSON esistenti (write-through: chi scrive un messaggio scrive sia il file sia una riga SQLite via lo stesso helper `ab_write_message`). Un layer WebSocket fa push verso una web UI minimale (React/vanilla) che Mario apre da browser: lista agenti con badge online/offline (calcolato da un heartbeat che ogni sessione Claude Code manda via hook), thread per conversazione, DM e "canali" per gruppo. Il file resta la fonte di verità per audit/portabilità; SQLite è solo un indice veloce per la UI.

**Pro**
- Fatto su misura: zero feature inutili, zero container esterni pesanti (SQLite è un file).
- Non introduce due sistemi di verità paralleli come nell'opzione A: SQLite è dichiaratamente un mirror derivato, ricostruibile dai file in qualsiasi momento (`rebuild-index` script).
- Riusa tutto il lavoro già fatto (`agent-team-os-lib.sh`, schema v1.1/v1.2, hook) — non butti via 4 mesi di lavoro sul bus.
- Diventa naturalmente una feature di Onda: Onda ha già la finestra "Agentic" e il plugin che legge questi stessi file (v1.3 read API, vedi `PLAN-AGENT-TEAM-OS.md`). Costruire il layer push+WebSocket qui è coerente col prodotto che Mario vende.

**Contro / rischi**
- È comunque codice nuovo per intero: server WebSocket, autenticazione minima (anche solo "gira su localhost, nessuno fuori"), UI da disegnare. Non è "zero effort" come l'opzione A sul fronte chat-server.
- Bisogna reinventare cose che Zulip ha gratis da anni: full-text search, mobile push, gestione allegati. Per ora Mario non ne ha bisogno, ma se le vorrà dopo le paga in sviluppo aggiuntivo.
- "Avviabile da agente se spento" ancora irrisolto allo stesso modo di A — ma qui è più facile da chiudere perché il demone Node/Bun È il posto naturale dove mettere anche il wake-up (può chiamare `onda_agent_spawn` via MCP Onda direttamente, dato che gira sulla stessa macchina).

**Stima**: server Bun+WebSocket+SQLite mirror con heartbeat/presenza: **4-6 ore wall-clock, 1 istanza Kai**. Web UI minimale (lista agenti, thread view, invio messaggio) in React o anche solo HTML+fetch/WS: **4-6 ore wall-clock, 1 istanza Kai in parallelo sulla UI mentre l'altra fa il server** (stream indipendenti dopo aver fissato il contratto WebSocket in 15 minuti). Wake-up via Onda MCP: **1-2 ore, stessa istanza del server**. Totale: **1 giornata wall-clock con 2 Kai paralleli**, possibile demo funzionante in **2-3 ore** per una prima versione grezza (solo lista+thread, no presenza raffinata).

### Opzione C — Evolvere il protocollo attuale: file come verità + push nativo (Claude Code SendMessage) + web UI di sola lettura/monitoraggio

**Come funzionerebbe**: non si tocca lo storage (resta `~/.agent-team-os/` con thread/inbox/outbox — è già solido, ha audit trail, ha versioning schema, ha workspace filtering v1.2). Si aggiungono due cose:
1. **Push nativo**: quando `ab_write_message` scrive un messaggio con `delivery:session`, invece di aspettare che Mario apra la sessione destinataria, si prova prima `SendMessage`/`ListAgents` (se il destinatario ha già una sessione Claude Code viva sulla stessa macchina) per notificarlo immediatamente. Fallback sul meccanismo attuale (hook SessionStart) se la sessione non è viva.
2. **Web UI di sola lettura**: un piccolo server statico (anche solo un file watcher + Server-Sent Events, niente WebSocket bidirezionale necessario dato che è read-only) che legge `inboxes/`, `threads/`, `outbox/` e li rende come una vista "Slack read-only" nel browser. Mario guarda, non scrive da lì (scrive sempre dal terminale/agente, coerente col fatto che il vero attore è l'agente, non un umano che digita in una chat box).

**Pro**
- **Rischio più basso di tutti**: non tocca la fonte di verità, non introduce un secondo sistema che può disallinearsi. Il bug del registry vuoto (`CONTRACT.md` §8) resta un problema isolato da fixare una volta, non moltiplicato su due storage.
- Sfrutta una feature che Anthropic ha appena spedito (SendMessage, agosto 2026) invece di reinventare un trasporto push — coerente col principio "usa quello che esiste quando è abbastanza buono".
- Il più veloce da spedire e il più coerente con "Ship > Perfect" — è un incremento, non una migrazione.
- Si integra naturalmente con Onda: la finestra Agentic esiste già e la v1.3 read API del bus è già una spec stabile (`PLAN-AGENT-TEAM-OS.md` §"Public Read API"). Il lavoro di Kai qui potrebbe letteralmente essere "finire quello che c'era già in roadmap" invece di aprire un fronte nuovo.

**Contro / rischi**
- Non risolve DM/gruppi come concetto "chat" pieno — resta un bus di messaggi strutturati (request/response/brief), non una vera chat conversazionale libera. Per il caso d'uso di Mario (agenti che si scambiano brief e task, non due umani che chiacchierano) questo è probabilmente giusto, non un difetto.
- La UI "di sola lettura" è meno soddisfacente del fantasma "Slack vero" che Mario descrive — non c'è dove scrivere un messaggio dal browser a un agente (ma si può aggiungere dopo, è additivo).
- `SendMessage` è nuovissimo (un mese di vita), solo macOS/Linux, nessuna garanzia di stabilità API nel tempo — leggero lock-in comportamentale su una feature che Anthropic potrebbe cambiare.
- "Avviabile da agente se spento" resta il buco: `SendMessage` esplicitamente non sveglia una sessione morta, serve comunque il layer Onda (`onda_agent_spawn`) sopra.

**Stima**: integrare `SendMessage` come tentativo di push prima del fallback hook: **2-3 ore wall-clock, 1 istanza Kai** (è un `if` in più nell'helper `ab_write_message` + test). Web UI read-only (file watcher + SSE + pagina HTML semplice, stile artifact Onda-friendly): **3-4 ore wall-clock, 1 istanza Kai in parallelo**. Wake-up via Onda MCP per il caso "agente spento": **1-2 ore**, stessa lib del wake-up già prevista in opzione B. Totale: **mezza giornata wall-clock con 2 Kai paralleli**, prima demo visibile in **2-3 ore**.

### Ibrido consigliato (C + pezzi di B)

L'opzione più sensata non è "scegli una delle tre", è: **parti da C** (push nativo + read-UI, rischio minimo, riusa tutto), e se dopo 1-2 settimane d'uso reale emerge il bisogno di scrivere dalla UI o di avere presenza raffinata stile pi-messenger (active/idle/stuck), **estendi verso B** aggiungendo lo strato scrivibile (WebSocket) sopra lo stesso storage a file — a quel punto SQLite mirror è un'ottimizzazione, non un redesign. Questo evita sia il costo di integrazione di A (due sistemi di verità, container esterno) sia il rischio di sovracostruire B prima di sapere se serve davvero la scrittura da browser.

---

## 4. Raccomandazione secca

**Fai così, in quest'ordine:**

1. **Ora (2-3 ore wall-clock, 1 istanza Kai)** — validazione rapida: fixa il bug del registry vuoto (`CONTRACT.md` §8, già trovato, 5 minuti dentro lo stream A del contratto Onda) e aggiungi il tentativo `SendMessage` come primo hop di consegna in `ab_write_message`, con fallback al meccanismo hook attuale. Questo da solo toglie già la lamentela "poco veloce" per il caso più comune (destinatario con sessione già aperta).
2. **Poi (mezza giornata wall-clock, 2 Kai paralleli)** — costruisci la web UI di sola lettura (opzione C): un mini-server che espone `inboxes/threads/outbox` via SSE e una pagina che li mostra come feed Slack-like, ospitabile anche come Artifact o come pannello dentro Onda (la finestra Agentic esiste già, è il posto giusto). Questo risolve "poco monitorabile".
3. **Dopo, solo se serve davvero (misurato dall'uso, non stimato a tavolino)** — aggiungi lo strato scrivibile (WebSocket, presenza raffinata stile pi-messenger, possibilità di scrivere un messaggio dal browser) come estensione additiva sopra lo stesso storage a file. Non toccare mai la fonte di verità.
4. **Non fare ora**: non migrare su Zulip/Mattermost/Matrix (opzione A) e non inseguire A2A/AGNTCY/Coral — sono protocolli/prodotti pensati per interoperabilità cross-vendor o scala enterprise, e per 5 agent personali sullo stesso Mac aggiungono più superficie di manutenzione di quanta ne tolgano. Tienili in radar (in particolare Zulip+MCP) se un giorno il team cresce oltre 8-10 agent o deve includere collaboratori umani reali nel loop.

**Cosa provare nelle prossime 2-3 ore per validare**: costruisci solo il passo 1 (push via SendMessage + fix registry) e osserva per una giornata reale d'uso se la sensazione di "lentezza" migliora. Se sì, procedi al passo 2. Se la vera lamentela era "monitorabilità" più che "velocità", salta dritto al passo 2 e lascia il push per dopo — sono indipendenti e parallelizzabili con 2 istanze Kai fin da subito.

### Onda: feature o prodotto a parte?

In 5 righe: fallo **feature di Onda**, non prodotto a parte. Onda ha già la finestra Agentic, il plugin che legge questo stesso bus (v1.3 read API), e un MCP server (`mcp__onda__*`) con `onda_agent_spawn`/`onda_terminal_wait_for` che sono esattamente i primitivi che servono per il wake-up "agente spento". Costruire un prodotto SaaS a parte tipo AgentSpace/Dust vorrebbe dire competere con player che hanno appena raccolto $40M (Dust) o hanno 5-6k stelle GitHub (AgentTeams) su un mercato enterprise che Mario non sta indirizzando. Il vantaggio competitivo reale di Onda è "il terminale dove il tuo team di agent Claude Code vive già" — la messaggistica osservabile è una feature che rende quel terminale più prezioso, non un business a sé.

---

## Fonti citate (verificate settembre 2026)

- [AgentTeams — GitHub](https://github.com/agentscope-ai/AgentTeams)
- [AgentSpace — GitHub](https://github.com/HKUDS/AgentSpace)
- [pi-messenger — GitHub](https://github.com/nicobailon/pi-messenger)
- [Dust — Series B blog](https://dust.tt/blog/series-b-multiplayer-ai)
- [AgentMail — Product Hunt](https://www.producthunt.com/products/agentmail)
- [Slack is where agents work](https://slack.com/blog/news/slack-is-where-agents-work)
- [Salesforce A2A protocol](https://www.salesforce.com/agentforce/ai-agents/agent2agent-protocol/)
- [LobeHub — GitHub](https://github.com/lobehub/lobehub)
- [Featurebase — open source chatbot list](https://www.featurebase.app/blog/open-source-chatbot)
- [agentgateway.dev](https://agentgateway.dev/)
- [A2A — Linux Foundation press release](https://www.linuxfoundation.org/press/a2a-protocol-surpasses-150-organizations-lands-in-major-cloud-platforms-and-sees-enterprise-production-use-in-first-year)
- [A2A upgrade — Google Cloud Blog](https://cloud.google.com/blog/products/ai-machine-learning/agent2agent-protocol-is-getting-an-upgrade)
- [IBM ACP → confluito in A2A — WorkOS](https://workos.com/blog/ibm-agent-communication-protocol-acp)
- [AGNTCY / Outshift Cisco](https://outshift.cisco.com/blog/ai-ml/building-the-internet-of-agents-introducing-the-agntcy)
- [Coral Protocol — arXiv](https://arxiv.org/html/2505.00749)
- [Mattermost MCP Server](https://mattermost.com/blog/mattermost-mcp-server/)
- [Zulip MCP — GitHub](https://github.com/zulip/zulipmcp)
- [Rocket.Chat 8.8 MCP Server](https://www.rocket.chat/blog/rocket-chat-8-8-introduces-native-mcp-server-for-ai-agents)
- [MCP vs A2A — DEV Community](https://dev.to/pockit_tools/mcp-vs-a2a-the-complete-guide-to-ai-agent-protocols-in-2026-30li)
- [Claude Code cross-session messaging — The Prompt Shelf](https://thepromptshelf.dev/blog/claude-code-cross-session-messaging-guide-2026/)
- [LangChain — best AI agent frameworks 2026](https://www.langchain.com/resources/ai-agent-frameworks)

## Fonti non pienamente verificate (segnalate, da non citare come certe)

- **AionUi**: citato in un digest secondario (startupcorners.com), non ho aperto il repo direttamente per confermare licenza/stato — verificare prima di considerarlo un candidato serio.
- **Data esatta di rilascio A2A v1.0**: le fonti divergono fra "gennaio 2026" e "aprile 2026" per il passaggio formale a v1.0 — il fatto che sia production-ready nel 2026 è confermato da più fonti indipendenti, la data esatta no.
