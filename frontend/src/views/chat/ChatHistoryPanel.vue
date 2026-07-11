<script lang="ts" setup>
import type { DataTableColumns } from 'naive-ui'
import ThemeSwitcher from '@/components/ThemeSwitcher/index.vue'
import { cssVar, themeCssVar } from '@/config/theme'

interface TableItem {
  uuid: string
  chat_id: string
  qa_type: string
  key: string
}

defineProps<{
  stylizingLoading: boolean
  isFocusSearchChat: boolean
  isLoadingHistory: boolean
  tableData: TableItem[]
  historySidebarColumns: DataTableColumns<TableItem>
  sessionContextMenuShow: boolean
  sessionContextMenuX: number
  sessionContextMenuY: number
  sessionContextMenuOptions: Array<{ label: string, key: string }>
  rowProps: (row: TableItem) => Record<string, unknown>
  /** 移动端历史抽屉底部：主题切换 + 用户头像 */
  showAccountActions?: boolean
}>()

const emit = defineEmits<{
  newChat: []
  focusSearch: []
  blurSearch: []
  search: []
  clear: []
  openModal: []
  contextMenuSelect: [key: string]
  contextMenuClose: []
}>()

const searchText = defineModel<string>('searchText', { required: true })

const searchChatRef = useTemplateRef('searchChatRef')

const router = useRouter()
const userStore = useUserStore()
const showUserMenu = ref(false)

async function handleLogout() {
  showUserMenu.value = false
  await userStore.logout()
  setTimeout(() => {
    router.replace('/login')
  }, 500)
}

defineExpose({ focusSearch: () => searchChatRef.value?.focus() })
</script>

<template>
  <div
    h-full
    class="chat-history-panel"
    flex="~ col"
  >
    <div class="sidebar-header-toolbar header p-20">
      <div
        class="create-chat-box"
        :class="{ hide: isFocusSearchChat }"
      >
        <n-button
          type="primary"
          icon-placement="left"
          strong
          class="create-chat"
          :disabled="stylizingLoading"
          @click="emit('newChat')"
        >
          <template #icon>
            <n-icon>
              <div class="i-hugeicons:add-01"></div>
            </n-icon>
          </template>
          新建对话
        </n-button>
      </div>
      <button
        v-if="!isFocusSearchChat"
        type="button"
        class="search-chat-trigger"
        aria-label="搜索对话"
        @click="emit('focusSearch')"
      >
        <span class="search-chat-trigger__icon i-hugeicons:search-01" aria-hidden="true"></span>
      </button>
      <n-input
        v-else
        ref="searchChatRef"
        v-model:value="searchText"
        placeholder="搜索"
        class="search-chat-input"
        clearable
        @blur="emit('blurSearch')"
        @input="emit('search')"
        @keyup.enter="emit('search')"
        @clear="emit('clear')"
      >
        <template #prefix>
          <span class="search-chat-input__icon i-hugeicons:search-01" aria-hidden="true"></span>
        </template>
      </n-input>
    </div>

    <div flex="1 ~ col" class="scrollable-table-container">
      <n-dropdown
        trigger="manual"
        placement="bottom-start"
        :show="sessionContextMenuShow"
        :x="sessionContextMenuX"
        :y="sessionContextMenuY"
        :options="sessionContextMenuOptions"
        @select="emit('contextMenuSelect', $event)"
        @clickoutside="emit('contextMenuClose')"
      />
      <n-data-table
        class="custom-table"
        :style="{
          'font-size': '14px',
          '--n-td-color': cssVar(themeCssVar.bgElevated),
          'font-family': `-apple-system, BlinkMacSystemFont,'Segoe UI', Roboto, 'Helvetica Neue', Arial,sans-serif`,
        }"
        size="small"
        :bordered="false"
        :bottom-bordered="false"
        :single-line="false"
        :columns="historySidebarColumns"
        :data="tableData"
        :loading="isLoadingHistory"
        :row-props="rowProps"
      />
    </div>

    <div class="footer" style="flex-shrink: 0">
      <n-divider class="footer-divider" />
      <div
        class="footer-toolbar"
        :class="{ 'footer-toolbar--with-actions': showAccountActions }"
      >
        <n-button
          quaternary
          icon-placement="left"
          type="primary"
          strong
          class="manage-chat-btn"
          @click="emit('openModal')"
        >
          <template #icon>
            <n-icon>
              <div class="i-hugeicons:voice-id"></div>
            </n-icon>
          </template>
          管理对话
        </n-button>
        <div
          v-if="showAccountActions"
          class="history-account-actions"
        >
          <ThemeSwitcher placement="top-end" compact />
          <n-popover
            v-model:show="showUserMenu"
            trigger="click"
            placement="top-end"
            :show-arrow="false"
          >
            <template #trigger>
              <button
                type="button"
                class="history-account-actions__avatar"
                aria-label="账户菜单"
              >
                <span class="history-account-actions__avatar-icon i-my-svg:avatar" aria-hidden="true"></span>
              </button>
            </template>
            <n-button
              quaternary
              strong
              @click="handleLogout"
            >
              退出登录
            </n-button>
          </n-popover>
        </div>
      </div>
    </div>
  </div>
</template>

<style lang="scss" scoped>
.create-chat-box {
  flex: 1;
  min-width: 0;
  overflow: visible;
  transition: flex 0.25s ease, opacity 0.25s ease, margin 0.25s ease;

  &.hide {
    flex: 0 0 0;
    width: 0;
    margin: 0;
    opacity: 0;
    overflow: hidden;
    pointer-events: none;
  }
}

