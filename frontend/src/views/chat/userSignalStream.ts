interface UserSignal {
  type?: string
  session_id?: string
  run_id?: string
  status?: string
}

interface UserSignalStreamCallbacks {
  onSignal: (signal: UserSignal) => void
  onOpen?: () => void
  onParseError?: (error: unknown) => void
}

export interface UserSignalEventSource {
  addEventListener: (type: string, listener: (event: MessageEvent) => void) => void
  close: () => void
}

type EventSourceFactory = (url: string) => UserSignalEventSource

/** 订阅用户级信令流：该用户任意会话的 run 状态变化（hint，不承载内容）。 */
export function createUserSignalEventSource(
  callbacks: UserSignalStreamCallbacks,
  factory: EventSourceFactory = (url) => new EventSource(url),
): UserSignalEventSource {
  const source = factory(`${location.origin}/api/chat/events/stream`)
  source.addEventListener('user-signal', (event) => {
    try {
      const payload = JSON.parse(event.data)
      if (payload && typeof payload === 'object') {
        callbacks.onSignal(payload as UserSignal)
      }
    } catch (error) {
      callbacks.onParseError?.(error)
    }
  })
  source.addEventListener('open', () => {
    callbacks.onOpen?.()
  })
  return source
}
