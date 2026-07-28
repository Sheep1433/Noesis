import { describe, expect, it } from 'vitest'
import { assertStrictMessageSequence } from '@/store/business/chatHistorySequence'

describe('chat history sequence', () => {
  it('accepts a strictly increasing user and assistant sequence', () => {
    expect(() => assertStrictMessageSequence([
      { message_sequence: 1 },
      { message_sequence: 2 },
    ])).not.toThrow()
  })

  it('rejects missing, duplicate, or reversed sequence values', () => {
    expect(() => assertStrictMessageSequence([
      { message_sequence: 2 },
      { message_sequence: 1 },
    ])).toThrow('消息顺序异常，请重新加载')
    expect(() => assertStrictMessageSequence([
      { message_sequence: 1 },
      { message_sequence: 1 },
    ])).toThrow('消息顺序异常，请重新加载')
    expect(() => assertStrictMessageSequence([{}])).toThrow('消息顺序异常，请重新加载')
  })
})
