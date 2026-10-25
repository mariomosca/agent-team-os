import type { WaitRow } from '../types'

// Pure logic of the "Aspetto" section: the results this session waits for,
// $AB_HOME/waits/<agent>/@<slug>/<task_id>.json (schema/bus-wait.schema.json,
// written by agent-bus-mcp and the lib, moved on by agent-team-os-wake.py). No `$` here.

// A question on the task, from whoever executes it, still without an answer
// (bus v2.7): the delegated session is waiting for this one.
export type Ask = {
  msgId: string
  from: string
  at: string
  summary: string
}

export type Wait = {
  taskId: string
  description: string
  delegatedTo: string | null
  createdAt: string
  deadline: string | null
  state: string
  expiredAt: string | null
  pendingReason: string | null
  asks: Ask[]
  // v2.8 (BS21): returns that woke this session (or are queued) and still have no
  // outcome from bus_gestisci. Questions are counted in asks, not here.
  unhandled: number
  // BR24-F6.4: the first of those returns (the one a click reads), the delegation
  // message, the session that executes it, and how the latest return was handled.
  firstUnhandled: string | null
  msgId: string | null
  executor: string | null
  lastHandled: string | null
  // BR24-F6.6: when the executor first read the delegation (centralino `letto_at`);
  // null: not yet; undefined: nobody says (a centralino before v4, the files).
  readAt: string | null | undefined
}

const str = (v: unknown) => (typeof v === 'string' && v !== '' ? v : null)

// A wait the band shows: still expected, or expired in the last day.
const SHOWN_STATES = new Set(['expected', 'expired'])
const EXPIRED_SHOWN_MS = 24 * 60 * 60_000

type Entry = Record<string, unknown>

const entries = (v: unknown): [string, Entry][] =>
  typeof v === 'object' && v !== null
    ? Object.entries(v as Record<string, Entry>).filter(([, e]) => typeof e === 'object' && e !== null)
    : []

// A message on the task that no outcome has consumed yet.
const OPEN_ENTRY = (e: Entry) => !['handled', 'seen', 'ignored'].includes(str(e.state) ?? '')

// Open questions: v2.7 `questions`, and since v2.8 the `returns` of type question.
const parseAsks = (questions: unknown, returns: unknown): Ask[] => {
  const seen = new Map<string, Ask>()
  const add = ([msgId, q]: [string, Entry]) => {
    if (str(q.answered_at) !== null || !OPEN_ENTRY(q)) return
    seen.set(msgId, { msgId, from: str(q.from) ?? '?', at: str(q.at) ?? '', summary: str(q.summary) ?? '' })
  }
  entries(questions).forEach(add)
  entries(returns).filter(([, e]) => e.type === 'question').forEach(add)

  return [...seen.values()].sort((a, b) => a.at.localeCompare(b.at))
}

const unhandledReturns = (returns: unknown) =>
  entries(returns)
    .filter(([, e]) => e.type !== 'question' && OPEN_ENTRY(e))
    .sort(([, a], [, b]) => (str(a.at) ?? '').localeCompare(str(b.at) ?? ''))
    .map(([id]) => id)

// How the newest return was handled (`riarma`, `risposto`, ...), null while it has none.
const lastHandled = (returns: unknown) => {
  const last = entries(returns).sort(([, a], [, b]) => (str(b.at) ?? '').localeCompare(str(a.at) ?? ''))[0]

  return last === undefined ? null : str(last[1].handled)
}

export const parseWait = (text: string): Wait | null => {
  try {
    const raw = JSON.parse(text)
    const taskId = str(raw.task_id)
    const state = str(raw.state)
    if (taskId === null || state === null || !SHOWN_STATES.has(state)) return null

    return {
      taskId,
      description: str(raw.description) ?? '',
      delegatedTo: str(raw.delegated_to),
      createdAt: str(raw.created_at) ?? '',
      deadline: str(raw.deadline),
      state,
      expiredAt: str(raw.expired_at),
      pendingReason: str(raw.pending_wake?.reason),
      asks: parseAsks(raw.questions, raw.returns),
      unhandled: unhandledReturns(raw.returns).length,
      firstUnhandled: unhandledReturns(raw.returns)[0] ?? null,
      msgId: str(raw.delegation?.msg_id),
      executor: str(raw.executor_session) ?? str(raw.letto_da) ?? str(raw.delegated_to),
      lastHandled: lastHandled(raw.returns),
      readAt: raw.letto_at === undefined ? undefined : str(raw.letto_at),
    }
  } catch {
    return null
  }
}

