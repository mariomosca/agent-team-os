export type Priority = 'urgent' | 'high' | 'normal' | 'low'

export type BusMessage = {
  id: string
  from: string
  intent: string
  priority: Priority
  type: string
  ts: string
}

export type BusSnapshot = {
  agent: string
  slug: string
  messages: BusMessage[]
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

export type TeamRow = {
  key: string
  name: string
  text: string
  stuck: boolean
  unmounted: boolean
  workspaceId: string
  canMount: boolean
}

export type TeamSnapshot = {
  headline: string
  rows: TeamRow[]
  hidden: number
  sessions: TeamSession[]
}

export type WaitRow = {
  key: string
  text: string
  alert: boolean
}

export type WaitsSnapshot = {
  headline: string
  rows: WaitRow[]
  hidden: number
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