.create-chat {
  width: 100%;
  height: 40px;
  text-align: center;
  font-family: inherit;
  font-weight: 500;
  font-size: 14px;
  border-radius: var(--noesis-radius-pill);

  &:deep(.n-button__border),
  &:deep(.n-button__state-border) {
    border-radius: inherit !important;
  }
}

.sidebar-header-toolbar {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-shrink: 0;
  position: sticky;
  top: 0;
  z-index: 1;
}

.search-chat-trigger {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  width: 36px;
  height: 36px;
  margin: 0;
  padding: 0;
  border: 1px solid var(--noesis-color-border, #e8eaf3);
  border-radius: var(--noesis-radius-round);
  background: var(--noesis-color-bg-elevated, #fff);
  color: var(--noesis-color-text-muted, #64748b);
  cursor: pointer;
  transition: border-color 0.2s ease, color 0.2s ease, background-color 0.2s ease;
}

.search-chat-trigger:hover {
  color: var(--noesis-color-primary, #5c7cfa);
  border-color: var(--noesis-color-primary-muted, #a48ef4);
  background: var(--noesis-color-primary-bg-subtle, rgb(92 124 250 / 4%));
}

.search-chat-trigger__icon {
  display: inline-block;
  width: 18px;
  height: 18px;
  font-size: 18px;
  line-height: 1;
  color: var(--noesis-color-text-secondary);
  flex-shrink: 0;
}

.search-chat-input {
  flex: 1;
  min-width: 0;
}

.search-chat-input :deep(.n-input-wrapper) {
  height: 36px;
  border-radius: var(--noesis-radius-pill);
}

.search-chat-input__icon {
  display: inline-block;
  width: 16px;
  height: 16px;
  font-size: 16px;
  color: var(--noesis-color-text-secondary);
  flex-shrink: 0;
}

.scrollable-table-container {
  overflow-y: auto;
  height: 100%;
  background-color: var(--noesis-color-bg-elevated);
  transition: background-color 0.3s cubic-bezier(0.4, 0, 0.2, 1);
}

.scrollable-table-container::-webkit-scrollbar {
  width: 5px;
}

.scrollable-table-container::-webkit-scrollbar-track {
  background: transparent;
}

.scrollable-table-container::-webkit-scrollbar-thumb {
  background-color: var(--noesis-scrollbar-thumb-muted);
  border-radius: 4px;
}

:deep(.custom-table .n-data-table-thead) {
  display: none;
}

:deep(.custom-table .n-data-table-table) {
  border-collapse: collapse;
}

:deep(.custom-table .n-data-table-th),
:deep(.custom-table .n-data-table-td) {
  border: none;
}

:deep(.custom-table td) {
  color: var(--noesis-color-text, #1a1d33);
  padding: 12px 16px;
  background-color: var(--noesis-color-bg-elevated, #fff);
  transition: background-color 0.3s cubic-bezier(0.4, 0, 0.2, 1);
  font-family:
    -apple-system,
    BlinkMacSystemFont,
    'Segoe UI',
    Roboto,
    Oxygen, Ubuntu, Cantarell,
    'Open Sans', 'Helvetica Neue', Arial,
    sans-serif,
    system-ui,
    "SF Pro Text";
  font-size: 14px;
  -webkit-font-smoothing: antialiased;
  -moz-osx-font-smoothing: grayscale;
  text-rendering: optimizelegibility;
}

:deep(.custom-table .selected-row td) {
  color: var(--noesis-color-primary) !important;
  font-weight: bold;
  padding: 12px 30px !important;
  background: var(--noesis-chat-selected-row-bg);
  transform: scale(1.001);
  transition: all 0.3s ease;
}

.header,
.footer {
  background-color: var(--noesis-color-bg-elevated);
}

.footer-divider {
  width: calc(100% - 32px);
  margin: 0 auto;
  --n-color: var(--noesis-color-bg-muted);
}

.footer-toolbar {
  display: flex;
  align-items: center;
  padding: 0 16px 12px;
}

.footer-toolbar--with-actions {
  gap: 12px;
}

.manage-chat-btn {
  height: 38px;
  font-family: inherit;
  font-size: 14px;
}

.footer-toolbar:not(.footer-toolbar--with-actions) .manage-chat-btn {
  width: min(200px, calc(100% - 32px));
  margin: 0 auto;
}

.footer-toolbar--with-actions .manage-chat-btn {
  flex: 1;
  min-width: 0;
  justify-content: flex-start;
}

.history-account-actions {
  display: flex;
  flex-shrink: 0;
  gap: 8px;
  align-items: center;
}

.history-account-actions__avatar {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 32px;
  height: 32px;
  margin: 0;
  padding: 0;
  border: 1px solid var(--noesis-color-border, #e8eaf3);
  border-radius: 50%;
  background: var(--noesis-color-bg-elevated, #fff);
  cursor: pointer;
  transition: border-color 0.15s ease, background-color 0.15s ease;
}

.history-account-actions__avatar:hover {
  border-color: var(--noesis-color-primary-muted, #a48ef4);
  background: var(--noesis-color-primary-bg-subtle, rgb(92 124 250 / 4%));
}

.history-account-actions__avatar-icon {
  display: inline-block;
  width: 20px;
  height: 20px;
}
</style>
