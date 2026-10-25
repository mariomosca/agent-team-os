import { atom, read, update } from 'claude-code'
import type { EngineInterface, Register } from 'claude-code'

import type { BusMessage, BusSnapshot, TeamSnapshot, WaitRow, WaitsSnapshot } from '../types'
import {
  PRIORITY_LABEL,
  agentForCwd,
  isPressing,
  parseMessage,
  slugForCwd,
  sortMessages,
  todoHeadline,
  todoRows,
  workspaceMatch,
} from './bus'
import {
  exceptionRows,
  isSelf,
  orderTeam,
  parseSessions,
  requestBody,
  requestName,
  stuckKeys,
  teamHeadline,
  toastText,
} from './team'
import { orderRows, orderWaits, parseWait, toDelegationRow, waitsFooter, waitsHeadline } from './waits'
import type { Wait } from './waits'
import { fallbackText, inboxOf, liveCountOf, noChannelOf, parseVista, sessionsOf, vistaRequest, waitsOf } from './vista'
import type { Vista } from './vista'

const snapshot = atom({ plugin: 'bus-band', key: 'snapshot' } as const, null)
const isHidden = atom({ plugin: 'bus-band', key: 'isHidden' } as const, false)

const team = atom({ plugin: 'bus-band', key: 'team' } as const, null)
const isTeamHidden = atom({ plugin: 'bus-band', key: 'isTeamHidden' } as const, false)

const waits = atom({ plugin: 'bus-band', key: 'waits' } as const, null)

// BR24-F4.3: why this round read the files instead of the centralino (null when it read the centralino).
const fallback = atom({ plugin: 'bus-band', key: 'fallback' } as const, null)

const POLL_MS = 20_000
const VISTA_TIMEOUT_MS = 2_000
const TODO_SHOWN = 5
const TEAM_SHOWN = 4
const WAITS_SHOWN = 5

const busRoot = async ($: EngineInterface) => {
  const busHome = await $.env.get('AB_HOME')
  const home = await $.env.get('HOME')
  if (busHome === undefined && home === undefined) return null

  return busHome ?? `${home}/.agent-team-os`
}

// The bus centralino's vista (BR24-F4.3), one call per round for the three sections.
// Null with the reason when the daemon is not there or does not answer: the round
// reads today's files instead and the band says so.
type Source = { vista: Vista } | { vista: null; why: string }

const loadVista = async ($: EngineInterface): Promise<Source> => {
  const root = await busRoot($)
  if (root === null) return { vista: null, why: 'nessuna cartella del bus' }
  const sock = (await $.env.get('AB_CENTRALINO_SOCK')) ?? `${root}/centralino.sock`
  if (!(await $.fs.exists(sock))) return { vista: null, why: 'il centralino del bus non è attivo' }
  const id = `r-band-${Math.random().toString(36).slice(2, 8)}`
  const out = await $.process
    .run(['nc', '-U', sock], { stdin: vistaRequest(id), timeoutMs: VISTA_TIMEOUT_MS })
    .catch(() => null)
  if (out === null || out.exitCode !== 0) return { vista: null, why: 'il centralino del bus non risponde' }
  const parsed = parseVista(out.stdout)

  return 'vista' in parsed ? { vista: parsed.vista } : { vista: null, why: parsed.error }
}

