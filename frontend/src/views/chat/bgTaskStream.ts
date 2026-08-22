import type { TaskCatalogEntry } from '@/api/chat'

interface BgTaskStreamCallbacks {
  onTask: (task: TaskCatalogEntry) => void
  onContinuation: (payload: Record<string, unknown>) => void
  onParseError?: (error: unknown) => void
}

export interface BgTaskEventSource {
  addEventListener: (type: string, listener: (event: MessageEvent) => void) => void
  close: () => void
}

type EventSourceFactory = (url: string) => BgTaskEventSource

export function createBgTaskEventSource(
  sessionId: string,
  callbacks: BgTaskStreamCallbacks,
  factory: EventSourceFactory = (url) => new EventSource(url),
): BgTaskEventSource {
  const source = factory(
    `${location.origin}/api/chat/sessions/${encodeURIComponent(sessionId)}/children/stream`,
  )
  source.addEventListener('bg-task', (event) => {
    try {
      const payload = JSON.parse(event.data)
      if (payload?.task) {
        callbacks.onTask(payload.task as TaskCatalogEntry)
      }
    } catch (error) {
      callbacks.onParseError?.(error)
    }
  })
  source.addEventListener('child-session', (event) => {
    try {
      const payload = JSON.parse(event.data)
      if (payload?.child) {
        callbacks.onTask({
          task_id: String(payload.child.session_id),
          child_session_id: String(payload.child.session_id),
          created_by_tool_call_id: payload.child.created_by_tool_call_id || null,
          session_id: sessionId,
          user_id: undefined,
          description: String(payload.child.title || '子 Agent'),
          kind: 'subagent',
          status: (payload.child.status || 'completed') as TaskCatalogEntry['status'],
          run_id: payload.child.run_id || null,
          started_at: payload.child.started_at || undefined,
          completed_at: payload.child.finished_at || null,
          interrupt: payload.child.interrupt || null,
          progress_count: Number(payload.child.step_count || 0),
        })
      }
    } catch (error) {
      callbacks.onParseError?.(error)
    }
  })
  source.addEventListener('bg-continuation', (event) => {
    try {
      callbacks.onContinuation(JSON.parse(event.data) as Record<string, unknown>)
    } catch (error) {
      callbacks.onParseError?.(error)
    }
  })
  return source
}

interface ActivateBgTaskSessionOptions {
  sessionId: string
  currentSessionId: string | null
  hasStream: boolean
  setCurrentSession: (sessionId: string) => void
  openStream: (sessionId: string) => void
}

/** 首次物化或连接缺失时激活会话后台任务流，已有连接不重复重开。 */
export function activateBgTaskSession(options: ActivateBgTaskSessionOptions): void {
  const shouldOpen = options.currentSessionId !== options.sessionId || !options.hasStream
  options.setCurrentSession(options.sessionId)
  if (shouldOpen) {
    options.openStream(options.sessionId)
  }
}