const minutes = (iso: string, now: number) => {
  const t = Date.parse(iso)

  return Number.isNaN(t) ? 0 : Math.max(0, Math.floor((now - t) / 60_000))
}

const age = (min: number) =>
  min < 1 ? 'meno di 1 min' : min < 120 ? `${min} min` : `${Math.floor(min / 60)} h`

// Why a wake is postponed, in words. The reasons come from agent-team-os-wake.py.
export const reasonText = (reason: string) => {
  if (/busy|working/.test(reason)) return 'lavori'
  if (/has_text|pending_input/.test(reason)) return 'hai testo nella casella'
  if (/asking|waiting_/.test(reason)) return 'aspetti un permesso o una risposta'
  if (reason === 'tetto_orario') return 'tetto di sveglie orarie raggiunto'
  if (reason === 'tetto_task') return 'tetto di sveglie per questo task'
  if (reason === 'tetto_coppia') return 'tetto di sveglie con questo agente'
  if (reason === 'tetto_urgenti') return 'un urgente da questo agente negli ultimi 15 min'
  if (reason === 'onda_senza_guardia') return 'Onda da aggiornare'
  if (reason === 'nessun_canale') return 'nessun canale per svegliarti'
  if (reason === 'onda:no_session') return 'Onda non trova la sessione nel pane'

  return reason
}


export const visible = (w: Wait, now: number) =>
  w.state === 'expected' || (w.expiredAt !== null && now - Date.parse(w.expiredAt) < EXPIRED_SHOWN_MS)

// Expired first, then the oldest wait.
export const orderWaits = (waits: readonly Wait[], now: number) =>
  waits
    .filter(w => visible(w, now))
    .sort(
      (a, b) =>
        Number(b.state === 'expired') - Number(a.state === 'expired') ||
        a.createdAt.localeCompare(b.createdAt),
    )

// ---- BR24-F6.4: one row per delegation, with who holds the ball ----

// A plan reference in an intent or a summary: ON24-F3, am18-f15, BR24-F6.4, br24-f8.3b.
export type PlanRef = { plan: string; phase: string; step: string | null; code: string }

export const planRef = (...texts: string[]): PlanRef | null => {
  for (const t of texts) {
    const m = /(?:^|[^A-Za-z0-9])([A-Za-z]{2,4}\d{2})-(f\d{1,2})(?:\.(\d{1,2}[a-z]?))?(?![A-Za-z0-9])/i.exec(t)
    if (m === null) continue
    const plan = m[1]!.toUpperCase()
    const phase = m[2]!.toUpperCase()
    const step = m[3] === undefined ? null : `${phase}.${m[3].toLowerCase()}`

    return { plan, phase, step, code: `${plan}-${step ?? phase}` }
  }

  return null
}

// What Orbit says about a reference: the title of its step (or phase), and whether
// Mario holds it: the step is his, or his is an open step in the same phase.
export type OrbitInfo = { title: string | null; mario: boolean }

// The rows read from orbit.db for some references (one query for all of them).
export type OrbitRow = {
  code: string
  phase: string
  phase_title: string | null
  step_key: string | null
  title: string | null
  state: string | null
  assignee: string | null
}

const sqlText = (s: string) => `'${s.replace(/'/g, "''")}'`

export const orbitSql = (refs: readonly PlanRef[]) => {
  const plans = [...new Set(refs.map(r => r.plan))].map(sqlText).join(', ')
  const phases = [...new Set(refs.map(r => r.phase))].map(sqlText).join(', ')

  return `select p.code as code, ph.key as phase, ph.title as phase_title, t.step_key, t.title, t.state, t.assignee
    from plans p join plan_phases ph on ph.plan_id = p.id left join tasks t on t.phase_id = ph.id
    where upper(p.code) in (${plans}) and ph.key in (${phases}) and coalesce(p.status, 'active') = 'active'`
}

const isMario = (assignee: string | null) => /^mario\b/i.test(assignee ?? '')

