/**
 * 推理档位（reasoning_effort）共享常量与纯函数。
 *
 * 后端权威枚见 noesis/llm/reasoning.py：通用三档 low/medium/high
 * （各家通用最大公约集），wire 值即档位名，经 chat/completions 顶层参数透传。
 * 「自动」（''）= 不传参 = provider 默认行为。
 */

/** 档位固定序（声明子集按此序展示） */
export const REASONING_LEVEL_ORDER = ['low', 'medium', 'high'] as const

export type ReasoningLevel = typeof REASONING_LEVEL_ORDER[number]

export const REASONING_LEVEL_LABELS: Record<ReasoningLevel, string> = {
  low: '低',
  medium: '中',
  high: '高',
}

/** 档位值是否合法（'' = 自动，单独判断） */
export function isReasoningLevel(value: unknown): value is ReasoningLevel {
  return typeof value === 'string' && (REASONING_LEVEL_ORDER as readonly string[]).includes(value)
}

/** 档位中文标签；'' → 自动 */
export function reasoningLevelLabel(value: string): string {
  if (!value) {
    return '自动'
  }
  return REASONING_LEVEL_LABELS[value as ReasoningLevel] ?? value
}

/**
 * 模型是否支持 reasoning_effort（入口显隐判定，按模型名规则匹配）。
 *
 * 支持系（2026-08 官方文档核实）：
 * - deepseek v4 系（low/high；medium 由端点映射）
 * - GLM-5.x（low/medium/high；GLM-5.3 无 medium）
 * - Kimi k3 系（low/high）
 * - OpenAI gpt-5 / o 系列
 * 不支持：Qwen（enable_thinking 专有体系）、Claude（budget_tokens）、
 * 各家旧款。规则按名匹配，新模型出现时更新此表。
 */
const REASONING_SUPPORTED_PATTERNS: RegExp[] = [
  /deepseek/i,
  /\bglm-5/i,
  /\bkimi/i,
  /gpt-5/i,
  /(^|[^a-z])o[1-9]/i,
]

export function modelSupportsReasoningEffort(modelId: string): boolean {
  const name = String(modelId || '')
  return REASONING_SUPPORTED_PATTERNS.some((pattern) => pattern.test(name))
}
