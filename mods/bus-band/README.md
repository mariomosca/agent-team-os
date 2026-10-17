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

## Aspetto (v2.3)

```
Aspetto 2 esiti, 1 scaduto
!! SCADUTO kai/f3: spike orbit
   nico/b1: refactor login, da 12 min
   leo: report licenze, esito arrivato, sveglia rimandata (lavori)
```

- Source: `$AB_HOME/waits/<agent>/@<slug>/*.json`, the results this session waits for (bus v2.6, README "La sveglia"). Expected ones, plus expired ones for a day.
- A postponed wake says why, in words. `SCADUTO` is spelled out. A toast once when a wait expires.

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

Mods are not sandboxed: they run with Claude Code's own access. This one reads `~/.agent-team-os/` (`AB_HOME`) and `ONDA_SOCKET`, makes no network call, and runs only `kill -0 <pid>` (is Onda alive), `mkdir -p` and `mv` inside `onda/requests/` (the button). It writes only there. Check with `claude plugin validate mods/bus-band`, which lists every engine call and env variable the module touches.

## Develop

```bash
claude plugin validate mods/bus-band   # manifest, hooks, calls, state contract
claude plugin test mods/bus-band       # hooks/bus.test.ts, hooks/team.test.ts and hooks/waits.test.ts: cwd→agent, slug, scoping, sort, sessions.json parse, team order, self exclusion
```

The pure bus logic lives in `hooks/bus.ts` (mirrors `scripts/agent-team-os-lib.sh`); `hooks/register.tsx` holds the hooks and the drawing; `hooks/team.ts` is the pure team logic. Known gap: the shell lib also matches the `realpath` of the cwd; the mod matches the cwd Claude Code reports, which is already resolved in practice.