// The team as Onda sees it. Null (section absent) when the file is missing,
// unusable, or written by an Onda that is no longer alive, and in a session another
// agent opened for a delegated task (SESSIONE_DELEGATA=1): there it is only noise.
const scanTeam = async ($: EngineInterface, vista: Vista | null): Promise<TeamSnapshot | null> => {
  if ((await $.env.get('SESSIONE_DELEGATA')) === '1') return null
  const root = await busRoot($)
  if (root === null) return null

  // The centralino already checked that Onda runs and the file is fresh.
  let parsed = vista !== null ? sessionsOf(vista) : null
  if (vista === null) {
    const file = `${root}/onda/sessions.json`
    if (!(await $.fs.exists(file))) return null
    parsed = parseSessions(await $.fs.read(file))
    if (parsed === null) return null
    const alive = await $.process.run(['kill', '-0', String(parsed.ondaPid)])
    if (alive.exitCode !== 0) return null
  }
  if (parsed === null) return null

  const cwd = await $.session.cwd()
  const map = JSON.parse(await $.fs.read(`${root}/AGENT_MAP.json`))
  const agent = agentForCwd(map.rules ?? [], cwd) ?? ''
  const slug = slugForCwd(cwd)
  const canMount = (await $.env.get('ONDA_SOCKET')) !== undefined
  const now = Date.now()
  const others = orderTeam(parsed.sessions, s => isSelf(s, cwd, agent, slug))
  // BR24-F6.7: the live count and the sessions without a channel come from the
  // centralino (it sees the sessions outside Onda too); without it, Onda's list.
  const live = Math.max(vista !== null ? liveCountOf(vista, agent, slug) : 0, others.length)
  if (live === 0 && others.length === 0) return null
  const rows = exceptionRows(others, vista !== null ? noChannelOf(vista, agent, slug) : [], now, canMount)

  return {
    headline: teamHeadline(live, rows.length),
    rows: rows.slice(0, TEAM_SHOWN),
    hidden: Math.max(0, rows.length - TEAM_SHOWN),
    sessions: others,
  }
}

// The results this session waits for (bus v2.6). Null when there are none.
const scanWaits = async ($: EngineInterface, vista: Vista | null): Promise<WaitsSnapshot | null> => {
  const root = await busRoot($)
  if (root === null) return null
  const cwd = await $.session.cwd()
  const map = JSON.parse(await $.fs.read(`${root}/AGENT_MAP.json`))
  const agent = agentForCwd(map.rules ?? [], cwd)
  if (agent === undefined) return null

  const texts: string[] = []
  if (vista !== null) {
    texts.push(...waitsOf(vista, agent, slugForCwd(cwd)))
  } else {
    const dir = `${root}/waits/${agent}/@${slugForCwd(cwd)}`
    if (!(await $.fs.exists(dir))) return null
    for (const entry of await $.fs.list(dir)) {
      if (entry.kind !== 'file' || !/^[^.].*\.json$/.test(entry.name)) continue
      texts.push(await $.fs.read(`${dir}/${entry.name}`))
    }
  }
  const found: Wait[] = []
  for (const text of texts) {
    const w = parseWait(text)
    if (w !== null) found.push(w)
  }
  const now = Date.now()
  const ordered = orderWaits(found, now)
  if (ordered.length === 0) return null

  const rows = orderRows(ordered.map(w => toDelegationRow(w, now)))

  return {
    headline: waitsHeadline(ordered),
    rows: rows.slice(0, WAITS_SHOWN),
    hidden: Math.max(0, rows.length - WAITS_SHOWN),
    footer: waitsFooter(rows),
    expired: ordered.filter(w => w.state === 'expired').map(w => w.taskId),
  }
}

