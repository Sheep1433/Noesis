import type { Todo } from '@/components/TodoList/types'

/** LangChain TodoListMiddleware 工具名，与 SSE tool-input-available.name 一致 */
export const WRITE_TODOS_TOOL_NAME = 'write_todos'

const TODO_STATUSES = new Set<Todo['status']>(['pending', 'in_progress', 'completed'])

function normalizeTodoStatus(raw: unknown): Todo['status'] | null {
  if (typeof raw !== 'string') {
    return null
  }
  const v = raw.trim().toLowerCase().replace(/\s+/g, '_').replace(/-/g, '_')
  if (v === 'inprogress') {
    return 'in_progress'
  }
  if (TODO_STATUSES.has(v as Todo['status'])) {
    return v as Todo['status']
  }
  return null
}

/** 从 tool input 取出 todos 数组（兼容桥接层 _tw_tool_input 包装） */
export function extractTodosArray(input: Record<string, unknown>): unknown[] | null {
  if (Array.isArray(input.todos)) {
    return input.todos
  }
  const wrapped = input._tw_tool_input
  if (wrapped && typeof wrapped === 'object' && !Array.isArray(wrapped)) {
    const inner = wrapped as Record<string, unknown>
    if (Array.isArray(inner.todos)) {
      return inner.todos
    }
  }
  return null
}

/** input 是否形如 write_todos 载荷（用于 toolName 缺失时的兜底） */
export function isWriteTodosLikeInput(input: Record<string, unknown>): boolean {
  return extractTodosArray(input) !== null
}

/**
 * 从 write_todos 的 tool input 解析 todo 列表（全量快照，非增量）。
 * @returns 合法数组（含空数组）；结构不可解析时返回 null（调用方不应更新 store）
 */
export function parseWriteTodosInput(input: Record<string, unknown>): Todo[] | null {
  const raw = extractTodosArray(input)
  if (raw === null) {
    return null
  }

  const todos: Todo[] = []
  for (const item of raw) {
    if (!item || typeof item !== 'object') {
      continue
    }
    const row = item as Record<string, unknown>
    const content = row.content
    const status = normalizeTodoStatus(row.status)
    if (typeof content !== 'string' || !content.trim() || !status) {
      continue
    }
    todos.push({ content: content.trim(), status })
  }
  return todos
}

/** 根据工具名或 input 形态决定是否更新 TodoList */
export function shouldApplyWriteTodos(
  name: string,
  input: Record<string, unknown>,
): boolean {
  if (name === WRITE_TODOS_TOOL_NAME) {
    return true
  }
  return !name && isWriteTodosLikeInput(input)
}

/** write_todos 由输入框上方 TodoList 展示，消息内不渲染 ToolCallCollapse */
export function shouldRenderToolCallCollapse(
  name: string | undefined,
  input?: Record<string, unknown>,
): boolean {
  if (name === WRITE_TODOS_TOOL_NAME) {
    return false
  }
  if (!name?.trim() && input && isWriteTodosLikeInput(input)) {
    return false
  }
  return true
}
