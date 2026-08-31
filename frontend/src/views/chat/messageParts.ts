/**
 * 会话消息 content.parts 与 UI 对齐（PRD：聊天记录 / SSE）
 */

import { parseStartTaskChildSessionId, START_TASK_TOOL_NAME } from '@/utils/parseTaskTool'

export type ToolRunStatus = 'running' | 'success' | 'error'
export type ToolLifecycleState =
  | 'running'
  | 'approval_pending'
  | 'succeeded'
  | 'failed'
  | 'timed_out'
  | 'rejected'
  | 'cancelled'

export interface TextUiPart {
  id: string
  type: 'text'
  content: string
  status?: string
  parent_task_call_id?: string
}

export interface ReasoningUiPart {
  id: string
  type: 'reasoning'
  content: string
  status?: string
  parent_task_call_id?: string
}

export interface ToolUiPart {
  id: string
  type: 'tool'
  tool_call_id?: string
  name: string
  input: Record<string, unknown>
  output: string
  child_session_id?: string
  status: ToolRunStatus
  state: ToolLifecycleState
  error?: string | null
  errorCategory?: string | null
  duration_ms?: number
  outcome?: string | null
  exit_code?: number
  timed_out?: boolean
  truncated?: boolean
  /** 归属某次 task 委派；有值时仅在 SubagentCollapse 内展示 */
  parent_task_call_id?: string
  /** 同一 model step 内并行调用的工具共享此 id，用于前端并行分组展示 */
  step_id?: string
  /** HITL 审批/澄清状态（可选扩展） */
  hitl?: {
    kind?: string
    status?: 'pending' | 'approved' | 'rejected' | 'answered'
    interrupt_id?: string
    decision?: string
  } | null
}

export interface RetrievalResultUi {
  evidence_id: string
  source_type?: 'knowledge_base' | 'web'
  document_id?: string
  document_version_id?: string
  segment_id?: string
  collection_name?: string
  url?: string
  title: string
  excerpt: string
  locator?: Record<string, unknown> | null
  score?: number | null
}

/** 来源归属：主 Agent 自检索 / 具体子 Agent 任务；缺省视为主 Agent（旧数据兼容） */
export interface RetrievalOrigin {
  kind: 'main' | 'subagent'
  label?: string
}

export interface RetrievalUiPart {
  id: string
  type: 'retrieval'
  tool_call_id: string
  query: string
  results: RetrievalResultUi[]
  truncated?: boolean
  parent_task_call_id?: string
  origin?: RetrievalOrigin
}

export type UiPart = TextUiPart | ReasoningUiPart | ToolUiPart | RetrievalUiPart

export function part_parent_task_call_id(part: UiPart): string | undefined {
  const raw = part.parent_task_call_id
  return typeof raw === 'string' && raw.trim() ? raw.trim() : undefined
}

export interface MessageContentV1 {
  version: 1
  parts: UiPart[]
}

/** 用户输入过长时默认收起，避免一条消息撑满整个对话页面。 */
export const USER_MESSAGE_COLLAPSE_THRESHOLD = 800

export function shouldCollapseUserMessage(content: string): boolean {
  return content.length > USER_MESSAGE_COLLAPSE_THRESHOLD
}

export function genPartId(prefix: string): string {
  return `${prefix}-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 9)}`
}

export function emptyMessageContent(): MessageContentV1 {
  return { version: 1, parts: [] }
}

const REDACTED_OPEN = '<think>'
const REDACTED_CLOSE = '</think>'
export const COMPACTION_BOUNDARY = '—— 以上对话已压缩摘要 ——'

function coerceToolStatus(p: Record<string, unknown>): ToolRunStatus {
  if (p.status === 'error' || p.error != null) {
    return 'error'
  }
  if (p.status === 'running' || p.status === 'streaming') {
    return 'running'
  }
  return 'success'
}

function parseToolState(p: Record<string, unknown>): ToolLifecycleState {
  const state = p.state
  if (
    state === 'running'
    || state === 'approval_pending'
    || state === 'succeeded'
    || state === 'failed'
    || state === 'timed_out'
    || state === 'rejected'
    || state === 'cancelled'
  ) {
    return state
  }
  throw new Error('工具状态协议错误')
}

export function isTerminalToolState(state: ToolLifecycleState): boolean {
  return !['running', 'approval_pending'].includes(state)
}

export const TOOL_STATE_LABELS: Record<ToolLifecycleState, string> = {
  running: '正在执行',
  approval_pending: '等待确认',
  succeeded: '已完成',
  failed: '执行失败',
  timed_out: '执行超时',
  rejected: '已拒绝',
  cancelled: '已停止',
}

export function assistantToolFailureSummary(parts: UiPart[]): {
  hasFailure: boolean
  hasFinalText: boolean
} {
  let lastToolIndex = -1
  parts.forEach((part, index) => {
    if (part.type === 'tool') {
      lastToolIndex = index
    }
  })
  return {
    hasFailure: parts.some((part) => part.type === 'tool'
      && ['failed', 'timed_out', 'rejected', 'cancelled'].includes(part.state)),
    hasFinalText: parts.some((part, index) => index > lastToolIndex
      && part.type === 'text'
      && Boolean(part.content.trim())),
  }
}

export function shouldShowAssistantToolFailureBlocker(parts: UiPart[], runIsActive: boolean): boolean {
  const summary = assistantToolFailureSummary(parts)
  return !runIsActive && summary.hasFailure && !summary.hasFinalText
}

