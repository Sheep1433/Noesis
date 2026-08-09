export type ChatModeQaType = 'COMMON_QA' | 'SUPER_AGENT_QA' | 'FAULT_OPERATION_QA'

export interface ChatModeOption {
  qaType: ChatModeQaType
  label: string
  description: string
  iconClass: string
}

export const CHAT_MODE_OPTIONS: readonly ChatModeOption[] = [
  {
    qaType: 'COMMON_QA',
    label: '聊天',
    description: '日常问答与知识库查询',
    iconClass: 'i-hugeicons:message-01',
  },
  {
    qaType: 'SUPER_AGENT_QA',
    label: '任务',
    description: '调研、分析与多步骤执行',
    iconClass: 'i-hugeicons:task-01',
  },
  {
    qaType: 'FAULT_OPERATION_QA',
    label: '故障排查',
    description: '定位异常并给出处理建议',
    iconClass: 'i-hugeicons:wrench-01',
  },
]

/** qa_type 展示文案（含历史库内 DEEP_RESEARCH_QA 只读映射） */
export const QA_TYPE_LABELS: Record<string, string> = {
  ...Object.fromEntries(CHAT_MODE_OPTIONS.map(({ qaType, label }) => [qaType, label])),
  TEST_CASE_QA: '测试用例',
  DEEP_RESEARCH_QA: CHAT_MODE_OPTIONS[1].label,
}

export function chatModeOption(qaType: string | undefined | null): ChatModeOption {
  const normalized = qaType === 'DEEP_RESEARCH_QA' ? 'SUPER_AGENT_QA' : qaType
  return CHAT_MODE_OPTIONS.find((option) => option.qaType === normalized) ?? CHAT_MODE_OPTIONS[0]
}

export function isChatModeChange(currentQaType: string, targetQaType: ChatModeQaType): boolean {
  return currentQaType !== targetQaType
}

export function qaTypeLabel(qaType: string | undefined | null): string {
  if (!qaType) {
    return QA_TYPE_LABELS.COMMON_QA
  }
  return QA_TYPE_LABELS[qaType] ?? QA_TYPE_LABELS.COMMON_QA
}

export function isSuperAgentQaType(qaType: string | undefined | null): boolean {
  return qaType === 'SUPER_AGENT_QA' || qaType === 'DEEP_RESEARCH_QA'
}
