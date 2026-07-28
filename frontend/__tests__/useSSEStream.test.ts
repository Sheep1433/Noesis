import type { AgentRunSnapshot } from '@/api/chat'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { useSSEStream } from '@/views/chat/useSSEStream'

const api = vi.hoisted(() => ({
  createAgentRun: vi.fn(),
  getAgentRun: vi.fn(),
  resumeAgentRunHitl: vi.fn(),
  resumeAgentRunTestCase: vi.fn(),
  stopAgentRun: vi.fn(),
  subscribeAgentRun: vi.fn(),
}))

vi.mock('@/api/chat', () => api)

class MemoryStorage implements Storage {
  private readonly values = new Map<string, string>()

  get length() {
    return this.values.size
  }

  clear() {
    this.values.clear()
  }

  getItem(key: string) {
    return this.values.get(key) ?? null
  }

  key(index: number) {
    return [...this.values.keys()][index] ?? null
  }

  removeItem(key: string) {
    this.values.delete(key)
  }

  setItem(key: string, value: string) {
    this.values.set(key, value)
  }
}

function snapshot(overrides: Partial<AgentRunSnapshot> = {}): AgentRunSnapshot {
  return {
    run_id: 'run-1',
    assistant_message_id: 'assistant-1',
    session_id: 'session-1',
    qa_type: 'COMMON_QA',
    origin: 'web',
    status: 'running',
    snapshot_sequence: 0,
    attempt_id: 1,
    content: { parts: [] },
    retry_attempt: 0,
    retry_max: 2,
    ...overrides,
  }
}

function sseResponse(frames: Array<{ event: string, data: Record<string, unknown> | '[DONE]' }>) {
  const body = frames.map(({ event, data }) => {
    const payload = data === '[DONE]' ? data : JSON.stringify(data)
    return `event: ${event}\ndata: ${payload}\n\n`
  }).join('')
  return new Response(body, { status: 200 })
}

