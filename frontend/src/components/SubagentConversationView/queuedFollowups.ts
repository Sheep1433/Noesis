import { reactive } from 'vue'

/**
 * 子 Agent 会话的前端待发队列：run 进行中用户发送的消息先在这里排队，
 * run 终态后逐条自动提交。
 *
 * 队列归属子会话（sessionId）而非组件实例——抽屉关闭即卸载会话视图，
 * 队列必须跨开关存活；localStorage 兜底页面刷新（仅内存会丢用户输入）。
 * localStorage 不可用（隐私模式/测试环境）时自动退化为仅内存。
 */

const STORAGE_KEY_PREFIX = 'noesis:subagent-queue:'

function loadQueue(sessionId: string): string[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY_PREFIX + sessionId)
    const parsed = raw ? JSON.parse(raw) : []
    return Array.isArray(parsed)
      ? parsed.filter((message): message is string => typeof message === 'string')
      : []
  } catch {
    return []
  }
}

function persistQueue(sessionId: string, messages: string[]): void {
  try {
    if (messages.length) {
      localStorage.setItem(STORAGE_KEY_PREFIX + sessionId, JSON.stringify(messages))
    } else {
      localStorage.removeItem(STORAGE_KEY_PREFIX + sessionId)
    }
  } catch {
    // 存储不可用：仅内存排队，刷新丢失
  }
}

const state = reactive(new Map<string, string[]>())

export function getQueuedFollowups(sessionId: string): string[] {
  if (!state.has(sessionId)) {
    state.set(sessionId, loadQueue(sessionId))
  }
  return state.get(sessionId) ?? []
}

export function setQueuedFollowups(sessionId: string, messages: string[]): void {
  state.set(sessionId, messages)
  persistQueue(sessionId, messages)
}

export function clearQueuedFollowups(sessionId: string): void {
  state.delete(sessionId)
  persistQueue(sessionId, [])
}