/** 将已落库的整段 text（含成对标签）拆成 text / reasoning 部件，供历史列表与折叠 UI 使用 */
function expandRedactedThinkingInPlainText(text: string): Array<{ kind: 'text' | 'reasoning', value: string }> {
  const segments: Array<{ kind: 'text' | 'reasoning', value: string }> = []
  let mode: 'text' | 'thinking' = 'text'
  let buf = text
  while (buf.length > 0) {
    if (mode === 'text') {
      const idx = buf.indexOf(REDACTED_OPEN)
      if (idx === -1) {
        segments.push({ kind: 'text', value: buf })
        break
      }
      if (idx > 0) {
        segments.push({ kind: 'text', value: buf.slice(0, idx) })
      }
      buf = buf.slice(idx + REDACTED_OPEN.length)
      mode = 'thinking'
      continue
    }
    const idx = buf.indexOf(REDACTED_CLOSE)
    if (idx === -1) {
      segments.push({ kind: 'reasoning', value: buf })
      break
    }
    if (idx > 0) {
      segments.push({ kind: 'reasoning', value: buf.slice(0, idx) })
    }
    buf = buf.slice(idx + REDACTED_CLOSE.length)
    mode = 'text'
  }
  return segments.filter((s) => s.value.length > 0)
}

function expandRedactedThinkingInParts(parts: UiPart[]): UiPart[] {
  const out: UiPart[] = []
  for (const p of parts) {
    if (p.type !== 'text' || !p.content.includes(REDACTED_OPEN)) {
      out.push(p)
      continue
    }
    const status = p.status || 'completed'
    const segs = expandRedactedThinkingInPlainText(p.content)
    if (segs.length === 0) {
      const stripped = p.content.replace(/<think>\s*<\/redacted_thinking>/g, '')
      out.push(stripped === p.content ? p : { ...p, content: stripped })
      continue
    }
    for (const seg of segs) {
      if (seg.kind === 'text') {
        out.push({
          id: genPartId('text'),
          type: 'text',
          content: seg.value,
          status,
        })
      } else {
        out.push({
          id: genPartId('reasoning'),
          type: 'reasoning',
          content: seg.value,
          status: 'completed',
        })
      }
    }
  }
  return out
}

function splitCompactionBoundaries(parts: UiPart[]): UiPart[] {
  const out: UiPart[] = []
  for (const part of parts) {
    if (part.type !== 'text' || !part.content.includes(COMPACTION_BOUNDARY)) {
      out.push(part)
      continue
    }
    const segments = part.content.split(COMPACTION_BOUNDARY)
    segments.forEach((segment, index) => {
      if (segment) {
        out.push({
          ...part,
          id: index === 0 ? part.id : genPartId('text'),
          content: segment,
        })
      }
      if (index < segments.length - 1) {
        out.push({
          id: genPartId('compaction-boundary'),
          type: 'text',
          content: COMPACTION_BOUNDARY,
          status: 'completed',
        })
      }
    })
  }
  return out
}

/**
 * 将指定的流式思考段标为已完成。
 *
 * reasoning-end 会携带 part_id；只有旧 SSE 没有 part_id 时才按 parent
 * 回退到最近一段，不能再跨主 Agent / subagent 全局取最后一段。
 */
export function completeReasoningPart(
  parts: UiPart[],
  part_id?: string,
  parent_task_call_id?: string,
): UiPart[] {
  const next = parts.map((q) => ({ ...q })) as UiPart[]
  const normalizedPartId = part_id?.trim() || undefined
  const normalizedParentId = parent_task_call_id?.trim() || undefined
  let targetIndex = -1

  if (normalizedPartId) {
    targetIndex = next.findIndex((part) => part.type === 'reasoning' && part.id === normalizedPartId)
  }
  if (targetIndex === -1) {
    for (let i = next.length - 1; i >= 0; i--) {
      const cur = next[i]
      if (cur.type === 'reasoning' && part_parent_task_call_id(cur) === normalizedParentId) {
        targetIndex = i
        break
      }
    }
  }
  if (targetIndex === -1) {
    return next
  }

  const cur = next[targetIndex]
  if (cur.type === 'reasoning' && cur.status !== 'completed') {
    next[targetIndex] = { ...cur, status: 'completed' }
  }
  return next
}

/** 将流式思考段标为已完成（兼容没有 part_id 的旧调用方）。 */
export function completeLastReasoningPart(parts: UiPart[], parent_task_call_id?: string): UiPart[] {
  return completeReasoningPart(parts, undefined, parent_task_call_id)
}

/** API 落库可能把 list 等放在 input/arguments，统一包成 Record 便于 UI 展示 */
function normalizeToolPartInput(inputRaw: unknown): Record<string, unknown> {
  if (inputRaw == null) {
    return {}
  }
  if (typeof inputRaw === 'string') {
    const t = inputRaw.trim()
    if (!t) {
      return {}
    }
    try {
      const parsed = JSON.parse(t) as unknown
      return normalizeToolPartInput(parsed)
    } catch {
      return { _tw_raw: inputRaw }
    }
  }
  if (Array.isArray(inputRaw)) {
    return { _tw_args: inputRaw }
  }
  if (typeof inputRaw === 'object') {
    return inputRaw as Record<string, unknown>
  }
  return { _tw_value: inputRaw }
}

function parentTaskCallIdFromRecord(record: Record<string, unknown>): string | undefined {
  const value = record.parent_task_call_id
  return typeof value === 'string' && value.trim() ? value.trim() : undefined
}

