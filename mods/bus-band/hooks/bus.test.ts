import { describe, expect, test } from 'claude-code/testing'

import { agentForCwd, parseMessage, slugForCwd, sortMessages, workspaceMatch } from './bus'

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
})
