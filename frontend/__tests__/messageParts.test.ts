import type { UiPart } from '@/views/chat/messageParts'
import { describe, expect, it } from 'vitest'
import { parseTaskToolOutput } from '@/utils/parseTaskTool'
import {
  appendReasoningDelta,
  appendStreamFailureNotice,
  appendTextDelta,
  appendTextDeltaWithRedactedThinking,
  applyToolOutput,
  assistantToolFailureSummary,
  COMPACTION_BOUNDARY,
  completeReasoningPart,
  createRedactedThinkingStreamCtx,
  formatDurationMs,
  formatUsageSummary,
  hasValidContextWindow,
  hasValidUsage,
  markStreamingPartsComplete,
  normalizeApiContent,
  resolveLoadedContextSnapshot,
  shouldCollapseUserMessage,
  shouldShowAssistantToolFailureBlocker,
  TOOL_STATE_LABELS,
  upsertToolInputPart,
} from '@/views/chat/messageParts'

describe('duration formatting', () => {
  it('uses one compact format across short, second and minute durations', () => {
    expect(formatDurationMs(5.6)).toBe('<1s')
    expect(formatDurationMs(12_300)).toBe('12s')
    expect(formatDurationMs(65_000)).toBe('1m 05s')
  })
})

describe('message parts snapshot normalization', () => {
  it('reasoning-end 按 part_id 闭合对应思考，不误关闭交错的 subagent 思考', () => {
    const parts = [
      { id: 'root-reasoning', type: 'reasoning' as const, content: '主线', status: 'streaming' },
      { id: 'child-reasoning', type: 'reasoning' as const, content: '子任务', status: 'streaming', parent_task_call_id: 'task-1' },
    ]

    const next = completeReasoningPart(parts, 'root-reasoning')

    expect(next[0]).toMatchObject({ id: 'root-reasoning', status: 'completed' })
    expect(next[1]).toMatchObject({ id: 'child-reasoning', status: 'streaming' })
  })

  it('仅对超过阈值的用户消息启用折叠', () => {
    expect(shouldCollapseUserMessage('a'.repeat(800))).toBe(false)
    expect(shouldCollapseUserMessage('a'.repeat(801))).toBe(true)
  })

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

  it('压缩边界独立成一行，兼容历史正文中已合并的标记', () => {
    const historical = normalizeApiContent({
      parts: [{ type: 'text', content: `压缩前${COMPACTION_BOUNDARY}压缩后` }],
    })
    expect(historical.parts.map((part) => part.type === 'text' ? part.content : '')).toEqual([
      '压缩前',
      COMPACTION_BOUNDARY,
      '压缩后',
    ])

    const streaming = appendTextDelta([
      { id: 'text-1', type: 'text', content: '压缩前', status: 'streaming' },
    ], `${COMPACTION_BOUNDARY}压缩后`)
    expect(streaming.map((part) => part.type === 'text' ? part.content : '')).toEqual([
      '压缩前',
      COMPACTION_BOUNDARY,
      '压缩后',
    ])
  })

  it('已有有效 retrieval 结果时，不把同一工具显示为连接失败', () => {
    const normalized = normalizeApiContent({
      parts: [
        {
          type: 'tool',
          name: 'search_knowledge_base',
          tool_call_id: 'call-kb',
          input: { query: '怀孕怎么办' },
          output: '',
          status: 'error',
          state: 'failed',
          error: '连接失败',
          errorCategory: 'network_unreachable',
        },
        {
          type: 'retrieval',
          tool_call_id: 'call-kb',
          query: '怀孕怎么办',
          results: [{ evidence_id: 'ev-1', title: '妊娠生理.md', excerpt: '资料' }],
        },
      ],
    })
    expect(normalized.parts.find((part) => part.type === 'tool')).toMatchObject({
      status: 'success',
      state: 'succeeded',
      output: '检索到 1 条来源',
      error: null,
    })
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

  it('错误详情已经出现在正文时不重复追加原始错误', () => {
    const detail = 'LLM 服务经多次重试后仍不可用，请稍候继续对话。'
    const parts = appendStreamFailureNotice([
      { id: 'text-1', type: 'text', content: detail, status: 'streaming' },
    ], detail)
    const text = parts
      .filter((part) => part.type === 'text')
      .map((part) => part.content)
      .join('\n')

    expect(text.match(/LLM 服务经多次重试后仍不可用/g)).toHaveLength(1)
    expect(parts).toHaveLength(1)
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

  it('流式 start_task 输出到达时提取 child_session_id（任务卡据此匹配目录状态）', () => {
    const childSessionId = '8d82f4ad-e51b-48e3-b419-a6878f8dd51c'
    const parts = upsertToolInputPart(
      [],
      'call-start-1',
      'start_task',
      { description: 'T1: 编码Agent评测', prompt: '完整指令' },
    )
    const withOutput = applyToolOutput(parts, 'call-start-1', {
      output: `子 Agent 已启动：${childSessionId}\n无需等待——可继续其他工作，之后用 check_task 收结果。`,
      status: 'success',
      state: 'succeeded',
    })

    expect((withOutput[0] as any).child_session_id).toBe(childSessionId)
  })

  it('非 start_task 工具输出不产生 child_session_id', () => {
    const parts = upsertToolInputPart([], 'call-web-1', 'web_search', { query: 'agent eval' })
    const withOutput = applyToolOutput(parts, 'call-web-1', {
      output: '子 Agent 已启动：8d82f4ad-e51b-48e3-b419-a6878f8dd51c',
      status: 'success',
      state: 'succeeded',
    })

    expect((withOutput[0] as any).child_session_id).toBeUndefined()
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

  it('刚收到的有效上下文不会被尚未落库的旧快照清空', () => {
    const current = { current_tokens: 2400, max_tokens: 128000, used_percentage: 1.8 }
    expect(resolveLoadedContextSnapshot(
      { current_tokens: 39_000_000, max_tokens: 128000, used_percentage: 100 },
      current,
      'session-1',
      'session-1',
      true,
    )).toEqual(current)
    expect(resolveLoadedContextSnapshot(
      { current_tokens: 39_000_000, max_tokens: 128000, used_percentage: 100 },
      current,
      'session-1',
      'session-2',
    )).toBeNull()
  })
})

describe('retrieval part origin 解析（research-source-provenance）', () => {
  it('解析 subagent origin（kind + label）', () => {
    const normalized = normalizeApiContent({ parts: [{
      id: 'r1',
      type: 'retrieval',
      tool_call_id: 'subagent-sources-abc',
      query: '调研 X',
      results: [{
        evidence_id: 'ev_1',
        source_type: 'web',
        url: 'https://example.com/a',
        title: 'A',
        excerpt: 'e',
      }],
      origin: { kind: 'subagent', label: '调研 X' },
    }] })
    const part = normalized.parts[0]
    expect(part.type).toBe('retrieval')
    if (part.type === 'retrieval') {
      expect(part.origin).toEqual({ kind: 'subagent', label: '调研 X' })
    }
  })

  it('旧数据无 origin 字段：不报错、不写默认占位（按 main 归组）', () => {
    const normalized = normalizeApiContent({ parts: [{
      id: 'r2',
      type: 'retrieval',
      tool_call_id: 'call-1',
      query: 'q',
      results: [{
        evidence_id: 'ev_2',
        source_type: 'web',
        url: 'https://example.com/b',
        title: 'B',
        excerpt: 'e',
      }],
    }] })
    const part = normalized.parts[0]
    if (part.type === 'retrieval') {
      expect(part.origin).toBeUndefined()
    }
  })

  it('未知 kind 按 main 归组（解析不失败）', () => {
    const normalized = normalizeApiContent({ parts: [{
      id: 'r3',
      type: 'retrieval',
      tool_call_id: 'call-3',
      query: 'q',
      results: [{
        evidence_id: 'ev_3',
        source_type: 'web',
        url: 'https://example.com/c',
        title: 'C',
        excerpt: 'e',
      }],
      origin: { kind: 'whatever' },
    }] })
    const part = normalized.parts[0]
    if (part.type === 'retrieval') {
      expect(part.origin).toEqual({ kind: 'main' })
    }
  })
})

/* ---- 流式热路径：copy-on-write 身份保持 + 批量合并应用等价性 ----
   见 docs/bug/chat-stream-hotpath-memory-bloat.md：append* 每次克隆全部 part、
   每 delta 全链重建是渲染进程内存膨胀主因。此组测试钉住两点：
   1) 未命中 part 复用对象引用（copy-on-write 契约，防止回退成全量克隆）；
   2) 连续同签名 delta 合并成一条后应用，与逐条应用产出一致（批量应用语义中性）。 */
describe('streaming hot-path copy-on-write 与批量应用等价性', () => {
  /** genPartId 含随机数，等价性比较需剥掉 id */
  function stripIds(parts: UiPart[]): Array<Record<string, unknown>> {
    return parts.map(({ id, ...rest }) => rest)
  }

  it('appendTextDelta 并入尾部 text 时仅替换命中 part，其余 part 复用引用', () => {
    const parts: UiPart[] = [
      { id: 't0', type: 'text', content: '头', status: 'completed' },
      { id: 't1', type: 'text', content: '尾', status: 'completed' },
    ]
    const next = appendTextDelta(parts, '+')

    expect(next).toHaveLength(2)
    expect(next[0]).toBe(parts[0])
    expect(next[1]).not.toBe(parts[1])
    expect(next[1]).toMatchObject({ id: 't1', content: '尾+', status: 'streaming' })
    // 输入不可变
    expect(parts[1]).toMatchObject({ content: '尾', status: 'completed' })
  })

  it('appendTextDelta 尾部非 text 时新开 part，既有 part 全部复用引用', () => {
    const parts: UiPart[] = [
      { id: 't0', type: 'text', content: '正文', status: 'streaming' },
      { id: 'r0', type: 'reasoning', content: '思考', status: 'streaming' },
    ]
    const next = appendTextDelta(parts, '续')

    expect(next).toHaveLength(3)
    expect(next[0]).toBe(parts[0])
    expect(next[1]).toBe(parts[1])
    expect(next[2]).toMatchObject({ type: 'text', content: '续' })
  })

  it('appendTextDelta 跳过其它 parent 的交错 part，并入同 parent 的最近 text', () => {
    const parts: UiPart[] = [
      { id: 'main-t', type: 'text', content: '主', status: 'streaming' },
      { id: 'child-r', type: 'reasoning', content: '子思考', status: 'streaming', parent_task_call_id: 'task-1' },
    ]
    const next = appendTextDelta(parts, '续')

    expect(next).toHaveLength(2)
    expect(next[0]).not.toBe(parts[0])
    expect(next[0]).toMatchObject({ id: 'main-t', content: '主续' })
    expect(next[1]).toBe(parts[1])
  })

  it('appendReasoningDelta 同样只替换命中 part，其余复用引用', () => {
    const parts: UiPart[] = [
      { id: 'r0', type: 'reasoning', content: '想', status: 'completed' },
    ]
    const next = appendReasoningDelta(parts, '继续')

    expect(next).toHaveLength(1)
    expect(next[0]).not.toBe(parts[0])
    expect(next[0]).toMatchObject({ id: 'r0', content: '想继续', status: 'streaming' })
    expect(parts[0]).toMatchObject({ content: '想', status: 'completed' })
  })

  it('连续同签名 delta 合并应用与逐条应用产出相同 parts（批量语义中性）', () => {
    const seq: Array<{ kind: 'text' | 'reasoning', data: string }> = [
      { kind: 'reasoning', data: '思' },
      { kind: 'reasoning', data: '考' },
      { kind: 'text', data: 'He' },
      { kind: 'text', data: 'llo' },
      { kind: 'text', data: '!' },
      { kind: 'reasoning', data: '再想' },
      { kind: 'text', data: '答' },
    ]
    const stepwise = seq.reduce<UiPart[]>((parts, d) =>
      d.kind === 'text' ? appendTextDelta(parts, d.data) : appendReasoningDelta(parts, d.data), [])

    // 模拟 streamDeltaBatcher 的合并：连续同签名拼成一条
    const merged: Array<{ kind: 'text' | 'reasoning', data: string }> = []
    for (const d of seq) {
      const tail = merged[merged.length - 1]
      if (tail && tail.kind === d.kind) {
        tail.data += d.data
      } else {
        merged.push({ ...d })
      }
    }
    const batched = merged.reduce<UiPart[]>((parts, d) =>
      d.kind === 'text' ? appendTextDelta(parts, d.data) : appendReasoningDelta(parts, d.data), [])

    expect(stripIds(batched)).toEqual(stripIds(stepwise))
  })

  it('<think> 标签在任意切分点分批应用与整段应用结果一致（合并 chunk 不改变解析）', () => {
    const src = '前文<think>推理中段</think>后文'
    const whole = appendTextDeltaWithRedactedThinking([], src, createRedactedThinkingStreamCtx())

    for (let split = 0; split <= src.length; split++) {
      const ctx = createRedactedThinkingStreamCtx()
      let chunked = appendTextDeltaWithRedactedThinking([], src.slice(0, split), ctx)
      chunked = appendTextDeltaWithRedactedThinking(chunked, src.slice(split), ctx)
      expect(stripIds(chunked)).toEqual(stripIds(whole))
    }
  })

  it('多批 text delta 共享同一 redacted ctx，顺序应用与整段应用结果一致', () => {
    const src = 'a<think>b1 b2</think>c1 c2'
    const whole = appendTextDeltaWithRedactedThinking([], src, createRedactedThinkingStreamCtx())

    // 三个切分点（含标签内部与闭合标签跨点）
    for (const [i, j] of [[1, 10], [7, 14], [8, 20]] as const) {
      const ctx = createRedactedThinkingStreamCtx()
      let chunked = appendTextDeltaWithRedactedThinking([], src.slice(0, i), ctx)
      chunked = appendTextDeltaWithRedactedThinking(chunked, src.slice(i, j), ctx)
      chunked = appendTextDeltaWithRedactedThinking(chunked, src.slice(j), ctx)
      expect(stripIds(chunked)).toEqual(stripIds(whole))
    }
  })
})