function normalizeRetrievalPart(id: string, record: Record<string, unknown>): RetrievalUiPart {
  const results = Array.isArray(record.results)
    ? record.results.flatMap((raw): RetrievalResultUi[] => {
        if (!raw || typeof raw !== 'object') {
          return []
        }
        const item = raw as Record<string, unknown>
        if (typeof item.evidence_id !== 'string' || typeof item.excerpt !== 'string') {
          return []
        }
        return [{
          evidence_id: item.evidence_id,
          source_type: item.source_type === 'web' ? 'web' : 'knowledge_base',
          document_id: String(item.document_id ?? ''),
          document_version_id: String(item.document_version_id ?? ''),
          segment_id: String(item.segment_id ?? ''),
          collection_name: typeof item.collection_name === 'string' ? item.collection_name : undefined,
          url: typeof item.url === 'string' ? item.url : undefined,
          title: String(item.title ?? ''),
          excerpt: item.excerpt,
          locator: item.locator && typeof item.locator === 'object'
            ? item.locator as Record<string, unknown>
            : null,
          score: item.score == null ? null : Number(item.score),
        }]
      })
    : []
  const origin = normalizeRetrievalOrigin(record.origin)
  return {
    id,
    type: 'retrieval',
    tool_call_id: String(record.tool_call_id ?? ''),
    query: String(record.query ?? ''),
    results,
    truncated: Boolean(record.truncated),
    ...(origin ? { origin } : {}),
  }
}

function normalizeRetrievalOrigin(raw: unknown): RetrievalOrigin | undefined {
  if (!raw || typeof raw !== 'object') {
    return undefined
  }
  const record = raw as Record<string, unknown>
  const label = typeof record.label === 'string' && record.label.trim() ? record.label.trim() : undefined
  if (record.kind === 'subagent') {
    return { kind: 'subagent', ...(label ? { label } : {}) }
  }
  // 未知 kind / 缺省按主 Agent 归组（旧数据兼容，解析不失败）
  return { kind: 'main' }
}

function normalizeToolHitl(raw: unknown): ToolUiPart['hitl'] {
  if (!raw || typeof raw !== 'object') {
    return undefined
  }
  const hitl = raw as Record<string, unknown>
  return {
    kind: typeof hitl.kind === 'string' ? hitl.kind : undefined,
    status: hitl.status,
    interrupt_id: typeof hitl.interrupt_id === 'string' ? hitl.interrupt_id : undefined,
    decision: typeof hitl.decision === 'string' ? hitl.decision : undefined,
  }
}

function normalizeToolPart(
  id: string,
  record: Record<string, unknown>,
): ToolUiPart {
  const parent_task_call_id = parentTaskCallIdFromRecord(record)
  const step_id = typeof record.step_id === 'string' && record.step_id ? record.step_id : undefined
  const hitl = normalizeToolHitl(record.hitl)
  const output = typeof record.output === 'string' ? record.output : ''
  // start_task 的 part→子会话关联：桥接层 tool_call_id 与子会话
  // created_by_tool_call_id 不是同一体系，落库 part 也不含该字段，
  // 只能从输出文本「子 Agent 已启动：<id>」提取（兼容存量数据）。
  const childSessionId = String(record.name ?? '') === START_TASK_TOOL_NAME
    ? parseStartTaskChildSessionId(output)
    : undefined
  return {
    id,
    type: 'tool',
    tool_call_id: typeof record.tool_call_id === 'string' ? record.tool_call_id : undefined,
    name: String(record.name ?? ''),
    input: normalizeToolPartInput(record.input),
    output,
    status: coerceToolStatus(record),
    state: parseToolState(record),
    error: record.error != null ? String(record.error) : null,
    errorCategory: record.errorCategory != null ? String(record.errorCategory) : null,
    duration_ms: record.duration_ms != null ? Number(record.duration_ms) : undefined,
    outcome: record.outcome != null ? String(record.outcome) : null,
    exit_code: record.exit_code != null ? Number(record.exit_code) : undefined,
    timed_out: record.timed_out != null ? Boolean(record.timed_out) : undefined,
    truncated: record.truncated != null ? Boolean(record.truncated) : undefined,
    ...(parent_task_call_id ? { parent_task_call_id } : {}),
    ...(childSessionId ? { child_session_id: childSessionId } : {}),
    ...(step_id ? { step_id } : {}),
    ...(hitl ? { hitl } : {}),
  }
}

function mergeToolPart(parts: UiPart[], toolPart: ToolUiPart): void {
  const toolCallId = toolPart.tool_call_id
  let existingIndex = toolCallId
    ? parts.findIndex((part) => part.type === 'tool' && part.tool_call_id === toolCallId)
    : -1
  if (existingIndex === -1 && !toolPart.hitl) {
    const hitlCandidates = parts
      .map((part, index) => ({ part, index }))
      .filter(({ part }) => part.type === 'tool'
        && !isTerminalToolState(part.state)
        && Boolean(part.hitl)
        && part.name === toolPart.name
        && JSON.stringify(part.input) === JSON.stringify(toolPart.input))
    if (hitlCandidates.length === 1) {
      existingIndex = hitlCandidates[0].index
    }
  }
  if (existingIndex === -1) {
    parts.push(toolPart)
    return
  }
  const existing = parts[existingIndex] as ToolUiPart
  parts[existingIndex] = {
    ...existing,
    ...toolPart,
    id: existing.id,
    tool_call_id: existing.tool_call_id || toolPart.tool_call_id,
    name: toolPart.name || existing.name,
    input: Object.keys(toolPart.input).length > 0 ? toolPart.input : existing.input,
    output: toolPart.output || existing.output,
    child_session_id: existing.child_session_id || toolPart.child_session_id,
    step_id: toolPart.step_id ?? existing.step_id,
    hitl: { ...(existing.hitl || {}), ...(toolPart.hitl || {}) },
  }
}

