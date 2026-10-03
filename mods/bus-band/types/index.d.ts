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

declare module 'claude-code' {
  interface PluginState {
    'bus-band': { snapshot: BusSnapshot | null; isHidden: boolean }
  }
}
