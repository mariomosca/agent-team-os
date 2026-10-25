import { describe, expect, test } from 'claude-code/testing'

import { agentForCwd, parseMessage, slugForCwd, sortMessages, todoHeadline, todoRows, workspaceMatch } from './bus'

const rules = [
  { pattern: '/Users/mariomosca/work-hub', match: 'prefix', agent: 'alita' },
  { pattern: '/Users/mariomosca/Projects/01-Building/', match: 'prefix', agent: 'kai' },
]

describe('bus', () => {
  test('agent from cwd by prefix', () => {
    expect(agentForCwd(rules, '/Users/mariomosca/work-hub')).toBe('alita')
    expect(agentForCwd(rules, '/Users/mariomosca/Projects/01-Building/Onda')).toBe('kai')
    expect(agentForCwd(rules, '/tmp')).toBe(undefined)
  })

  test('the longest pattern wins, on a folder boundary', () => {
    const nested = [
      { pattern: '/p', agent: 'kai' },
      { pattern: '/p/06-Brandart', agent: 'nico' },
      { pattern: '/w/work-hub', agent: 'alita' },
    ]
    expect(agentForCwd(nested, '/p/06-Brandart/x')).toBe('nico')
    expect(agentForCwd(nested, '/p/07-Tooling')).toBe('kai')
    expect(agentForCwd(nested, '/w/work-hub-x')).toBe(undefined)
    expect(agentForCwd(nested, '/w/work-hub/')).toBe('alita')
  })

  test('slug like the shell lib', () => {
    expect(slugForCwd('/Users/mariomosca/work-hub')).toBe('work-hub')
    expect(slugForCwd('/x/Noi Calendar/')).toBe('noi-calendar')
    expect(slugForCwd('/')).toBe('root')
  })

  test('workspace scoping', () => {
    expect(workspaceMatch('', '/a')).toBe('global')
    expect(workspaceMatch('/a/b', '/a')).toBe('here')
    expect(workspaceMatch('/a', '/a/b')).toBe('here')
    expect(workspaceMatch('/c', '/a')).toBe('elsewhere')
  })

  test('parse and sort, pressing first', () => {
    const low = parseMessage('{"id":"1","from":"kai","priority":"low","ts":"2026-10-03"}', 'f')
    const urgent = parseMessage(
      '{"id":"2","from":"leo","priority":"urgent","intent":"x","ts":"2026-10-01","context":{"project_path":"/p"}}',
      'f',
    )
    expect(parseMessage('not json', 'f')).toBe(null)
    expect(urgent?.projectPath).toBe('/p')
    expect(sortMessages([low!.message, urgent!.message]).map(m => m.id)).toEqual(['2', '1'])
  })

  test('BR24-F6.8: Da fare, the centralino row, urgent first then by state, returns left out', () => {
    const NOW = Date.parse('2026-10-25T09:00:00Z')
    const m = (id: string, stato: string, ordine: number, over: Record<string, unknown> = {}) =>
      parseMessage(
        JSON.stringify({
          id,
          from: 'alita',
          intent: `x-${id}`,
          priority: 'normal',
          ts: '2026-10-25T08:50:00Z',
          stato_msg: stato,
          riga: { glifo: '✉', parola: stato, ordine, codice: 'BR24-F6.7', piano: 'BR24', titolo: `T ${id}`, mittente: 'alita/work-hub', leggi: `/read ${id}` },
          ...over,
        }),
        'f',
      )!.message
    const rows = todoRows(
      [m('a', 'lavori', 3), m('b', 'nuova', 1), m('c', 'ritorno', 0, { riga: null }), m('d', 'comunicazione', 2, { priority: 'high' })],
      NOW,
    )
    expect(rows.map(r => r.key)).toEqual(['d', 'b', 'a'])
    expect(rows[0]).toMatchObject({ label: 'comunicazione · alta', pressing: true })
    expect(rows[1]).toMatchObject({ glyph: '✉', label: 'nuova', code: 'BR24-F6.7', title: 'T b', from: 'da alita/work-hub', since: '10 min', read: '/read b' })
    expect(todoHeadline(rows, 0)).toBe('Da fare · 3')
    expect(todoHeadline(rows, 2)).toBe('Da fare · 3 (+2 di altri workspace)')
  })

  test('BR24-F6.8: without the centralino row, the message as it is', () => {
    const r = todoRows([parseMessage('{"id":"1","from":"kai","type":"brief","intent":"x\\ny","ts":"2026-10-25T08:00:00Z"}', 'f')!.message], Date.parse('2026-10-25T09:00:00Z'))[0]!
    expect(r).toMatchObject({ state: 'ignoto', glyph: '·', label: 'brief', from: 'da kai', read: '/read 1' })
  })
})

