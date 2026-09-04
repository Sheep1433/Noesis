import type { ReasoningUiPart, ToolUiPart, UiPart } from '@/views/chat/messageParts'
import { shouldRenderSubagentPart } from '@/utils/parseTaskTool'
import { shouldRenderToolCallCollapse } from '@/utils/parseWriteTodosInput'
import { COMPACTION_BOUNDARY, part_parent_task_call_id } from '@/views/chat/messageParts'

export type DisplayPartEntry =
  | { kind: 'part', part: UiPart }
  | { kind: 'subagent', part: ToolUiPart, childParts: UiPart[] }
  | { kind: 'parallel_tools', parts: ToolUiPart[] }

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
  return mergeAdjacentParallelTools(out)
}

/**
 * 子 Agent 时间线 parts → 展示条目。
 *
 * 与 ``buildDisplayParts`` 的区别：子 Agent 的 childParts 本身就是「带
 * parent_task_call_id 的内部 part」，不应再被 ``isNestedSubagentChild`` 收走
 * （否则输出为空）。这里只做三件事：过滤不渲染的工具、合并相邻同 parent 的
 * reasoning 碎块、邻接并行工具按 step_id 合并。
 */
export function buildChildDisplayParts(parts: UiPart[]): DisplayPartEntry[] {
  const filtered: UiPart[] = []
  for (const p of parts) {
    if (p.type === 'tool' && !shouldRenderToolCallCollapse(p.name, p.input)) {
      continue
    }
    filtered.push(p)
  }
  const coalesced = coalesceAdjacentReasoning(filtered)
  const out: DisplayPartEntry[] = coalesced.map((p) => ({ kind: 'part', part: p }))
  return mergeAdjacentParallelTools(out)
}

/**
 * 把相邻且同 ``step_id``（≥2）的 tool part 合并为一个 ``parallel_tools`` entry。
 * bridge 背靠背发同一 model step 的并行工具，持久化保序，故邻接即可精确分组。
 * 单工具或无 step_id 的工具保持 ``part``。
 */
function mergeAdjacentParallelTools(entries: DisplayPartEntry[]): DisplayPartEntry[] {
  const result: DisplayPartEntry[] = []
  let i = 0
  while (i < entries.length) {
    const entry = entries[i]
    // 每次 start_task 委派都是独立卡片，不能折叠进通用并行工具组。
    if (
      entry.kind !== 'part'
      || entry.part.type !== 'tool'
      || entry.part.name === 'start_task'
      || !entry.part.step_id
    ) {
      result.push(entry)
      i += 1
      continue
    }
    const stepId = entry.part.step_id
    const group: ToolUiPart[] = [entry.part]
    let j = i + 1
    while (j < entries.length) {
      const next = entries[j]
      if (
        next.kind === 'part'
        && next.part.type === 'tool'
        && next.part.name !== 'start_task'
        && next.part.step_id === stepId
      ) {
        group.push(next.part)
        j += 1
        continue
      }
      break
    }
    if (group.length >= 2) {
      result.push({ kind: 'parallel_tools', parts: group })
    } else {
      result.push({ kind: 'part', part: group[0] })
    }
    i = j
  }
  return result
}

/** compact 工具模式折叠视图的终稿判定：最后一段非边界、非空的顶层正文 */
export function lastTopLevelTextEntry(entries: DisplayPartEntry[]): DisplayPartEntry | null {
  for (let index = entries.length - 1; index >= 0; index -= 1) {
    const entry = entries[index]
    if (
      entry.kind === 'part'
      && entry.part.type === 'text'
      && entry.part.content !== COMPACTION_BOUNDARY
      && entry.part.content.trim()
    ) {
      return entry
    }
  }
  return null
}

/**
 * compact 工具模式的折叠条目：只保留委派卡（前台 task / 后台 start_task）
 * 与最后一段终稿正文，其余工具与推理收起（点击气泡头部展开全量）。
 */
export function collapseDisplayEntries(entries: DisplayPartEntry[]): DisplayPartEntry[] {
  const finalText = lastTopLevelTextEntry(entries)
  if (!finalText) {
    return entries
  }
  const kept = entries.filter((entry) =>
    entry.kind === 'subagent'
    || (entry.kind === 'part' && entry.part.type === 'tool' && entry.part.name === 'start_task'),
  )
  return [...kept, finalText]
}
