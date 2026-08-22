<script setup lang="ts">
import type { TaskCatalogEntry } from '@/api/chat'
import type { ToolUiPart } from '@/views/chat/messageParts'
import { GitNetworkOutline } from '@vicons/ionicons-v5'
import { computed, ref } from 'vue'
import SubagentConversationDrawer from '@/components/SubagentConversationDrawer/index.vue'
import { formatDurationMs } from '@/views/chat/messageParts'

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

const statusLabel: Record<TaskCatalogEntry['status'], string> = {
  running: '进行中',
  awaiting_approval: '待审批',
  completed: '已完成',
  failed: '失败',
  cancelled: '已取消',
  timed_out: '超时',
  partial: '已停止',
  error: '失败',
  interrupted: '已中断',
}

function timestampMs(value: number | null | undefined): number | undefined {
  if (value == null || !Number.isFinite(value)) {
    return undefined
  }
  return Math.abs(value) < 1e12 ? value * 1000 : value
}

const duration = computed(() => {
  const started = timestampMs(props.task?.started_at)
  if (!started) {
    return ''
  }
  const finished = timestampMs(props.task?.completed_at) ?? Date.now()
  return formatDurationMs(Math.max(0, finished - started))
})

const status = computed(() => props.task?.status || (props.toolPart.status === 'running' ? 'running' : 'completed'))
</script>

<template>
  <button type="button" class="subagent-card" @click="show = true">
    <span class="subagent-card__icon"><n-icon size="16"><GitNetworkOutline /></n-icon></span>
    <span class="subagent-card__main">
      <span class="subagent-card__title">{{ title }}</span>
      <span class="subagent-card__meta">
        <span>子 Agent</span>
        <span>·</span>
        <span>{{ statusLabel[status] || status }}</span>
        <span v-if="props.task?.progress_count">· {{ props.task.progress_count }} 步</span>
      </span>
    </span>
    <span v-if="duration" class="subagent-card__duration">{{ duration }}</span>
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
.subagent-card {
  display: flex;
  align-items: center;
  gap: 10px;
  width: 100%;
  min-height: 52px;
  padding: 8px 12px;
  border: 1px solid var(--noesis-block-light-border);
  border-radius: var(--noesis-radius-md);
  background: var(--noesis-block-light-bg);
  color: var(--noesis-color-text);
  text-align: left;
  cursor: pointer;
}
.subagent-card:hover {
  border-color: var(--noesis-color-primary-border-soft);
  background: var(--noesis-color-primary-bg-hover);
}
.subagent-card__icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex: 0 0 auto;
  width: 28px;
  height: 28px;
  border-radius: var(--noesis-radius-md);
  background: var(--noesis-color-primary-bg-icon);
  color: var(--noesis-color-primary);
}
.subagent-card__main {
  display: flex;
  flex: 1;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}
.subagent-card__title {
  overflow: hidden;
  font-size: 13px;
  font-weight: 600;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.subagent-card__meta,
.subagent-card__duration {
  color: var(--noesis-color-text-muted);
  font-size: 11px;
  line-height: 16px;
}
.subagent-card__duration {
  flex: 0 0 auto;
  font-variant-numeric: tabular-nums;
}
</style>