function reconcileRetrievalToolStates(parts: UiPart[]): UiPart[] {
  const resultCounts = new Map<string, number>()
  for (const part of parts) {
    if (part.type === 'retrieval' && part.tool_call_id && part.results.length > 0) {
      resultCounts.set(part.tool_call_id, part.results.length)
    }
  }
  if (resultCounts.size === 0) {
    return parts
  }
  return parts.map((part) => {
    if (part.type !== 'tool' || !part.tool_call_id || !resultCounts.has(part.tool_call_id)) {
      return part
    }
    if (part.status === 'success' && part.state === 'succeeded') {
      return part
    }
    return {
      ...part,
      output: `检索到 ${resultCounts.get(part.tool_call_id)} 条来源`,
      status: 'success',
      state: 'succeeded',
      error: null,
      errorCategory: null,
      outcome: 'ok',
    }
  }) as UiPart[]
}

function normalizeApiPart(rawPart: unknown): UiPart | null {
  if (!rawPart || typeof rawPart !== 'object') {
    return null
  }
  const record = rawPart as Record<string, unknown>
  const type = record.type
  const id = typeof record.id === 'string' && record.id ? record.id : genPartId(String(type || 'p'))
  const parent_task_call_id = parentTaskCallIdFromRecord(record)
  if (type === 'text' || type === 'reasoning') {
    return {
      id,
      type,
      content: String(record.content ?? ''),
      status: String(record.status || 'completed'),
      ...(parent_task_call_id ? { parent_task_call_id } : {}),
    }
  }
  if (type === 'retrieval') {
    return normalizeRetrievalPart(id, record)
  }
  if (type === 'tool') {
    return normalizeToolPart(id, record)
  }
  return null
}

export function normalizeApiContent(raw: unknown): MessageContentV1 {
  let value: unknown = raw
  if (typeof raw === 'string') {
    try {
      value = JSON.parse(raw) as unknown
    } catch {
      if (!raw.trim()) {
        return emptyMessageContent()
      }
      const parts = expandRedactedThinkingInParts([
        { id: genPartId('text'), type: 'text', content: raw, status: 'completed' },
      ])
      return { version: 1, parts: reconcileRetrievalToolStates(splitCompactionBoundaries(parts)) }
    }
  }
  if (!value || typeof value !== 'object' || !Array.isArray((value as Record<string, unknown>).parts)) {
    return emptyMessageContent()
  }

  const parts: UiPart[] = []
  for (const rawPart of (value as Record<string, unknown>).parts as unknown[]) {
    const part = normalizeApiPart(rawPart)
    if (!part) {
      continue
    }
    if (part.type === 'tool') {
      mergeToolPart(parts, part)
    } else {
      parts.push(part)
    }
  }
  return {
    version: 1,
    parts: reconcileRetrievalToolStates(splitCompactionBoundaries(expandRedactedThinkingInParts(parts))),
  }
}

export function syncLegacyFieldsFromParts(parts: UiPart[]): { content: string, reasoning?: string } {
  let content = ''
  let reasoning = ''
  for (const p of parts) {
    if (p.type === 'text') {
      content += p.content
    }
    if (p.type === 'reasoning') {
      reasoning += p.content
    }
  }
  return { content, reasoning: reasoning || undefined }
}

export function appendRetrievalPart(parts: UiPart[], raw: Record<string, unknown>): UiPart[] {
  const normalized = normalizeApiContent({ parts: [{ ...raw, type: 'retrieval' }] }).parts[0]
  if (!normalized || normalized.type !== 'retrieval') {
    return parts
  }
  const index = parts.findIndex((part) => part.type === 'retrieval' && part.id === normalized.id)
  if (index === -1) {
    return reconcileRetrievalToolStates([...parts, normalized])
  }
  const next = [...parts]
  next[index] = normalized
  return reconcileRetrievalToolStates(next)
}

/**
 * 取最后一段顶层正文文本（最终回答），用于「复制」。
 * 排除 reasoning / tool 与嵌套 subagent 子 part，避免工具调用信息混入。
 */
export function extractLastTopLevelText(parts: UiPart[]): string {
  for (let i = parts.length - 1; i >= 0; i--) {
    const p = parts[i]
    if (p.type === 'text' && !part_parent_task_call_id(p)) {
      return p.content || ''
    }
  }
  return ''
}

/** 是否仍有流式中的正文 / 思考 / 运行中的工具（用于统一气泡底部工具栏仅在结束时展示） */
export function assistantPartsStillStreaming(parts: UiPart[]): boolean {
  return parts.some((p) => {
    if (p.type === 'text' && p.status === 'streaming') {
      return true
    }
    if (p.type === 'reasoning' && p.status === 'streaming') {
      return true
    }
    if (p.type === 'tool' && p.status === 'running') {
      return true
    }
    return false
  })
}

function finalizeStreamingParts(
  parts: UiPart[],
  unresolvedToolState: 'failed' | 'cancelled',
): UiPart[] {
  return parts.map((p) => {
    if (p.type === 'text' && p.status === 'streaming') {
      return { ...p, status: 'completed' }
    }
    if (p.type === 'reasoning' && p.status === 'streaming') {
      return { ...p, status: 'completed' }
    }
    if (p.type === 'tool' && !isTerminalToolState(p.state)) {
      return {
        ...p,
        status: 'error',
        state: unresolvedToolState,
        outcome: unresolvedToolState,
        error: p.error || (unresolvedToolState === 'cancelled' ? '本次操作已停止' : '工具未返回结果'),
      }
    }
    return p
  }) as UiPart[]
}

