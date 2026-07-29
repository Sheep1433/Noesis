import { describe, expect, it } from 'vitest'
import { applyToolOutput, assistantToolFailureSummary, normalizeApiContent, TOOL_STATE_LABELS } from '@/views/chat/messageParts'

describe('message parts snapshot normalization', () => {
  it('兼容旧消息并严格解析 citation 与 retrieval parts', () => {
    const normalized = normalizeApiContent({ parts: [
      { type: 'text', content: '旧消息' },
      {
        type: 'text', content: '五分钟', annotations: [
          {
            type: 'kb_citation', citation_id: 'cit_1', start_index: 0, end_index: 3,
            document_id: 'doc', document_version_id: 'docv', segment_id: 'seg',
            title: '需求.md', excerpt: '证据', verification: 'structural',
          },
          { type: 'kb_citation', citation_id: 'bad', start_index: 3, end_index: 1 },
        ],
      },
      {
        id: 'retrieval_1', type: 'retrieval', tool_call_id: 'call_1', query: '有效期',
        results: [{
          evidence_id: 'ev_1', document_id: 'doc', document_version_id: 'docv',
          segment_id: 'seg', title: '需求.md', excerpt: '证据',
        }],
      },
    ] })
    expect(normalized.parts[0]).toMatchObject({ type: 'text', content: '旧消息' })
    expect(normalized.parts[1]).toMatchObject({ type: 'text', annotations: [{ citation_id: 'cit_1' }] })
    expect(normalized.parts[2]).toMatchObject({ type: 'retrieval', results: [{ evidence_id: 'ev_1' }] })
  })

  it('工具状态文案互斥且覆盖完整生命周期', () => {
    expect(TOOL_STATE_LABELS).toEqual({
      running: '正在执行',
      approval_pending: '等待确认',
      succeeded: '已完成',
      failed: '执行失败',
      timed_out: '执行超时',
      rejected: '已拒绝',
      cancelled: '已停止',
    })
  })
  it('刷新或 HITL 续跑时按 tool_call_id 合并重复工具块', () => {
    const normalized = normalizeApiContent({
      parts: [
        {
          type: 'tool',
          name: 'execute',
          input: { command: 'curl example.com' },
          output: null,
          tool_call_id: 'call-1',
          status: 'running',
          state: 'approval_pending',
          hitl: { status: 'pending', interrupt_id: 'interrupt-1' },
        },
        {
          type: 'tool',
          name: 'execute',
          input: { command: 'curl example.com' },
          output: 'ok',
          tool_call_id: 'call-1',
          status: 'success',
          state: 'succeeded',
          hitl: { status: 'approved', interrupt_id: 'interrupt-1' },
        },
      ],
    })

    expect(normalized.parts).toHaveLength(1)
    expect(normalized.parts[0]).toMatchObject({
      type: 'tool',
      tool_call_id: 'call-1',
      status: 'success',
      state: 'succeeded',
      output: 'ok',
      hitl: { status: 'approved', interrupt_id: 'interrupt-1' },
    })
  })

  it('归并旧 snapshot 中模型 call id 与 callback run id 产生的重复块', () => {
    const normalized = normalizeApiContent({
      parts: [
        {
          type: 'tool',
          name: 'execute',
          input: { command: 'curl example.com', timeout: 15 },
          output: null,
          tool_call_id: 'call-model-1',
          status: 'running',
          state: 'approval_pending',
          hitl: { status: 'approved', interrupt_id: 'interrupt-1' },
        },
        {
          type: 'tool',
          name: 'execute',
          input: { command: 'curl example.com', timeout: 15 },
          output: 'ok',
          tool_call_id: 'callback-run-uuid',
          status: 'success',
          state: 'succeeded',
        },
      ],
    })

    expect(normalized.parts).toHaveLength(1)
    expect(normalized.parts[0]).toMatchObject({
      type: 'tool',
      tool_call_id: 'call-model-1',
      status: 'success',
      state: 'succeeded',
      output: 'ok',
      hitl: { status: 'approved', interrupt_id: 'interrupt-1' },
    })
  })

  it('失败终态不会被晚到 running 覆盖，多个失败只派生一次回答级提示', () => {
    const initial = normalizeApiContent({
      parts: [
        { type: 'tool', tool_call_id: 'call-1', name: 'web_fetch', status: 'error', state: 'failed' },
        { type: 'tool', tool_call_id: 'call-2', name: 'execute', status: 'success', state: 'timed_out' },
        { type: 'text', content: '这是基于部分来源生成的回答。' },
      ],
    })
    const afterLateEvent = applyToolOutput(initial.parts, 'call-1', {
      output: '',
      status: 'success',
      state: 'running',
    })

    expect((afterLateEvent[0] as any).state).toBe('failed')
    expect(assistantToolFailureSummary(afterLateEvent)).toEqual({
      hasFailure: true,
      hasVisibleText: true,
    })
  })
})
