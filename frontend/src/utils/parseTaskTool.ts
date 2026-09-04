/** DeepAgents SubAgentMiddleware 工具名，与 SSE tool-input-available.name 一致 */
export const TASK_TOOL_NAME = 'task'

/** 后台子 Agent 启动工具名（BackgroundSubagentCollapse 渲染入口） */
export const START_TASK_TOOL_NAME = 'start_task'

export const TASK_SUCCEEDED_PREFIX = 'Task Succeeded. Result:'

/**
 * 启动回执文案（与后端 tools_middleware 的两条回执保持同一来源）：
 * - 「子 Agent 已启动：<uuid>」后台直启
 * - 「任务运行超过 Ns，已自动转为后台：<uuid>」前台等待超时自动转后台
 * 输出文本历史上以 Command repr 形态落库，正则按子串匹配两种形态均覆盖。
 */
const START_TASK_LAUNCHED_RE = /(?:子 Agent 已启动|已自动转为后台)[：:]\s*([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})/

/**
 * 从 start_task 工具输出文本提取子会话 id。
 * 后端把真实模型 tool_call_id 记在子会话 created_by_tool_call_id 上，
 * 与父消息 part 的 tool_call_id（桥接层生成）不是同一体系，
 * 因此 part → 子会话的关联只能从输出文本提取。
 */
export function parseStartTaskChildSessionId(output: unknown): string | undefined {
  if (typeof output !== 'string') {
    return undefined
  }
  return START_TASK_LAUNCHED_RE.exec(output)?.[1]
}

export type SubagentRunStatus = 'in_progress' | 'completed' | 'failed'

export interface ParsedTaskToolInput {
  description: string
  subagent_type: string
  prompt: string
}

export interface ParsedTaskToolOutput {
  status: SubagentRunStatus
  result?: string
  error?: string
}

const DEFAULT_DESCRIPTION = '子任务'
const DEFAULT_SUBAGENT_TYPE = 'general-purpose'

function coerceString(value: unknown): string {
  return typeof value === 'string' ? value : ''
}

/** 从 tool input 取出业务字段（兼容桥接层 _tw_tool_input 包装） */
function unwrapTaskInput(input: Record<string, unknown>): Record<string, unknown> {
  const wrapped = input._tw_tool_input
  if (wrapped && typeof wrapped === 'object' && !Array.isArray(wrapped)) {
    return wrapped as Record<string, unknown>
  }
  return input
}

/**
 * 从 task 工具 input 解析子任务元数据。
 * 非法结构时使用约定默认值，不抛错。
 */
export function parseTaskToolInput(input: Record<string, unknown>): ParsedTaskToolInput {
  const raw = unwrapTaskInput(input ?? {})
  const description = coerceString(raw.description).trim() || DEFAULT_DESCRIPTION
  const subagent_type = coerceString(raw.subagent_type).trim() || DEFAULT_SUBAGENT_TYPE
  const prompt = coerceString(raw.prompt).trim()
  return { description, subagent_type, prompt }
}

export interface TaskToolOutputContext {
  output?: string
  status?: string
  error?: string | null
  state?: string
}

/**
 * 从 task part 的 output / status / error 派生子任务运行状态。
 */
export function parseTaskToolOutput(ctx: TaskToolOutputContext): ParsedTaskToolOutput {
  const output = coerceString(ctx.output)
  const trimmed = output.trim()
  const partError = ctx.error != null ? String(ctx.error).trim() : ''

  if (['failed', 'timed_out', 'rejected', 'cancelled'].includes(ctx.state || '') || ctx.status === 'error') {
    return {
      status: 'failed',
      error: partError || trimmed || '子任务失败',
    }
  }

  if (ctx.state === 'running' || ctx.state === 'approval_pending'
    || ctx.status === 'running' || ctx.status === 'streaming') {
    return { status: 'in_progress' }
  }

  const result = trimmed.startsWith(TASK_SUCCEEDED_PREFIX)
    ? trimmed.slice(TASK_SUCCEEDED_PREFIX.length).trim()
    : trimmed
  return { status: 'completed', result: result || undefined }
}

/** 是否应对该 part 使用 SubagentCollapse 而非 ToolCallCollapse */
export function shouldRenderSubagentPart(part: {
  type?: string
  name?: string
}): boolean {
  return part.type === 'tool' && part.name === TASK_TOOL_NAME
}
