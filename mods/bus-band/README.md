# bus-band

A [Claude Code mod](https://code.claude.com/docs/en/plugins/mods/overview) (Claude Code 2.1.287+) that draws a band above the prompt with the messages waiting for **this session** on the Agent Team OS bus.

```
Bus alita/work-hub: 2 in attesa, 1 urgenti o alta (+1 di altri workspace)  [Nascondi]
!! [ALTA] da kai: crewmoor-stato-piano-v1
   [normale] da vera: content-brief
```

- UI text is in Italian (the agents' working language). Shows only when something is pending; disappears when the inbox is empty.
- Same scoping as the shell lib (v1.4): agent from `AGENT_MAP.json` by cwd prefix, slug = workspace basename, shared root `inboxes/<agent>/` plus `inboxes/<agent>/@<slug>/`, messages whose `context.project_path` belongs to another workspace counted apart.
- Priority is spelled out (`URGENTE`, `ALTA`, a `!!` marker), never colour alone.
- A toast when a new `urgent`/`high` message arrives. Polls every 20 s and at the end of every turn.
- `/bus-band` toggles it; the **Nascondi** button hides it until the next new message.
- Read-only: it never moves, archives or writes a message. `/read`, `/inbox` and the MCP tools stay the way to act on them.
- Honours `AB_HOME` like the shell lib (default `~/.agent-team-os`).

## Install

The mod is its own plugin, separate from the main `agent-team-os` plugin, so a manual install (hooks registered in `settings.json`) never gets its hooks registered twice.

**With `install.sh`** (manual install): the installer copies it to `~/.claude/mods/bus-band/`. Load it in every session by adding the folder to `CLAUDE_CODE_PLUGIN_DIRS` in the `env` block of `~/.claude/settings.json`:

```json
{ "env": { "CLAUDE_CODE_PLUGIN_DIRS": "~/.claude/mods/bus-band" } }
```

**One session only**: `claude --plugin-dir /path/to/agent-team-os/mods/bus-band`.

## Security

Mods are not sandboxed: they run with Claude Code's own access. This one reads `~/.agent-team-os/` and `$HOME`/`AB_HOME`, nothing else, and makes no network call. Check with `claude plugin validate mods/bus-band`, which lists every engine call and env variable the module touches.

## Develop

```bash
claude plugin validate mods/bus-band   # manifest, hooks, calls, state contract
claude plugin test mods/bus-band       # hooks/bus.test.ts: cwd→agent, slug, scoping, sort
```

The pure bus logic lives in `hooks/bus.ts` (mirrors `scripts/agent-team-os-lib.sh`); `hooks/register.tsx` holds the hooks and the drawing. Known gap: the shell lib also matches the `realpath` of the cwd; the mod matches the cwd Claude Code reports, which is already resolved in practice.