export function markStreamingPartsComplete(parts: UiPart[]): UiPart[] {
  return finalizeStreamingParts(parts, 'failed')
}

const USER_STOP_TOOL_ERROR = '用户已停止生成'
/** 统一单一形态：斜体括号附注（与后端 append_user_stop_notice_to_content 对齐） */
const USER_STOP_NOTICE = '（本轮回复已被用户中断。）'

function partsContainUserStopNotice(parts: UiPart[]): boolean {
  return parts.some((p) => {
    if (p.type !== 'text') {
      return false
    }
    const c = String(p.content ?? '')
    // 兼容识别历史消息里的纯文本变体
    return c.includes(USER_STOP_NOTICE) || c.includes('本轮回复已被用户中断')
  })
}

/** 用户主动停止：与后端 append_user_stop_notice_to_content 文案对齐 */
export function appendUserStopNotice(parts: UiPart[]): UiPart[] {
  if (partsContainUserStopNotice(parts)) {
    return parts
  }
  const completed = finalizeStreamingParts(parts, 'cancelled').map((p) =>
    p.type === 'tool' && p.state === 'cancelled'
      ? { ...p, error: USER_STOP_TOOL_ERROR }
      : p,
  ) as UiPart[]

  return [
    ...completed,
    {
      id: genPartId('text'),
      type: 'text',
      content: `\n\n*${USER_STOP_NOTICE}*`,
      status: 'completed',
    },
  ]
}

/** 流式失败收尾：未完成工具标为 error，避免误显示「完成」。 */
export function finalizePartsOnStreamError(parts: UiPart[]): UiPart[] {
  return finalizeStreamingParts(parts, 'failed')
}

function hasToolErrorPart(parts: UiPart[]): boolean {
  return parts.some((p) => p.type === 'tool' && p.status === 'error')
}

export interface ContextWindowSnapshot {
  current_tokens: number
  max_tokens: number
  used_percentage: number
  updated_at?: string
}

export function hasValidContextWindow(context: unknown): context is ContextWindowSnapshot {
  if (!context || typeof context !== 'object') {
    return false
  }
  const c = context as Record<string, unknown>
  const max = Number(c.max_tokens ?? 0)
  const current = Number(c.current_tokens ?? 0)
  const pct = Number(c.used_percentage ?? Number.NaN)
  return max > 0 && current >= 0 && current <= max && !Number.isNaN(pct)
}

/** Keep a valid live snapshot when it races an older persisted snapshot. */
export function resolveLoadedContextSnapshot(
  raw: unknown,
  current: ContextWindowSnapshot | null,
  currentSessionId: string,
  sessionId: string,
  currentIsLive = false,
): ContextWindowSnapshot | null {
  if (currentIsLive && currentSessionId === sessionId && current) {
    return current
  }
  if (hasValidContextWindow(raw)) {
    return raw
  }
  return currentSessionId === sessionId ? current : null
}

export function formatTokenCount(n: number): string {
  if (n >= 1_000_000_000) {
    return `${(n / 1_000_000_000).toFixed(1).replace(/\.0$/, '')}B`
  }
  if (n >= 1_000_000) {
    return `${(n / 1_000_000).toFixed(1).replace(/\.0$/, '')}M`
  }
  if (n >= 1000) {
    return `${(n / 1000).toFixed(1).replace(/\.0$/, '')}K`
  }
  return String(n)
}

export function hasValidUsage(usage: unknown): usage is {
  input_tokens: number
  output_tokens: number
  total_tokens?: number
} {
  if (!usage || typeof usage !== 'object') {
    return false
  }
  const value = usage as Record<string, unknown>
  const input = Number(value.input_tokens ?? Number.NaN)
  const output = Number(value.output_tokens ?? Number.NaN)
  return Number.isFinite(input) && input >= 0
    && Number.isFinite(output) && output >= 0
    && (input > 0 || output > 0)
}

export function formatUsageSummary(usage: unknown): string {
  const value = usage && typeof usage === 'object'
    ? usage as Record<string, unknown>
    : {}
  const input = Math.max(0, Number(value.input_tokens ?? 0))
  const output = Math.max(0, Number(value.output_tokens ?? 0))
  const total = Math.max(0, Number(value.total_tokens ?? input + output))
  return `本轮用量 ↑${formatTokenCount(input)} ↓${formatTokenCount(output)} · 共 ${formatTokenCount(total)}`
}

export function formatDurationMs(ms: number): string {
  const value = Math.max(0, Math.round(Number(ms) || 0))
  if (value < 1000) {
    return '<1s'
  }
  const totalSeconds = Math.round(value / 1000)
  if (totalSeconds < 60) {
    return `${totalSeconds}s`
  }
  const minutes = Math.floor(totalSeconds / 60)
  const seconds = totalSeconds % 60
  if (minutes < 60) {
    return seconds ? `${minutes}m ${String(seconds).padStart(2, '0')}s` : `${minutes}m`
  }
  const hours = Math.floor(minutes / 60)
  const restMinutes = minutes % 60
  return restMinutes ? `${hours}h ${String(restMinutes).padStart(2, '0')}m` : `${hours}h`
}

const MODEL_API_TIMEOUT_RE = /readtimeout|writetimeout|connecttimeout|pooltimeout|streamchunktimeouterror|stream_chunk_timeout|apitimeout|request timed out|timed out waiting/i

const NETWORK_TIMEOUT_RE = /request timed out|timed out|\btimeout\b|apitimeout|connecterror|connection refused|econnrefused|network is unreachable|socket hang up|无法连接|网络异常|网络错误|网络或服务异常/i

