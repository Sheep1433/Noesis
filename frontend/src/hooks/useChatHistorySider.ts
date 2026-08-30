import { useLocalStorage } from '@vueuse/core'

/**
 * 聊天历史侧栏折叠态：模块级单例。
 * chat.vue 的 Sider 与全局导航栏的智枢 logo（聊天页悬停变形为侧栏开关）
 * 共享同一实例，保证两处状态始终一致；localStorage 持久化沿用旧 key。
 */
export const chatHistorySiderCollapsed = useLocalStorage('collapsed-chat-menu', false)

export function toggleChatHistorySider() {
  chatHistorySiderCollapsed.value = !chatHistorySiderCollapsed.value
}
