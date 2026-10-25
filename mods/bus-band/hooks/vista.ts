import type { NoChannel, SessionsFile } from './team'
import { parseSessionList } from './team'

// Pure logic of the bus centralino's `vista` (BR24-F4.3): one read-only op on
// $AB_HOME/centralino.sock that gives this band its three sections, records in
// the bus file format so the parsers of bus.ts and waits.ts read them as they
// are. The mod engine cannot speak line-JSON on a socket ($.http speaks HTTP),
// so register.tsx sends the request through `nc -U`. No `$` here.

export type VistaMessage = {
  agent: string
  slug: string | null
  letto: boolean
  raw: unknown
  stato_msg?: unknown
  riga?: unknown
}
// BR24-F6.7: stato_palla and riga, who holds the ball and the row to draw (centralino/palla.py).
export type VistaWait = { agent: string; slug: string | null; raw: unknown; stato_palla?: unknown; riga?: unknown }
// A bus session as the centralino sees it (only the fields the band reads).
export type VistaSession = {
  agent?: unknown
  slug?: unknown
  host?: unknown
  viva?: unknown
  ferma?: unknown
  onda_state?: unknown
  canale?: unknown
  started_at?: unknown
}

export type Vista = {
  onda?: { valido?: boolean; writtenAt?: string | null; sessioni?: unknown[] }
  messaggi?: VistaMessage[]
  attese?: VistaWait[]
  sessioni?: VistaSession[]
}

// Every pending message, however old (the band lists them all, like the inbox);
// no read ones; waits: open ones plus those of the last day.
export const VISTA_ARGS = { pending_s: 365 * 86400, letti_s: 0, attese_s: 86400 }

export const vistaRequest = (id: string) =>
  `${JSON.stringify({ v: 1, id, op: 'vista', args: VISTA_ARGS })}\n`

// The vista, or why there is none (the band says it in words).
export const parseVista = (stdout: string): { vista: Vista } | { error: string } => {
  const line = stdout.split('\n').find(l => l.trim() !== '')
  if (line === undefined) return { error: 'il centralino non ha risposto' }
  try {
    const resp = JSON.parse(line)
    if (resp?.ok === true && typeof resp.result === 'object' && resp.result !== null) {
      return { vista: resp.result as Vista }
    }
    const msg = typeof resp?.error?.message === 'string' ? resp.error.message : 'errore sconosciuto'

    return { error: `il centralino ha risposto con un errore (${msg})` }
  } catch {
    return { error: 'risposta del centralino illeggibile' }
  }
}

// Pending messages of this session: the shared root (slug null) and its own @slug,
// as [text, file name] for parseMessage.
export const inboxOf = (v: Vista, agent: string, slug: string): [string, string][] =>
  (v.messaggi ?? [])
    .filter(m => m.agent === agent && !m.letto && (m.slug === null || m.slug === slug))
    .map(m => {
      const id = (m.raw as { id?: unknown } | null)?.id
      // BR24-F6.8: the state for this session and the row, beside the record.
      const raw =
        'stato_msg' in m && typeof m.raw === 'object' && m.raw !== null
          ? { ...m.raw, stato_msg: m.stato_msg, riga: m.riga }
          : m.raw

      return [JSON.stringify(raw), typeof id === 'string' ? `${id}.json` : 'msg-.json']
    })

// The waits of this session, as texts for parseWait, with who holds the ball.
export const waitsOf = (v: Vista, agent: string, slug: string): string[] =>
  (v.attese ?? [])
    .filter(w => w.agent === agent && w.slug === slug)
    .map(w =>
      JSON.stringify(
        'stato_palla' in w && typeof w.raw === 'object' && w.raw !== null
          ? { ...w.raw, stato_palla: w.stato_palla, riga: w.riga }
          : w.raw,
      ),
    )

// Onda's sessions as the centralino sees them; null when it says sessions.json
// is not valid (stale, or Onda not running): the section is absent, as with the file.
export const sessionsOf = (v: Vista): SessionsFile | null =>
  v.onda?.valido === true && Array.isArray(v.onda.sessioni)
    ? { ondaPid: 0, sessions: parseSessionList(v.onda.sessioni) }
    : null

export const fallbackText = (why: string) => `Bus letto dai file: ${why}.`

// BR24-F6.7: the other live bus sessions of this machine (not this one), and those of
// them the centralino cannot wake (alive, no Onda pane, no tmux).
const others = (v: Vista, agent: string, slug: string) =>
  (v.sessioni ?? []).filter(
    s => s.viva === true && typeof s.agent === 'string' && typeof s.slug === 'string' && !(s.agent === agent && s.slug === slug),
  )

export const liveCountOf = (v: Vista, agent: string, slug: string) => others(v, agent, slug).length

export const noChannelOf = (v: Vista, agent: string, slug: string): NoChannel[] =>
  others(v, agent, slug)
    .filter(s => s.canale === 'nessuno')
    .map(s => ({ agent: s.agent as string, slug: s.slug as string, since: typeof s.started_at === 'string' ? s.started_at : '' }))

