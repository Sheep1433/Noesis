import { describe, expect, it } from 'vitest'
import {
  initialRunEventState,
  parseRunEvent,
  runEventReducer,
} from '@/views/chat/runEventReducer'

const snapshot = {
  run_id: 'run-1',
  assistant_message_id: 'am-1',
  session_id: 's-1',
  qa_type: 'SUPER_AGENT_QA',
  origin: 'web',
  status: 'running',
  snapshot_sequence: 3,
  attempt_id: 1,
  content: { version: 1, parts: [{ type: 'text', content: 'hi' }] },
}

describe('runEventReducer', () => {
  it('快照重置：run 整体替换并清统计与终态时刻', () => {
    const state = runEventReducer(initialRunEventState(), { type: 'run-snapshot', snapshot: snapshot as never })
    expect(state.run?.run_id).toBe('run-1')
    expect(state.stats).toBeNull()
    expect(state.finishedAt).toBeNull()
  })

  it('增量事件推进 snapshot_sequence，迟到序号不回退', () => {
    let state = runEventReducer(initialRunEventState(), { type: 'run-snapshot', snapshot: snapshot as never })
    state = runEventReducer(state, { type: 'context-update', context: { current_tokens: 1 }, sequence: 5 })
    expect(state.run?.snapshot_sequence).toBe(5)
    state = runEventReducer(state, { type: 'context-update', context: { current_tokens: 2 }, sequence: 4 })
    expect(state.run?.snapshot_sequence).toBe(5)
    expect(state.contextSnapshot).toEqual({ current_tokens: 2 })
  })

  it('approval-resumed 清空 pending_hitl 并回到 running', () => {
    let state = runEventReducer(initialRunEventState(), { type: 'run-snapshot', snapshot: { ...snapshot, pending_hitl: { kind: 'approval' } } as never })
    state = runEventReducer(state, { type: 'approval-resumed', sequence: 6 })
    expect(state.run?.pending_hitl).toBeNull()
    expect(state.run?.status).toBe('running')
  })

  it('run-started 仅把 queued 推进到 running', () => {
    let state = runEventReducer(initialRunEventState(), { type: 'run-snapshot', snapshot: { ...snapshot, status: 'queued' } as never })
    state = runEventReducer(state, { type: 'run-started', sequence: 4 })
    expect(state.run?.status).toBe('running')
    state = runEventReducer(state, { type: 'run-started', sequence: 5 })
    expect(state.run?.status).toBe('running')
  })

  it('approval 挂起与恢复的往返', () => {
    let state = runEventReducer(initialRunEventState(), { type: 'run-snapshot', snapshot: snapshot as never })
    const pending = { kind: 'approval', interrupt_id: 'i-1' }
    state = runEventReducer(state, { type: 'approval-required', content: { parts: [] }, pendingHitl: pending as never, sequence: 7 })
    expect(state.run?.status).toBe('hitl_pending')
    expect(state.run?.pending_hitl).toEqual(pending)
    state = runEventReducer(state, { type: 'approval-resumed', sequence: 8 })
    expect(state.run?.status).toBe('running')
    expect(state.run?.pending_hitl).toBeNull()
  })

  it('run-finished 置终态与终态时刻；快照重置后新 turn 可再次落终态', () => {
    let state = runEventReducer(initialRunEventState(), { type: 'run-snapshot', snapshot: snapshot as never })
    state = runEventReducer(state, { type: 'run-finished', status: 'completed', finishedAt: 1234, sequence: 9 })
    expect(state.run?.status).toBe('completed')
    expect(state.finishedAt).toBe(1234)
    // 事件为权威：重放的终态更新状态值；消息列表的一次性落盘由宿主 guard
    state = runEventReducer(state, { type: 'run-finished', status: 'completed', finishedAt: 9999, sequence: 9 })
    expect(state.finishedAt).toBe(9999)
    // 新 turn 快照重置终态时刻
    state = runEventReducer(state, { type: 'run-snapshot', snapshot: { ...snapshot, run_id: 'run-2', assistant_message_id: 'am-2' } as never })
    expect(state.finishedAt).toBeNull()
    state = runEventReducer(state, { type: 'run-finished', status: 'completed', finishedAt: 5678, sequence: 2 })
    expect(state.finishedAt).toBe(5678)
  })

  it('无 run 时的 run-finished 只记录终态时刻', () => {
    const state = runEventReducer(initialRunEventState(), { type: 'run-finished', status: 'completed', finishedAt: 100 })
    expect(state.run).toBeNull()
    expect(state.finishedAt).toBe(100)
  })
})

