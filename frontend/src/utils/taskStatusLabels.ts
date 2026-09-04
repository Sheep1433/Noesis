import type { AgentRunStatus, TaskCatalogEntry } from '@/api/chat'

/**
 * 任务状态文案的统一来源：消息流任务卡（BackgroundSubagentCollapse）、
 * 任务目录抽屉（TaskCatalogPanel）、子会话视图（SubagentConversationView）
 * 共用，避免三处手抄漂移。
 *
 * launching / launched 是任务卡专用的 UI 伪状态：目录未匹配时只描述
 * start_task 工具下发本身（启动中 / 已启动），不宣称子 Agent 生命周期。
 */
export type TaskStatusKey =
  | AgentRunStatus
  | TaskCatalogEntry['status']
  | 'launching'
  | 'launched'

export const TASK_STATUS_LABELS: Record<TaskStatusKey, string> = {
  queued: '排队中',
  running: '进行中',
  stopping: '停止中',
  retrying: '重试中',
  hitl_pending: '待审批',
  awaiting_approval: '待审批',
  completed: '已完成',
  failed: '失败',
  cancelled: '已取消',
  timed_out: '超时',
  partial: '已停止',
  error: '失败',
  interrupted: '已中断',
  launching: '启动中',
  launched: '已启动',
}

/** 未收录状态原样展示（新状态先落文案再删兜底） */
export function taskStatusLabel(status: string): string {
  return (TASK_STATUS_LABELS as Record<string, string>)[status] || status
}