// A row opens in the prompt box, for the person to send: `/read <msg>` or
// `/orbit <plan>`. A draft already there is never overwritten.
const openInPrompt = async ($: EngineInterface, text: string) => {
  const box = await $.prompt.read().catch(() => null)
  if (box !== null && box.text.trim() !== '') {
    $.ui.toast(`La casella ha già del testo: ${text}`)

    return
  }
  await $.prompt.fill({ text, mode: 'replace' }).catch(() => $.ui.toast(text))
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

const scan = async ($: EngineInterface, vista: Vista | null): Promise<BusSnapshot | null> => {
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
  const found: [string, string][] = []
  if (vista !== null) {
    found.push(...inboxOf(vista, agent, slug))
  } else {
    for (const dir of [`${root}/inboxes/${agent}`, `${root}/inboxes/${agent}/@${slug}`]) {
      if (!(await $.fs.exists(dir))) continue
      for (const entry of await $.fs.list(dir)) {
        if (entry.kind !== 'file' || !/^msg-.*\.json$/.test(entry.name)) continue
        found.push([await $.fs.read(`${dir}/${entry.name}`), entry.name])
      }
    }
  }
  const messages: BusMessage[] = []
  let elsewhere = 0
  for (const [text, name] of found) {
    const parsed = parseMessage(text, name)
    if (parsed === null) continue
    if (workspaceMatch(parsed.projectPath, cwd) === 'elsewhere') {
      elsewhere += 1
    } else {
      messages.push(parsed.message)
    }
  }

  const sorted = sortMessages(messages)

  return { agent, slug, messages: sorted, rows: todoRows(sorted, Date.now()), elsewhere }
}

// Ids already seen, so only a message that arrives later raises a toast.
let seen: Set<string> | null = null

// Stuck episodes already announced (session:state), so a toast fires once per episode.
let seenStuck: Set<string> | null = null

// Expired waits already announced, so the deadline toasts once.
let seenExpired: Set<string> | null = null

async function refreshWaits($: EngineInterface, vista: Vista | null) {
  const next = await scanWaits($, vista).catch(() => null)
  const expired = next?.expired ?? []
  const fresh = seenExpired === null ? [] : expired.filter(id => !seenExpired!.has(id))
  seenExpired = new Set(expired)

  await update($, waits, () => next)
  if (fresh.length > 0) $.ui.toast(`Bus: nessun esito entro la scadenza per ${fresh.join(', ')}`)
}

async function refreshTeam($: EngineInterface, vista: Vista | null) {
  const next = await scanTeam($, vista).catch(() => null)
  const keys = stuckKeys(next?.sessions ?? [])
  const fresh = seenStuck === null ? [] : [...keys].filter(([, episode]) => !seenStuck!.has(episode))
  seenStuck = new Set(keys.values())

  await update($, team, () => next)
  const s = next?.sessions.find(x => fresh.some(([id]) => id === x.sessionId))
  if (s !== undefined) $.ui.toast(toastText(s))
}

async function refresh($: EngineInterface) {
  const source = await loadVista($).catch(() => ({ vista: null, why: 'il centralino del bus non risponde' }))
  await update($, fallback, () => (source.vista === null ? fallbackText(source.why) : null))
  await refreshTeam($, source.vista)
  await refreshWaits($, source.vista)
  const next = await scan($, source.vista)
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
    const showMessages = snap !== null && snap.rows.length > 0
    const attese = await read($, waits)
    const showWaits = attese !== null
    const isQuiet =
      e.props.hasSurvey || (!showMessages && !showTeam && !showWaits) || (await read($, isHidden))

    if (isQuiet) return next(e)

    const { Box, Button, Text } = $.ui.resolve(e)
    // Only inside a band already drawn: a missing centralino alone never raises it.
    const viaFile = await read($, fallback)

    return (
      <Box flexDirection="column">
        {showMessages ? todoBlock($, snap, e.props.bodyColumns, Box, Button, Text) : null}
        {showWaits
          ? waitsBlock($, attese, e.props.bodyColumns, showMessages ? Math.min(TODO_SHOWN, snap.rows.length) : 0, Box, Button, Text)
          : null}
        {showTeam ? teamBlock($, squadra, Box, Button, Text) : null}
        {viaFile !== null ? <Text dimColor>{viaFile}</Text> : null}
      </Box>
    )
  })
}

type Els = ReturnType<EngineInterface['ui']['resolve']>