describe('parseRunEvent（子会话 wire → 领域事件）', () => {
  it('run-snapshot 直通；未知事件返回 null', () => {
    expect(parseRunEvent('run-snapshot', snapshot as never)?.type).toBe('run-snapshot')
    expect(parseRunEvent('whatever', {})).toBeNull()
  })

  it('context-update 缺 context 字段时丢弃', () => {
    expect(parseRunEvent('context-update', { sequence: 1 })).toBeNull()
    expect(parseRunEvent('context-update', { context: { current_tokens: 2 }, sequence: 1 })?.type).toBe('context-update')
  })

  it('run.finished 归一化秒级时间戳并提取 pending_hitl 字段名', () => {
    const event = parseRunEvent('run.finished', { status: 'failed', finished_at: 1000 })
    expect(event).toMatchObject({ type: 'run-finished', status: 'failed', finishedAt: 1_000_000 })
    const approval = parseRunEvent('approval.required', { content: { parts: [] }, pending_hitl: { kind: 'approval' }, sequence: 2 })
    expect(approval).toMatchObject({ type: 'approval-required', pendingHitl: { kind: 'approval' } })
  })
})

describe('runEventReducer 流式扩展', () => {
  it('stats-update：usage 字段在 payload 顶层，落入 state.stats', () => {
    const domain = parseRunEvent('stats-update', {
      type: 'stats-update', run_id: 'r', sequence: 2, status: 'running', transient: true,
      turns: 1, steps: 3, llm_ms: 9000, ttft_ms: 1200,
      input_tokens: 500, output_tokens: 80, cache_read_tokens: 300, cache_write_tokens: 0,
    })
    expect(domain).toEqual({ type: 'stats-update', stats: expect.objectContaining({ steps: 3, ttft_ms: 1200 }) })
    const state = runEventReducer(initialRunEventState(), domain!)
    expect(state.stats?.steps).toBe(3)
  })

  it('stats-update 无 steps 字段（无效载荷）不产出事件', () => {
    expect(parseRunEvent('stats-update', { type: 'stats-update' })).toBeNull()
  })

  it('run-snapshot 重置流式统计（新 turn 不得泄漏上一 turn 的 stats）', () => {
    let state = runEventReducer(initialRunEventState(), { type: 'run-snapshot', snapshot: snapshot as never })
    state = runEventReducer(state, { type: 'stats-update', stats: { steps: 2 } as never })
    expect(state.stats?.steps).toBe(2)
    state = runEventReducer(state, { type: 'run-snapshot', snapshot: { ...snapshot, snapshot_sequence: 9 } as never })
    expect(state.stats).toBeNull()
  })

  it('帧词汇（text-delta 等）不经 reducer——内容投影由宿主 appenders 组装', () => {
    // 帧事件在宿主 dispatchRunFrame 层经共享帧分派表处理（appenders），
    // 不进 parseRunEvent / reducer；reducer 只认生命周期与统计事件
    expect(parseRunEvent('text-delta', { type: 'text-delta', text_delta: '你好' })).toBeNull()
    expect(parseRunEvent('message.updated', { type: 'message.updated', content: {} })).toBeNull()
    expect(parseRunEvent('tool-input-available', { type: 'tool-input-available' })).toBeNull()
  })
})
