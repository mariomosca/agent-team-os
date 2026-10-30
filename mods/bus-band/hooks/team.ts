import type { TeamRow, TeamSession, TeamState } from '../types'

// Pure logic of the "Squadra" section: parse $AB_HOME/onda/sessions.json
// (schema/CONTRACT.md, "Onda → mod"), order it, say it in words. No `$` here.

const STATES: readonly TeamState[] = [
  'working',
  'idle',
  'waiting_permission',
  'waiting_answer',
  'pending_input',
  'exited',
]

export const isStuck = (state: TeamState) =>
  state === 'pending_input' || state === 'waiting_permission' || state === 'waiting_answer'

export type SessionsFile = { ondaPid: number; sessions: TeamSession[] }

const str = (v: unknown) => (typeof v === 'string' ? v : null)

// The entries of sessions.json (or of the centralino's vista, which carries them as they are).
export const parseSessionList = (list: readonly unknown[]): TeamSession[] => {
  const sessions: TeamSession[] = []
  for (const x of list) {
    const s = (typeof x === 'object' && x !== null ? x : {}) as Record<string, unknown>
    const sessionId = str(s.sessionId)
    const workspaceId = str(s.workspaceId)
    const slug = str(s.slug)
    const cwd = str(s.cwd)
    if (sessionId === null || workspaceId === null || slug === null || cwd === null) continue

    sessions.push({
      sessionId,
      terminalId: str(s.terminalId),
      workspaceId,
      agent: str(s.agent),
      slug,
      cwd,
      // An unknown state is read as idle (contract §13).
      state: STATES.includes(s.state as TeamState) ? (s.state as TeamState) : 'idle',
      stateSince: str(s.stateSince) ?? '',
      lastTool: str(s.lastTool),
      mounted: s.mounted === true,
      host: str(s.host) ?? 'laptop',
    })
  }

  return sessions
}

// Null when the file is not usable: not JSON, unknown major version, no pid.
export const parseSessions = (text: string): SessionsFile | null => {
  try {
    const raw = JSON.parse(text)
    if (raw.v !== 1 || !Number.isInteger(raw.ondaPid) || !Array.isArray(raw.sessions)) return null

    return { ondaPid: raw.ondaPid, sessions: parseSessionList(raw.sessions) }
  } catch {
    return null
  }
}

const trim = (p: string) => p.replace(/\/+$/, '')

export const isSelf = (s: TeamSession, cwd: string, agent: string, slug: string) =>
  trim(s.cwd) === trim(cwd) || (s.agent === agent && s.slug === slug)

export const minutesSince = (iso: string, now: number) => {
  const t = Date.parse(iso)

  return Number.isNaN(t) ? 0 : Math.max(0, Math.floor((now - t) / 60_000))
}

// Stuck and waiting first (longest first), then the rest by how long they have
// been in their state. Exited sessions are not part of the team.
export const orderTeam = (sessions: readonly TeamSession[], self: (s: TeamSession) => boolean) =>
  sessions
    .filter(s => s.state !== 'exited' && !self(s))
    .sort(
      (a, b) =>
        Number(isStuck(b.state)) - Number(isStuck(a.state)) ||
        (Date.parse(a.stateSince) || 0) - (Date.parse(b.stateSince) || 0),
    )

const duration = (min: number) => (min < 1 ? 'meno di 1 min' : min < 120 ? `${min} min` : `${Math.floor(min / 60)} h`)

// The state in words, never colour alone (toasts and the old wording).
export const describe = (s: TeamSession, now: number) => {
  const min = minutesSince(s.stateSince, now)

  switch (s.state) {
    case 'pending_input':
      return `TESTO FERMO da ${duration(min)}`
    case 'waiting_permission':
      return `aspetta un permesso da ${duration(min)}`
    case 'waiting_answer':
      return `aspetta una risposta da ${duration(min)}`
    case 'working':
      return s.lastTool === null ? 'lavora' : `lavora (${s.lastTool})`
    default:
      return 'in attesa'
  }
}

// ---- BR24-F6.7: only the sessions that need Mario ----
// The band says what Mario has to do now; Orbit's office gives the whole team.

