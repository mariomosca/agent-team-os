import { describe, expect, test } from 'claude-code/testing'

import { askRows, orderWaits, parseWait, reasonText, toWaitRow, waitsHeadline } from './waits'

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
    expect(toWaitRow(w, NOW).text).toBe('kai/f3: spike orbit, ritorno arrivato, sveglia rimandata (lavori)')
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
    expect(waitsHeadline(ordered)).toBe('Aspetto 1 delega, 1 scaduto')
  })
})

describe('questions (bus v2.7)', () => {
  const questions = {
    'msg-q1': { from: 'kai/f3', at: '2026-10-17T10:55:00Z', summary: 'tema scuro?', state: 'woken' },
    'msg-q0': { from: 'kai/f3', at: '2026-10-17T10:50:00Z', summary: 'vecchia', state: 'seen', answered_at: '2026-10-17T10:52:00Z' },
  }

  test('open questions only, as alerts', () => {
    const w = parseWait(wait({ questions }))!
    expect(w.asks.map(a => a.msgId)).toEqual(['msg-q1'])
    const rows = askRows([w], NOW)
    expect(rows).toEqual([
      { key: 'msg-q1', text: 'DOMANDA da kai/f3, aspetta te da 5 min: tema scuro? (bus_read msg-q1)', alert: true },
    ])
  })

  test('headline counts them', () => {
    const w = parseWait(wait({ questions }))!
    expect(waitsHeadline([w])).toBe('Aspetto 1 delega (1 domanda aspetta te)')
  })

  test('no questions field, no rows', () => {
    const w = parseWait(wait({}))!
    expect(w.asks).toEqual([])
    expect(askRows([w], NOW)).toEqual([])
  })
})

describe('returns (bus v2.8)', () => {
  const returns = {
    'msg-r1': { from: 'kai/f3', at: '2026-10-17T10:50:00Z', type: 'response', state: 'woken' },
    'msg-r2': { from: 'kai/f3', at: '2026-10-17T10:52:00Z', type: 'event', state: 'handled', handled: 'riarma' },
    'msg-r3': { from: 'kai/f3', at: '2026-10-17T10:53:00Z', type: 'brief', state: 'pending' },
    'msg-q2': { from: 'kai/f3', at: '2026-10-17T10:56:00Z', type: 'question', summary: 'quale branch?', state: 'woken' },
  }

  test('returns without an outcome are counted, questions become asks', () => {
    const w = parseWait(wait({ returns }))!
    expect(w.unhandled).toBe(2)
    expect(w.asks.map(a => a.msgId)).toEqual(['msg-q2'])
    expect(toWaitRow(w, NOW).text).toBe('kai/f3: spike orbit, 2 ritorni da gestire (bus_gestisci)')
  })

  test('a cap is an alert, in words', () => {
    const w = parseWait(wait({ returns, pending_wake: { reason: 'tetto_coppia' } }))!
    const r = toWaitRow(w, NOW)
    expect(r.text).toBe('kai/f3: spike orbit, ritorno arrivato, sveglia rimandata (tetto di sveglie con questo agente)')
    expect(r.alert).toBe(true)
    expect(reasonText('tetto_task')).toBe('tetto di sveglie per questo task')
    expect(reasonText('onda:no_session')).toBe('Onda non trova la sessione nel pane')
  })

  test('closed waits are not shown', () => {
    expect(parseWait(wait({ state: 'closed' }))).toBe(null)
  })
})
