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

// Null when the file is not usable: not JSON, unknown major version, no pid.
export const parseSessions = (text: string): SessionsFile | null => {
  try {
    const raw = JSON.parse(text)
    if (raw.v !== 1 || !Number.isInteger(raw.ondaPid) || !Array.isArray(raw.sessions)) return null

    const sessions: TeamSession[] = []
    for (const s of raw.sessions) {
      const sessionId = str(s?.sessionId)
      const workspaceId = str(s?.workspaceId)
      const slug = str(s?.slug)
      const cwd = str(s?.cwd)
      if (sessionId === null || workspaceId === null || slug === null || cwd === null) continue

      sessions.push({
        sessionId,
        workspaceId,
        agent: str(s.agent),
        slug,
        cwd,
        // An unknown state is read as idle (contract §13).
        state: STATES.includes(s.state) ? s.state : 'idle',
        stateSince: str(s.stateSince) ?? '',
        lastTool: str(s.lastTool),
        mounted: s.mounted === true,
        host: str(s.host) ?? 'laptop',
      })
    }

    return { ondaPid: raw.ondaPid, sessions }
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

const duration = (min: number) => (min < 1 ? 'meno di 1 min' : `${min} min`)

// The state in words, never colour alone.
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

export const toRow = (s: TeamSession, now: number, canMount: boolean): TeamRow => ({
  key: s.sessionId,
  name: `${s.agent ?? '?'}/${s.slug}`,
  text: describe(s, now),
  stuck: isStuck(s.state),
  unmounted: !s.mounted,
  workspaceId: s.workspaceId,
  canMount,
})

export const teamHeadline = (rows: readonly TeamRow[]) => {
  const stuck = rows.filter(r => r.stuck).length
  const live = rows.length === 1 ? '1 viva' : `${rows.length} vive`
  if (stuck === 0) return `Squadra: ${live}`

  return `Squadra: ${live}, ${stuck === 1 ? '1 ferma' : `${stuck} ferme`}`
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
export const requestBody = (workspaceId: string) =>
  JSON.stringify({ action: 'mount', workspaceId })
