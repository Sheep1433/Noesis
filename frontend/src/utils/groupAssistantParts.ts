import type { ReasoningUiPart, ToolUiPart, UiPart } from '@/views/chat/messageParts'
import { shouldRenderSubagentPart } from '@/utils/parseTaskTool'
import { shouldRenderToolCallCollapse } from '@/utils/parseWriteTodosInput'
import { part_parent_task_call_id } from '@/views/chat/messageParts'

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

/** 合并相邻且同 parent 的 reasoning，修复历史/交错流造成的碎块 */
export function coalesceAdjacentReasoning(parts: UiPart[]): UiPart[] {
  const out: UiPart[] = []
  for (const p of parts) {
    const last = out[out.length - 1]
    if (
      p.type === 'reasoning'
      && last?.type === 'reasoning'
      && part_parent_task_call_id(last) === part_parent_task_call_id(p)
    ) {
      const prev = last as ReasoningUiPart
      const cur = p as ReasoningUiPart
      out[out.length - 1] = {
        ...prev,
        content: `${prev.content}${cur.content}`,
        status: cur.status === 'streaming' || prev.status === 'streaming' ? 'streaming' : 'completed',
      }
      continue
    }
    out.push(p)
  }
  return out
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

  for (const [id, list] of childByParent) {
    childByParent.set(id, coalesceAdjacentReasoning(list))
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
