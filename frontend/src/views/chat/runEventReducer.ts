import type { AgentRunSnapshot } from '@/api/chat'
import type { SessionStats } from '@/utils/statsFormat'
import { wireTimestampMs } from '@/utils/formatTime'

/**
 * run 级领域事件 reducer——子会话消费的单一状态转移（当前唯一消费方）。
 *
 * 统一帧词汇后 reducer 收窄为 run 生命周期 + 统计 + 上下文：内容投影由
 * 宿主经 messageParts appenders（与主聊天同一投影函数族）从帧事件组装，
 * 权威恢复走 run-snapshot 快照 replace——assistantContent / message-updated
 * 已退役。reducer 是纯函数；消息列表 upsert / 终态时间回填等 DOM 副作用
 * 由宿主按 state 差异执行。
 */

export type RunPendingHitl = AgentRunSnapshot['pending_hitl']

export type RunDomainEvent =
  /** 快照重置（订阅起点 / 断线重连的权威全量；宿主同步 replace 消息内容） */
  | { type: 'run-snapshot', snapshot: AgentRunSnapshot }
  | { type: 'context-update', context: Record<string, unknown>, sequence?: number }
  /** 实时统计（executor 每次模型调用发布；终态由宿主回落 DB 重建） */
  | { type: 'stats-update', stats: SessionStats }
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
  /** 运行中流式统计（stats-update 累进；run-snapshot 重置、终态后宿主回落 DB 重建） */
  stats: SessionStats | null
  /** 终态时刻（run.finished 一次性置位；宿主回填后可忽略后续） */
  finishedAt: number | null
}

export function initialRunEventState(): RunEventState {
  return { run: null, contextSnapshot: null, stats: null, finishedAt: null }
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
      // 新 run 快照重置终态时刻与流式统计（上一 run 的不得泄漏到新 turn）
      return {
        ...state,
        run: event.snapshot,
        stats: null,
        finishedAt: null,
      }

    case 'context-update':
      return {
        ...state,
        run: advanceSequence(state.run, event.sequence),
        contextSnapshot: { ...event.context },
      }

    case 'stats-update':
      return { ...state, stats: event.stats }

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
    case 'context-update':
      if (!payload.context || typeof payload.context !== 'object') {
        return null
      }
      return {
        type: 'context-update',
        context: payload.context as Record<string, unknown>,
        sequence: Number(payload.sequence ?? 0) || undefined,
      }
    case 'stats-update':
      // executor 发布时 usage 字段直接并入 payload 顶层（wire 合并）
      if (typeof payload.steps !== 'number') {
        return null
      }
      return {
        type: 'stats-update',
        stats: payload as unknown as SessionStats,
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
