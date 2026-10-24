import type { BusMessage, Priority } from '../types'

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
