import { describe, expect, test } from 'claude-code/testing'

import { oneLine, orderRows, orderWaits, parseWait, toDelegationRow, waitsFooter, waitsHeadline } from './waits'

// Who holds the ball is computed by the centralino (centralino/palla.py, tests in
// centralino/tests/test_palla.py); here only how the band reads and draws it.

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
    delegation: { msg_id: 'msg-d' },
    ...over,
  })

const ball = (state: string, riga: Record<string, unknown> = {}) => ({
  stato_palla: state,
  riga: { glifo: '◐', parola: 'Kai ci lavora', codice: 'BR24-F6.6', piano: 'BR24', titolo: 'Banda', esecutore: 'kai/f3', leggi: '/read msg-d', allerta: false, nota: null, ...riga },
})

describe('parseWait', () => {
  test('reads an expected wait with its ball', () => {
    const w = parseWait(wait(ball('lavora')))
    expect(w?.taskId).toBe('t-20261017-abc123')
    expect(w?.ball).toBe('lavora')
    expect(w?.row?.parola).toBe('Kai ci lavora')
  })

  test('drops closed waits and junk', () => {
    expect(parseWait(wait({ state: 'woken' }))).toBe(null)
    expect(parseWait(wait({ state: 'closed' }))).toBe(null)
    expect(parseWait('not json')).toBe(null)
    expect(parseWait(JSON.stringify({ state: 'expected' }))).toBe(null)
  })
})

describe('one row per delegation, as the centralino says (BR24-F6.4/F6.7)', () => {
  test('the row is the centralino row', () => {
    const r = toDelegationRow(parseWait(wait(ball('coda', { glifo: '⧗', parola: 'in coda: Kai occupato' })))!, NOW)
    expect(r).toMatchObject({
      state: 'coda',
      glyph: '⧗',
      label: 'in coda: Kai occupato',
      code: 'BR24-F6.6',
      plan: 'BR24',
      title: 'Banda',
      who: 'kai/f3',
      since: '12 min',
      read: '/read msg-d',
      alert: false,
    })
  })

  test('no newline survives in the title', () => {
    const r = toDelegationRow(parseWait(wait(ball('lavora', { titolo: 'Hairline.\n\n  nuova' })))!, NOW)
    expect(r.title).toBe('Hairline. nuova')
    expect(oneLine(' a\n b  c ')).toBe('a b c')
  })

  test('without the centralino row: says it does not know, opens the delegation', () => {
    const r = toDelegationRow(parseWait(wait({ description: 'spike orbit\naltro' }))!, NOW)
    expect(r).toMatchObject({ state: 'ignoto', glyph: '·', label: 'stato non noto', title: 'spike orbit altro', read: '/read msg-d' })
  })

  test('order, headline and footer', () => {
    const a = parseWait(wait({ task_id: 't-a', ...ball('lavora') }))!
    const b = parseWait(wait({ task_id: 't-b', ...ball('esito', { glifo: '✉', parola: 'esito da leggere', allerta: true }) }))!
    const c = parseWait(wait({ task_id: 't-c', ...ball('coda') }))!
    const d = parseWait(wait({ task_id: 't-d' }))!
    const rows = orderRows([a, b, c, d].map(w => toDelegationRow(w, NOW)))
    expect(rows.map(r => r.key)).toEqual(['t-b', 't-c', 't-a', 't-d'])
    expect(waitsHeadline([a, b, c, d])).toBe('Deleghe aperte · 4')
    expect(waitsFooter(rows)).toBe('✉ 1 esito da leggere')
    const q = toDelegationRow(parseWait(wait(ball('domanda', { glifo: '?', parola: 'domanda per te' })))!, NOW)
    expect(waitsFooter([q])).toBe('? 1 domanda per te')
  })

  test('expired first in the list, an old expiry disappears, the headline counts it', () => {
    const open = parseWait(wait({ task_id: 't-a' }))!
    const late = parseWait(wait({ task_id: 't-b', state: 'expired', expired_at: '2026-10-17T10:00:00Z' }))!
    const old = parseWait(wait({ task_id: 't-c', state: 'expired', expired_at: '2026-10-15T10:00:00Z' }))!
    const ordered = orderWaits([open, late, old], NOW)
    expect(ordered.map(w => w.taskId)).toEqual(['t-b', 't-a'])
    expect(waitsHeadline(ordered)).toBe('Deleghe aperte · 1, 1 scaduta')
  })

  test('da verificare: last, counted apart in the headline (t-20261029-lbgswf)', () => {
    const a = parseWait(wait({ task_id: 't-a', ...ball('lavora') }))!
    const d = parseWait(wait({ task_id: 't-d', ...ball('da_verificare', { glifo: '◇', parola: 'da verificare: nessun ritorno da 6 h' }) }))!
    const rows = orderRows([d, a].map(w => toDelegationRow(w, NOW)))
    expect(rows.map(r => r.key)).toEqual(['t-a', 't-d'])
    expect(rows[1]).toMatchObject({ state: 'da_verificare', glyph: '◇' })
    expect(waitsHeadline([a, d])).toBe('Deleghe aperte · 1, 1 da verificare')
  })

  test('a cap on the wake: the centralino note, said in words', () => {
    const r = toDelegationRow(parseWait(wait(ball('esito', { allerta: true, nota: 'tetto di sveglie con questo agente' })))!, NOW)
    expect(r.alert).toBe(true)
    expect(r.note).toBe('tetto di sveglie con questo agente')
  })
})
