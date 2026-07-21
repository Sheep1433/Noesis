export interface MainNavItem {
  label: string
  key: string
  /** vue-router name，智枢首页用空字符串表示 `/` */
  routeName: string
  iconClass: string
  fill?: boolean
}

export const mainNavItems: MainNavItem[] = [
  {
    label: '智枢',
    key: 'SystemLogo',
    routeName: '',
    iconClass: 'i-my-svg:system-logo',
    fill: true,
  },
  {
    label: '对话',
    key: 'ChatIndex',
    routeName: 'ChatIndex',
    iconClass: 'i-my-svg:chat-index',
  },
  {
    label: '知识库',
    key: 'KnowledgeBase',
    routeName: 'KnowledgeBase',
    iconClass: 'i-my-svg:chat-knowledge',
  },
  {
    label: 'Skills',
    key: 'SkillsManagement',
    routeName: 'SkillsManagement',
    iconClass: 'i-my-svg:chat-skill',
  },
  {
    label: 'MCP',
    key: 'McpManagement',
    routeName: 'McpManagement',
    iconClass: 'i-mdi:toy-brick-outline',
  },
  {
    label: '测试',
    key: 'TestCaseGenerate',
    routeName: 'TestCaseGenerate',
    iconClass: 'i-mdi:clipboard-text-outline',
  },
]
