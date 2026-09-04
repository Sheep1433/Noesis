import type { StreamDelta } from '@/views/chat/streamDeltaBatcher'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createStreamDeltaBatcher } from '@/views/chat/streamDeltaBatcher'

describe('streamDeltaBatcher', () => {
  beforeEach(() => {
    vi.useFakeTimers()
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('连续同签名 push 合并成一条 delta，flush 前不应用', () => {
    const applied: StreamDelta[][] = []
    const batcher = createStreamDeltaBatcher((deltas) => applied.push(deltas), { flushIntervalMs: 100 })

    batcher.push({ kind: 'text', data: 'a' })
    batcher.push({ kind: 'text', data: 'b' })
    batcher.push({ kind: 'text', data: 'c' })
    expect(applied).toHaveLength(0)

    batcher.flush()
    expect(applied).toEqual([[{ kind: 'text', data: 'abc' }]])
  })

  it('kind / parent / 拆分开关变化时分桶，并保持 push 顺序', () => {
    const applied: StreamDelta[][] = []
    const batcher = createStreamDeltaBatcher((deltas) => applied.push(deltas), { flushIntervalMs: 100 })

    batcher.push({ kind: 'text', data: 'a', redactedThinking: true })
    batcher.push({ kind: 'text', data: 'b', redactedThinking: true })
    batcher.push({ kind: 'text', data: 'c', redactedThinking: false })
    batcher.push({ kind: 'reasoning', data: 'r' })
    batcher.push({ kind: 'text', data: 'a2', parentTaskCallId: 'task-1' })
    batcher.push({ kind: 'text', data: 'b2', parentTaskCallId: 'task-1' })
    batcher.push({ kind: 'text', data: 'main' })
    batcher.flush()

    expect(applied).toEqual([[
      { kind: 'text', data: 'ab', redactedThinking: true },
      { kind: 'text', data: 'c', redactedThinking: false },
      { kind: 'reasoning', data: 'r' },
      { kind: 'text', data: 'a2b2', parentTaskCallId: 'task-1' },
      { kind: 'text', data: 'main' },
    ]])
  })

  it('到点自动 flush，一次性应用', () => {
    const applied: StreamDelta[][] = []
    const batcher = createStreamDeltaBatcher((deltas) => applied.push(deltas), { flushIntervalMs: 100 })

    batcher.push({ kind: 'text', data: 'x' })
    vi.advanceTimersByTime(99)
    expect(applied).toHaveLength(0)

    vi.advanceTimersByTime(1)
    expect(applied).toEqual([[{ kind: 'text', data: 'x' }]])
  })

  it('pendingChars 达阈值同步强 flush（后台 timer 节流下的缓冲量兜底）', () => {
    const applied: StreamDelta[][] = []
    const batcher = createStreamDeltaBatcher((deltas) => applied.push(deltas), {
      flushIntervalMs: 100,
      maxPendingChars: 10,
    })

    batcher.push({ kind: 'text', data: '12345' })
    expect(applied).toHaveLength(0)

    batcher.push({ kind: 'text', data: '67890' })
    expect(applied).toEqual([[{ kind: 'text', data: '1234567890' }]])

    // 强刷后重新调度，后续 delta 正常走定时 flush
    batcher.push({ kind: 'text', data: 'z' })
    vi.advanceTimersByTime(100)
    expect(applied).toEqual([
      [{ kind: 'text', data: '1234567890' }],
      [{ kind: 'text', data: 'z' }],
    ])
  })

  it('空缓冲 flush 不触发 apply（结构性回调可无条件先 flush）', () => {
    const applied: StreamDelta[][] = []
    const batcher = createStreamDeltaBatcher((deltas) => applied.push(deltas))

    batcher.flush()
    batcher.flush()
    expect(applied).toHaveLength(0)
  })

  it('flush 后再 push 重新起定时器，旧内容不重复应用', () => {
    const applied: StreamDelta[][] = []
    const batcher = createStreamDeltaBatcher((deltas) => applied.push(deltas), { flushIntervalMs: 100 })

    batcher.push({ kind: 'text', data: 'a' })
    batcher.flush()
    batcher.push({ kind: 'text', data: 'b' })
    vi.advanceTimersByTime(100)

    expect(applied).toEqual([
      [{ kind: 'text', data: 'a' }],
      [{ kind: 'text', data: 'b' }],
    ])
  })

  it('clear 丢弃缓冲并清定时器', () => {
    const applied: StreamDelta[][] = []
    const batcher = createStreamDeltaBatcher((deltas) => applied.push(deltas), { flushIntervalMs: 100 })

    batcher.push({ kind: 'text', data: 'a' })
    batcher.clear()
    vi.advanceTimersByTime(1000)
    batcher.flush()
    expect(applied).toHaveLength(0)
  })

  it('dispose 后 push 静默忽略', () => {
    const applied: StreamDelta[][] = []
    const batcher = createStreamDeltaBatcher((deltas) => applied.push(deltas), { flushIntervalMs: 100 })

    batcher.push({ kind: 'text', data: 'a' })
    batcher.dispose()
    batcher.push({ kind: 'text', data: 'b' })
    vi.advanceTimersByTime(1000)
    expect(applied).toHaveLength(0)
  })
})