export type Exception = TeamRow['kind']

// Glyph and word per exception, in the order they are shown: never colour alone.
const EXCEPTION: Record<Exception, { glyph: string; label: string }> = {
  testo: { glyph: '✎', label: 'testo fermo nella casella' },
  permesso: { glyph: '⚑', label: 'aspetta un permesso' },
  domanda: { glyph: '?', label: 'aspetta una risposta' },
  canale: { glyph: '⊘', label: 'irraggiungibile, senza canale di sveglia' },
}
const ORDER: readonly Exception[] = ['testo', 'permesso', 'domanda', 'canale']

const exceptionOf = (state: TeamState): Exception | null =>
  state === 'pending_input' ? 'testo' : state === 'waiting_permission' ? 'permesso' : state === 'waiting_answer' ? 'domanda' : null

// A bus session the centralino sees alive without a channel to wake it (not in Onda, no tmux).
export type NoChannel = { agent: string; slug: string; since: string }

export const exceptionRows = (
  onda: readonly TeamSession[],
  noChannel: readonly NoChannel[],
  now: number,
  canMount: boolean,
): TeamRow[] => {
  const rows: { row: TeamRow; min: number }[] = []
  for (const s of onda) {
    const kind = exceptionOf(s.state)
    if (kind === null) continue
    const min = minutesSince(s.stateSince, now)
    rows.push({ min, row: {
      key: s.sessionId,
      kind,
      ...EXCEPTION[kind],
      name: `${s.agent ?? '?'}/${s.slug}`,
      since: duration(min),
      stuck: true,
      unmounted: !s.mounted,
      workspaceId: s.workspaceId,
      terminalId: s.terminalId,
      canMount,
    } })
  }
  for (const s of noChannel) {
    const min = s.since === '' ? 0 : minutesSince(s.since, now)
    rows.push({ min, row: {
      key: `canale:${s.agent}/${s.slug}`,
      kind: 'canale',
      ...EXCEPTION.canale,
      name: `${s.agent}/${s.slug}`,
      since: s.since === '' ? '' : duration(min),
      stuck: true,
      unmounted: false,
      workspaceId: null,
      terminalId: null,
      canMount: false,
    } })
  }

  // By kind, then the one waiting longest.
  return rows
    .sort((a, b) => ORDER.indexOf(a.row.kind) - ORDER.indexOf(b.row.kind) || b.min - a.min)
    .map(r => r.row)
}

// t-20261029-lbgswf (A): a session alive by heartbeat that nothing can wake is not
// counted with the live ones: it is said apart, as unreachable.
export const teamHeadline = (live: number, exceptions: number, unreachable = 0) => {
  const vive = live === 1 ? '1 viva' : `${live} vive`
  const off = unreachable === 0 ? '' : unreachable === 1 ? ', 1 irraggiungibile' : `, ${unreachable} irraggiungibili`

  return `Squadra: ${vive}${off} · ${exceptions === 0 ? 'niente per te' : `${exceptions} per te`}`
}

// Episode key: a session toasts once per (session, state) until it leaves it.
export const stuckKeys = (sessions: readonly TeamSession[]) =>
  new Map(sessions.filter(s => isStuck(s.state)).map(s => [s.sessionId, `${s.sessionId}:${s.state}`]))

export const toastText = (s: TeamSession) => {
  const who = `${s.agent ?? '?'}/${s.slug}`

  return s.state === 'pending_input'
    ? `Squadra: ${who}, testo fermo senza Invio`
    : `Squadra: ${who} aspetta ${s.state === 'waiting_answer' ? 'una risposta' : 'un permesso'}`
}

// requests/ — mod → Onda. The temp name must not start with "req-".
export const requestName = (ts: number, rand: string) => `req-${ts}-${rand}.json`
// WT30: terminalId tells Onda which pane to focus when a workspace holds more than one agent.
export const requestBody = (workspaceId: string, terminalId?: string | null) =>
  JSON.stringify(terminalId ? { action: 'mount', workspaceId, terminalId } : { action: 'mount', workspaceId })
