# bus-band

A [Claude Code mod](https://code.claude.com/docs/en/plugins/mods/overview) (Claude Code 2.1.287+) that draws a band above the prompt with the messages waiting for **this session** on the Agent Team OS bus.

```
Bus alita/work-hub: 2 in attesa, 1 urgenti o alta (+1 di altri workspace)  [Nascondi]
!! [ALTA] da kai: crewmoor-stato-piano-v1
   [normale] da vera: content-brief
```

- A second section, **Squadra**, shows the sibling sessions (see below).
- UI text is in Italian (the agents' working language). Shows only when something is pending; disappears when the inbox is empty.
- Same scoping as the shell lib (v1.4): agent from `AGENT_MAP.json` by cwd prefix, slug = workspace basename, shared root `inboxes/<agent>/` plus `inboxes/<agent>/@<slug>/`, messages whose `context.project_path` belongs to another workspace counted apart.
- Priority is spelled out (`URGENTE`, `ALTA`, a `!!` marker), never colour alone.
- A toast when a new `urgent`/`high` message arrives. Polls every 20 s and at the end of every turn.
- `/bus-band` toggles it; the **Nascondi** button hides it until the next new message.
- Read-only on messages: it never moves, archives or writes a message (the only thing it ever writes is the Onda request file above). `/read`, `/inbox` and the MCP tools stay the way to act on them.
- Honours `AB_HOME` like the shell lib (default `~/.agent-team-os`).

## Fonte: il centralino (BR24-F4.3)

From BR24 the band reads the bus from its **centralino** (`agent-team-os/centralino`, contract `docs/CENTRALINO.md`): one read-only op, `vista`, on `$AB_HOME/centralino.sock` (or `AB_CENTRALINO_SOCK`), once per round for all three sections. Liveness of the Squadra rows, pending messages and waits come from there, records in the bus file format, so the same parsers read them. The mod engine cannot speak line-JSON on a socket (`$.http` speaks HTTP), so the request goes through `nc -U` (`$.process.run`, 2 s timeout).

If the socket is missing or does not answer, the round reads today's files as before, and a dim line at the bottom of the band says so (`Bus letto dai file: ...`). The line only appears in a band that is already drawn: a missing centralino alone never raises it.

Agent from cwd now uses the **longest** matching rule on a folder boundary, the centralino's rule, instead of the first one.

## Deleghe aperte (v3, BR24-F6.4)

```
Deleghe aperte · 2                                                         [-]
──────────────────────────────────────────────────────────────────────────────
1: ● tocca a Mario    ON24-F3   Hairline nella schermata vuota   kai/onda-electron  10 h
2: ○ Kai non aperto   AM18-F15  Analisi notturna del 24/10       kai/alita-mobile    3 min
──────────────────────────────────────────────────────────────────────────────
✉ 1 esito da leggere   ? 1 domanda per te        1-2 o clic: apri · clic sul codice: piano in Orbit
```

On the model of the Orbit pane: a title, rules, one aligned line per delegation, counts and keys at the bottom.

- Source: the waits of this session in the centralino's vista (the files of `$AB_HOME/waits/<agent>/@<slug>/` when it is not there). Open ones, plus expired ones for a day.
- **One line per delegation, always**: newlines and runs of spaces become one space, the title is cut with `…` at the band's width (`bodyColumns`). The executor column goes when the band is under 80 columns.
- **Who holds the ball**, in a word and a glyph, never colour alone:
  - `? domanda per te`: the executor asked something and nobody answered;
  - `✉ esito da leggere`: a return is in the box with no outcome;
  - `! scaduta`: the deadline passed;
  - `● tocca a Mario`: the last return was handled with `riarma` and in Orbit the linked step is Mario's, or Mario has an open step in the same phase;
  - `⧗ in coda: Kai occupato`: the executor's session is alive and working, but has not read the delegation yet (centralino `letto_at`, BR24-F6.6): it is on something else;
  - `◐ Kai ci lavora`, `◌ Kai fermo`: the executor's session is alive, working (and has read the delegation) or not (centralino `sessioni`, else Onda's `sessions.json`);
  - `✓ Kai ha consegnato`: the last return was re-armed, the executor is not working and Orbit names no step of Mario's: the executor is done for now, the ball is outside it;
  - `○ Kai non aperto`: no live session of the executor; `○ Kai non aperto qui · vivo in onda-electron` (`+1`, `+2` for more) when the agent is alive in other folders on the same machine.
  Rows in this order, then by age.
- **Plan code** from the intent or the summary (`ON24-F3`, `am18-f15`, `BR24-F6.4`, `wh17-f8.3b`; any case). The title is the step's (or phase's) in Orbit, first clause only, read with one `sqlite3 -readonly` query for all the waits and kept a minute; without Orbit (the mini, no code) the first sentence of the summary, without the intent and Alita's opening `Piano ON24, fase F3, nuova:`.
- **Keys and clicks**: ctrl+x tab gives the band the keys; `1`-`9` or a click on a row puts `/read <msg>` in the prompt box (the open question, the first unread return, else the delegation); a click on the code puts `/orbit <plan>`. A draft already in the box is never overwritten: a toast says what to type. The engine's `[-]` (ctrl+x ctrl+a) collapses the band.
- A wake held back by a cap (`tetto_task`, `tetto_coppia`, `tetto_orario`) is said in words at the end of the title. A toast once when a wait expires.

