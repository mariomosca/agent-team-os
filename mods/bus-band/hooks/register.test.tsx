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

const world = (on: On, env: Record<string, string>, centralino = false, vista = VISTA) => {
  mock.env(on, { AB_HOME: ROOT, HOME: '/Users/m', ...env })
  mock.clock(on, { now: NOW })
  on('session.start', (_$, e) => ({ cwd: e.cwd }))
  on('session.cwd', () => ({ value: CWD }))
  on('command.register', (_$, e) => ({ value: { command: e.name } }))
  on('ui.toast', () => ({ value: undefined }))
  on('fs.exists', (_$, e) => ({
    value:
      (centralino && e.path === `${ROOT}/centralino.sock`) ||
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
      stdout: centralino && e.argv[0] === 'nc' ? vista : '',
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
    // BR24-F6.7: nico is idle, nothing for Mario: one line, no list of the living
    expect(await ui.find({ text: 'Squadra: 1 viva · niente per te' })).toBeDefined()
    expect(await ui.find({ text: /nico\/brandart/ })).toBeUndefined()
    expect(await ui.find({ text: 'Bus letto dai file' })).toBeUndefined()
  })

  test('BR24-F6.7: only the sessions that need Mario, glyph and word, the pane button', async ($, on) => {
    const v = JSON.parse(VISTA)
    v.result.onda.sessioni = [
      { sessionId: 'b', workspaceId: 'ws-2', agent: 'nico', slug: 'brandart', cwd: '/w/brandart', state: 'idle', stateSince: '2026-10-18T08:50:00Z', mounted: true },
      { sessionId: 'c', workspaceId: 'ws-3', agent: 'leo', slug: 'pm-os', cwd: '/w/pm-os', state: 'pending_input', stateSince: '2026-10-18T08:48:00Z', mounted: true },
      { sessionId: 'd', workspaceId: 'ws-4', agent: 'vera', slug: 'growth', cwd: '/w/growth', state: 'waiting_permission', stateSince: '2026-10-18T08:57:00Z', mounted: false },
      { sessionId: 'me', workspaceId: 'ws-5', agent: 'kai', slug: 'portineria', cwd: CWD, state: 'pending_input', stateSince: '2026-10-18T08:40:00Z', mounted: true },
    ]
    v.result.sessioni = [
      { agent: 'nico', slug: 'brandart', viva: true, canale: 'onda' },
      { agent: 'leo', slug: 'pm-os', viva: true, canale: 'onda' },
      { agent: 'vera', slug: 'growth', viva: true, canale: 'onda' },
      { agent: 'kai', slug: 'onda-electron', viva: true, canale: 'nessuno', started_at: '2026-10-18T08:30:00Z' },
      { agent: 'kai', slug: 'portineria', viva: true, canale: 'onda' },
    ]
    world(on, { ONDA_SOCKET: '/tmp/onda.sock' }, true, JSON.stringify(v))
    await $.session.start(START)
    const ui = await $.ui.mount({ plugin: 'bus-band', surface: 'terminal', component: 'AbovePrompt', requestId: 'band', props: PROPS })
    expect(await ui.find({ text: 'Squadra: 3 vive, 1 irraggiungibile · 3 per te' })).toBeDefined()
    expect(await ui.find({ text: /^✎ testo fermo nella casella/ })).toBeDefined()
    expect(await ui.find({ text: /^⚑ aspetta un permesso/ })).toBeDefined()
    expect(await ui.find({ text: /^⊘ irraggiungibile, senza canale di sveglia/ })).toBeDefined()
    expect(await ui.find({ text: /^ {2}leo\/pm-os/ })).toBeDefined()
    expect(await ui.find({ text: /kai\/onda-electron/ })).toBeDefined()
    expect(await ui.find({ text: /da \d+ (min|h)/ })).toBeDefined()
    expect(await ui.find({ text: /nico\/brandart/ })).toBeUndefined()
    // the current session is never listed, even when it is stuck itself
    expect(await ui.find({ text: /^ {2}kai\/portineria/ })).toBeUndefined()
    expect(await ui.find({ label: 'Vai al pannello' })).toBeDefined()
    expect(await ui.find({ label: 'Rimetti nel mosaico' })).toBeDefined()
  })

  test("without it: today's files, and the band says so", async ($, on) => {
    world(on, {})
    await $.session.start(START)
    const ui = await $.ui.mount({ plugin: 'bus-band', surface: 'terminal', component: 'AbovePrompt', requestId: 'band', props: PROPS })
    expect(await ui.find({ text: 'da alita' })).toBeDefined()
    expect(await ui.find({ text: 'Bus letto dai file: il centralino del bus non è attivo.' })).toBeDefined()
  })
})

