import { describe, expect, test } from 'claude-code/testing'

import { inboxOf, parseVista, sessionsOf, vistaRequest, waitsOf } from './vista'

const V = {
  onda: { valido: true, sessioni: [{ sessionId: 's', workspaceId: 'w', slug: 'orbit', cwd: '/p/Orbit', state: 'working' }] },
  messaggi: [
    { agent: 'kai', slug: null, letto: false, raw: { id: 'msg-a' } },
    { agent: 'kai', slug: 'orbit', letto: false, raw: { id: 'msg-b' } },
    { agent: 'kai', slug: 'orbit', letto: true, raw: { id: 'msg-c' } },
    { agent: 'kai', slug: 'other', letto: false, raw: { id: 'msg-d' } },
    { agent: 'leo', slug: 'orbit', letto: false, raw: { id: 'msg-e' } },
  ],
  attese: [
    { agent: 'kai', slug: 'orbit', raw: { task_id: 't-1' } },
    { agent: 'kai', slug: 'other', raw: { task_id: 't-2' } },
  ],
}

describe('vista', () => {
  test('one v1 request line for the vista op', () => {
    const req = JSON.parse(vistaRequest('r-1'))
    expect(req.v).toBe(1)
    expect(req.op).toBe('vista')
    expect(req.caller).toBe(undefined)
    expect(vistaRequest('r-1').endsWith('\n')).toBe(true)
  })

  test('parses ok answers, says why otherwise', () => {
    expect(parseVista(`${JSON.stringify({ v: 1, ok: true, result: V })}\n`)).toEqual({ vista: V })
    expect(parseVista('')).toEqual({ error: 'il centralino non ha risposto' })
    expect(parseVista('{"ok":false,"error":{"code":"x","message":"rotto"}}')).toEqual({
      error: 'il centralino ha risposto con un errore (rotto)',
    })
    expect(parseVista('garbage')).toEqual({ error: 'risposta del centralino illeggibile' })
  })

  test('pending messages of this session and of the shared root only', () => {
    expect(inboxOf(V, 'kai', 'orbit').map(([, name]) => name)).toEqual(['msg-a.json', 'msg-b.json'])
  })

  test('waits of this session only', () => {
    expect(waitsOf(V, 'kai', 'orbit')).toEqual([JSON.stringify({ task_id: 't-1' })])
  })

  test('the reading of the delegation goes into the wait (BR24-F6.6)', () => {
    const v = { attese: [{ agent: 'kai', slug: 'orbit', raw: { task_id: 't-1' }, letto_at: 'T', letto_da: 'nico/x' }] }
    expect(JSON.parse(waitsOf(v, 'kai', 'orbit')[0]!)).toEqual({ task_id: 't-1', letto_at: 'T', letto_da: 'nico/x' })
    const unread = { attese: [{ agent: 'kai', slug: 'orbit', raw: { task_id: 't-1' }, letto_at: null, letto_da: null }] }
    expect(JSON.parse(waitsOf(unread, 'kai', 'orbit')[0]!).letto_at).toBe(null)
  })

  test('Onda sessions only when the centralino says the file is valid', () => {
    expect(sessionsOf(V)?.sessions.map(s => s.slug)).toEqual(['orbit'])
    expect(sessionsOf({ ...V, onda: { valido: false, sessioni: V.onda.sessioni } })).toBe(null)
  })
})
