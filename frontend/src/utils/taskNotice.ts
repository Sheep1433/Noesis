/** 续跑通知条目解析：一次续跑可能合并多条子 Agent 终态事件，全部展示 */

export interface TaskNoticeMeta {
  title: string
  detail: string
  tone: 'success' | 'warning' | 'error' | 'info'
}

interface NoticeEntry {
  label: string
  status: string
}

/** 后端 render_block 拼接的「子 Agent「label」status」稳定文本契约 */
const NOTICE_ENTRY_RE = /子 Agent「([^」]+)」(已完成|执行失败|执行超时|已取消)?/g

const NOTICE_TONE_BY_STATUS: Record<string, TaskNoticeMeta['tone']> = {
  已完成: 'success',
  已取消: 'warning',
  执行超时: 'error',
  执行失败: 'error',
}

const NOTICE_DETAIL_BY_STATUS: Record<string, string> = {
  已完成: '执行结果已收到，可打开任务详情查看完整过程。',
  已取消: '任务已停止，可重新发起或调整任务要求。',
  执行超时: '任务超过执行时限，可打开任务详情查看已完成的过程。',
  执行失败: '任务未能正常完成，可打开任务详情查看原因。',
}

function shortenLabel(label: string, max: number): string {
  return label.length > max ? `${label.slice(0, max)}…` : label
}

export function taskNoticeMeta(notice: string): TaskNoticeMeta {
  const entries: NoticeEntry[] = Array.from(notice.matchAll(NOTICE_ENTRY_RE)).map((match) => ({
    label: (match[1] || '').trim(),
    status: match[2] || '',
  }))
  if (entries.length === 0) {
    return legacyTaskNoticeMeta(notice)
  }
  if (entries.length === 1) {
    const { label, status } = entries[0]
    const metricText = notice.includes('·')
      ? notice.split('·').slice(1).join('·').split(/[（：。]/, 1)[0].trim()
      : ''
    const detail = NOTICE_DETAIL_BY_STATUS[status] ?? '可打开任务详情查看最新进度。'
    return {
      title: `子 Agent「${shortenLabel(label, 42)}」${status || '有新的状态'}`,
      detail: metricText ? `${detail}（${metricText}）` : detail,
      tone: NOTICE_TONE_BY_STATUS[status] ?? 'info',
    }
  }
  const tone: TaskNoticeMeta['tone'] = entries.some((entry) => NOTICE_TONE_BY_STATUS[entry.status] === 'error')
    ? 'error'
    : entries.some((entry) => NOTICE_TONE_BY_STATUS[entry.status] === 'warning')
      ? 'warning'
      : 'success'
  const allCompleted = entries.every((entry) => entry.status === '已完成')
  return {
    title: allCompleted ? `${entries.length} 个子 Agent 已完成` : `${entries.length} 个后台任务已结束`,
    detail: `${entries.map((entry) => shortenLabel(entry.label, 30)).join('、')}，可打开任务详情查看各自结果。`,
    tone,
  }
}

/** 无「子 Agent「…」」标签的兜底：按整体文本判定（正常通知不可达） */
function legacyTaskNoticeMeta(notice: string): TaskNoticeMeta {
  if (/取消|cancelled/i.test(notice)) {
    return { title: '后台子 Agent 已取消', detail: '任务已停止，可重新发起或调整任务要求。', tone: 'warning' }
  }
  if (/超时|timed_out/i.test(notice)) {
    return { title: '后台子 Agent 执行超时', detail: '任务超过执行时限，可打开任务详情查看已完成的过程。', tone: 'error' }
  }
  if (/失败|failed/i.test(notice)) {
    return { title: '后台子 Agent 执行失败', detail: '任务未能正常完成，可打开任务详情查看原因。', tone: 'error' }
  }
  return { title: '后台子 Agent 有新的状态', detail: '可打开任务详情查看最新进度。', tone: 'info' }
}
