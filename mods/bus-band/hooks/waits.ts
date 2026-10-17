import type { WaitRow } from '../types'

// Pure logic of the "Aspetto" section: the results this session waits for,
// $AB_HOME/waits/<agent>/@<slug>/<task_id>.json (schema/bus-wait.schema.json,
// written by agent-bus-mcp and the lib, moved on by agent-team-os-wake.py). No `$` here.

export type Wait = {
  taskId: string
  description: string
  delegatedTo: string | null
  createdAt: string
  deadline: string | null
  state: string
  expiredAt: string | null
  pendingReason: string | null
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
      deadline: str(raw.deadline),
      state,
      expiredAt: str(raw.expired_at),
      pendingReason: str(raw.pending_wake?.reason),
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
      text: `${who}: ${what}, esito arrivato, sveglia rimandata (${reasonText(w.pendingReason)})`,
      alert: false,
    }
  }

  return { key: w.taskId, text: `${who}: ${what}, da ${since}`, alert: false }
}

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
  const parts = []
  if (open > 0) parts.push(open === 1 ? '1 esito' : `${open} esiti`)
  if (expired > 0) parts.push(expired === 1 ? '1 scaduto' : `${expired} scaduti`)

  return `Aspetto ${parts.join(', ')}`
}
