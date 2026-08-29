<script setup lang="ts">
import type { TaskCatalogEntry } from '@/api/chat'
import type { ToolUiPart } from '@/views/chat/messageParts'
import { GitNetworkOutline } from '@vicons/ionicons-v5'
import { computed, ref } from 'vue'
import SubagentConversationDrawer from '@/components/SubagentConversationDrawer/index.vue'

const props = defineProps<{
  toolPart: ToolUiPart
  task?: TaskCatalogEntry
}>()

const show = ref(false)
const sessionId = computed(() => props.toolPart.child_session_id || props.task?.child_session_id || props.task?.task_id || '')
const title = computed(() => {
  const value = props.task?.description
    || (typeof props.toolPart.input.description === 'string' ? props.toolPart.input.description : '')
  return value.trim() || '子 Agent'
})

/** 下发即失败（工具调用 error 且目录无匹配）：没有子会话可开，卡片降级为失败提示 */
const dispatchFailed = computed(() => props.toolPart.status === 'error' && !props.task)
</script>

<template>
  <button
    type="button"
    class="subagent-card"
    :class="{ 'subagent-card--failed': dispatchFailed }"
    :disabled="dispatchFailed"
    :title="dispatchFailed ? '子 Agent 启动失败' : '查看子 Agent 会话'"
    @click="show = true"
  >
    <span class="subagent-card__icon"><n-icon size="14"><GitNetworkOutline /></n-icon></span>
    <span class="subagent-card__title">{{ title }}</span>
    <span v-if="dispatchFailed" class="subagent-card__failed">启动失败</span>
    <span v-else class="subagent-card__chevron" aria-hidden="true">›</span>
  </button>

  <SubagentConversationDrawer
    v-if="sessionId"
    v-model:show="show"
    :session-id="sessionId"
    :run-id="props.task?.run_id"
    :title="title"
  />
</template>

<style scoped lang="scss">
/* 静态任务入口：仅锚定「该轮派发了哪个后台任务」并提供跳转；
   实时状态（进行中/步数/耗时）由后台任务目录承载，不在聊天流重复展示 */
.subagent-card {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  min-height: 32px;
  padding: 4px 10px;
  border: 1px solid var(--noesis-block-light-border);
  border-radius: var(--noesis-radius-md);
  background: var(--noesis-block-light-bg);
  color: var(--noesis-color-text);
  font-size: 12px;
  text-align: left;
  cursor: pointer;
}

.subagent-card:hover:not(:disabled) {
  border-color: var(--noesis-color-primary-border-soft);
  background: var(--noesis-color-primary-bg-hover);
}

.subagent-card:disabled {
  cursor: default;
}

.subagent-card--failed {
  border-color: var(--noesis-color-danger-border-soft, rgb(220 38 38 / 25%));
}

.subagent-card__icon {
  display: inline-flex;
  flex: 0 0 auto;
  color: var(--noesis-color-text-secondary);
}

.subagent-card__title {
  flex: 1 1 auto;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-weight: 500;
}

.subagent-card__failed {
  flex: none;
  color: var(--noesis-color-danger);
}

.subagent-card__chevron {
  flex: none;
  color: var(--noesis-color-text-hint);
  font-size: 14px;
  line-height: 1;
}
</style>
