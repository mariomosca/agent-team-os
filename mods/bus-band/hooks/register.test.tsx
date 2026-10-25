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

const VISTA = JSON.stringify({
  v: 1,
  id: 'r',
  ok: true,
  result: {
    onda: {
      valido: true,
      sessioni: [
        { sessionId: 'b', workspaceId: 'ws-2', agent: 'nico', slug: 'brandart', cwd: '/w/brandart', state: 'idle', stateSince: '2026-10-18T08:50:00Z', mounted: true },
      ],
    },
    messaggi: [
      { agent: 'kai', slug: 'portineria', letto: false, raw: { id: 'msg-v', from: 'vera', intent: 'dal-centralino', priority: 'normal' } },
      { agent: 'kai', slug: 'altra', letto: false, raw: { id: 'msg-o', from: 'leo', intent: 'altra-sessione', priority: 'normal' } },
    ],
    attese: [],
  },
})

const world = (on: On, env: Record<string, string>, centralino = false, vista = VISTA, orbit = '') => {
  mock.env(on, { AB_HOME: ROOT, HOME: '/Users/m', ...env })
  mock.clock(on, { now: NOW })
  on('session.start', (_$, e) => ({ cwd: e.cwd }))
  on('session.cwd', () => ({ value: CWD }))
  on('command.register', (_$, e) => ({ value: { command: e.name } }))
  on('ui.toast', () => ({ value: undefined }))
  on('fs.exists', (_$, e) => ({
    value:
      (centralino && e.path === `${ROOT}/centralino.sock`) ||
      e.path === '/Users/m/Library/Application Support/Orbit/orbit.db' ||
      e.path in FILES ||
      Object.keys(FILES).some(f => f.startsWith(`${e.path}/`)),
  }))
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
  on('process.run', (_$, e) => ({
    value: {
      exitCode: 0,
      stdout: centralino && e.argv[0] === 'nc' ? vista : e.argv[0] === 'sqlite3' ? orbit : '',
      stderr: '',
      isStdoutTruncated: false,
      isStderrTruncated: false,
    },
  }))
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

describe('centralino (BR24-F4.3)', () => {
  test('with the centralino: messages and Squadra from its vista, no warning', async ($, on) => {
    world(on, {}, true)
    await $.session.start(START)
    const ui = await $.ui.mount({ plugin: 'bus-band', surface: 'terminal', component: 'AbovePrompt', requestId: 'band', props: PROPS })
    expect(await ui.find({ text: 'dal-centralino' })).toBeDefined()
    expect(await ui.find({ text: 'da alita' })).toBeUndefined()
    expect(await ui.find({ text: 'altra-sessione' })).toBeUndefined()
    expect(await ui.find({ text: 'nico/brandart' })).toBeDefined()
    expect(await ui.find({ text: 'Bus letto dai file' })).toBeUndefined()
  })

  test("without it: today's files, and the band says so", async ($, on) => {
    world(on, {})
    await $.session.start(START)
    const ui = await $.ui.mount({ plugin: 'bus-band', surface: 'terminal', component: 'AbovePrompt', requestId: 'band', props: PROPS })
    expect(await ui.find({ text: 'da alita' })).toBeDefined()
    expect(await ui.find({ text: 'Bus letto dai file: il centralino del bus non è attivo.' })).toBeDefined()
  })
})

// Alita's two waits of 25/10 08:42, as the centralino gave them (BR24-F6.4).
const ALITA_WAITS = JSON.stringify({
  v: 1,
  id: 'r',
  ok: true,
  result: {
    host: 'laptop',
    sessioni: [{ agent: 'kai', slug: 'onda-electron', host: 'laptop', viva: true, ferma: true, onda_state: 'idle' }],
    messaggi: [],
    attese: [
      {
        agent: 'kai',
        slug: 'portineria',
        raw: {
          task_id: 't-20261024-qgd2r2',
          state: 'expected',
          created_at: '2026-10-17T23:00:00Z',
          delegated_to: 'kai/onda-electron',
          executor_session: 'kai/onda-electron',
          delegation: { msg_id: 'msg-on24' },
          description:
            "on24-f3-hairline: Piano ON24, fase F3, nuova: Hairline nella schermata del workspace vuoto. Richiesta di Mario delle 22:51.\n\nCos'è: @lucasmarkes/hairline (github.com/lucasmarkes/hairline), licenza MIT",
          returns: {
            'msg-q': { at: '2026-10-17T23:05:00Z', type: 'question', state: 'handled', handled: 'risposto' },
            'msg-r': { at: '2026-10-18T01:03:00Z', type: 'response', state: 'handled', handled: 'riarma' },
          },
        },
      },
      {
        agent: 'kai',
        slug: 'portineria',
        raw: {
          task_id: 't-20261025-i57l3z',
          state: 'expected',
          created_at: '2026-10-18T08:57:00Z',
          delegated_to: 'kai/alita-mobile',
          delegation: { msg_id: 'msg-am18' },
          description:
            "am18-f15-analisi-24ott: AM18, fase F15, nuova: correzioni dall'analisi notturna del 24/10. Ok di Mario del 25/10 alle 08:38; si fa oggi pomeriggio, quando apri la sessione.\n\nReport: ~/.alita-memoria/a",
        },
      },
    ],
  },
})

const ORBIT_ROWS = JSON.stringify([
  { code: 'ON24', phase: 'F3', phase_title: 'Hairline nella schermata del workspace vuoto', step_key: 'F3.4', title: 'Prova di Mario su Onda-dev', state: 'todo', assignee: 'mario' },
  { code: 'AM18', phase: 'F15', phase_title: "Analisi notturna del 24/10: voce persa", step_key: 'F15.1', title: 'Verifica', state: 'todo', assignee: 'kai' },
])

describe('deleghe aperte (BR24-F6.4)', () => {
  test("Alita's two waits: one line each, who holds the ball in words", async ($, on) => {
    world(on, {}, true, ALITA_WAITS, ORBIT_ROWS)
    await $.session.start(START)
    const ui = await $.ui.mount({ plugin: 'bus-band', surface: 'terminal', component: 'AbovePrompt', requestId: 'band', props: { hasSurvey: false, bodyColumns: 110 } as never })
    expect(await ui.find({ text: 'Deleghe aperte · 2' })).toBeDefined()
    expect(await ui.find({ text: /tocca a Mario/ })).toBeDefined()
    expect(await ui.find({ text: /Kai non aperto qui · vivo in onda-electron/ })).toBeDefined()
    expect(await ui.find({ text: /^ON24-F3/ })).toBeDefined()
    // the longer state ("non aperto qui · vivo in ...", BR24-F6.6) leaves the title less room
    expect(await ui.find({ text: /Hairline nella schermata d…/ })).toBeDefined()
    expect(await ui.find({ text: /Analisi notturna del 24\/10/ })).toBeDefined()
    expect(await ui.find({ text: /\n/ })).toBeUndefined()
    expect(await ui.find({ text: /Report:/ })).toBeUndefined()
  })

  test('without Orbit the title is the first sentence, still one line; re-armed is "consegnato"', async ($, on) => {
    world(on, {}, true, ALITA_WAITS, '')
    await $.session.start(START)
    const ui = await $.ui.mount({ plugin: 'bus-band', surface: 'terminal', component: 'AbovePrompt', requestId: 'band', props: { hasSurvey: false, bodyColumns: 110 } as never })
    expect(await ui.find({ text: /Kai ha consegnato/ })).toBeDefined()
    expect(await ui.find({ text: /^ {2}Hairline nella schermata d/ })).toBeDefined()
    expect(await ui.find({ text: /Richiesta di Mario|Piano ON24/ })).toBeUndefined()
    expect(await ui.find({ text: /^○ Kai non aperto qui · vivo in onda-electron {2}$/ })).toBeDefined()
  })

  test('BR24-F6.6: in coda while the executor has not read the delegation', async ($, on) => {
    const vista = (letto: string | null) =>
      JSON.stringify({
        v: 1,
        id: 'r',
        ok: true,
        result: {
          host: 'laptop',
          sessioni: [{ agent: 'kai', slug: 'f3', host: 'laptop', viva: true, ferma: false, onda_state: 'working' }],
          messaggi: [],
          attese: [
            {
              agent: 'kai',
              slug: 'portineria',
              letto_at: letto,
              letto_da: letto === null ? null : 'kai/f3',
              raw: {
                task_id: 't-1',
                state: 'expected',
                created_at: '2026-10-25T08:16:00Z',
                delegated_to: 'kai/f3',
                delegation: { msg_id: 'msg-d' },
                description: 'br24-f6.6: BR24-F6.6: lettura della delega.',
              },
            },
          ],
        },
      })
    world(on, {}, true, vista(null), '')
    await $.session.start(START)
    const ui = await $.ui.mount({ plugin: 'bus-band', surface: 'terminal', component: 'AbovePrompt', requestId: 'band', props: { hasSurvey: false, bodyColumns: 110 } as never })
    expect(await ui.find({ text: /^⧗ in coda: Kai occupato {2}$/ })).toBeDefined()
    expect(await ui.find({ text: /ci lavora/ })).toBeUndefined()
  })

  test('BR24-F6.6: ci lavora once read', async ($, on) => {
    const v = JSON.parse(ALITA_WAITS)
    v.result.sessioni = [{ agent: 'kai', slug: 'alita-mobile', host: 'laptop', viva: true, ferma: false, onda_state: 'working' }]
    v.result.attese = [{ ...v.result.attese[1], letto_at: '2026-10-25T08:20:00Z', letto_da: 'kai/alita-mobile' }]
    world(on, {}, true, JSON.stringify(v), '')
    await $.session.start(START)
    const ui = await $.ui.mount({ plugin: 'bus-band', surface: 'terminal', component: 'AbovePrompt', requestId: 'band', props: { hasSurvey: false, bodyColumns: 110 } as never })
    expect(await ui.find({ text: /^◐ Kai ci lavora {2}$/ })).toBeDefined()
    expect(await ui.find({ text: /in coda/ })).toBeUndefined()
  })
})
