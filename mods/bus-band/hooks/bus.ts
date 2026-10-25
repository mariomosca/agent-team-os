import type { BusMessage, Priority, TodoRow } from '../types'

// Pure logic of the Agent Team OS bus, mirrored from
// ~/.claude/scripts/agent-team-os-lib.sh (v1.4): agent from cwd, session slug,
// workspace scoping. No `$` here, so the tests run it as is.

export type AgentRule = { pattern: string; match?: string; agent: string }

const PRIORITIES: readonly Priority[] = ['urgent', 'high', 'normal', 'low']

// The longest matching pattern wins, on a folder boundary (/a/work-hub does not
// match /a/work-hub-x): the centralino's rule (BR24, docs/CENTRALINO.md §2).
export const agentForCwd = (rules: readonly AgentRule[], cwd: string) => {
  const c = cwd.replace(/\/+$/, '')
  let best: AgentRule | undefined
  let bestLen = -1
  for (const rule of rules) {
    const p = rule.pattern.replace(/\/+$/, '')
    if (c !== p && !c.startsWith(`${p}/`)) continue
    if (p.length > bestLen) {
      best = rule
      bestLen = p.length
    }
  }

  return best?.agent
}

export const slugForCwd = (cwd: string) => {
  const base = cwd.replace(/\/+$/, '').split('/').pop() || 'root'

  return base.toLowerCase().replace(/[^a-z0-9._-]/g, '-')
}

export const workspaceMatch = (projectPath: string, cwd: string) => {
  if (projectPath === '') return 'global'
  const p = projectPath.replace(/\/+$/, '')
  const c = cwd.replace(/\/+$/, '')
  const isHere = p === c || c.startsWith(`${p}/`) || p.startsWith(`${c}/`)

  return isHere ? 'here' : 'elsewhere'
}

export type Parsed = { message: BusMessage; projectPath: string }

export const parseMessage = (text: string, fallbackId: string): Parsed | null => {
  try {
    const raw = JSON.parse(text)
    const priority = PRIORITIES.includes(raw.priority) ? raw.priority : 'normal'

    return {
      message: {
        id: String(raw.id ?? fallbackId),
        from: String(raw.from ?? '?'),
        intent: String(raw.intent ?? raw.type ?? ''),
        priority,
        type: String(raw.type ?? ''),
        ts: String(raw.ts ?? ''),
        state: typeof raw.stato_msg === 'string' ? raw.stato_msg : null,
        row: typeof raw.riga === 'object' && raw.riga !== null ? raw.riga : null,
      },
      projectPath: typeof raw.context?.project_path === 'string' ? raw.context.project_path : '',
    }
  } catch {
    return null
  }
}

// Most pressing first, newest first within a priority.
export const sortMessages = (messages: readonly BusMessage[]) =>
  [...messages].sort(
    (a, b) =>
      PRIORITIES.indexOf(a.priority) - PRIORITIES.indexOf(b.priority) ||
      b.ts.localeCompare(a.ts),
  )

export const isPressing = (m: BusMessage) => m.priority === 'urgent' || m.priority === 'high'

// Text labels, never colour alone: the person reading this is colour-blind.
export const PRIORITY_LABEL: Record<Priority, string> = {
  urgent: 'URGENTE',
  high: 'ALTA',
  normal: 'normale',
  low: 'bassa',
}

// ---- BR24-F6.8: "Da fare", what this session has to do, one row per message ----
// The centralino says the state of each message for this session (centralino/palla.py,
// `stato_msg` and `riga`); here only the drawing data. A return on a delegation of this
// session (`ritorno`) is not here: it is in "Deleghe aperte".

export const isTodo = (m: BusMessage) => m.state !== 'ritorno'

const age = (iso: string, now: number) => {
  const t = Date.parse(iso)
  const min = Number.isNaN(t) ? 0 : Math.max(0, Math.floor((now - t) / 60_000))

  return min < 1 ? 'meno di 1 min' : min < 120 ? `${min} min` : `${Math.floor(min / 60)} h`
}

const text = (v: unknown) => (typeof v === 'string' && v !== '' ? v.replace(/\s+/g, ' ').trim() : null)

export const toTodoRow = (m: BusMessage, now: number): TodoRow => {
  const r = m.row ?? {}
  const word = text(r.parola) ?? (m.type === '' ? 'messaggio' : m.type)
  // Priority only when it matters, in a word.
  const priority = m.priority === 'urgent' ? 'urgente' : m.priority === 'high' ? 'alta' : null

  return {
    key: m.id,
    state: m.state ?? 'ignoto',
    glyph: text(r.glifo) ?? '·',
    label: priority === null ? word : `${word} · ${priority}`,
    code: text(r.codice),
    plan: text(r.piano),
    title: text(r.titolo) ?? m.intent,
    from: `da ${text(r.mittente) ?? m.from}`,
    since: age(m.ts, now),
    read: text(r.leggi) ?? `/read ${m.id}`,
    pressing: isPressing(m),
  }
}

const order = (m: BusMessage) => (typeof m.row?.ordine === 'number' ? m.row.ordine : 99)

// Urgent and high first, then by state (what sets this session going first), then the oldest.
export const todoRows = (messages: readonly BusMessage[], now: number) =>
  messages
    .filter(isTodo)
    .map(m => ({ m, r: toTodoRow(m, now) }))
    .sort(
      (a, b) =>
        Number(b.r.pressing) - Number(a.r.pressing) || order(a.m) - order(b.m) || a.m.ts.localeCompare(b.m.ts),
    )
    .map(x => x.r)

export const todoHeadline = (rows: readonly TodoRow[], elsewhere: number) =>
  `Da fare · ${rows.length}${elsewhere > 0 ? ` (+${elsewhere} di altri workspace)` : ''}`