// Alita's two waits of 25/10 08:42, as the centralino gave them (BR24-F6.4), with
// who holds the ball computed there (BR24-F6.7, centralino/palla.py).
const ON24 = {
  task_id: 't-20261024-qgd2r2',
  state: 'expected',
  created_at: '2026-10-17T23:00:00Z',
  delegated_to: 'kai/onda-electron',
  executor_session: 'kai/onda-electron',
  delegation: { msg_id: 'msg-on24' },
  description:
    "on24-f3-hairline: Piano ON24, fase F3, nuova: Hairline nella schermata del workspace vuoto. Richiesta di Mario delle 22:51.\n\nCos'è: @lucasmarkes/hairline (github.com/lucasmarkes/hairline), licenza MIT",
}
const AM18 = {
  task_id: 't-20261025-i57l3z',
  state: 'expected',
  created_at: '2026-10-18T08:57:00Z',
  delegated_to: 'kai/alita-mobile',
  delegation: { msg_id: 'msg-am18' },
  description:
    "am18-f15-analisi-24ott: AM18, fase F15, nuova: correzioni dall'analisi notturna del 24/10. Ok di Mario del 25/10 alle 08:38; si fa oggi pomeriggio, quando apri la sessione.\n\nReport: ~/.alita-memoria/a",
}
const riga = (glifo: string, parola: string, codice: string, titolo: string, esecutore: string, leggi: string) => ({
  glifo, parola, codice, piano: codice.split('-')[0], titolo, esecutore, leggi, allerta: false, nota: null, vivo_in: [],
})

const vista = (attese: unknown[]) =>
  JSON.stringify({ v: 1, id: 'r', ok: true, result: { host: 'laptop', sessioni: [], messaggi: [], attese } })

const ALITA_WAITS = vista([
  {
    agent: 'kai', slug: 'portineria', raw: ON24, stato_palla: 'mario',
    riga: riga('●', 'tocca a Mario', 'ON24-F3', 'Hairline nella schermata del workspace vuoto', 'kai/onda-electron', '/read msg-on24'),
  },
  {
    agent: 'kai', slug: 'portineria', raw: AM18, stato_palla: 'chiusa',
    riga: riga('○', 'Kai non aperto qui · vivo in onda-electron', 'AM18-F15', 'Analisi notturna del 24/10', 'kai/alita-mobile', '/read msg-am18'),
  },
])

const mount = ($: Parameters<Parameters<typeof test>[1]>[0]) =>
  $.ui.mount({ plugin: 'bus-band', surface: 'terminal', component: 'AbovePrompt', requestId: 'band', props: { hasSurvey: false, bodyColumns: 110 } as never })

