export interface MainNavItem {
  label: string
  key: string
  /** vue-router name，智枢首页用空字符串表示 `/` */
  routeName: string
  iconClass: string
  fill?: boolean
}

export const CHAT_ROUTE_NAMES = ['ChatRoot', 'ChatIndex', 'ChatNew', 'ChatSession'] as const
const MOBILE_BOTTOM_NAV_HIDDEN_ROUTE_NAMES = ['KnowledgeBaseDetail'] as const

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

const settingsNavItem: MainNavItem = {
  label: '设置',
  key: 'Settings',
  routeName: 'Settings',
  iconClass: 'i-hugeicons:settings-01',
}

/** 移动端顶层页面统一使用的全局导航。 */
export const mobileProductNavItems: MainNavItem[] = [
  mainNavItems[1],
  mainNavItems[2],
  mainNavItems[3],
  settingsNavItem,
]

/** 历史抽屉保留管理入口，不重复展示当前所在的对话入口。 */
export const mobileHistoryNavItems: MainNavItem[] = [
  mainNavItems[2],
  mainNavItems[3],
  settingsNavItem,
]
