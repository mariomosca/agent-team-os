import { atom, read, update } from 'claude-code'
import type { EngineInterface, Register } from 'claude-code'

import type { BusMessage, BusSnapshot, TeamSnapshot, WaitsSnapshot } from '../types'
import {
  PRIORITY_LABEL,
  agentForCwd,
  isPressing,
  parseMessage,
  slugForCwd,
  sortMessages,
  workspaceMatch,
} from './bus'
import {
  isSelf,
  orderTeam,
  parseSessions,
  requestBody,
  requestName,
  stuckKeys,
  teamHeadline,
  toRow,
  toastText,
} from './team'
import { orderWaits, parseWait, toWaitRow, waitsHeadline } from './waits'
import type { Wait } from './waits'

const snapshot = atom({ plugin: 'bus-band', key: 'snapshot' } as const, null)
const isHidden = atom({ plugin: 'bus-band', key: 'isHidden' } as const, false)

const team = atom({ plugin: 'bus-band', key: 'team' } as const, null)
const isTeamHidden = atom({ plugin: 'bus-band', key: 'isTeamHidden' } as const, false)

const waits = atom({ plugin: 'bus-band', key: 'waits' } as const, null)

const POLL_MS = 20_000
const SHOWN = 3
const TEAM_SHOWN = 4
const WAITS_SHOWN = 3

const busRoot = async ($: EngineInterface) => {
  const busHome = await $.env.get('AB_HOME')
  const home = await $.env.get('HOME')
  if (busHome === undefined && home === undefined) return null

  return busHome ?? `${home}/.agent-team-os`
}

// The team as Onda sees it. Null (section absent) when the file is missing,
// unusable, or written by an Onda that is no longer alive, and in a session another
// agent opened for a delegated task (SESSIONE_DELEGATA=1): there it is only noise.
const scanTeam = async ($: EngineInterface): Promise<TeamSnapshot | null> => {
  if ((await $.env.get('SESSIONE_DELEGATA')) === '1') return null
  const root = await busRoot($)
  if (root === null) return null
  const file = `${root}/onda/sessions.json`
  if (!(await $.fs.exists(file))) return null

  const parsed = parseSessions(await $.fs.read(file))
  if (parsed === null) return null
  const alive = await $.process.run(['kill', '-0', String(parsed.ondaPid)])
  if (alive.exitCode !== 0) return null

  const cwd = await $.session.cwd()
  const map = JSON.parse(await $.fs.read(`${root}/AGENT_MAP.json`))
  const agent = agentForCwd(map.rules ?? [], cwd) ?? ''
  const slug = slugForCwd(cwd)
  const canMount = (await $.env.get('ONDA_SOCKET')) !== undefined
  const now = Date.now()
  const others = orderTeam(parsed.sessions, s => isSelf(s, cwd, agent, slug))
  if (others.length === 0) return null

  const rows = others.map(s => toRow(s, now, canMount))

  return {
    headline: teamHeadline(rows),
    rows: rows.slice(0, TEAM_SHOWN),
    hidden: Math.max(0, rows.length - TEAM_SHOWN),
    sessions: others,
  }
}

// The results this session waits for (bus v2.6). Null when there are none.
const scanWaits = async ($: EngineInterface): Promise<WaitsSnapshot | null> => {
  const root = await busRoot($)
  if (root === null) return null
  const cwd = await $.session.cwd()
  const map = JSON.parse(await $.fs.read(`${root}/AGENT_MAP.json`))
  const agent = agentForCwd(map.rules ?? [], cwd)
  if (agent === undefined) return null
  const dir = `${root}/waits/${agent}/@${slugForCwd(cwd)}`
  if (!(await $.fs.exists(dir))) return null

  const found: Wait[] = []
  for (const entry of await $.fs.list(dir)) {
    if (entry.kind !== 'file' || !/^[^.].*\.json$/.test(entry.name)) continue
    const w = parseWait(await $.fs.read(`${dir}/${entry.name}`))
    if (w !== null) found.push(w)
  }
  const now = Date.now()
  const ordered = orderWaits(found, now)
  if (ordered.length === 0) return null

  return {
    headline: waitsHeadline(ordered),
    rows: ordered.slice(0, WAITS_SHOWN).map(w => toWaitRow(w, now)),
    hidden: Math.max(0, ordered.length - WAITS_SHOWN),
    expired: ordered.filter(w => w.state === 'expired').map(w => w.taskId),
  }
}