export const orbitInfo = (rows: readonly OrbitRow[], ref: PlanRef): OrbitInfo | null => {
  const mine = rows.filter(r => (r.code ?? '').toUpperCase() === ref.plan && r.phase === ref.phase)
  if (mine.length === 0) return null
  const step = ref.step === null ? undefined : mine.find(r => r.step_key === ref.step)
  const open = (r: OrbitRow) => r.step_key !== null && r.state !== 'done'

  return {
    title: step?.title ?? mine[0]!.phase_title,
    mario: (step !== undefined && isMario(step.assignee)) || mine.some(r => open(r) && isMario(r.assignee)),
  }
}

// A live session as the centralino's vista (or Onda) sees it.
export type Live = { agent: string; slug: string; host: string; working: boolean }

// `kai`, `kai/onda-electron`, `kai@mini/onda-electron`.
const parseAddress = (address: string) => {
  const m = /^([^@/]+)(?:@([^/]+))?(?:\/(.+))?$/.exec(address)

  return m === null ? null : { agent: m[1]!, host: m[2] ?? null, slug: m[3] ?? null }
}

// The executor's session, if alive: same agent, same slug when the address names one.
export const executorLive = (address: string | null, live: readonly Live[], host: string) => {
  const a = address === null ? null : parseAddress(address)
  if (a === null) return null
  const found = live.filter(
    s => s.agent === a.agent && (a.slug === null || s.slug === a.slug) && s.host === (a.host ?? host),
  )

  return found.find(s => s.working) ?? found[0] ?? null
}

// BR24-F6.6: the executor's agent alive in other folders on the same host, for
// "non aperto qui · vivo in <slug>". Working sessions first, then by slug.
export const agentElsewhere = (address: string | null, live: readonly Live[], host: string): string[] => {
  const a = address === null ? null : parseAddress(address)
  if (a === null) return []

  return live
    .filter(s => s.agent === a.agent && s.host === (a.host ?? host) && s.slug !== a.slug)
    .sort((x, y) => Number(y.working) - Number(x.working) || x.slug.localeCompare(y.slug))
    .map(s => s.slug)
}

export type BallState =
  | 'domanda'
  | 'esito'
  | 'scaduta'
  | 'mario'
  | 'consegnato'
  | 'fermo'
  | 'chiusa'
  | 'coda'
  | 'lavora'

// A glyph per state, never colour alone: the shape says it too.
export const BALL_GLYPH: Record<BallState, string> = {
  domanda: '?',
  esito: '✉',
  scaduta: '!',
  mario: '●',
  consegnato: '✓',
  fermo: '◌',
  chiusa: '○',
  coda: '⧗',
  lavora: '◐',
}

// Rows that ask something of this session (or of Mario) come first.
const BALL_ORDER: BallState[] = [
  'domanda',
  'esito',
  'scaduta',
  'mario',
  'consegnato',
  'fermo',
  'chiusa',
  'coda',
  'lavora',
]

const capital = (s: string) => (s === '' ? s : s[0]!.toUpperCase() + s.slice(1))

// Where the executor's agent is alive when not here: the first folder, `+N` for the others.
const elsewhereText = (slugs: readonly string[]) =>
  slugs.length === 0 ? '' : ` qui · vivo in ${slugs[0]}${slugs.length > 1 ? ` +${slugs.length - 1}` : ''}`

// Who holds the ball, in words.
export const ballLabel = (state: BallState, executor: string | null, elsewhere: readonly string[] = []) => {
  const who = capital(parseAddress(executor ?? '')?.agent ?? 'chi esegue')
  switch (state) {
    case 'domanda':
      return 'domanda per te'
    case 'esito':
      return 'esito da leggere'
    case 'scaduta':
      return 'scaduta'
    case 'mario':
      return 'tocca a Mario'
    case 'consegnato':
      return `${who} ha consegnato`
    case 'fermo':
      return `${who} fermo`
    case 'chiusa':
      return `${who} non aperto${elsewhereText(elsewhere)}`
    case 'coda':
      return `in coda: ${who} occupato`
    case 'lavora':
      return `${who} ci lavora`
  }
}