/** 上游 LLM HTTP 流式读超时（如 ReadTimeout），与浏览器网络错误区分 */
export function isModelApiTimeoutError(raw: string): boolean {
  const t = raw.trim()
  if (!t) {
    return false
  }
  return MODEL_API_TIMEOUT_RE.test(t)
}

export function getModelApiTimeoutNoticeText(hasProse: boolean): string {
  return hasProse
    ? '（模型响应超时，后续内容未能继续生成。请稍后重试，或尝试精简问题、缩短对话上下文。）'
    : '模型响应超时，请稍后重试。'
}

/** 连接/超时类错误：不向用户展示原始英文栈或重复长文案 */
export function isConnectionOrTimeoutError(raw: string): boolean {
  const t = raw.trim().toLowerCase().replace(/\s+/g, ' ')
  if (!t) {
    return true
  }
  if (isModelApiTimeoutError(raw)) {
    return true
  }
  if (/^(?:connection error|failed to fetch|networkerror|network request failed|load failed|fetch error|typeerror:\s*failed to fetch)$/.test(t.replace(/[.。…!！]+$/g, '').trim())) {
    return true
  }
  return NETWORK_TIMEOUT_RE.test(t)
}

/** LangGraph 递归步数触顶 */
export function isRecursionLimitError(raw: string): boolean {
  const t = raw.trim().toLowerCase()
  return /recursion limit|graphrecursionerror|recursion_limit|已达到最大处理步数/.test(t)
}

/** 与后端 get_stream_failure_notice_text / append_stream_failure_notice_to_content 文案对齐 */
const STREAM_FAILURE_NOTICE_MARKERS = [
  '生成过程中出现问题',
  '后续内容未能生成',
  '后续内容未能继续生成',
  '已达到最大处理步数',
  '模型响应超时',
  '生成失败，请稍后重试',
] as const

/** 历史回放 / 流式 onError：避免对已落库的失败说明重复追加 */
export function partsContainStreamFailureNotice(parts: UiPart[]): boolean {
  return parts.some((p) => {
    if (p.type !== 'text') {
      return false
    }
    const c = String(p.content ?? '')
    return STREAM_FAILURE_NOTICE_MARKERS.some((marker) => c.includes(marker))
  })
}

function partsContainFailureDetail(parts: UiPart[], detail?: string): boolean {
  const needle = detail?.trim()
  if (!needle) {
    return false
  }
  return parts.some((p) => p.type === 'text' && String(p.content ?? '').includes(needle))
}

/** 将 SSE/流式错误转为气泡内展示文案；null 表示不追加说明 */
export function getStreamFailureNoticeText(
  detail: string | undefined,
  hasProse: boolean,
  parts?: UiPart[],
): string | null {
  const raw = detail?.trim() ?? ''
  if (isModelApiTimeoutError(raw)) {
    return getModelApiTimeoutNoticeText(hasProse)
  }
  if (isConnectionOrTimeoutError(raw)) {
    return null
  }
  if (isRecursionLimitError(raw)) {
    return hasProse
      ? '（已达到最大处理步数，后续内容未能继续生成。）'
      : '已达到最大处理步数，任务已停止。请精简问题后重试。'
  }
  if (parts && hasToolErrorPart(parts)) {
    return hasProse ? '（后续内容未能生成）' : null
  }
  if (!raw || raw === '操作失败，请稍后重试。') {
    return hasProse ? null : '生成失败，请稍后重试。'
  }
  const DETAIL_MAX = 160
  const clipped = raw.length > DETAIL_MAX ? `${raw.slice(0, DETAIL_MAX)}…` : raw
  const head = '生成过程中出现问题，请稍后重试。'
  return hasProse ? `（后续内容未能生成）\n\n${clipped}` : `${head}\n\n${clipped}`
}

/** 气泡外：全局 Toast 限制长度，避免异常对象串进提示 */
export function shortenChatErrorToast(msg: string, maxLen = 72): string {
  const raw = msg.trim()
  if (!raw) {
    return '请求失败'
  }
  if (isModelApiTimeoutError(raw)) {
    return '模型响应超时，请稍后重试'
  }
  if (isConnectionOrTimeoutError(raw)) {
    return '网络异常，请稍后重试'
  }
  if (isRecursionLimitError(raw)) {
    return '已达到最大处理步数'
  }
  if (raw.length <= maxLen) {
    return raw
  }
  return `${raw.slice(0, maxLen - 1)}…`
}

/** 流式失败时在助手气泡内补充可读说明（保留已有正文 / 工具块） */
export function appendStreamFailureNotice(parts: UiPart[], detail?: string): UiPart[] {
  if (partsContainStreamFailureNotice(parts)) {
    return finalizePartsOnStreamError(parts)
  }
  const completed = finalizePartsOnStreamError(parts)

  // 模型 fallback 可能已经把用户可见错误写入正文；此时只收口，不再重复追加同一详情。
  if (partsContainFailureDetail(completed, detail)) {
    return completed
  }

  const hasProse = completed.some((p) => {
    if (p.type === 'text' || p.type === 'reasoning') {
      return String((p as TextUiPart | ReasoningUiPart).content ?? '').trim().length > 0
    }
    return false
  })

  const notice = getStreamFailureNoticeText(detail, hasProse, completed)
  if (notice === null) {
    return completed
  }

  if (!hasProse) {
    if (completed.length === 0) {
      return [
        {
          id: genPartId('text'),
          type: 'text',
          content: notice,
          status: 'completed',
        },
      ]
    }
    return [
      ...completed,
      {
        id: genPartId('text'),
        type: 'text',
        content: notice,
        status: 'completed',
      },
    ]
  }

  const tail = notice.startsWith('（')
    ? `\n\n---\n\n*${notice}*`
    : `\n\n---\n\n*（后续内容未能生成，请稍后重试。）*\n\n${notice}`

  return [
    ...completed,
    {
      id: genPartId('text'),
      type: 'text',
      content: tail,
      status: 'completed',
    },
  ]
}

