import { describe, expect, test } from 'claude-code/testing'

import { orderWaits, parseWait, reasonText, toWaitRow, waitsHeadline } from './waits'

const NOW = Date.parse('2026-10-17T11:00:00Z')

const wait = (over: Record<string, unknown>) =>
  JSON.stringify({
    v: 1,
    task_id: 't-20261017-abc123',
    agent: 'alita',
    slug: 'work-hub',
    description: 'spike orbit',
    delegated_to: 'kai/f3',
    created_at: '2026-10-17T10:48:00Z',
    deadline: null,
    state: 'expected',
    ...over,
  })

describe('parseWait', () => {
  test('reads an expected wait', () => {
    const w = parseWait(wait({}))
    expect(w?.taskId).toBe('t-20261017-abc123')
    expect(w?.delegatedTo).toBe('kai/f3')
  })

  test('drops closed waits and junk', () => {
    expect(parseWait(wait({ state: 'woken' }))).toBe(null)
    expect(parseWait(wait({ state: 'seen' }))).toBe(null)
    expect(parseWait('not json')).toBe(null)
    expect(parseWait(JSON.stringify({ state: 'expected' }))).toBe(null)
  })
})

describe('rows', () => {
  test('age in minutes', () => {
    const w = parseWait(wait({}))!
    expect(toWaitRow(w, NOW).text).toBe('kai/f3: spike orbit, da 12 min')
  })

  test('postponed wake says why, in words', () => {
    const w = parseWait(wait({ pending_wake: { reason: 'onda:working' } }))!
    expect(toWaitRow(w, NOW).text).toBe('kai/f3: spike orbit, esito arrivato, sveglia rimandata (lavori)')
    expect(reasonText('onda:has_text')).toBe('hai testo nella casella')
  })

  test('expired is spelled out and flagged', () => {
    const w = parseWait(wait({ state: 'expired', expired_at: '2026-10-17T10:55:00Z' }))!
    const r = toWaitRow(w, NOW)
    expect(r.text).toBe('SCADUTO kai/f3: spike orbit')
    expect(r.alert).toBe(true)
  })

  test('expired first, an old expiry disappears', () => {
    const open = parseWait(wait({ task_id: 't-a' }))!
    const late = parseWait(wait({ task_id: 't-b', state: 'expired', expired_at: '2026-10-17T10:00:00Z' }))!
    const old = parseWait(wait({ task_id: 't-c', state: 'expired', expired_at: '2026-10-15T10:00:00Z' }))!
    const ordered = orderWaits([open, late, old], NOW)
    expect(ordered.map(w => w.taskId)).toEqual(['t-b', 't-a'])
    expect(waitsHeadline(ordered)).toBe('Aspetto 1 esito, 1 scaduto')
  })
})
