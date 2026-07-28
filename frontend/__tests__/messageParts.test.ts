import { describe, expect, it } from 'vitest'
import { normalizeApiContent } from '@/views/chat/messageParts'

describe('message parts snapshot normalization', () => {
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
