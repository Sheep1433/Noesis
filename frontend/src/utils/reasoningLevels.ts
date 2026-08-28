/**
 * 推理档位（reasoning_effort）共享常量与纯函数。
 *
 * 后端权威枚举为 off/low/medium/high/max（noesis/llm/reasoning.py）；
 * wire 映射 off→none 由后端完成，前端只消费档位值本身。
 */

/** 档位固定序（声明子集按此序展示） */
export const REASONING_LEVEL_ORDER = ['off', 'low', 'medium', 'high', 'max'] as const

export type ReasoningLevel = typeof REASONING_LEVEL_ORDER[number]

export const REASONING_LEVEL_LABELS: Record<ReasoningLevel, string> = {
  off: '关',
  low: '低',
  medium: '中',
  high: '高',
  max: '最高',
}

/** 声明子集按固定序排列；非法值过滤 */
export function orderReasoningLevels(levels: unknown): ReasoningLevel[] {
  if (!Array.isArray(levels)) {
    return []
  }
  return REASONING_LEVEL_ORDER.filter((level) => levels.includes(level))
}

/** n-select / n-dropdown 选项（关/低/中/高/最高） */
export function reasoningLevelOptions() {
  return REASONING_LEVEL_ORDER.map((level) => ({
    label: REASONING_LEVEL_LABELS[level],
    value: level,
  }))
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