export function appendTextDelta(
  parts: UiPart[],
  delta: string,
  parent_task_call_id?: string,
): UiPart[] {
  if (delta.includes(COMPACTION_BOUNDARY)) {
    const segments = delta.split(COMPACTION_BOUNDARY)
    let out = parts
    segments.forEach((segment, index) => {
      if (segment) {
        if (index === 0) {
          out = appendTextDelta(out, segment, parent_task_call_id)
        } else {
          out = [
            ...out,
            {
              id: genPartId('text'),
              type: 'text',
              content: segment,
              status: 'streaming',
              ...(parent_task_call_id ? { parent_task_call_id } : {}),
            },
          ]
        }
      }
      if (index < segments.length - 1) {
        out = [
          ...out,
          {
            id: genPartId('compaction-boundary'),
            type: 'text',
            content: COMPACTION_BOUNDARY,
            status: 'completed',
          },
        ]
      }
    })
    return out
  }
  const parentId = parent_task_call_id?.trim() || undefined
  const next = parts.map((p) => ({ ...p })) as UiPart[]
  // 跳过其它 parent 的交错 part，避免子 Agent 正文被拆碎
  for (let i = next.length - 1; i >= 0; i--) {
    const p = next[i]
    if (part_parent_task_call_id(p) !== parentId) {
      continue
    }
    if (p.type === 'text') {
      next[i] = {
        ...p,
        content: p.content + delta,
        status: p.status === 'completed' ? 'streaming' : (p.status || 'streaming'),
      }
      return next
    }
    break
  }
  next.push({
    id: genPartId('text'),
    type: 'text',
    content: delta,
    status: 'streaming',
    ...(parentId ? { parent_task_call_id: parentId } : {}),
  })
  return next
}

export type RedactedThinkingStreamMode = 'text' | 'thinking'

/** 与 {@link appendTextDeltaWithRedactedThinking} 配合，跨 text-delta 缓冲可能被拆开的标签 */
export interface RedactedThinkingStreamCtx {
  mode: RedactedThinkingStreamMode
  pending: string
}

export function createRedactedThinkingStreamCtx(): RedactedThinkingStreamCtx {
  return { mode: 'text', pending: '' }
}

/** 若末尾可能是完整 token 的真前缀，则暂不输出，留待与下一 chunk 拼接 */
function takeEmitAndHoldForToken(s: string, token: string): { emit: string, hold: string } {
  const maxCheck = Math.min(s.length, token.length - 1)
  for (let k = maxCheck; k >= 1; k--) {
    const suf = s.slice(-k)
    if (token.startsWith(suf)) {
      return { emit: s.slice(0, s.length - k), hold: suf }
    }
  }
  return { emit: s, hold: '' }
}

/**
 * 将正文流中的 `<think>…</think>` 拆成 reasoning 部件（折叠展示），其余仍走 text。
 * 标签可跨多个 SSE chunk；ctx 须在每条助手流开始时 reset，结束时 {@link flushRedactedThinkingStreamCtx}。
 */
export function appendTextDeltaWithRedactedThinking(
  parts: UiPart[],
  delta: string,
  ctx: RedactedThinkingStreamCtx,
  parent_task_call_id?: string,
): UiPart[] {
  let out = parts
  let s = ctx.pending + delta
  ctx.pending = ''

  while (s.length > 0) {
    if (ctx.mode === 'text') {
      const idx = s.indexOf(REDACTED_OPEN)
      if (idx !== -1) {
        const before = s.slice(0, idx)
        if (before) {
          out = appendTextDelta(out, before, parent_task_call_id)
        }
        s = s.slice(idx + REDACTED_OPEN.length)
        ctx.mode = 'thinking'
        continue
      }
      const { emit, hold } = takeEmitAndHoldForToken(s, REDACTED_OPEN)
      if (emit) {
        out = appendTextDelta(out, emit, parent_task_call_id)
      }
      ctx.pending = hold
      return out
    }
    const idx = s.indexOf(REDACTED_CLOSE)
    if (idx !== -1) {
      const before = s.slice(0, idx)
      if (before) {
        out = appendReasoningDelta(out, before, parent_task_call_id)
      }
      out = completeReasoningPart(out, undefined, parent_task_call_id)
      s = s.slice(idx + REDACTED_CLOSE.length)
      ctx.mode = 'text'
      continue
    }
    const { emit, hold } = takeEmitAndHoldForToken(s, REDACTED_CLOSE)
    if (emit) {
      out = appendReasoningDelta(out, emit, parent_task_call_id)
    }
    ctx.pending = hold
    return out
  }
  return out
}

/** 流结束或中断时把 pending 写入对应部件并回到 text 模式 */
export function flushRedactedThinkingStreamCtx(
  parts: UiPart[],
  ctx: RedactedThinkingStreamCtx,
): UiPart[] {
  let out = parts
  if (ctx.pending) {
    if (ctx.mode === 'text') {
      out = appendTextDelta(out, ctx.pending)
    } else {
      out = appendReasoningDelta(out, ctx.pending)
    }
    ctx.pending = ''
  }
  ctx.mode = 'text'
  return out
}

