import { describe, expect, it } from 'vitest'
import { formatElapsedSeconds } from '@/utils/formatTime'

describe('formatElapsedSeconds', () => {
  it('按整秒展示处理时长', () => {
    expect(formatElapsedSeconds(10_000, 13_999)).toBe('已处理 3 秒')
  })

  it('未开始时不展示计时', () => {
    expect(formatElapsedSeconds(undefined, 13_999)).toBe('')
  })
})
