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

const countUnhandled = (returns: unknown) =>
  entries(returns).filter(([, e]) => e.type !== 'question' && OPEN_ENTRY(e)).length

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
      unhandled: countUnhandled(raw.returns),
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
  if (reason === 'onda_senza_guardia') return 'Onda da aggiornare'
  if (reason === 'nessun_canale') return 'nessun canale per svegliarti'

  return reason
}

export const visible = (w: Wait, now: number) =>
  w.state === 'expected' || (w.expiredAt !== null && now - Date.parse(w.expiredAt) < EXPIRED_SHOWN_MS)

export const toWaitRow = (w: Wait, now: number): WaitRow => {
  const who = w.delegatedTo ?? '?'
  const what = w.description === '' ? w.taskId : w.description
  if (w.state === 'expired') {
    return { key: w.taskId, text: `SCADUTO ${who}: ${what}`, alert: true }
  }
  const since = age(minutes(w.createdAt, now))
  if (w.pendingReason !== null) {
    return {
      key: w.taskId,
      text: `${who}: ${what}, ritorno arrivato, sveglia rimandata (${reasonText(w.pendingReason)})`,
      alert: w.pendingReason.startsWith('tetto_'),
    }
  }
  if (w.unhandled > 0) {
    const n = w.unhandled === 1 ? '1 ritorno' : `${w.unhandled} ritorni`

    return { key: w.taskId, text: `${who}: ${what}, ${n} da gestire (bus_gestisci)`, alert: false }
  }

  return { key: w.taskId, text: `${who}: ${what}, da ${since}`, alert: false }
}

// One row per open question: the delegated session waits for you, so it is an alert.
export const askRows = (waits: readonly Wait[], now: number): WaitRow[] =>
  waits
    .filter(w => w.state === 'expected')
    .flatMap(w => w.asks)
    .map(a => ({
      key: a.msgId,
      text: `DOMANDA da ${a.from}, aspetta te da ${age(minutes(a.at, now))}: ${a.summary === '' ? a.msgId : a.summary} (bus_read ${a.msgId})`,
      alert: true,
    }))

// Expired first, then the oldest wait.
export const orderWaits = (waits: readonly Wait[], now: number) =>
  waits
    .filter(w => visible(w, now))
    .sort(
      (a, b) =>
        Number(b.state === 'expired') - Number(a.state === 'expired') ||
        a.createdAt.localeCompare(b.createdAt),
    )

export const waitsHeadline = (waits: readonly Wait[]) => {
  const open = waits.filter(w => w.state === 'expected').length
  const expired = waits.length - open
  const asks = waits.filter(w => w.state === 'expected').reduce((n, w) => n + w.asks.length, 0)
  const parts = []
  if (open > 0) parts.push(open === 1 ? '1 delega' : `${open} deleghe`)
  if (expired > 0) parts.push(expired === 1 ? '1 scaduto' : `${expired} scaduti`)

  const ask = asks === 0 ? '' : asks === 1 ? ' (1 domanda aspetta te)' : ` (${asks} domande aspettano te)`

  return `Aspetto ${parts.join(', ')}${ask}`
}
