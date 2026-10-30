export type Priority = 'urgent' | 'high' | 'normal' | 'low'

export type BusMessage = {
  id: string
  from: string
  intent: string
  priority: Priority
  type: string
  ts: string
  // BR24-F6.8: the state for this session and the row, as the centralino's vista computed them.
  state: string | null
  row: Record<string, unknown> | null
}

// One message to do (BR24-F6.8), drawn like a delegation row.
export type TodoRow = {
  key: string
  state: string
  glyph: string
  label: string
  code: string | null
  plan: string | null
  title: string
  from: string
  since: string
  read: string
  pressing: boolean
}

export type BusSnapshot = {
  agent: string
  slug: string
  messages: BusMessage[]
  rows: TodoRow[]
  elsewhere: number
}

export type TeamState =
  | 'working'
  | 'idle'
  | 'waiting_permission'
  | 'waiting_answer'
  | 'pending_input'
  | 'exited'

export type TeamSession = {
  sessionId: string
  // WT30: the pane to focus on mount (several agents in one workspace: worktrees of one repo)
  terminalId: string | null
  workspaceId: string
  agent: string | null
  slug: string
  cwd: string
  state: TeamState
  stateSince: string
  lastTool: string | null
  mounted: boolean
  host: string
}

// BR24-F6.7: one session that needs Mario, in word and glyph.
export type TeamRow = {
  key: string
  kind: 'testo' | 'permesso' | 'domanda' | 'canale'
  glyph: string
  label: string
  name: string
  since: string
  stuck: boolean
  unmounted: boolean
  workspaceId: string | null
  terminalId: string | null
  canMount: boolean
}

export type TeamSnapshot = {
  headline: string
  rows: TeamRow[]
  hidden: number
  sessions: TeamSession[]
}

// One delegation this session waits for (BR24-F6.4): who holds the ball, in words
// and glyph, the plan code, a one-line title, the executor, and what a click opens.
export type WaitRow = {
  key: string
  state: 'domanda' | 'esito' | 'scaduta' | 'mario' | 'consegnato' | 'fermo' | 'chiusa' | 'coda' | 'lavora' | 'da_verificare' | 'ignoto'
  glyph: string
  label: string
  code: string | null
  plan: string | null
  title: string
  who: string
  since: string
  read: string | null
  alert: boolean
  note: string | null
}

export type WaitsSnapshot = {
  headline: string
  rows: WaitRow[]
  hidden: number
  footer: string
  expired: string[]
}

declare module 'claude-code' {
  interface PluginState {
    'bus-band': {
      snapshot: BusSnapshot | null
      isHidden: boolean
      team: TeamSnapshot | null
      isTeamHidden: boolean
      waits: WaitsSnapshot | null
      fallback: string | null
    }
  }
}
