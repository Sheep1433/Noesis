/** 统计条格式化。 */

export interface SessionStats {
  turns: number
  steps: number
  llm_ms: number
  ttft_ms: number
  input_tokens: number
  output_tokens: number
  cache_read_tokens: number
  cache_write_tokens: number
}

export function formatTokens(n: number): string {
  const scaled = (v: number): string =>
    v >= 100 ? String(Math.round(v)) : String(Math.round(v * 10) / 10)
  if (n < 1_000) {
    return String(n)
  }
  if (n < 1_000_000) {
    return `${scaled(n / 1_000)}K`
  }
  return `${scaled(n / 1_000_000)}M`
}

/**
 * 解码吞吐（tok/s）：输出 token ÷ 各次调用的解码时长之和（llm_ms − ttft_ms）。
 * 分母是「每路流时长总和」而非墙钟时间——多个子 Agent 并行输出时速率不虚增，
 * 展示的始终是模型的平均解码速度；解码样本不足 1s 噪声过大，返回 null 不展示。
 */
export function decodeTokensPerSecond(stats: SessionStats): number | null {
  const decodeMs = stats.llm_ms - (stats.ttft_ms || 0)
  if (stats.output_tokens <= 0 || decodeMs < 1_000) {
    return null
  }
  return stats.output_tokens / (decodeMs / 1_000)
}

export function formatTokensPerSecond(v: number): string {
  return v < 10 ? `${Math.round(v * 10) / 10} tok/s` : `${Math.round(v)} tok/s`
}

export function formatDuration(ms: number): string {
  const s = ms / 1_000
  if (s < 60) {
    return `${Math.round(s * 10) / 10}s`
  }
  const whole = Math.round(s)
  return `${Math.floor(whole / 60)}m${whole % 60}s`
}

export function formatStatsLine(stats: SessionStats | null, template?: string): string {
  if (!stats || stats.steps === 0) {
    return ''
  }

  // 自定义模板（/statsline 配置）：{turns} {steps} {llm} {cache} {in} {out} 占位符
  if (template && template.trim()) {
    return applyStatsTemplate(template, stats)
  }

  const groups: string[] = []

  // 轮数 · 步数
  groups.push(`${stats.turns} 轮 · ${stats.steps} 步`)

  // LLM 耗时
  if (stats.llm_ms > 0) {
    groups.push(`LLM ${formatDuration(stats.llm_ms)}`)
  }

  // 缓存命中
  const inputTotal = stats.input_tokens
  if (inputTotal > 0) {
    const cacheHit = stats.cache_read_tokens > 0
      ? Math.round(stats.cache_read_tokens / inputTotal * 100)
      : 0
    groups.push(`缓存命中 ${cacheHit}%`)
  }

  // 输入 · 输出 · 解码吞吐
  if (stats.input_tokens > 0 || stats.output_tokens > 0) {
    const tps = decodeTokensPerSecond(stats)
    const tpsText = tps !== null ? ` · ${formatTokensPerSecond(tps)}` : ''
    groups.push(`输入 ${formatTokens(stats.input_tokens)} · 输出 ${formatTokens(stats.output_tokens)}${tpsText}`)
  }

  return groups.join(' | ')
}

/** 模板可用占位符（/statsline 弹窗展示用）。 */
export const STATS_TEMPLATE_VARIABLES: Array<{ token: string, label: string }> = [
  { token: '{turns}', label: '轮数' },
  { token: '{steps}', label: '步数' },
  { token: '{llm}', label: 'LLM 耗时（如 9.2s / 2m42s）' },
  { token: '{cache}', label: '缓存命中百分比' },
  { token: '{in}', label: '输入 token（紧凑格式 13.1K）' },
  { token: '{out}', label: '输出 token（紧凑格式 755）' },
  { token: '{tps}', label: '解码吞吐（tok/s，样本不足时为 —）' },
]

export function applyStatsTemplate(template: string, stats: SessionStats): string {
  const inputTotal = stats.input_tokens
  const cacheHit = inputTotal > 0
    ? Math.round(stats.cache_read_tokens / inputTotal * 100)
    : 0
  const tps = decodeTokensPerSecond(stats)
  return template
    .replaceAll('{turns}', String(stats.turns))
    .replaceAll('{steps}', String(stats.steps))
    .replaceAll('{llm}', formatDuration(stats.llm_ms))
    .replaceAll('{cache}', `${cacheHit}%`)
    .replaceAll('{in}', formatTokens(stats.input_tokens))
    .replaceAll('{out}', formatTokens(stats.output_tokens))
    .replaceAll('{tps}', tps !== null ? formatTokensPerSecond(tps) : '—')
}