// BR24-F6.8, like the delegations: what this session has to do, one aligned line
// per message (state in glyph and word, plan code, title, sender, age). A digit or a
// click puts `/read <msg>` in the prompt; a click on the code puts `/orbit <plan>`.
// The digits go on in "Deleghe aperte" below, so a key is never taken twice.
const todoBlock = (
  $: EngineInterface,
  snap: BusSnapshot,
  columns: number | undefined,
  Box: Els['Box'],
  Button: Els['Button'],
  Text: Els['Text'],
) => {
  const width = Math.max(40, columns ?? 100)
  const rows = snap.rows.slice(0, TODO_SHOWN)
  const rest = snap.rows.length - rows.length
  const labelW = Math.max(...rows.map(r => cells(r.label)))
  const stateW = labelW + 4
  const codeW = Math.max(0, ...rows.map(r => cells(r.code ?? '')))
  const sinceW = Math.max(...rows.map(r => cells(r.since)))
  const fromW = width >= 80 ? Math.min(24, Math.max(...rows.map(r => cells(r.from)))) : 0
  const gaps = 2 + (codeW > 0 ? 2 : 0) + (fromW > 0 ? 2 : 0) + 1
  const titleW = Math.max(8, width - stateW - codeW - fromW - sinceW - gaps)

  return (
    <Box flexDirection="column">
      <Text bold>{todoHeadline(snap.rows, snap.elsewhere)}</Text>
      {rows.map((r, i) => {
        const open = () => void openInPrompt($, r.read)
        const tone = TODO_COLOR[r.state]

        return (
          <Box key={r.key}>
            <Button key={`read-${r.key}`} plain {...(i < 9 ? { hotkey: String(i + 1) } : {})} onPress={open}>
              <Text bold={r.pressing || r.state === 'nuova' || r.state === 'risposta'} {...(tone !== undefined ? { color: tone } : { dimColor: true })}>
                {r.glyph} {pad(r.label, labelW)}{'  '}
              </Text>
            </Button>
            {codeW > 0 ? (
              r.plan !== null ? (
                <Button key={`plan-${r.key}`} plain onPress={() => void openInPrompt($, `/orbit ${r.plan}`)}>
                  {pad(r.code ?? '', codeW)}
                </Button>
              ) : (
                <Text>{' '.repeat(codeW)}</Text>
              )
            ) : null}
            <Button key={`title-${r.key}`} plain onPress={open}>
              {codeW > 0 ? '  ' : ''}
              {pad(r.title, titleW)}
              {fromW > 0 ? <Text dimColor>{`  ${pad(r.from, fromW)}`}</Text> : null}
              <Text dimColor>{` ${r.since.padStart(sinceW)}`}</Text>
            </Button>
          </Box>
        )
      })}
      {rest > 0 ? <Text dimColor>{`  +${rest}, /inbox per la lista`}</Text> : null}
    </Box>
  )
}

// Cells a string takes: one per code point (the band draws no wide glyphs).
const cells = (text: string) => Array.from(text).length

// `text` cut to `width` cells, `…` last when cut.
const fit = (text: string, width: number) => {
  if (width <= 0) return ''
  const chars = Array.from(text)

  return chars.length <= width ? text : `${chars.slice(0, width - 1).join('')}…`
}

const pad = (text: string, width: number) => {
  const cut = fit(text, width)

  return cut + ' '.repeat(Math.max(0, width - cells(cut)))
}

const TODO_COLOR: Partial<Record<string, string>> = {
  nuova: 'warning',
  risposta: 'warning',
  lavori: 'success',
}

const STATE_COLOR: Partial<Record<WaitRow['state'], string>> = {
  domanda: 'warning',
  esito: 'warning',
  scaduta: 'error',
  mario: 'claude',
  lavora: 'success',
}

