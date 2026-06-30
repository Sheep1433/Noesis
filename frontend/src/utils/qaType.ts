/** qa_type 展示文案（含历史库内 DEEP_RESEARCH_QA 只读映射） */
export const QA_TYPE_LABELS: Record<string, string> = {
  COMMON_QA: '智能问答',
  SUPER_AGENT_QA: '智能体',
  FAULT_OPERATION_QA: '故障运维',
  TEST_CASE_QA: '测试用例',
  DEEP_RESEARCH_QA: '智能体',
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