// The mod cannot open Onda's socket (JSON-RPC; $.http speaks HTTP), so the
// request is a file Onda consumes. Temp name first, then mv: Onda never sees half a file.
const requestMount = async ($: EngineInterface, workspaceId: string) => {
  const root = await busRoot($)
  if (root === null) return
  const dir = `${root}/onda/requests`
  const rand = Math.random().toString(36).slice(2, 8)
  const tmp = `${dir}/.tmp-${rand}.json`

  await $.process.run(['mkdir', '-p', dir])
  await $.fs.write(tmp, requestBody(workspaceId))
  await $.process.run(['mv', tmp, `${dir}/${requestName(Date.now(), rand)}`])
  $.ui.toast('Richiesta inviata a Onda')
}

const scan = async ($: EngineInterface): Promise<BusSnapshot | null> => {
  // Same bus home as the shell lib: AB_HOME, else ~/.agent-team-os.
  const busHome = await $.env.get('AB_HOME')
  const home = await $.env.get('HOME')
  if (busHome === undefined && home === undefined) return null
  const root = busHome ?? `${home}/.agent-team-os`
  const cwd = await $.session.cwd()

  const map = JSON.parse(await $.fs.read(`${root}/AGENT_MAP.json`))
  const agent = agentForCwd(map.rules ?? [], cwd)
  if (agent === undefined) return null
  const slug = slugForCwd(cwd)

  // Shared root (messages to the bare agent name) plus this session's own dir.
  const dirs = [`${root}/inboxes/${agent}`, `${root}/inboxes/${agent}/@${slug}`]
  const messages: BusMessage[] = []
  let elsewhere = 0

  for (const dir of dirs) {
    if (!(await $.fs.exists(dir))) continue
    for (const entry of await $.fs.list(dir)) {
      if (entry.kind !== 'file' || !/^msg-.*\.json$/.test(entry.name)) continue
      const parsed = parseMessage(await $.fs.read(`${dir}/${entry.name}`), entry.name)
      if (parsed === null) continue
      if (workspaceMatch(parsed.projectPath, cwd) === 'elsewhere') {
        elsewhere += 1
      } else {
        messages.push(parsed.message)
      }
    }
  }

  return { agent, slug, messages: sortMessages(messages), elsewhere }
}

// Ids already seen, so only a message that arrives later raises a toast.
let seen: Set<string> | null = null

// Stuck episodes already announced (session:state), so a toast fires once per episode.
let seenStuck: Set<string> | null = null

// Expired waits already announced, so the deadline toasts once.
let seenExpired: Set<string> | null = null

async function refreshWaits($: EngineInterface) {
  const next = await scanWaits($).catch(() => null)
  const expired = next?.expired ?? []
  const fresh = seenExpired === null ? [] : expired.filter(id => !seenExpired!.has(id))
  seenExpired = new Set(expired)

  await update($, waits, () => next)
  if (fresh.length > 0) $.ui.toast(`Bus: nessun esito entro la scadenza per ${fresh.join(', ')}`)
}

async function refreshTeam($: EngineInterface) {
  const next = await scanTeam($).catch(() => null)
  const keys = stuckKeys(next?.sessions ?? [])
  const fresh = seenStuck === null ? [] : [...keys].filter(([, episode]) => !seenStuck!.has(episode))
  seenStuck = new Set(keys.values())

  await update($, team, () => next)
  const s = next?.sessions.find(x => fresh.some(([id]) => id === x.sessionId))
  if (s !== undefined) $.ui.toast(toastText(s))
}

async function refresh($: EngineInterface) {
  await refreshTeam($)
  await refreshWaits($)
  const next = await scan($)
  const ids = new Set(next?.messages.map(m => m.id) ?? [])
  const fresh = seen === null ? [] : (next?.messages ?? []).filter(m => !seen!.has(m.id))
  seen = ids

  await update($, snapshot, () => next)
  if (fresh.length > 0) await update($, isHidden, () => false)

  const m = fresh.find(isPressing)
  if (m !== undefined) {
    $.ui.toast(`Bus: ${PRIORITY_LABEL[m.priority]} da ${m.from} (${m.intent})`)
  }
}