// BR24-F6.4, on the model of the Orbit pane: a title, rules, one aligned line per
// delegation (state in words and glyph, plan code, title, executor, age), counts
// and keys at the bottom. A digit or a click on a row puts `/read <msg>` in the
// prompt; a click on the code puts `/orbit <plan>`.
const waitsBlock = (
  $: EngineInterface,
  attese: WaitsSnapshot,
  columns: number | undefined,
  first: number,
  Box: Els['Box'],
  Button: Els['Button'],
  Text: Els['Text'],
) => {
  const width = Math.max(40, columns ?? 100)
  const rows = attese.rows
  const labelW = Math.max(...rows.map(r => cells(r.label)))
  // Glyph, space, label, two spaces.
  const stateW = labelW + 4
  const codeW = Math.max(0, ...rows.map(r => cells(r.code ?? '')))
  const sinceW = Math.max(...rows.map(r => cells(r.since)))
  // The executor column goes first when the band is narrow.
  const whoW = width >= 80 ? Math.min(24, Math.max(...rows.map(r => cells(r.who)))) : 0
  const gaps = 2 + (codeW > 0 ? 2 : 0) + (whoW > 0 ? 2 : 0) + 1
  const titleW = Math.max(8, width - stateW - codeW - whoW - sinceW - gaps)
  const rule = <Text color="subtle">{'─'.repeat(width)}</Text>
  const keys = Math.max(0, Math.min(9, first + rows.length) - first)
  const hint = `${keys > 1 ? `${first + 1}-${first + keys}` : keys === 1 ? `${first + 1}` : ''}${keys > 0 ? ' o ' : ''}clic: apri · clic sul codice: piano in Orbit`

  return (
    <Box flexDirection="column">
      <Text bold>{attese.headline}</Text>
      {rule}
      {rows.map((r, i) => {
        const open = () => (r.read !== null ? void openInPrompt($, r.read) : undefined)
        const tone = STATE_COLOR[r.state]

        return (
          <Box key={r.key}>
            <Button key={`read-${r.key}`} plain {...(first + i < 9 ? { hotkey: String(first + i + 1) } : {})} onPress={open}>
              <Text bold={r.alert || r.state === 'mario'} {...(tone !== undefined ? { color: tone } : { dimColor: true })}>
                {r.glyph} {pad(r.label, labelW)}{'  '}
              </Text>
            </Button>
            {codeW > 0 ? (
              r.plan !== null ? (
                <Button key={`plan-${r.key}`} plain onPress={() => void openInPrompt($, `/orbit ${r.plan}`)}>
                  {pad(r.code ?? '', codeW)}
                </Button>
              ) : (
                <Text>{' '.repeat(codeW)}</Text>
              )
            ) : null}
            <Button key={`title-${r.key}`} plain onPress={open}>
              {codeW > 0 ? '  ' : ''}
              {pad(r.note === null ? r.title : `${r.title} (${r.note})`, titleW)}
              {whoW > 0 ? <Text dimColor>{`  ${pad(r.who, whoW)}`}</Text> : null}
              <Text dimColor>{` ${r.since.padStart(sinceW)}`}</Text>
            </Button>
          </Box>
        )
      })}
      {attese.hidden > 0 ? <Text dimColor>{`  +${attese.hidden}, bus_inbox per la lista`}</Text> : null}
      {rule}
      <Box justifyContent="space-between">
        <Text bold>{attese.footer}</Text>
        <Text dimColor>{fit(hint, Math.max(0, width - cells(attese.footer) - 2))}</Text>
      </Box>
    </Box>
  )
}

// BR24-F6.7: only what needs Mario. One line when nothing does; otherwise one
// aligned line per session, glyph and word first (never colour alone), the pane
// button on the right.
const teamBlock = (
  $: EngineInterface,
  squadra: TeamSnapshot,
  Box: Els['Box'],
  Button: Els['Button'],
  Text: Els['Text'],
) => {
  const labelW = Math.max(0, ...squadra.rows.map(r => cells(r.label)))
  const nameW = Math.max(0, ...squadra.rows.map(r => cells(r.name)))
  const sinceW = Math.max(0, ...squadra.rows.map(r => cells(r.since)))

  return (
    <Box flexDirection="column">
      <Text bold>{squadra.headline}</Text>
      {squadra.rows.map(r => (
        <Box key={r.key}>
          <Text bold color="warning" wrap="truncate-end">
            {r.glyph} {pad(r.label, labelW)}
          </Text>
          <Text wrap="truncate-end">
            {'  '}
            {pad(r.name, nameW)}
          </Text>
          <Text dimColor>
            {`  ${r.since === '' ? '' : `da ${r.since}`.padEnd(sinceW + 3)}`}
            {r.unmounted ? '  fuori dal mosaico' : ''}{' '}
          </Text>
          {r.canMount && r.workspaceId !== null ? (
            <Button
              key={`mount-${r.key}`}
              label={r.unmounted ? 'Rimetti nel mosaico' : 'Vai al pannello'}
              onPress={() => requestMount($, r.workspaceId!)}
            />
          ) : null}
        </Box>
      ))}
      {squadra.hidden > 0 ? <Text dimColor>  +{squadra.hidden}</Text> : null}
    </Box>
  )
}
