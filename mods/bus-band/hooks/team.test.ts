import { describe, expect, test } from 'claude-code/testing'

import {
  describe as say,
  exceptionRows,
  isSelf,
  orderTeam,
  parseSessions,
  requestBody,
  requestName,
  stuckKeys,
  teamHeadline,
} from './team'

const NOW = Date.parse('2026-10-14T09:30:00Z')

const session = (over: Record<string, unknown>) => ({
  sessionId: 's',
  terminalId: 'pane-1',
  workspaceId: 'workspace-1',
  agent: 'kai',
  slug: 'x',
  cwd: '/w/x',
  state: 'working',
  stateSince: '2026-10-14T09:20:00Z',
  lastTool: 'Bash',
  mounted: true,
  host: 'laptop',
  ...over,
})

const file = (sessions: unknown[], over: Record<string, unknown> = {}) =>
  JSON.stringify({ v: 1, writtenAt: '2026-10-14T09:25:00Z', ondaPid: 123, sessions, ...over })

describe('team', () => {
  test('parse: valid, bad json, unknown version, bad rows, unknown state', () => {
    const ok = parseSessions(file([session({ state: 'pending_input' }), { junk: 1 }, session({ sessionId: 't', state: 'zzz' })]))
    expect(ok?.ondaPid).toBe(123)
    expect(ok?.sessions.map(s => s.state)).toEqual(['pending_input', 'idle'])
    expect(parseSessions('nope')).toBe(null)
    expect(parseSessions(file([], { v: 2 }))).toBe(null)
    expect(parseSessions(file([], { ondaPid: 'abc' }))).toBe(null)
  })

  test('order: stuck first, then longest in state; exited and self dropped', () => {
    const p = parseSessions(
      file([
        session({ sessionId: 'a', slug: 'a', state: 'working', stateSince: '2026-10-14T09:00:00Z' }),
        session({ sessionId: 'b', slug: 'b', state: 'waiting_permission', stateSince: '2026-10-14T09:27:00Z' }),
        session({ sessionId: 'c', slug: 'c', state: 'pending_input', stateSince: '2026-10-14T09:18:00Z' }),
        session({ sessionId: 'd', slug: 'd', state: 'exited' }),
        session({ sessionId: 'me', agent: 'kai', slug: 'me', cwd: '/w/me', state: 'pending_input' }),
      ]),
    )!
    const rows = orderTeam(p.sessions, s => isSelf(s, '/w/me/', 'kai', 'me'))
    expect(rows.map(s => s.sessionId)).toEqual(['c', 'b', 'a'])
  })

  test('self: same cwd, or same agent and slug', () => {
    const s = parseSessions(file([session({})]))!.sessions[0]!
    expect(isSelf(s, '/w/x', 'leo', 'y')).toBe(true)
    expect(isSelf(s, '/elsewhere', 'kai', 'x')).toBe(true)
    expect(isSelf(s, '/elsewhere', 'kai', 'y')).toBe(false)
  })

  test('words, never colour alone', () => {
    const base = parseSessions(file([session({})]))!.sessions[0]!
    expect(say({ ...base, state: 'pending_input', stateSince: '2026-10-14T09:18:00Z' }, NOW)).toBe('TESTO FERMO da 12 min')
    expect(say({ ...base, state: 'waiting_permission', stateSince: '2026-10-14T09:27:00Z' }, NOW)).toBe('aspetta un permesso da 3 min')
    expect(say(base, NOW)).toBe('lavora (Bash)')
    expect(say({ ...base, state: 'waiting_answer', stateSince: '2026-10-14T06:00:00Z' }, NOW)).toBe('aspetta una risposta da 3 h')
  })

  test('BR24-F6.7: only the exceptions, by kind then the longest waiting, glyph and word', () => {
    const sessions = parseSessions(
      file([
        session({ sessionId: 'a', slug: 'a', state: 'working' }),
        session({ sessionId: 'b', slug: 'b', state: 'waiting_answer', stateSince: '2026-10-14T09:25:00Z' }),
        session({ sessionId: 'c', slug: 'c', state: 'pending_input', stateSince: '2026-10-14T09:28:00Z' }),
        session({ sessionId: 'd', slug: 'd', state: 'pending_input', stateSince: '2026-10-14T09:10:00Z', mounted: false }),
        session({ sessionId: 'e', slug: 'e', state: 'idle' }),
      ]),
    )!.sessions
    const rows = exceptionRows(sessions, [{ agent: 'kai', slug: 'onda-electron', since: '' }], NOW, true)
    expect(rows.map(r => `${r.glyph} ${r.label} ${r.name} ${r.since}`)).toEqual([
      '✎ testo fermo nella casella kai/d 20 min',
      '✎ testo fermo nella casella kai/c 2 min',
      '? aspetta una risposta kai/b 5 min',
      '⊘ irraggiungibile, senza canale di sveglia kai/onda-electron ',
    ])
    expect(rows[0]!.unmounted).toBe(true)
    expect(rows[3]!.workspaceId).toBe(null)
    expect(rows[3]!.canMount).toBe(false)
  })

  test('headline: how many alive, and what is for Mario', () => {
    expect(teamHeadline(3, 0)).toBe('Squadra: 3 vive · niente per te')
    expect(teamHeadline(1, 0)).toBe('Squadra: 1 viva · niente per te')
    expect(teamHeadline(5, 2)).toBe('Squadra: 5 vive · 2 per te')
    expect(teamHeadline(2, 1, 1)).toBe('Squadra: 2 vive, 1 irraggiungibile · 1 per te')
    expect(teamHeadline(2, 3, 3)).toBe('Squadra: 2 vive, 3 irraggiungibili · 3 per te')
  })

  test('toast episodes: one key per session and stuck state', () => {
    const s = parseSessions(file([session({ sessionId: 'a', state: 'pending_input' }), session({ sessionId: 'b' })]))!.sessions
    expect([...stuckKeys(s).values()]).toEqual(['a:pending_input'])
  })

  test('request file', () => {
    expect(requestName(1791969798000, 'ab12cd')).toBe('req-1791969798000-ab12cd.json')
    expect(JSON.parse(requestBody('workspace-9'))).toEqual({ action: 'mount', workspaceId: 'workspace-9' })
    expect(JSON.parse(requestBody('workspace-9', null))).toEqual({ action: 'mount', workspaceId: 'workspace-9' })
    expect(JSON.parse(requestBody('workspace-9', 'pane-3'))).toEqual({
      action: 'mount',
      workspaceId: 'workspace-9',
      terminalId: 'pane-3',
    })
  })

  test('rows carry the terminal to focus (WT30)', () => {
    const s = parseSessions(file([session({ sessionId: 'a', state: 'pending_input', terminalId: 'pane-7' })]))!
    expect(s.sessions[0]!.terminalId).toBe('pane-7')
    expect(exceptionRows(s.sessions, [], NOW, true)[0]!.terminalId).toBe('pane-7')
  })
})