## Squadra (v2.2)

```
Bus kai/portineria: ...                    (messages, as above)
Squadra: 3 vive, 1 ferma
!! kai/portineria   TESTO FERMO da 12 min   [Rimetti nel mosaico]
   leo/pm-os        aspetta un permesso da 3 min
   nico/brandart    lavora (Bash)   fuori dal mosaico   [Rimetti]
```

- Source: `$AB_HOME/onda/sessions.json`, written by [Onda](https://github.com/mariomosca) (only Onda sees the terminal buffer, so only it knows who is stuck). Contract: [`schema/CONTRACT.md`](../../schema/CONTRACT.md) "Onda → mod", schema `schema/onda-sessions.schema.json`.
- Read in the same poll (20 s and end of turn). The section is **absent** if the file is missing, unreadable, of an unknown version, or if `ondaPid` is not alive (`kill -0`). The current session is left out (same cwd, or same agent and slug).
- Order: stuck first (`TESTO FERMO` = text in the prompt box with no Enter, waiting for a permission, waiting for an answer), then by time in state. State is always in words, never only colour. At most 4 rows, then `+N`.
- **Toast** once per episode when a sibling session becomes stuck (not for sessions already stuck when this one starts: they are in the band).
- `/bus-band` hides/shows the whole band; `/bus-band squadra` only the team section.
- **Delegated sessions**: with `SESSIONE_DELEGATA=1` in the session's environment (a session another agent opened through Onda for a task) the team section is never drawn and raises no toasts. The messages for that session stay.

### The button

Shown only when this session runs inside Onda (`ONDA_SOCKET` set). What the mod engine allows: `$.fs` reads and writes, `$.process.run` runs a command by argv, `$.http.fetch` can use a Unix socket **but speaks HTTP**, while Onda's socket is JSON-RPC, so the mod cannot call `window.mountWorkspace` directly. The button therefore writes `$AB_HOME/onda/requests/req-<ts>-<rand>.json` (`{"action":"mount","workspaceId":"…"}`): temp file, then `mv` through `$.process.run` (`$.fs` has no rename), so Onda never reads half a file. Onda consumes and deletes it. If Onda is not watching the folder, the request just sits there.

## Install

The mod is its own plugin, separate from the main `agent-team-os` plugin, so a manual install (hooks registered in `settings.json`) never gets its hooks registered twice.

**With `install.sh`** (manual install): the installer copies it to `~/.claude/mods/bus-band/`. Load it in every session by adding the folder to `CLAUDE_CODE_PLUGIN_DIRS` in the `env` block of `~/.claude/settings.json`:

```json
{ "env": { "CLAUDE_CODE_PLUGIN_DIRS": "~/.claude/mods/bus-band" } }
```

**One session only**: `claude --plugin-dir /path/to/agent-team-os/mods/bus-band`.

## Security

Mods are not sandboxed: they run with Claude Code's own access. This one reads `~/.agent-team-os/` (`AB_HOME`) and `ONDA_SOCKET`, makes no network call, and runs only `nc -U` on the centralino socket (read-only `vista`), `sqlite3 -readonly` on Orbit's DB (titles and assignees of the plan codes), `kill -0 <pid>` (is Onda alive), `mkdir -p` and `mv` inside `onda/requests/` (the button). It writes only there, and the prompt box when a row is opened. Check with `claude plugin validate mods/bus-band`, which lists every engine call and env variable the module touches.

## Develop

```bash
claude plugin validate mods/bus-band   # manifest, hooks, calls, state contract
claude plugin test mods/bus-band       # hooks/bus.test.ts, hooks/team.test.ts, hooks/waits.test.ts and hooks/register.test.tsx (band with and without SESSIONE_DELEGATA): cwd→agent, slug, scoping, sort, sessions.json parse, team order, self exclusion
```

The pure bus logic lives in `hooks/bus.ts` (mirrors `scripts/agent-team-os-lib.sh`); `hooks/register.tsx` holds the hooks and the drawing; `hooks/team.ts` is the pure team logic. Known gap: the shell lib also matches the `realpath` of the cwd; the mod matches the cwd Claude Code reports, which is already resolved in practice.