export const ballState = (w: Wait, orbit: OrbitInfo | null, exec: Live | null): BallState => {
  if (w.state === 'expired') return 'scaduta'
  if (w.asks.length > 0) return 'domanda'
  if (w.unhandled > 0) return 'esito'
  if (w.lastHandled === 'riarma' && orbit?.mario === true) return 'mario'
  // Working, but on something else until it reads the delegation (BR24-F6.6).
  if (exec?.working === true) return w.readAt === null ? 'coda' : 'lavora'
  // Delivered and re-armed: the executor is done for now, the ball is outside it.
  if (w.lastHandled === 'riarma') return 'consegnato'
  if (exec !== null) return 'fermo'

  return 'chiusa'
}

// One line, whatever the summary held: newlines and runs of spaces become one space.
export const oneLine = (s: string) => s.replace(/\s+/g, ' ').trim()

// The short title: Orbit's, else the first sentence of the description, without
// the intent and the plan code it starts with ("br24-f6.4-banda: BR24-F6.4: ...").
export const shortTitle = (description: string, orbitTitle: string | null) => {
  // A step's title can be a paragraph: its first clause, when that is not too short.
  if (orbitTitle !== null && orbitTitle.trim() !== '') {
    const t = oneLine(orbitTitle)
    const cut = /^(.{15,}?)(?: \(|: |\. | - )/.exec(t)?.[1]

    return cut ?? t
  }
  const text = oneLine(description)
    .replace(/^[a-z0-9][a-z0-9._-]*: /i, '')
    .replace(/^[A-Za-z]{2,4}\d{2}-F\d+(?:\.\w+)?:? /i, '')
    // Alita's opening: "Piano ON24, fase F3, nuova: ...", "AM18, fase F15, nuova: ...".
    .replace(/^(?:piano )?[A-Za-z]{2,4}\d{2}, fase F\d+(?:\.\w+)?(?:, [^:,]{1,20})?: /i, '')
  const sentence = /^(.+?[.!?])(?:\s|$)/.exec(text)?.[1] ?? text

  return sentence
}

export const shownSince = (iso: string, now: number) => age(minutes(iso, now))

// What a row needs to be drawn; register.tsx lays it out in columns.
export const toDelegationRow = (
  w: Wait,
  now: number,
  orbit: OrbitInfo | null,
  exec: Live | null,
  elsewhere: readonly string[] = [],
): WaitRow => {
  const ref = planRef(w.description)
  const state = ballState(w, orbit, exec)
  const read =
    state === 'domanda' ? w.asks[0]!.msgId : state === 'esito' ? w.firstUnhandled ?? w.msgId : w.msgId

  return {
    key: w.taskId,
    state,
    glyph: BALL_GLYPH[state],
    label: ballLabel(state, w.executor, elsewhere),
    code: ref?.code ?? null,
    plan: ref?.plan ?? null,
    title: shortTitle(w.description, orbit?.title ?? null) || w.taskId,
    who: w.executor ?? w.delegatedTo ?? '?',
    since: shownSince(w.createdAt, now),
    read: read === null ? null : `/read ${read}`,
    alert: state === 'domanda' || state === 'esito' || state === 'scaduta' || w.pendingReason?.startsWith('tetto_') === true,
    note: w.pendingReason?.startsWith('tetto_') === true ? reasonText(w.pendingReason) : null,
  }
}

export const orderRows = (rows: readonly WaitRow[]) =>
  [...rows].sort((a, b) => BALL_ORDER.indexOf(a.state) - BALL_ORDER.indexOf(b.state))

export const waitsHeadline = (waits: readonly Wait[]) => {
  const open = waits.filter(w => w.state === 'expected').length
  const expired = waits.length - open
  const tail = expired === 0 ? '' : expired === 1 ? ', 1 scaduta' : `, ${expired} scadute`

  return `Deleghe aperte · ${open}${tail}`
}

// The footer's counts, only those not zero: what waits for this session.
export const waitsFooter = (rows: readonly WaitRow[]) => {
  const n = (s: BallState) => rows.filter(r => r.state === s).length
  const parts: string[] = []
  const esiti = n('esito')
  const domande = n('domanda')
  const mario = n('mario')
  if (esiti > 0) parts.push(`✉ ${esiti} ${esiti === 1 ? 'esito' : 'esiti'} da leggere`)
  if (domande > 0) parts.push(`? ${domande} ${domande === 1 ? 'domanda' : 'domande'} per te`)
  if (mario > 0) parts.push(`● ${mario} ${mario === 1 ? 'tocca' : 'toccano'} a Mario`)

  return parts.join('   ')
}
