import type { SessionStats } from './statsFormat'
import type { ChatMessageResponse } from '@/api/chat'

/**
 * 从历史 assistant 消息 extra.usage 重建会话级统计（主/子会话同口径）。
 * usage 由统一 run 管道在终态落库；子会话统计条与主会话统计条共用本函数。
 */
export function rebuildSessionStats(
  messages: ReadonlyArray<Pick<ChatMessageResponse, 'role' | 'extra'>>,
): SessionStats | null {
  const totals: SessionStats = {
    turns: 0,
    steps: 0,
    llm_ms: 0,
    input_tokens: 0,
    output_tokens: 0,
    cache_read_tokens: 0,
    cache_write_tokens: 0,
  }
  for (const item of messages) {
    if (item.role !== 'assistant') {
      continue
    }
    const usage = (item.extra as { usage?: Partial<SessionStats> } | undefined)?.usage
    if (!usage || typeof usage !== 'object') {
      continue
    }
    totals.turns += 1
    totals.steps += Number(usage.steps) || 0
    totals.llm_ms += Number(usage.llm_ms) || 0
    totals.input_tokens += Number(usage.input_tokens) || 0
    totals.output_tokens += Number(usage.output_tokens) || 0
    totals.cache_read_tokens += Number(usage.cache_read_tokens) || 0
    totals.cache_write_tokens += Number(usage.cache_write_tokens) || 0
  }
  return totals.steps > 0 ? totals : null
}
