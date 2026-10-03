import { atom, read, update } from 'claude-code'
import type { EngineInterface, Register } from 'claude-code'

import type { BusMessage, BusSnapshot } from '../types'
import {
  PRIORITY_LABEL,
  agentForCwd,
  isPressing,
  parseMessage,
  slugForCwd,
  sortMessages,
  workspaceMatch,
} from './bus'

const snapshot = atom({ plugin: 'bus-band', key: 'snapshot' } as const, null)
const isHidden = atom({ plugin: 'bus-band', key: 'isHidden' } as const, false)

const POLL_MS = 20_000
const SHOWN = 3

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

async function refresh($: EngineInterface) {
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
      description: 'Mostra o nasconde la banda dei messaggi del bus',
    })
    await refresh($).catch(() => {})
    $.clock.every(POLL_MS, () => refresh($).catch(() => {}))

    return next(e)
  })

  on('turn.complete', async ($, e, next) => {
    await refresh($).catch(() => {})

    return next(e)
  })

  on('command.run', { command: 'bus-band' }, async $ => {
    const hidden = await read($, isHidden)
    await update($, isHidden, () => !hidden)
    await refresh($).catch(() => {})

    return { text: hidden ? 'Banda del bus visibile.' : 'Banda del bus nascosta.' }
  })

  on('ui.render', { component: 'AbovePrompt' }, async ($, e, next) => {
    const snap = await read($, snapshot)
    const isQuiet =
      e.props.hasSurvey || snap === null || snap.messages.length === 0 || (await read($, isHidden))

    if (isQuiet) return next(e)

    const { Box, Button, Text } = $.ui.resolve(e)
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
  })
}