export function appendReasoningDelta(
  parts: UiPart[],
  delta: string,
  parent_task_call_id?: string,
): UiPart[] {
  const parentId = parent_task_call_id?.trim() || undefined
  const next = parts.map((p) => ({ ...p })) as UiPart[]
  // 跳过其它 parent 的交错 part（主 Agent 与子 Agent 事件交错时），
  // 合并进「同 parent 最近一条 reasoning」；同 parent 的 text/tool 之后才新开块。
  for (let i = next.length - 1; i >= 0; i--) {
    const p = next[i]
    if (part_parent_task_call_id(p) !== parentId) {
      continue
    }
    if (p.type === 'reasoning') {
      next[i] = {
        ...p,
        content: p.content + delta,
        status: 'streaming',
      }
      return next
    }
    break
  }
  next.push({
    id: genPartId('reasoning'),
    type: 'reasoning',
    content: delta,
    status: 'streaming',
    ...(parentId ? { parent_task_call_id: parentId } : {}),
  })
  return next
}

export function upsertToolInputPart(
  parts: UiPart[],
  tool_call_id: string,
  name: string,
  input: Record<string, unknown>,
  parent_task_call_id?: string,
  step_id?: string,
): UiPart[] {
  const next = parts.map((p) => ({ ...p })) as UiPart[]
  const idx = next.findIndex((p) => p.type === 'tool' && p.tool_call_id === tool_call_id)
  const parentId = parent_task_call_id?.trim() || undefined
  if (idx !== -1) {
    const tp = next[idx] as ToolUiPart
    next[idx] = {
      ...tp,
      name: name || tp.name,
      input,
      ...(parentId ? { parent_task_call_id: parentId } : {}),
      ...(step_id ? { step_id } : {}),
    }
    return next
  }
  next.push({
    id: genPartId('tool'),
    type: 'tool',
    tool_call_id,
    name,
    input,
    output: '',
    status: 'running',
    state: 'running',
    ...(parentId ? { parent_task_call_id: parentId } : {}),
    ...(step_id ? { step_id } : {}),
  })
  return next
}

export function applyToolOutput(
  parts: UiPart[],
  tool_call_id: string,
  payload: {
    output: string
    error?: string
    status: 'success' | 'error'
    duration_ms?: number
    errorCategory?: string
    state?: ToolLifecycleState
    outcome?: string
    exit_code?: number
    timed_out?: boolean
    truncated?: boolean
    step_id?: string
  },
): UiPart[] {
  const next = parts.map((p) => ({ ...p })) as UiPart[]
  const idx = next.findIndex((p) => p.type === 'tool' && p.tool_call_id === tool_call_id)
  const state = payload.state ?? (payload.status === 'error' ? 'failed' : 'succeeded')
  const status: ToolRunStatus = payload.status === 'error' ? 'error' : 'success'
  if (idx === -1) {
    next.push({
      id: genPartId('tool'),
      type: 'tool',
      tool_call_id,
      name: '',
      input: {},
      output: payload.output,
      status,
      state,
      error: payload.error,
      errorCategory: payload.errorCategory,
      duration_ms: payload.duration_ms,
      outcome: payload.outcome,
      exit_code: payload.exit_code,
      timed_out: payload.timed_out,
      truncated: payload.truncated,
      ...(payload.step_id ? { step_id: payload.step_id } : {}),
    })
    return next
  }
  const tp = next[idx] as ToolUiPart
  if (isTerminalToolState(tp.state) && tp.state !== state) {
    return next
  }
  // start_task 输出文本含「子 Agent 已启动：<uuid>」：流式路径同样要提取
  // child_session_id，否则工具下发后任务卡匹配不到目录状态（只能显示
  // 「已完成」fallback）。normalizeToolPart（落库回放路径）已有同一解析。
  const streamedChildSessionId = tp.name === START_TASK_TOOL_NAME
    ? parseStartTaskChildSessionId(payload.output)
    : undefined
  next[idx] = {
    ...tp,
    output: payload.output,
    error: payload.error,
    errorCategory: payload.errorCategory ?? tp.errorCategory,
    status,
    state,
    duration_ms: payload.duration_ms ?? tp.duration_ms,
    outcome: payload.outcome ?? tp.outcome,
    exit_code: payload.exit_code ?? tp.exit_code,
    timed_out: payload.timed_out ?? tp.timed_out,
    truncated: payload.truncated ?? tp.truncated,
    step_id: payload.step_id ?? tp.step_id,
    ...(streamedChildSessionId ? { child_session_id: streamedChildSessionId } : {}),
  }
  return next
}

export function applyHitlPendingParts(
  parts: UiPart[],
  payload: {
    interrupt_id: string
    kind: string
    action_requests: Array<{ tool_call_id?: string, name?: string, args?: Record<string, unknown> }>
  },
): UiPart[] {
  let next = parts.map((p) => ({ ...p })) as UiPart[]
  for (const action of payload.action_requests || []) {
    const tool_call_id = action.tool_call_id || ''
    const name = action.name || ''
    const args = action.args && typeof action.args === 'object' ? action.args : {}
    if (tool_call_id) {
      next = upsertToolInputPart(next, tool_call_id, name, args)
    }
    const idx = next.findIndex((p) => p.type === 'tool' && p.tool_call_id === tool_call_id)
    if (idx === -1) {
      continue
    }
    const tp = next[idx] as ToolUiPart
    next[idx] = {
      ...tp,
      status: 'running',
      state: 'approval_pending',
      hitl: {
        kind: payload.kind,
        status: 'pending',
        interrupt_id: payload.interrupt_id,
      },
    }
  }
  return next
}
