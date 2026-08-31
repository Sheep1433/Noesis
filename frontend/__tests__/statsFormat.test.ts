import type { SessionStats } from '@/utils/statsFormat'
import { describe, expect, it } from 'vitest'
import { rebuildSessionStats } from '@/utils/sessionStats'
import {
  applyStatsTemplate,
  decodeTokensPerSecond,
  formatStatsLine,
  formatTokensPerSecond,

} from '@/utils/statsFormat'

function stats(partial: Partial<SessionStats>): SessionStats {
  return {
    turns: 1,
    steps: 1,
    llm_ms: 0,
    ttft_ms: 0,
    input_tokens: 0,
    output_tokens: 0,
    cache_read_tokens: 0,
    cache_write_tokens: 0,
    ...partial,
  }
}

describe('decodeTokensPerSecond', () => {
  it('输出 token ÷ 解码时长（llm_ms − ttft_ms）', () => {
    // 1000 token / 10s 解码 = 100 tok/s（首 token 等待不计入分母）
    expect(decodeTokensPerSecond(stats({ llm_ms: 14_000, ttft_ms: 4_000, output_tokens: 1_000 }))).toBe(100)
  })

  it('多路并行合并后速率不虚增：分母是各路流时长之和', () => {
    // 三个子 Agent 各自 30 tok/s（3000 token / 100s 解码），
    // 合并累计 9000 token / 300s —— 仍是 30 tok/s，不是 90
    const single = stats({ llm_ms: 100_000, ttft_ms: 0, output_tokens: 3_000 })
    const merged = stats({
      steps: 3,
      llm_ms: 3 * single.llm_ms,
      ttft_ms: 0,
      output_tokens: 3 * single.output_tokens,
    })
    expect(decodeTokensPerSecond(merged)).toBe(decodeTokensPerSecond(single))
  })

  it('解码样本不足 1s 不展示（噪声防护）', () => {
    expect(decodeTokensPerSecond(stats({ llm_ms: 1_500, ttft_ms: 1_000, output_tokens: 50 }))).toBeNull()
  })

  it('无输出 token 不展示', () => {
    expect(decodeTokensPerSecond(stats({ llm_ms: 10_000, ttft_ms: 0, output_tokens: 0 }))).toBeNull()
  })

  it('ttft 缺省按 0 处理（旧数据兼容）', () => {
    expect(decodeTokensPerSecond(stats({ llm_ms: 10_000, output_tokens: 100 }))).toBe(10)
  })
})

describe('formatTokensPerSecond', () => {
  it('低于 10 保留一位小数，否则取整', () => {
    expect(formatTokensPerSecond(7.44)).toBe('7.4 tok/s')
    expect(formatTokensPerSecond(28.6)).toBe('29 tok/s')
  })
})

describe('formatStatsLine', () => {
  it('输入/输出组附带解码吞吐', () => {
    const line = formatStatsLine(stats({
      steps: 2,
      llm_ms: 20_000,
      ttft_ms: 5_000,
      input_tokens: 13_100,
      output_tokens: 755,
    }))
    expect(line).toContain('输出 755 · 50 tok/s')
  })

  it('吞吐无效时不追加，统计条其余部分不受影响', () => {
    const line = formatStatsLine(stats({ steps: 1, llm_ms: 1_200, ttft_ms: 1_000, output_tokens: 20 }))
    expect(line).toContain('输出 20')
    expect(line).not.toContain('tok/s')
  })

  it('模板 {tps} 有效值与无效占位', () => {
    const s = stats({ steps: 2, llm_ms: 20_000, ttft_ms: 5_000, output_tokens: 755 })
    expect(applyStatsTemplate('{out} @ {tps}', s)).toBe('755 @ 50 tok/s')
    const tiny = stats({ steps: 1, llm_ms: 1_200, ttft_ms: 1_000, output_tokens: 20 })
    expect(applyStatsTemplate('{tps}', tiny)).toBe('—')
  })
})

describe('rebuildSessionStats', () => {
  it('从 extra.usage 累计 ttft_ms（解码吞吐的分母原料）', () => {
    const rebuilt = rebuildSessionStats([
      { role: 'user', extra: {} },
      { role: 'assistant', extra: { usage: { steps: 2, llm_ms: 8_000, ttft_ms: 3_000, output_tokens: 300 } } },
      { role: 'assistant', extra: { usage: { steps: 1, llm_ms: 7_000, ttft_ms: 2_000, output_tokens: 200 } } },
    ])
    expect(rebuilt).not.toBeNull()
    expect(rebuilt!.ttft_ms).toBe(5_000)
    expect(decodeTokensPerSecond(rebuilt!)).toBe(50) // 500 token / 10s 解码
  })
})
