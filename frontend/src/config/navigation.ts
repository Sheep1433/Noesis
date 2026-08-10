export interface MainNavItem {
  label: string
  key: string
  /** vue-router name，智枢首页用空字符串表示 `/` */
  routeName: string
  iconClass: string
  fill?: boolean
}

export const CHAT_ROUTE_NAMES = ['ChatRoot', 'ChatIndex', 'ChatNew', 'ChatSession'] as const
const MOBILE_BOTTOM_NAV_HIDDEN_ROUTE_NAMES = [
  ...CHAT_ROUTE_NAMES,
  'Settings',
  'KnowledgeBase',
  'KnowledgeBaseDetail',
] as const

export function isChatRouteName(routeName: unknown): boolean {
  return CHAT_ROUTE_NAMES.includes(routeName as (typeof CHAT_ROUTE_NAMES)[number])
}

export function shouldShowMobileBottomNav(routeName: unknown, isMobile: boolean): boolean {
  return isMobile && !MOBILE_BOTTOM_NAV_HIDDEN_ROUTE_NAMES.includes(
    routeName as (typeof MOBILE_BOTTOM_NAV_HIDDEN_ROUTE_NAMES)[number],
  )
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
    label: '扩展',
    key: 'Extensions',
    routeName: 'Extensions',
    iconClass: 'i-mdi:puzzle-outline',
  },
]
