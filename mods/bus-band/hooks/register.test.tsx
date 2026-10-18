import { describe, expect, mock, test } from 'claude-code/testing'
import type { On } from 'claude-code'

const ROOT = '/bus'
const CWD = '/w/portineria'
const NOW = Date.parse('2026-10-18T09:00:00Z')

const MAP = JSON.stringify({ rules: [{ pattern: '/w/', agent: 'kai' }] })
const SESSIONS = JSON.stringify({
  v: 1,
  writtenAt: '2026-10-18T08:59:00Z',
  ondaPid: 123,
  sessions: [
    { sessionId: 'a', terminalId: 'pane-1', workspaceId: 'ws-1', agent: 'leo', slug: 'pm-os', cwd: '/w/pm-os', state: 'working', stateSince: '2026-10-18T08:50:00Z', lastTool: 'Bash', mounted: true, host: 'laptop' },
  ],
})
const MESSAGE = JSON.stringify({ id: 'msg-1', from: 'alita', intent: 'brief', priority: 'normal', context: { project_path: CWD } })

const FILES: Record<string, string> = {
  [`${ROOT}/AGENT_MAP.json`]: MAP,
  [`${ROOT}/onda/sessions.json`]: SESSIONS,
  [`${ROOT}/inboxes/kai/@portineria/msg-1.json`]: MESSAGE,
}

const world = (on: On, env: Record<string, string>) => {
  mock.env(on, { AB_HOME: ROOT, HOME: '/Users/m', ...env })
  mock.clock(on, { now: NOW })
  on('session.start', (_$, e) => ({ cwd: e.cwd }))
  on('session.cwd', () => ({ value: CWD }))
  on('command.register', (_$, e) => ({ value: { command: e.name } }))
  on('ui.toast', () => ({ value: undefined }))
  on('fs.exists', (_$, e) => ({ value: e.path in FILES || Object.keys(FILES).some(f => f.startsWith(`${e.path}/`)) }))
  on('fs.read', (_$, e) => {
    const text = FILES[e.path]
    if (text === undefined) throw new Error(`ENOENT ${e.path}`)

    return { value: text }
  })
  on('fs.list', (_$, e) => ({
    value: Object.keys(FILES)
      .filter(f => f.startsWith(`${e.path}/`) && !f.slice(e.path.length + 1).includes('/'))
      .map(f => ({ name: f.slice(e.path.length + 1), kind: 'file', size: 0, mtimeMs: 0, isLink: false })),
  }))
  on('process.run', () => ({ value: { exitCode: 0, stdout: '', stderr: '', isStdoutTruncated: false, isStderrTruncated: false } }))
}

const START = { cwd: CWD, surface: 'terminal', isInteractive: true } as const
const PROPS = { hasSurvey: false } as never

describe('delegated session', () => {
  test('without SESSIONE_DELEGATA: messages and the Squadra section', async ($, on) => {
    world(on, {})
    await $.session.start(START)
    const ui = await $.ui.mount({ plugin: 'bus-band', surface: 'terminal', component: 'AbovePrompt', requestId: 'band', props: PROPS })
    expect(await ui.find({ text: 'da alita' })).toBeDefined()
    expect(await ui.find({ text: 'Squadra' })).toBeDefined()
  })

  test('SESSIONE_DELEGATA=1: messages to this session stay, no Squadra section', async ($, on) => {
    world(on, { SESSIONE_DELEGATA: '1' })
    await $.session.start(START)
    const ui = await $.ui.mount({ plugin: 'bus-band', surface: 'terminal', component: 'AbovePrompt', requestId: 'band', props: PROPS })
    expect(await ui.find({ text: 'da alita' })).toBeDefined()
    expect(await ui.find({ text: 'Squadra' })).toBeUndefined()
  })
})
