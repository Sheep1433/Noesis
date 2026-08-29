import type { AgentRunSnapshot } from '@/api/chat'

/**
 * run 级领域事件 reducer（主/子会话共用消费单点）。
 *
 * 领域事件与传输形态无关：子会话 run-event 经 parseRunEvent 产出、
 * 主会话 SSE 帧解析层可同样产出（后续迁移项）。reducer 是纯函数——
 * 输入 (state, event)，输出新 state；消息列表 upsert / 终态时间回填等
 * DOM 副作用由宿主根据 state 差异执行。
 */

export type RunPendingHitl = AgentRunSnapshot['pending_hitl']

export type RunDomainEvent =
  /** 快照重置（订阅起点 / 断线重连的权威全量） */
  | { type: 'run-snapshot', snapshot: AgentRunSnapshot }
  /** assistant 内容投影更新（message.updated） */
  | { type: 'message-updated', content: unknown, sequence?: number }
  | { type: 'context-update', context: Record<string, unknown>, sequence?: number }
  /** 排队任务被调度启动（快照仍是 queued，推进到 running） */
  | { type: 'run-started', sequence?: number }
  /** 审批挂起：内容投影 + pending_hitl */
  | { type: 'approval-required', content?: unknown, pendingHitl: RunPendingHitl, sequence?: number }
  | { type: 'approval-resumed', sequence?: number }
  /** 终态：status + finishedAt（宿主回填 assistant 消息 run_finished_at） */
  | { type: 'run-finished', status: string, finishedAt?: number, sequence?: number }

export interface RunEventState {
  run: AgentRunSnapshot | null
  contextSnapshot: Record<string, unknown> | null
  /** 事件携带的最新 assistant 内容（run-snapshot / message-updated / approval-required） */
  assistantContent: unknown
  /** 终态时刻（run.finished 一次性置位；宿主回填后可忽略后续） */
  finishedAt: number | null
}

export function initialRunEventState(): RunEventState {
  return { run: null, contextSnapshot: null, assistantContent: null, finishedAt: null }
}

function advanceSequence(
  run: AgentRunSnapshot | null,
  sequence?: number,
): AgentRunSnapshot | null {
  if (!run || sequence === undefined) {
    return run
  }
  if (sequence > Number(run.snapshot_sequence ?? 0)) {
    return { ...run, snapshot_sequence: sequence }
  }
  return run
}

export function runEventReducer(state: RunEventState, event: RunDomainEvent): RunEventState {
  switch (event.type) {
    case 'run-snapshot':
      // 新 run 快照重置终态时刻（上一 run 的 finishedAt 不得泄漏到新 turn）
      return { ...state, run: event.snapshot, assistantContent: event.snapshot.content, finishedAt: null }

    case 'message-updated': {
      const run = advanceSequence(state.run, event.sequence)
      return {
        ...state,
        run: run ? { ...run, pending_hitl: null } : run,
        assistantContent: event.content,
      }
    }

    case 'context-update':
      return {
        ...state,
        run: advanceSequence(state.run, event.sequence),
        contextSnapshot: { ...event.context },
      }

    case 'run-started': {
      const run = advanceSequence(state.run, event.sequence)
      if (!run || run.status !== 'queued') {
        return { ...state, run }
      }
      return { ...state, run: { ...run, status: 'running' } }
    }

    case 'approval-required': {
      const run = advanceSequence(state.run, event.sequence)
      return {
        ...state,
        run: {
          ...(run ?? {} as AgentRunSnapshot),
          status: 'hitl_pending',
          pending_hitl: event.pendingHitl,
        },
        assistantContent: event.content ?? state.assistantContent,
      }
    }

    case 'approval-resumed': {
      const run = advanceSequence(state.run, event.sequence)
      if (!run) {
        return state
      }
      return { ...state, run: { ...run, status: 'running', pending_hitl: null } }
    }

    case 'run-finished': {
      const run = advanceSequence(state.run, event.sequence)
      if (!run) {
        return { ...state, finishedAt: event.finishedAt ?? null }
      }
      return {
        ...state,
        run: {
          ...run,
          status: event.status as AgentRunSnapshot['status'],
          pending_hitl: null,
        },
        finishedAt: event.finishedAt ?? state.finishedAt,
      }
    }
  }
}

/** wire 时间戳归一化（秒/毫秒自适应；无效值返回 undefined） */
function wireTimestampMs(value: number | null | undefined): number | undefined {
  if (value == null || !Number.isFinite(value)) {
    return undefined
  }
  return Math.abs(value) < 1e12 ? value * 1000 : value
}

/**
 * 子会话 run-event wire（executor 发布）→ 领域事件。
 * 传输层解析集中于此：字段名（snake_case / pending_hitl 提取 / finished_at
 * 归一化）不进入 reducer。
 */
export function parseRunEvent(
  event: string,
  payload: Record<string, unknown>,
): RunDomainEvent | null {
  switch (event) {
    case 'run-snapshot':
      return { type: 'run-snapshot', snapshot: payload as unknown as AgentRunSnapshot }
    case 'message.updated':
      return {
        type: 'message-updated',
        content: payload.content,
        sequence: Number(payload.sequence ?? 0) || undefined,
      }
    case 'context-update':
      if (!payload.context || typeof payload.context !== 'object') {
        return null
      }
      return {
        type: 'context-update',
        context: payload.context as Record<string, unknown>,
        sequence: Number(payload.sequence ?? 0) || undefined,
      }
    case 'run.started':
      return { type: 'run-started', sequence: Number(payload.sequence ?? 0) || undefined }
    case 'approval.required':
      return {
        type: 'approval-required',
        content: payload.content,
        pendingHitl: payload.pending_hitl as RunPendingHitl,
        sequence: Number(payload.sequence ?? 0) || undefined,
      }
    case 'approval.resumed':
      return { type: 'approval-resumed', sequence: Number(payload.sequence ?? 0) || undefined }
    case 'run.finished':
      return {
        type: 'run-finished',
        status: String(payload.status || 'completed'),
        finishedAt: wireTimestampMs(payload.finished_at as number | null) ?? Date.now(),
        sequence: Number(payload.sequence ?? 0) || undefined,
      }
    default:
      return null
  }
}