describe('useSSEStream durable run recovery', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    Object.defineProperty(globalThis, 'sessionStorage', {
      configurable: true,
      value: new MemoryStorage(),
    })
    api.createAgentRun.mockResolvedValue({
      run_id: 'run-1',
      assistant_message_id: 'assistant-1',
      session_id: 'session-1',
      status: 'running',
    })
  })

  it('刷新后恢复同一 run，并以服务端终态 snapshot 收尾', async () => {
    sessionStorage.setItem('noesis:active-run:session-1', 'run-1')
    api.getAgentRun.mockResolvedValue(snapshot({ snapshot_sequence: 4 }))
    api.subscribeAgentRun.mockResolvedValue(sseResponse([
      {
        event: 'run-snapshot',
        data: snapshot({ status: 'completed', snapshot_sequence: 5, finish_reason: 'stop' }),
      },
      { event: 'message', data: '[DONE]' },
    ]))
    const onFinish = vi.fn()
    const onMessageStart = vi.fn()
    const stream = useSSEStream({ onFinish, onMessageStart })

    await stream.resumeActiveRun('session-1')

    expect(api.subscribeAgentRun).toHaveBeenCalledWith('run-1', 4, expect.any(AbortSignal))
    expect(onMessageStart).not.toHaveBeenCalled()
    expect(onFinish).toHaveBeenCalledOnce()
    expect(sessionStorage.getItem('noesis:active-run:session-1')).toBeNull()
  })

  it('从权威 snapshot 恢复 HITL 审批，不依赖实时事件仍在连接', async () => {
    sessionStorage.setItem('noesis:active-run:session-1', 'run-hitl')
    api.getAgentRun.mockResolvedValue(snapshot({
      run_id: 'run-hitl',
      status: 'hitl_pending',
      snapshot_sequence: 8,
      pending_hitl: {
        interrupt_id: 'interrupt-1',
        kind: 'approval',
        action_requests: [{ tool_call_id: 'call-curl', name: 'execute', args: {} }],
        review_configs: [],
        expires_at: 123,
      },
    }))
    api.subscribeAgentRun.mockResolvedValue(sseResponse([
      {
        event: 'run-snapshot',
        data: snapshot({
          run_id: 'run-hitl',
          status: 'hitl_pending',
          snapshot_sequence: 8,
          pending_hitl: {
            interrupt_id: 'interrupt-1',
            kind: 'approval',
            action_requests: [{ tool_call_id: 'call-curl', name: 'execute', args: {} }],
          },
        }),
      },
    ]))
    const onCustomEvent = vi.fn()
    const stream = useSSEStream({ onCustomEvent })

    const pending = stream.resumeActiveRun('session-1')
    await vi.waitFor(() => expect(onCustomEvent).toHaveBeenCalledWith(
      'hitl-required',
      expect.objectContaining({
        interrupt_id: 'interrupt-1',
        run_id: 'run-hitl',
        session_id: 'session-1',
      }),
    ))
    stream.abortStream()
    await pending
  })

  it('刷新后内存 run id 丢失时仍可用持久化 run id 提交 HITL 决策', async () => {
    sessionStorage.setItem('noesis:active-run:session-1', 'run-hitl')
    api.resumeAgentRunHitl.mockResolvedValue(snapshot({
      run_id: 'run-hitl',
      status: 'running',
      snapshot_sequence: 9,
      pending_hitl: undefined,
    }))
    api.getAgentRun.mockResolvedValue(snapshot({
      run_id: 'run-hitl',
      status: 'running',
      snapshot_sequence: 9,
    }))
    api.subscribeAgentRun.mockResolvedValue(sseResponse([
      {
        event: 'run-snapshot',
        data: snapshot({
          run_id: 'run-hitl',
          status: 'completed',
          snapshot_sequence: 10,
          finish_reason: 'stop',
        }),
      },
    ]))
    const stream = useSSEStream()

    await stream.resumeHitl('session-1', {
      interrupt_id: 'interrupt-1',
      decisions: [{ type: 'approve' }],
      grant_scope: 'once',
    })

    expect(api.resumeAgentRunHitl).toHaveBeenCalledWith('run-hitl', {
      interrupt_id: 'interrupt-1',
      decisions: [{ type: 'approve' }],
      grant_scope: 'once',
    })
    await vi.waitFor(() => expect(api.subscribeAgentRun).toHaveBeenCalledWith(
      'run-hitl', 9, expect.any(AbortSignal),
    ))
  })

  it('切换会话会释放旧订阅，旧 Run 后续事件不得污染新会话', async () => {
    sessionStorage.setItem('noesis:active-run:session-1', 'run-1')
    sessionStorage.setItem('noesis:active-run:session-2', 'run-2')
    api.getAgentRun.mockImplementation(async (runId: string) => snapshot({
      run_id: runId,
      session_id: runId === 'run-1' ? 'session-1' : 'session-2',
      snapshot_sequence: 1,
    }))

    let releaseOldStream!: (response: Response) => void
    const oldStream = new Promise<Response>((resolve) => {
      releaseOldStream = resolve
    })
    api.subscribeAgentRun.mockImplementation((runId: string) => {
      if (runId === 'run-1') {
        return oldStream
      }
      return Promise.resolve(sseResponse([{
        event: 'run-snapshot',
        data: snapshot({
          run_id: 'run-2',
          session_id: 'session-2',
          status: 'completed',
          snapshot_sequence: 2,
        }),
      }]))
    })
    const onSnapshot = vi.fn()
    const stream = useSSEStream({ onSnapshot })

    const first = stream.resumeActiveRun('session-1')
    await vi.waitFor(() => expect(api.subscribeAgentRun).toHaveBeenCalledWith(
      'run-1', 1, expect.any(AbortSignal),
    ))
    const second = stream.resumeActiveRun('session-2')
    await second
    releaseOldStream(sseResponse([{
      event: 'run-snapshot',
      data: snapshot({
        run_id: 'run-1',
        session_id: 'session-1',
        status: 'completed',
        snapshot_sequence: 2,
      }),
    }]))
    await first

    expect(onSnapshot).not.toHaveBeenCalledWith(expect.objectContaining({
      run_id: 'run-1',
      status: 'completed',
    }))
    expect(onSnapshot).toHaveBeenLastCalledWith(expect.objectContaining({ run_id: 'run-2' }))
  })

  it('hitl 审批严格使用目标 session 的 run id，不使用上一会话 currentRunId', async () => {
    sessionStorage.setItem('noesis:active-run:session-1', 'run-1')
    sessionStorage.setItem('noesis:active-run:session-2', 'run-2')
    api.getAgentRun.mockImplementation(async (runId: string) => snapshot({
      run_id: runId,
      session_id: runId === 'run-1' ? 'session-1' : 'session-2',
      status: runId === 'run-2' ? 'completed' : 'running',
    }))
    api.subscribeAgentRun.mockImplementation((_runId: string, _after: number, signal: AbortSignal) => (
      new Promise((_resolve, reject) => {
        signal.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError')))
      })
    ))
    api.resumeAgentRunHitl.mockResolvedValue(snapshot({
      run_id: 'run-2',
      session_id: 'session-2',
      status: 'running',
      snapshot_sequence: 3,
    }))
    const stream = useSSEStream()
    const first = stream.resumeActiveRun('session-1')
    await vi.waitFor(() => expect(api.subscribeAgentRun).toHaveBeenCalledWith(
      'run-1', 0, expect.any(AbortSignal),
    ))

    await stream.resumeHitl('session-2', {
      interrupt_id: 'interrupt-2',
      decisions: [{ type: 'approve' }],
    })
    await first

    expect(api.resumeAgentRunHitl).toHaveBeenCalledWith('run-2', expect.objectContaining({
      interrupt_id: 'interrupt-2',
    }))
  })

  it('遇到 sequence gap 时丢弃缺口事件，查询 snapshot 后完成', async () => {
    api.subscribeAgentRun.mockResolvedValue(sseResponse([
      { event: 'text-delta', data: { type: 'text-delta', sequence: 1, text_delta: 'A' } },
      { event: 'text-delta', data: { type: 'text-delta', sequence: 3, text_delta: '不应展示' } },
    ]))
    api.getAgentRun.mockResolvedValue(snapshot({
      status: 'completed',
      snapshot_sequence: 3,
      finish_reason: 'stop',
      content: { parts: [{ type: 'text', content: 'AB' }] },
    }))
    const onTextDelta = vi.fn()
    const onSnapshot = vi.fn()
    const stream = useSSEStream({ onTextDelta, onSnapshot })

    await stream.sendMessage('session-1', 'hello')

    expect(onTextDelta).toHaveBeenCalledTimes(1)
    expect(onTextDelta).toHaveBeenCalledWith('A', undefined)
    expect(onSnapshot).toHaveBeenCalledWith(expect.objectContaining({ snapshot_sequence: 3 }))
  })

  it('创建 ACK 丢失时使用同一 client_request_id 重试', async () => {
    api.createAgentRun
      .mockRejectedValueOnce(new TypeError('network lost'))
      .mockResolvedValueOnce({
        run_id: 'run-1',
        assistant_message_id: 'assistant-1',
        session_id: 'session-1',
        status: 'running',
      })
    api.subscribeAgentRun.mockResolvedValue(sseResponse([
      {
        event: 'run-snapshot',
        data: snapshot({ status: 'completed', snapshot_sequence: 1, finish_reason: 'stop' }),
      },
    ]))
    const stream = useSSEStream()

    await stream.sendMessage('session-1', 'hello')

    expect(api.createAgentRun).toHaveBeenCalledTimes(2)
    const firstKey = api.createAgentRun.mock.calls[0][0].client_request_id
    const secondKey = api.createAgentRun.mock.calls[1][0].client_request_id
    expect(secondKey).toBe(firstKey)
  })

  it('向用户报告 retrying，并只在重试耗尽后报告最终失败', async () => {
    api.subscribeAgentRun.mockResolvedValue(sseResponse([
      {
        event: 'run-status',
        data: { type: 'run-status', sequence: 1, status: 'retrying', message: '正在重试' },
      },
      {
        event: 'run-status',
        data: { type: 'run-status', sequence: 2, status: 'running' },
      },
      {
        event: 'error',
        data: { type: 'error', sequence: 3, error: '暂时无法生成，请稍后重试' },
      },
    ]))
    const onRunStatus = vi.fn()
    const onError = vi.fn()
    const stream = useSSEStream({ onRunStatus, onError })

    await stream.sendMessage('session-1', 'hello')

    expect(onRunStatus).toHaveBeenNthCalledWith(1, 'retrying', '正在重试')
    expect(onRunStatus).toHaveBeenNthCalledWith(2, 'running', undefined)
    expect(onError).toHaveBeenCalledOnce()
    expect(onError).toHaveBeenCalledWith('暂时无法生成，请稍后重试')
  })

  it('明确停止时调用 run stop，断开订阅不伪造网络失败', async () => {
    api.subscribeAgentRun.mockImplementation((_runId: string, _after: number, signal: AbortSignal) => (
      new Promise((_resolve, reject) => {
        signal.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError')))
      })
    ))
    api.stopAgentRun.mockResolvedValue(snapshot({
      status: 'partial',
      snapshot_sequence: 2,
      finish_reason: 'stopped',
    }))
    const onFinish = vi.fn()
    const onError = vi.fn()
    const stream = useSSEStream({ onFinish, onError })

    const pending = stream.sendMessage('session-1', 'hello')
    await vi.waitFor(() => expect(api.subscribeAgentRun).toHaveBeenCalledOnce())
    await stream.stopCurrentRun()
    await pending

    expect(api.stopAgentRun).toHaveBeenCalledWith('run-1')
    expect(onFinish).toHaveBeenCalledWith({ finish_reason: 'stopped' })
    expect(onError).not.toHaveBeenCalled()
  })

  it('重连持续失败时保留 active run 并提供手动恢复，不伪造 Agent 失败', async () => {
    vi.useFakeTimers()
    try {
      api.subscribeAgentRun.mockRejectedValue(new TypeError('offline'))
      api.getAgentRun.mockResolvedValue(snapshot())
      const onError = vi.fn()
      const onRunStatus = vi.fn()
      const stream = useSSEStream({ onError, onRunStatus })

      const pending = stream.sendMessage('session-1', 'hello')
      await vi.runAllTimersAsync()
      await pending

      expect(api.subscribeAgentRun).toHaveBeenCalledTimes(6)
      expect(api.getAgentRun).toHaveBeenCalledTimes(5)
      expect(onError).not.toHaveBeenCalled()
      expect(onRunStatus).toHaveBeenCalledWith('disconnected', '连接已中断，可重新连接')
      expect(sessionStorage.getItem('noesis:active-run:session-1')).toBe('run-1')
    } finally {
      vi.useRealTimers()
    }
  })
})
