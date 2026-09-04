import { describe, expect, it, vi } from 'vitest'
import { consumeRunStream, parseSseFrame, parseSseFrames } from '@/views/chat/useRunStreamClient'

function sseResponse(
  chunks: string[],
  status = 200,
): Response {
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      const encoder = new TextEncoder()
      for (const chunk of chunks) {
        controller.enqueue(encoder.encode(chunk))
      }
      controller.close()
    },
  })
  return new Response(body, { status })
}

function jsonFrame(event: string, data: Record<string, unknown>): string {
  return `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`
}

const DONE_FRAME = 'data: [DONE]\n\n'

/** 领域终态建模：收到 [DONE]（data===null）后视为完成，isActive 翻 false */
function domainSettlesOnDone() {
  const state = { active: true }
  return {
    isActive: () => state.active,
    onFrame: (event: string, data: Record<string, unknown> | null, dataStr: string) => {
      if (data === null && dataStr === '[DONE]') {
        state.active = false
      }
    },
  }
}

describe('parseSseFrames / parseSseFrame', () => {
  it('cRlf 分隔与多行 data', () => {
    const { frames, rest } = parseSseFrames('event: a\r\ndata: l1\r\ndata: l2\r\n\r\nevent: b\r\n')
    expect(frames).toHaveLength(1)
    expect(rest).toBe('event: b\r\n')
    const parsed = parseSseFrame(frames[0])
    expect(parsed.event).toBe('a')
    expect(parsed.dataStr).toBe('l1\nl2')
  })

  it('注释帧（keepalive）不产生 event/data', () => {
    const parsed = parseSseFrame(': keepalive')
    expect(parsed.event).toBe('message')
    expect(parsed.dataStr).toBe('')
  })
})

