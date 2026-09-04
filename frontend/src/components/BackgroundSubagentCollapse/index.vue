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
    <span class="subagent-card__kind">子智能体</span>
    <span class="subagent-card__title">{{ title }}</span>
    <span v-if="dispatchFailed" class="subagent-card__failed">启动失败</span>
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
/* 扁平行入口：与主时间线的子 Agent 头部同构——kind 灰字 + 标题主题色可点。
   仅锚定「该轮派发了哪个后台任务」；实时状态（进行中/步数/耗时）由后台任务目录承载 */
.subagent-card {
  display: flex;
  align-items: center;
  gap: 6px;
  width: 100%;
  padding: 3px 8px;
  border: none;
  border-radius: var(--noesis-radius-md);
  background: transparent;
  color: var(--noesis-color-text);
  font-size: 13px;
  text-align: left;
  cursor: pointer;
  transition: background 0.15s ease;
}

.subagent-card:hover:not(:disabled) {
  background: var(--noesis-color-primary-bg-subtle, rgb(0 0 0 / 4%));
}

.subagent-card:disabled {
  cursor: default;
}

.subagent-card__icon {
  display: inline-flex;
  flex: 0 0 auto;
  color: var(--noesis-color-text-secondary);
}

.subagent-card__kind {
  flex: 0 0 auto;
  color: var(--noesis-color-text-secondary);
}

.subagent-card__title {
  flex: 1 1 auto;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-weight: 500;
  color: var(--noesis-color-primary);
}

.subagent-card:hover:not(:disabled) .subagent-card__title {
  text-decoration: underline;
}

.subagent-card--failed .subagent-card__title {
  color: var(--noesis-color-text-secondary);
}

.subagent-card__failed {
  flex: none;
  color: var(--noesis-color-danger);
}
</style>
