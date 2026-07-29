import { describe, expect, it } from 'vitest'
import { normalizeApiContent } from '@/views/chat/messageParts'

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
          hitl: { status: 'pending', interrupt_id: 'interrupt-1' },
        },
        {
          type: 'tool',
          name: 'execute',
          input: { command: 'curl example.com' },
          output: 'ok',
          tool_call_id: 'call-1',
          status: 'success',
          hitl: { status: 'approved', interrupt_id: 'interrupt-1' },
        },
      ],
    })

    expect(normalized.parts).toHaveLength(1)
    expect(normalized.parts[0]).toMatchObject({
      type: 'tool',
      tool_call_id: 'call-1',
      status: 'success',
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
          hitl: { status: 'approved', interrupt_id: 'interrupt-1' },
        },
        {
          type: 'tool',
          name: 'execute',
          input: { command: 'curl example.com', timeout: 15 },
          output: 'ok',
          tool_call_id: 'callback-run-uuid',
          status: 'success',
        },
      ],
    })

    expect(normalized.parts).toHaveLength(1)
    expect(normalized.parts[0]).toMatchObject({
      type: 'tool',
      tool_call_id: 'call-model-1',
      status: 'success',
      output: 'ok',
      hitl: { status: 'approved', interrupt_id: 'interrupt-1' },
    })
  })
})
