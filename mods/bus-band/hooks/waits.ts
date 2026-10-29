import type { WaitRow } from '../types'

// Pure logic of the "Deleghe aperte" section: the waits of this session from the
// centralino's vista, each with who holds the ball computed there. No `$` here.

export type Wait = {
  taskId: string
  description: string
  delegatedTo: string | null
  createdAt: string
  state: string
  expiredAt: string | null
  // BR24-F6.4: the delegation message and the session that executes it.
  msgId: string | null
  executor: string | null
  // BR24-F6.7: who holds the ball and the row, as the centralino's vista computed them.
  ball: string | null
  row: Record<string, unknown> | null
}

const str = (v: unknown) => (typeof v === 'string' && v !== '' ? v : null)

// A wait the band shows: still expected, or expired in the last day.
const SHOWN_STATES = new Set(['expected', 'expired'])
const EXPIRED_SHOWN_MS = 24 * 60 * 60_000

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
      state,
      expiredAt: str(raw.expired_at),
      msgId: str(raw.delegation?.msg_id),
      executor: str(raw.executor_session) ?? str(raw.delegated_to),
      ball: str(raw.stato_palla),
      row: typeof raw.riga === 'object' && raw.riga !== null ? raw.riga : null,
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

// ---- BR24-F6.4/F6.7: one row per delegation, with who holds the ball ----
// The centralino computes the state and the row in its vista (centralino/palla.py,
// `stato_palla` and `riga` on each wait): the band and Orbit's office draw the same
// thing. Here only the drawing data, read as it comes.

export type BallState = WaitRow['state']

const BALL_STATES: readonly BallState[] = [
  'domanda',
  'esito',
  'scaduta',
  'mario',
  'consegnato',
  'fermo',
  'chiusa',
  'coda',
  'lavora',
  // t-20261029-lbgswf: open, no return, still for hours: probably a duplicate. Last, counted apart.
  'da_verificare',
]

// One line, whatever the summary held: newlines and runs of spaces become one space.
export const oneLine = (s: string) => s.replace(/\s+/g, ' ').trim()

export const shownSince = (iso: string, now: number) => age(minutes(iso, now))

const asBall = (v: unknown): BallState | null =>
  typeof v === 'string' && (BALL_STATES as readonly string[]).includes(v) ? (v as BallState) : null

// What a row needs to be drawn; register.tsx lays it out in columns. Without the
// centralino's row (an older centralino, the files) the row says it does not know.
export const toDelegationRow = (w: Wait, now: number): WaitRow => {
  const r = w.row ?? {}
  const state = asBall(w.ball) ?? 'ignoto'
  const text = (v: unknown) => (typeof v === 'string' && v !== '' ? v : null)

  return {
    key: w.taskId,
    state,
    glyph: text(r.glifo) ?? '·',
    label: text(r.parola) ?? 'stato non noto',
    code: text(r.codice),
    plan: text(r.piano),
    title: oneLine(text(r.titolo) ?? w.description) || w.taskId,
    who: text(r.esecutore) ?? w.executor ?? w.delegatedTo ?? '?',
    since: shownSince(w.createdAt, now),
    read: text(r.leggi) ?? (w.msgId === null ? null : `/read ${w.msgId}`),
    alert: r.allerta === true,
    note: text(r.nota),
  }
}

// Rows that ask something of this session (or of Mario) come first; the unknown last.
const rank = (s: WaitRow['state']) => {
  const i = (BALL_STATES as readonly string[]).indexOf(s)

  return i < 0 ? BALL_STATES.length : i
}

export const orderRows = (rows: readonly WaitRow[]) => [...rows].sort((a, b) => rank(a.state) - rank(b.state))

export const waitsHeadline = (waits: readonly Wait[]) => {
  const doubtful = waits.filter(w => w.state === 'expected' && w.ball === 'da_verificare').length
  const open = waits.filter(w => w.state === 'expected').length - doubtful
  const expired = waits.filter(w => w.state !== 'expected').length
  const tail = expired === 0 ? '' : expired === 1 ? ', 1 scaduta' : `, ${expired} scadute`
  const check = doubtful === 0 ? '' : `, ${doubtful} da verificare`

  return `Deleghe aperte · ${open}${tail}${check}`
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
