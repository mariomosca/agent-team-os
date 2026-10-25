import type { SessionsFile } from './team'
import { parseSessionList } from './team'
import type { Live } from './waits'

// Pure logic of the bus centralino's `vista` (BR24-F4.3): one read-only op on
// $AB_HOME/centralino.sock that gives this band its three sections, records in
// the bus file format so the parsers of bus.ts and waits.ts read them as they
// are. The mod engine cannot speak line-JSON on a socket ($.http speaks HTTP),
// so register.tsx sends the request through `nc -U`. No `$` here.

export type VistaMessage = { agent: string; slug: string | null; letto: boolean; raw: unknown }
// BR24-F6.6: letto_at/letto_da, when the executor first read the delegation
// (absent from a centralino before schema v4).
export type VistaWait = { agent: string; slug: string | null; raw: unknown; letto_at?: unknown; letto_da?: unknown }
// A bus session as the centralino sees it (only the fields the band reads).
export type VistaSession = {
  agent?: unknown
  slug?: unknown
  host?: unknown
  viva?: unknown
  ferma?: unknown
  onda_state?: unknown
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

      return [JSON.stringify(m.raw), typeof id === 'string' ? `${id}.json` : 'msg-.json']
    })

// The waits of this session, as texts for parseWait, with the reading of the delegation.
export const waitsOf = (v: Vista, agent: string, slug: string): string[] =>
  (v.attese ?? [])
    .filter(w => w.agent === agent && w.slug === slug)
    .map(w =>
      JSON.stringify(
        'letto_at' in w && typeof w.raw === 'object' && w.raw !== null
          ? { ...w.raw, letto_at: w.letto_at ?? null, letto_da: w.letto_da ?? null }
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

// The live sessions (BR24-F6.4: is the executor of a delegation open, and working?).
// Working: Onda says so, or, without Onda, the centralino does not call it stopped.
export const liveOf = (v: Vista): Live[] =>
  (v.sessioni ?? [])
    .filter(s => s.viva === true && typeof s.agent === 'string' && typeof s.slug === 'string')
    .map(s => ({
      agent: s.agent as string,
      slug: s.slug as string,
      host: typeof s.host === 'string' ? s.host : 'laptop',
      working: s.onda_state === 'working' || (typeof s.onda_state !== 'string' && s.ferma === false),
    }))
