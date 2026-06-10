/** DeepAgents SubAgentMiddleware 工具名，与 SSE tool-input-available.name 一致 */
export const TASK_TOOL_NAME = 'task'

export const TASK_SUCCEEDED_PREFIX = 'Task Succeeded. Result:'
export const TASK_FAILED_PREFIX = 'Task failed.'
export const TASK_TIMED_OUT_PREFIX = 'Task timed out'

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
}

/**
 * 从 task part 的 output / status / error 派生子任务运行状态。
 */
export function parseTaskToolOutput(ctx: TaskToolOutputContext): ParsedTaskToolOutput {
  const output = coerceString(ctx.output)
  const trimmed = output.trim()
  const partError = ctx.error != null ? String(ctx.error).trim() : ''

  if (ctx.status === 'error') {
    return {
      status: 'failed',
      error: partError || trimmed || '子任务失败',
    }
  }

  if (!trimmed) {
    if (ctx.status === 'running' || ctx.status === 'streaming') {
      return { status: 'in_progress' }
    }
    return { status: 'in_progress' }
  }

  if (trimmed.startsWith(TASK_SUCCEEDED_PREFIX)) {
    const result = trimmed.slice(TASK_SUCCEEDED_PREFIX.length).trim()
    return { status: 'completed', result: result || undefined }
  }

  if (trimmed.startsWith(TASK_FAILED_PREFIX)) {
    const error = trimmed.slice(TASK_FAILED_PREFIX.length).trim()
    return { status: 'failed', error: error || '子任务失败' }
  }

  if (trimmed.startsWith(TASK_TIMED_OUT_PREFIX)) {
    return { status: 'failed', error: trimmed }
  }

  return { status: 'in_progress' }
}

/** 是否应对该 part 使用 SubagentCollapse 而非 ToolCallCollapse */
export function shouldRenderSubagentPart(part: {
  type?: string
  name?: string
}): boolean {
  return part.type === 'tool' && part.name === TASK_TOOL_NAME
}
