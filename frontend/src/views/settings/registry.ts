export const SETTINGS_SECTION_IDS = [
  'overview',
  'models',
  'profile',
  'memory',
  'capabilities',
  'automation',
  'channels',
  'diagnostics',
  'account',
] as const

export type SettingsSectionId = typeof SETTINGS_SECTION_IDS[number]

export type SettingsSectionDefinition = {
  id: SettingsSectionId
  label: string
  description: string
  keywords: readonly string[]
}

export const SETTINGS_SECTIONS: readonly SettingsSectionDefinition[] = [
  {
    id: 'overview',
    label: '概览',
    description: '配置健康与常用入口',
    keywords: ['首页', '健康', '状态'],
  },
  {
    id: 'models',
    label: '模型',
    description: '平台可用模型目录',
    keywords: ['模型', '聊天', '视觉', '默认'],
  },
  {
    id: 'profile',
    label: '画像',
    description: '用户画像与稳定信息',
    keywords: ['USER.md', '用户', '身份', '时区'],
  },
  {
    id: 'memory',
    label: '记忆',
    description: '跨会话偏好与惯例',
    keywords: ['AGENTS.md', '偏好', '上下文'],
  },
  {
    id: 'capabilities',
    label: '扩展',
    description: 'Skills、MCP 与知识库',
    keywords: ['能力', 'Skill', 'MCP', '知识库'],
  },
  {
    id: 'automation',
    label: '自动化',
    description: '定时任务与运行状态',
    keywords: ['cron', '任务', '调度', '运行'],
  },
  {
    id: 'channels',
    label: '通讯',
    description: '外部消息通道',
    keywords: ['Telegram', 'Bot', '通道', '消息'],
  },
  {
    id: 'diagnostics',
    label: '系统与迁移',
    description: '通知、健康检查与设置迁移',
    keywords: ['通知', '诊断', '健康', '导入', '导出', '恢复'],
  },
  {
    id: 'account',
    label: '账户',
    description: '账户与登录状态',
    keywords: ['用户', '退出', '安全'],
  },
]

const SECTION_ID_SET = new Set<string>(SETTINGS_SECTION_IDS)

export function isSettingsSectionId(value: unknown): value is SettingsSectionId {
  return typeof value === 'string' && SECTION_ID_SET.has(value)
}

export function resolveSettingsSection(value: unknown): SettingsSectionId {
  return isSettingsSectionId(value) ? value : 'overview'
}

export function filterSettingsSections(query: string): readonly SettingsSectionDefinition[] {
  const normalized = query.trim().toLocaleLowerCase()
  if (!normalized) {
    return SETTINGS_SECTIONS
  }

  return SETTINGS_SECTIONS.filter((section) => {
    const searchable = [section.id, section.label, section.description, ...section.keywords]
    return searchable.some((item) => item.toLocaleLowerCase().includes(normalized))
  })
}
