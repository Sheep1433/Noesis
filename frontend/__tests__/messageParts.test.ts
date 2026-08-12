import { describe, expect, it } from 'vitest'
import { parseTaskToolOutput } from '@/utils/parseTaskTool'
import {
  applyToolOutput,
  assistantToolFailureSummary,
  formatUsageSummary,
  hasValidContextWindow,
  hasValidUsage,
  markStreamingPartsComplete,
  normalizeApiContent,
  shouldShowAssistantToolFailureBlocker,
  TOOL_STATE_LABELS,
} from '@/views/chat/messageParts'

describe('message parts snapshot normalization', () => {
  it('解析普通文本与 retrieval parts', () => {
    const normalized = normalizeApiContent({ parts: [
      { type: 'text', content: '旧消息' },
      { type: 'text', content: '五分钟[1]\n\n### 参考资料\n1. 需求.md' },
      {
        id: 'retrieval_1', type: 'retrieval', tool_call_id: 'call_1', query: '有效期',
        results: [{
          evidence_id: 'ev_1', document_id: 'doc', document_version_id: 'docv',
          segment_id: 'seg', title: '需求.md', excerpt: '证据',
        }],
      },
    ] })
    expect(normalized.parts[0]).toMatchObject({ type: 'text', content: '旧消息' })
    expect(normalized.parts[1]).toMatchObject({ type: 'text', content: expect.stringContaining('参考资料') })
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

  it('失败终态不会被晚到 running 覆盖，有最终回答时不派生误导性汇总', () => {
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
      hasFinalText: true,
    })
    expect(shouldShowAssistantToolFailureBlocker(afterLateEvent, false)).toBe(false)
  })

  it('run 仍在继续时不应提前显示本轮未完成', () => {
    const parts = normalizeApiContent({
      parts: [
        { type: 'reasoning', content: '继续调研其它来源' },
        { type: 'tool', tool_call_id: 'call-1', name: 'web_fetch', status: 'error', state: 'failed' },
        { type: 'tool', tool_call_id: 'call-2', name: 'web_search', status: 'running', state: 'running' },
      ],
    }).parts

    expect(assistantToolFailureSummary(parts).hasFailure).toBe(true)
    expect(shouldShowAssistantToolFailureBlocker(parts, true)).toBe(false)
    expect(shouldShowAssistantToolFailureBlocker(parts, false)).toBe(true)
  })

  it('工具前的过程文本不应被当作最终回答', () => {
    const parts = normalizeApiContent({
      parts: [
        { type: 'text', content: '我来查询一下。' },
        { type: 'tool', tool_call_id: 'call-1', name: 'web_fetch', status: 'error', state: 'failed' },
      ],
    }).parts

    expect(shouldShowAssistantToolFailureBlocker(parts, false)).toBe(true)
  })

  it('缺少权威 state 时拒绝猜测工具状态', () => {
    expect(() => normalizeApiContent({
      parts: [{ type: 'tool', tool_call_id: 'call-1', name: 'execute', status: 'success' }],
    })).toThrow('工具状态协议错误')
  })

  it('finish 收口未完成工具时同时更新 status 与 state', () => {
    const parts = normalizeApiContent({
      parts: [{ type: 'tool', tool_call_id: 'call-1', name: 'execute', status: 'running', state: 'running' }],
    }).parts
    expect(markStreamingPartsComplete(parts)[0]).toMatchObject({
      type: 'tool',
      status: 'error',
      state: 'failed',
      outcome: 'failed',
    })
  })

  it('子 Agent 完成状态不依赖英文输出前缀', () => {
    expect(parseTaskToolOutput({ state: 'succeeded', status: 'success', output: '研究已完成' })).toEqual({
      status: 'completed',
      result: '研究已完成',
    })
    expect(parseTaskToolOutput({ state: 'succeeded', status: 'success', output: '' })).toEqual({
      status: 'completed',
      result: undefined,
    })
  })
})

describe('usage summary 兼容零值与缺失 details', () => {
  it('基础 usage（无 details）正常展示', () => {
    const text = formatUsageSummary({
      input_tokens: 21400,
      output_tokens: 593,
      total_tokens: 21993,
    })
    expect(text).toContain('本轮用量 ↑21.4K')
    expect(text).toContain('↓593')
    expect(text).toContain('共 22K')
  })

  it('大于百万的 usage 使用 M 单位，避免显示成难读的五位 K 数', () => {
    const text = formatUsageSummary({
      input_tokens: 55506500,
      output_tokens: 1184300,
      total_tokens: 56690800,
    })
    expect(text).toBe('本轮用量 ↑55.5M ↓1.2M · 共 56.7M')
  })

  it('含 details 的 usage 默认摘要不展示 cache/reasoning', () => {
    const text = formatUsageSummary({
      input_tokens: 21400,
      output_tokens: 593,
      total_tokens: 21993,
      input_token_details: { cache_read: 60, cache_write: 0 },
      output_token_details: { reasoning: 8 },
    })
    // 默认摘要只展示 input/output/total
    expect(text).toBe('本轮用量 ↑21.4K ↓593 · 共 22K')
    expect(text).not.toContain('cache')
    expect(text).not.toContain('reasoning')
  })

  it('零值 usage 仍可格式化', () => {
    const text = formatUsageSummary({
      input_tokens: 0,
      output_tokens: 0,
    })
    expect(text).toContain('↑0')
    expect(text).toContain('↓0')
  })
})

describe('hasValidUsage / hasValidContextWindow 降级', () => {
  it('hasValidUsage 拒绝空对象与缺失字段', () => {
    expect(hasValidUsage(null)).toBe(false)
    expect(hasValidUsage({})).toBe(false)
    expect(hasValidUsage({ input_tokens: 0, output_tokens: 0 })).toBe(false)
    expect(hasValidUsage({ input_tokens: 100, output_tokens: 0 })).toBe(true)
  })

  it('hasValidContextWindow 拒绝缺字段与零 max', () => {
    expect(hasValidContextWindow(null)).toBe(false)
    expect(hasValidContextWindow({ current_tokens: 100 })).toBe(false)
    expect(hasValidContextWindow({ current_tokens: 100, max_tokens: 0, used_percentage: 1 })).toBe(false)
    expect(hasValidContextWindow({ current_tokens: 100, max_tokens: 128000, used_percentage: 1 })).toBe(true)
    expect(hasValidContextWindow({ current_tokens: 128001, max_tokens: 128000, used_percentage: 100 })).toBe(false)
  })

  it('历史消息只有核心字段仍通过校验', () => {
    expect(hasValidContextWindow({
      current_tokens: 5000, max_tokens: 128000, used_percentage: 4,
    })).toBe(true)
  })
})
