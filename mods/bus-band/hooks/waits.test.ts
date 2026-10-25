import { describe, expect, test } from 'claude-code/testing'

import {
  ballState,
  executorLive,
  oneLine,
  orbitInfo,
  orderRows,
  orderWaits,
  parseWait,
  planRef,
  reasonText,
  shortTitle,
  toDelegationRow,
  waitsFooter,
  waitsHeadline,
} from './waits'
import type { Live, OrbitRow } from './waits'

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

const KAI_WORKING: Live = { agent: 'kai', slug: 'f3', host: 'laptop', working: true }
const KAI_IDLE: Live = { ...KAI_WORKING, working: false }

describe('one row per delegation (BR24-F6.4)', () => {
  test('no newline survives: the title is one line', () => {
    const w = parseWait(
      wait({ description: 'on24-f3-hairline: Piano ON24, fase F3, nuova: Hairline.\n\nCos\'è: @x/hairline' }),
    )!
    const r = toDelegationRow(w, NOW, null, null)
    expect(r.title).toBe('Hairline.')
    expect(r.title.includes('\n')).toBe(false)
    expect(oneLine('a\n\n  b\tc ')).toBe('a b c')
  })

  test('the title comes from Orbit when there is one', () => {
    expect(shortTitle('x: y. z', 'Hairline nella schermata\nvuota')).toBe('Hairline nella schermata vuota')
    expect(shortTitle('', 'Banda del bus ridisegnata sul modello di Orbit (Mario 25/10 08:44): una riga')).toBe(
      'Banda del bus ridisegnata sul modello di Orbit',
    )
    expect(shortTitle('', 'Analisi notturna del 24/10: voce persa')).toBe('Analisi notturna del 24/10')
    expect(shortTitle('br24-f6.4-banda: BR24-F6.4: ridisegnare la banda. Altro', null)).toBe('ridisegnare la banda.')
  })

  test('the plan code, from the intent or the summary, any case', () => {
    expect(planRef('on24-f3-hairline: Piano ON24')?.code).toBe('ON24-F3')
    expect(planRef('am18-f15-analisi-24ott: AM18')).toEqual({ plan: 'AM18', phase: 'F15', step: null, code: 'AM18-F15' })
    expect(planRef('br24-f6.4-banda-deleghe: x')).toEqual({ plan: 'BR24', phase: 'F6', step: 'F6.4', code: 'BR24-F6.4' })
    expect(planRef('fix WH17-F8.3b oggi')?.code).toBe('WH17-F8.3b')
    expect(planRef('spike orbit, utf8-f3 no')?.code).toBe(undefined)
    expect(planRef('nessun codice')).toBe(null)
  })

  test('Orbit: step title, Mario on the step or an open step of his in the phase', () => {
    const rows: OrbitRow[] = [
      { code: 'AM18', phase: 'F15', phase_title: 'Analisi notturna', step_key: 'F15.1', title: 'Verifica', state: 'todo', assignee: 'kai' },
      { code: 'AM18', phase: 'F15', phase_title: 'Analisi notturna', step_key: 'F15.5', title: 'Ok di Mario', state: 'todo', assignee: 'mario' },
      { code: 'ON24', phase: 'F3', phase_title: 'Hairline', step_key: 'F3.2', title: 'Scelta', state: 'done', assignee: 'mario' },
    ]
    expect(orbitInfo(rows, planRef('am18-f15')!)).toEqual({ title: 'Analisi notturna', mario: true })
    expect(orbitInfo(rows, planRef('AM18-F15.1')!)).toEqual({ title: 'Verifica', mario: true })
    expect(orbitInfo(rows, planRef('on24-f3')!)).toEqual({ title: 'Hairline', mario: false })
    expect(orbitInfo(rows, planRef('XX11-F1')!)).toBe(null)
  })

  test('who holds the ball, in order of what to do first', () => {
    const asked = parseWait(wait({ returns: { q: { type: 'question', at: '2026-10-17T10:55:00Z', state: 'woken' } } }))!
    const back = parseWait(wait({ returns: { r: { type: 'response', at: '2026-10-17T10:55:00Z', state: 'woken' } } }))!
    const rearmed = parseWait(
      wait({ returns: { r: { type: 'response', at: '2026-10-17T10:55:00Z', state: 'handled', handled: 'riarma' } } }),
    )!
    const plain = parseWait(wait({}))!
    const late = parseWait(wait({ state: 'expired', expired_at: '2026-10-17T10:58:00Z' }))!
    expect(ballState(asked, null, null)).toBe('domanda')
    expect(ballState(back, null, null)).toBe('esito')
    expect(ballState(late, null, KAI_WORKING)).toBe('scaduta')
    expect(ballState(rearmed, { title: null, mario: true }, KAI_IDLE)).toBe('mario')
    expect(ballState(rearmed, { title: null, mario: false }, KAI_IDLE)).toBe('consegnato')
    expect(ballState(plain, { title: null, mario: true }, null)).toBe('chiusa')
    expect(ballState(plain, null, KAI_WORKING)).toBe('lavora')
    expect(ballState(plain, null, KAI_IDLE)).toBe('fermo')
  })

  test('words for each state, and what a click opens', () => {
    const w = parseWait(wait({ delegation: { msg_id: 'msg-d' }, executor_session: 'kai/f3' }))!
    expect(toDelegationRow(w, NOW, null, null)).toMatchObject({
      label: 'Kai non aperto',
      glyph: '○',
      who: 'kai/f3',
      since: '12 min',
      read: '/read msg-d',
    })
    expect(toDelegationRow(w, NOW, null, KAI_WORKING).label).toBe('Kai lavora')
    const back = parseWait(
      wait({ delegation: { msg_id: 'msg-d' }, returns: { 'msg-r': { type: 'response', at: '2026-10-17T10:55:00Z', state: 'woken' } } }),
    )!
    expect(toDelegationRow(back, NOW, null, null)).toMatchObject({ label: 'esito da leggere', glyph: '✉', read: '/read msg-r' })
  })

  test('the executor: same agent and slug, on the same host', () => {
    const live: Live[] = [
      { agent: 'kai', slug: 'alita-mobile', host: 'laptop', working: false },
      { agent: 'kai', slug: 'f3', host: 'mini', working: true },
    ]
    expect(executorLive('kai/alita-mobile', live, 'laptop')?.slug).toBe('alita-mobile')
    expect(executorLive('kai/f3', live, 'laptop')).toBe(null)
    expect(executorLive('kai@mini/f3', live, 'laptop')?.working).toBe(true)
    expect(executorLive('kai/onda-electron', live, 'laptop')).toBe(null)
  })

  test('headline, order and footer', () => {
    const open = parseWait(wait({ task_id: 't-a' }))!
    const back = parseWait(wait({ task_id: 't-b', returns: { r: { type: 'response', at: '2026-10-17T10:55:00Z', state: 'woken' } } }))!
    const rows = orderRows([toDelegationRow(open, NOW, null, KAI_WORKING), toDelegationRow(back, NOW, null, null)])
    expect(rows.map(r => r.key)).toEqual(['t-b', 't-a'])
    expect(waitsHeadline([open, back])).toBe('Deleghe aperte · 2')
    expect(waitsFooter(rows)).toBe('✉ 1 esito da leggere')
  })

  test('expired first in the list, an old expiry disappears, the headline counts it', () => {
    const open = parseWait(wait({ task_id: 't-a' }))!
    const late = parseWait(wait({ task_id: 't-b', state: 'expired', expired_at: '2026-10-17T10:00:00Z' }))!
    const old = parseWait(wait({ task_id: 't-c', state: 'expired', expired_at: '2026-10-15T10:00:00Z' }))!
    const ordered = orderWaits([open, late, old], NOW)
    expect(ordered.map(w => w.taskId)).toEqual(['t-b', 't-a'])
    expect(waitsHeadline(ordered)).toBe('Deleghe aperte · 1, 1 scaduta')
  })

  test('a cap on the wake is said in words on the row', () => {
    const w = parseWait(
      wait({ returns: { r: { type: 'response', at: '2026-10-17T10:55:00Z', state: 'woken' } }, pending_wake: { reason: 'tetto_coppia' } }),
    )!
    const r = toDelegationRow(w, NOW, null, null)
    expect(r.alert).toBe(true)
    expect(r.note).toBe('tetto di sveglie con questo agente')
    expect(reasonText('onda:has_text')).toBe('hai testo nella casella')
    expect(reasonText('onda:no_session')).toBe('Onda non trova la sessione nel pane')
  })

  test('questions: open ones only (v2.7 questions and v2.8 returns)', () => {
    const w = parseWait(
      wait({
        questions: {
          'msg-q1': { from: 'kai/f3', at: '2026-10-17T10:55:00Z', summary: 'tema scuro?', state: 'woken' },
          'msg-q0': { from: 'kai/f3', at: '2026-10-17T10:50:00Z', state: 'seen', answered_at: '2026-10-17T10:52:00Z' },
        },
        returns: { 'msg-r2': { type: 'event', at: '2026-10-17T10:52:00Z', state: 'handled', handled: 'riarma' } },
      }),
    )!
    expect(w.asks.map(a => a.msgId)).toEqual(['msg-q1'])
    expect(w.unhandled).toBe(0)
    expect(toDelegationRow(w, NOW, null, null)).toMatchObject({ label: 'domanda per te', glyph: '?', read: '/read msg-q1' })
    expect(waitsFooter([toDelegationRow(w, NOW, null, null)])).toBe('? 1 domanda per te')
  })

  test('closed waits are not shown', () => {
    expect(parseWait(wait({ state: 'closed' }))).toBe(null)
  })
})