describe('consumeRunStream', () => {
  it('正常流：帧分派 + [DONE] 以 data===null 交付', async () => {
    const domain = domainSettlesOnDone()
    const frames: Array<{ event: string, data: Record<string, unknown> | null }> = []
    await consumeRunStream({
      subscribe: () => Promise.resolve(sseResponse([
        jsonFrame('text-delta', { sequence: 1, text_delta: 'hi' }),
        DONE_FRAME,
      ])),
      onFrame: (event, data, dataStr) => {
        frames.push({ event, data })
        domain.onFrame(event, data, dataStr)
      },
      isActive: domain.isActive,
      maxAttempts: 1,
      backoffMs: () => 0,
    })
    expect(frames).toEqual([
      { event: 'text-delta', data: { sequence: 1, text_delta: 'hi' } },
      { event: 'message', data: null },
    ])
  })

  it('坏 JSON 帧静默跳过', async () => {
    const domain = domainSettlesOnDone()
    const seen: string[] = []
    await consumeRunStream({
      subscribe: () => Promise.resolve(sseResponse([
        'data: not-json\n\n',
        jsonFrame('ok', { a: 1 }),
        DONE_FRAME,
      ])),
      onFrame: (event, data, dataStr) => {
        if (data) {
          seen.push(event)
        }
        domain.onFrame(event, data, dataStr)
      },
      isActive: domain.isActive,
      maxAttempts: 1,
      backoffMs: () => 0,
    })
    expect(seen).toEqual(['ok'])
  })

  it('fatalStatuses 永久退出（不重试）', async () => {
    const subscribe = vi.fn(() => Promise.resolve(new Response('gone', { status: 404 })))
    await consumeRunStream({
      subscribe,
      onFrame: () => {},
      isActive: () => true,
      maxAttempts: Infinity,
      backoffMs: () => 0,
      fatalStatuses: [401, 404],
    })
    expect(subscribe).toHaveBeenCalledTimes(1)
  })

  it('流提前结束 → resync 收口 → 重连续传', async () => {
    const domain = domainSettlesOnDone()
    const resync = vi.fn()
    const subscribe = vi
      .fn()
      .mockResolvedValueOnce(sseResponse([jsonFrame('text-delta', { sequence: 1 })])) // 无 [DONE] 即断
      .mockResolvedValueOnce(sseResponse([DONE_FRAME]))
    await consumeRunStream({
      subscribe,
      onFrame: domain.onFrame,
      isActive: domain.isActive,
      maxAttempts: 2,
      backoffMs: () => 0,
      resync,
    })
    expect(subscribe).toHaveBeenCalledTimes(2)
    expect(resync).toHaveBeenCalledTimes(1)
  })

  it('resync 抛错向上传播（不吞进重试）', async () => {
    const subscribe = vi.fn().mockResolvedValue(sseResponse(['']))
    await expect(consumeRunStream({
      subscribe,
      onFrame: () => {},
      isActive: () => true,
      maxAttempts: 3,
      backoffMs: () => 0,
      resync: () => Promise.reject(new Error('snapshot unreachable')),
    })).rejects.toThrow('snapshot unreachable')
    expect(subscribe).toHaveBeenCalledTimes(1)
  })

  it('onFrame 返回 stop → 断开当前连接走恢复流程', async () => {
    const domain = domainSettlesOnDone()
    const resync = vi.fn()
    const subscribe = vi
      .fn()
      .mockResolvedValueOnce(sseResponse([
        jsonFrame('text-delta', { sequence: 1 }),
        jsonFrame('text-delta', { sequence: 3 }), // gap → stop
        jsonFrame('text-delta', { sequence: 9 }), // 不应到达
      ]))
      .mockResolvedValueOnce(sseResponse([DONE_FRAME]))
    const seen: number[] = []
    await consumeRunStream({
      subscribe,
      onFrame: (event, data, dataStr) => {
        domain.onFrame(event, data, dataStr)
        if (data) {
          seen.push(Number(data.sequence))
          return Number(data.sequence) === 3 ? 'stop' : undefined
        }
      },
      isActive: domain.isActive,
      maxAttempts: 2,
      backoffMs: () => 0,
      resync,
    })
    expect(seen).toEqual([1, 3])
    expect(resync).toHaveBeenCalledTimes(1)
  })

  it('重试耗尽 → 默认抛「连接已中断」', async () => {
    const subscribe = vi.fn(() => Promise.resolve(sseResponse([''])))
    await expect(consumeRunStream({
      subscribe,
      onFrame: () => {},
      isActive: () => true,
      maxAttempts: 3,
      backoffMs: () => 0,
    })).rejects.toThrow('连接已中断')
    expect(subscribe).toHaveBeenCalledTimes(3)
  })

  it('重试耗尽 → onExhausted 回调（可见失败）', async () => {
    const exhausted = vi.fn()
    const subscribe = vi.fn(() => Promise.reject(new Error('network down')))
    await consumeRunStream({
      subscribe,
      onFrame: () => {},
      isActive: () => true,
      maxAttempts: 2,
      backoffMs: () => 0,
      onExhausted: exhausted,
    })
    expect(exhausted).toHaveBeenCalledTimes(1)
    expect(subscribe).toHaveBeenCalledTimes(2)
  })

  it('isActive 失效 → 断流后安静退出不再重连', async () => {
    let active = true
    const subscribe = vi.fn().mockResolvedValue(sseResponse([jsonFrame('run-status', { status: 'running' })]))
    await consumeRunStream({
      subscribe,
      onFrame: () => {
        active = false
      },
      isActive: () => active,
      maxAttempts: Infinity,
      backoffMs: () => 0,
    })
    // 首连帧处理后失效：流结束、不再重连
    expect(subscribe).toHaveBeenCalledTimes(1)
  })

  it('读超时 → 断开进入恢复流程', async () => {
    vi.useFakeTimers()
    try {
      const body = new ReadableStream<Uint8Array>({ start() { /* 永不产出：模拟半开连接 */ } })
      const subscribe = vi
        .fn()
        .mockResolvedValueOnce(new Response(body, { status: 200 }))
        .mockResolvedValueOnce(sseResponse([DONE_FRAME]))
      const domain = domainSettlesOnDone()
      const promise = consumeRunStream({
        subscribe,
        onFrame: domain.onFrame,
        isActive: domain.isActive,
        maxAttempts: 2,
        backoffMs: () => 0,
        readTimeoutMs: 50,
      })
      await vi.advanceTimersByTimeAsync(80)
      await promise
      expect(subscribe).toHaveBeenCalledTimes(2)
    } finally {
      vi.useRealTimers()
    }
  })
})