describe('deleghe aperte (BR24-F6.4)', () => {
  test("Alita's two waits: one line each, who holds the ball in words", async ($, on) => {
    world(on, {}, true, ALITA_WAITS)
    await $.session.start(START)
    const ui = await mount($)
    expect(await ui.find({ text: 'Deleghe aperte · 2' })).toBeDefined()
    expect(await ui.find({ text: /tocca a Mario/ })).toBeDefined()
    expect(await ui.find({ text: /^○ Kai non aperto qui · vivo in onda-electron {2}$/ })).toBeDefined()
    expect(await ui.find({ text: /^ON24-F3/ })).toBeDefined()
    // the longer state ("non aperto qui · vivo in ...", BR24-F6.6) leaves the title less room
    expect(await ui.find({ text: /Hairline nella schermata d…/ })).toBeDefined()
    expect(await ui.find({ text: /Analisi notturna del 24\/10/ })).toBeDefined()
    expect(await ui.find({ text: /\n/ })).toBeUndefined()
    expect(await ui.find({ text: /Report:/ })).toBeUndefined()
  })

  test('BR24-F6.6: in coda while the executor has not read the delegation, ci lavora after', async ($, on) => {
    world(
      on,
      {},
      true,
      vista([
        { agent: 'kai', slug: 'portineria', raw: AM18, stato_palla: 'coda', riga: riga('⧗', 'in coda: Kai occupato', 'AM18-F15', 'Analisi', 'kai/alita-mobile', '/read msg-am18') },
        { agent: 'kai', slug: 'portineria', raw: ON24, stato_palla: 'lavora', riga: riga('◐', 'Kai ci lavora', 'ON24-F3', 'Hairline', 'kai/onda-electron', '/read msg-on24') },
      ]),
    )
    await $.session.start(START)
    const ui = await mount($)
    expect(await ui.find({ text: /^⧗ in coda: Kai occupato {2}$/ })).toBeDefined()
    expect(await ui.find({ text: /^◐ Kai ci lavora {10}$/ })).toBeDefined()
  })

  test('an older centralino without the ball: the row says it does not know', async ($, on) => {
    world(on, {}, true, vista([{ agent: 'kai', slug: 'portineria', raw: AM18 }]))
    await $.session.start(START)
    const ui = await mount($)
    expect(await ui.find({ text: /stato non noto/ })).toBeDefined()
  })

  test('BR24-F6.8: Da fare above, delegations below, digits go on from one to the other', async ($, on) => {
    const v = JSON.parse(ALITA_WAITS)
    v.result.messaggi = [
      {
        agent: 'kai', slug: 'portineria', letto: false, stato_msg: 'nuova',
        raw: { id: 'msg-n', from: 'alita', from_session: 'alita/work-hub', intent: 'br24-f6.7', priority: 'normal', ts: '2026-10-18T08:58:00Z' },
        riga: { glifo: '✉', parola: 'delega nuova', ordine: 1, codice: 'BR24-F6.7', piano: 'BR24', titolo: 'Squadra nella banda solo con le eccezioni', mittente: 'alita/work-hub', leggi: '/read msg-n' },
      },
      {
        agent: 'kai', slug: 'portineria', letto: false, stato_msg: 'lavori',
        raw: { id: 'msg-l', from: 'alita', intent: 'br24-f6.6', priority: 'urgent', ts: '2026-10-18T08:52:00Z' },
        riga: { glifo: '◐', parola: 'ci lavori', ordine: 3, codice: 'BR24-F6.6', piano: 'BR24', titolo: 'Banda: in coda o ci lavora', mittente: 'alita/work-hub', leggi: '/read msg-l' },
      },
      {
        agent: 'kai', slug: 'portineria', letto: false, stato_msg: 'ritorno', riga: null,
        raw: { id: 'msg-r', from: 'kai', intent: 're', priority: 'normal', ts: '2026-10-18T08:55:00Z' },
      },
    ]
    world(on, {}, true, JSON.stringify(v))
    await $.session.start(START)
    const ui = await mount($)
    expect(await ui.find({ text: 'Da fare · 2' })).toBeDefined()
    expect(await ui.find({ text: /^◐ ci lavori · urgente {2}$/ })).toBeDefined()
    expect(await ui.find({ text: /^✉ delega nuova {9}$/ })).toBeDefined()
    expect(await ui.find({ text: /da alita\/work-hub/ })).toBeDefined()
    expect(await ui.find({ text: /msg-r|re$/ })).toBeUndefined()
    // keys 1-2 for Da fare, 3-4 for the delegations
    expect(await ui.find({ text: /3-4 o clic: apri/ })).toBeDefined()
    expect(await ui.find({ text: /Bus kai\/portineria/ })).toBeUndefined()
  })
})