export const register: Register = on => {
  on('session.start', async ($, e, next) => {
    await $.command.register({
      name: 'bus-band',
      description: 'Mostra o nasconde la banda del bus; "squadra" solo la sezione squadra',
      argumentHint: '[squadra]',
    })
    await refresh($).catch(() => {})
    $.clock.every(POLL_MS, () => refresh($).catch(() => {}))

    return next(e)
  })

  on('turn.complete', async ($, e, next) => {
    await refresh($).catch(() => {})

    return next(e)
  })

  on('command.run', { command: 'bus-band' }, async ($, e) => {
    if (e.args.trim() === 'squadra') {
      const teamHidden = await read($, isTeamHidden)
      await update($, isTeamHidden, () => !teamHidden)

      return { text: teamHidden ? 'Sezione squadra visibile.' : 'Sezione squadra nascosta.' }
    }

    const hidden = await read($, isHidden)
    await update($, isHidden, () => !hidden)
    await refresh($).catch(() => {})

    return { text: hidden ? 'Banda del bus visibile.' : 'Banda del bus nascosta.' }
  })

  on('ui.render', { component: 'AbovePrompt' }, async ($, e, next) => {
    const snap = await read($, snapshot)
    const squadra = await read($, team)
    const showTeam = squadra !== null && !(await read($, isTeamHidden))
    const showMessages = snap !== null && snap.messages.length > 0
    const attese = await read($, waits)
    const showWaits = attese !== null
    const isQuiet =
      e.props.hasSurvey || (!showMessages && !showTeam && !showWaits) || (await read($, isHidden))

    if (isQuiet) return next(e)

    const { Box, Button, Text } = $.ui.resolve(e)

    return (
      <Box flexDirection="column">
        {showMessages ? messagesBlock($, snap, Box, Button, Text) : null}
        {showWaits ? waitsBlock(attese, Box, Text) : null}
        {showTeam ? teamBlock($, squadra, Box, Button, Text) : null}
      </Box>
    )
  })
}

type Els = ReturnType<EngineInterface['ui']['resolve']>

const messagesBlock = (
  $: EngineInterface,
  snap: BusSnapshot,
  Box: Els['Box'],
  Button: Els['Button'],
  Text: Els['Text'],
) => {
  const pressing = snap.messages.filter(isPressing).length
  const rest = snap.messages.length - SHOWN

  return (
    <Box flexDirection="column">
      <Box>
        <Text bold>
          Bus {snap.agent}/{snap.slug}: {snap.messages.length} in attesa
          {pressing > 0 ? `, ${pressing} urgenti o alta` : ''}
          {snap.elsewhere > 0 ? ` (+${snap.elsewhere} di altri workspace)` : ''}{' '}
        </Text>
        <Button key="hide" label="Nascondi" onPress={() => update($, isHidden, () => true)} />
      </Box>
      {snap.messages.slice(0, SHOWN).map(m => (
        <Text key={m.id} dimColor={!isPressing(m)} wrap="truncate-end">
          {isPressing(m) ? '!! ' : '   '}[{PRIORITY_LABEL[m.priority]}] da {m.from}: {m.intent}
        </Text>
      ))}
      {rest > 0 ? <Text dimColor>   ...altri {rest}, /inbox per la lista</Text> : null}
    </Box>
  )
}

const waitsBlock = (attese: WaitsSnapshot, Box: Els['Box'], Text: Els['Text']) => (
  <Box flexDirection="column">
    <Text bold>{attese.headline}</Text>
    {attese.rows.map(r => (
      <Text key={r.key} dimColor={!r.alert} bold={r.alert} wrap="truncate-end">
        {r.alert ? '!! ' : '   '}
        {r.text}
      </Text>
    ))}
    {attese.hidden > 0 ? <Text dimColor>   +{attese.hidden}, bus_waits per la lista</Text> : null}
  </Box>
)

const teamBlock = (
  $: EngineInterface,
  squadra: TeamSnapshot,
  Box: Els['Box'],
  Button: Els['Button'],
  Text: Els['Text'],
) => (
  <Box flexDirection="column">
    <Text bold>{squadra.headline}</Text>
    {squadra.rows.map(r => (
      <Box key={r.key}>
        <Text dimColor={!r.stuck} bold={r.stuck} wrap="truncate-end">
          {r.stuck ? '!! ' : '   '}
          {r.name.padEnd(22)}
          {r.text}
          {r.unmounted ? '   fuori dal mosaico' : ''}{' '}
        </Text>
        {r.canMount ? (
          <Button
            key={`mount-${r.key}`}
            label={!r.unmounted ? 'Vai al pannello' : r.stuck ? 'Rimetti nel mosaico' : 'Rimetti'}
            onPress={() => requestMount($, r.workspaceId)}
          />
        ) : null}
      </Box>
    ))}
    {squadra.hidden > 0 ? <Text dimColor>   +{squadra.hidden}</Text> : null}
  </Box>
)
