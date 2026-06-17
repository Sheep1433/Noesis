import type { ToolUiPart, UiPart } from '@/views/chat/messageParts'
import { part_parent_task_call_id } from '@/views/chat/messageParts'
import { shouldRenderSubagentPart } from '@/utils/parseTaskTool'
import { shouldRenderToolCallCollapse } from '@/utils/parseWriteTodosInput'

export type DisplayPartEntry =
  | { kind: 'part', part: UiPart }
  | { kind: 'subagent', part: ToolUiPart, childParts: UiPart[] }

/** 子 Agent 内部 part（text / reasoning / tool），不含 task 本身 */
export function isNestedSubagentChild(part: UiPart): boolean {
  const parentId = part_parent_task_call_id(part)
  if (!parentId) {
    return false
  }
  if (part.type === 'tool' && shouldRenderSubagentPart(part)) {
    return false
  }
  return true
}

/**
 * 将 flat parts 分组：子 Agent 内部 parts 按原序挂到对应 task，主循环不重复渲染。
 */
export function buildDisplayParts(parts: UiPart[]): DisplayPartEntry[] {
  const childByParent = new Map<string, UiPart[]>()

  for (const p of parts) {
    if (!isNestedSubagentChild(p)) {
      continue
    }
    if (p.type === 'tool' && !shouldRenderToolCallCollapse(p.name, p.input)) {
      continue
    }
    const parentId = part_parent_task_call_id(p)!
    const list = childByParent.get(parentId) ?? []
    list.push(p)
    childByParent.set(parentId, list)
  }

  const out: DisplayPartEntry[] = []
  for (const p of parts) {
    if (isNestedSubagentChild(p)) {
      continue
    }
    if (p.type === 'tool' && !shouldRenderToolCallCollapse(p.name, p.input)) {
      continue
    }
    if (p.type === 'tool' && shouldRenderSubagentPart(p)) {
      const taskId = p.tool_call_id?.trim() ?? ''
      out.push({
        kind: 'subagent',
        part: p,
        childParts: taskId ? (childByParent.get(taskId) ?? []) : [],
      })
      continue
    }
    out.push({ kind: 'part', part: p })
  }
  return out
}
