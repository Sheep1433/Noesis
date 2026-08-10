/** 按 qa_type 可用的 subagent 静态表（与后端 MentionResolveService 对齐） */

export interface SubagentOption {
  id: string
  label: string
  description: string
}

const SUBAGENTS_BY_QA: Record<string, SubagentOption[]> = {
  SUPER_AGENT_QA: [
    {
      id: 'task-worker',
      label: 'task-worker',
      description: '独立上下文执行单个子任务',
    },
  ],
  FAULT_OPERATION_QA: [
    {
      id: 'general-purpose',
      label: 'general-purpose',
      description: '多步远程诊断与并行排查',
    },
  ],
}

export function getSubagentsForQaType(qaType: string): SubagentOption[] {
  return SUBAGENTS_BY_QA[qaType] ?? []
}

export function supportsSlashSkills(qaType: string): boolean {
  return qaType === 'SUPER_AGENT_QA'
}

export function supportsAtMentions(qaType: string): boolean {
  return qaType === 'SUPER_AGENT_QA' || qaType === 'FAULT_OPERATION_QA'
}

export function composerPlaceholder(qaType: string, uploading: boolean): string {
  if (uploading) {
    return '正在上传附件…'
  }
  if (supportsSlashSkills(qaType)) {
    return '输入消息，使用 / 调用 Skill，使用 @ 引用文件或协作助手…'
  }
  if (supportsAtMentions(qaType)) {
    return '输入消息，使用 @ 引用文件或协作助手…'
  }
  return '输入消息…'
}
