<script setup lang="ts">
import type { BgTask } from '@/api/chat'
import { NButton, NTag } from 'naive-ui'
import { computed } from 'vue'

const props = defineProps<{
  tasks: BgTask[]
}>()

const emit = defineEmits<{
  (e: 'decide', payload: { task: BgTask, decisions: Array<{ type: 'approve' | 'reject' }> }): void
  (e: 'cancel', task: BgTask): void
}>()

const pending = computed(() => props.tasks.filter((t) => {
  return t.status === 'awaiting_approval'
}))
const running = computed(() => props.tasks.filter((t) => {
  return t.status === 'running'
}))
const recent = computed(() =>
  props.tasks
    .filter((t) => {
      return t.status === 'completed' || t.status === 'failed' || t.status === 'timed_out'
    })
    .slice(-5)
    .reverse(),
)

const statusLabel: Record<string, { label: string, type: 'default' | 'info' | 'success' | 'warning' | 'error' }> = {
  running: { label: '运行中', type: 'info' },
  awaiting_approval: { label: '待审批', type: 'warning' },
  completed: { label: '已完成', type: 'success' },
  failed: { label: '失败', type: 'error' },
  cancelled: { label: '已取消', type: 'default' },
  timed_out: { label: '超时', type: 'error' },
}

function actionPreview(task: BgTask): string {
  const first = task.interrupt?.action_requests?.[0]
  if (!first) {
    return task.description
  }
  const args = first.args ? JSON.stringify(first.args) : ''
  return `${first.name ?? 'tool'} ${args}`
}
</script>

<template>
  <section v-if="tasks.length" class="bg-task-panel" aria-label="后台任务">
    <div
      v-for="task in pending"
      :key="task.task_id"
      class="bg-task-panel__card bg-task-panel__card--approval"
    >
      <div class="bg-task-panel__head">
        <NTag size="small" type="warning">待审批</NTag>
        <span class="bg-task-panel__desc">{{ task.description }}</span>
      </div>
      <pre class="bg-task-panel__preview">{{ actionPreview(task) }}</pre>
      <div class="bg-task-panel__actions">
        <NButton size="small" type="primary" @click="emit('decide', { task, decisions: [{ type: 'approve' }] })">
          批准
        </NButton>
        <NButton
          size="small"
          type="error"
          quaternary
          @click="emit('decide', { task, decisions: [{ type: 'reject', message: '用户拒绝了该操作' }] })"
        >
          拒绝
        </NButton>
      </div>
    </div>

    <div v-if="running.length || recent.length" class="bg-task-panel__list">
      <div v-for="task in [...running, ...recent]" :key="task.task_id" class="bg-task-panel__row">
        <NTag size="small" :type="statusLabel[task.status]?.type ?? 'default'">
          {{ statusLabel[task.status]?.label ?? task.status }}
        </NTag>
        <span class="bg-task-panel__desc">{{ task.description }}</span>
        <span v-if="task.status === 'running'" class="bg-task-panel__cancel">
          <NButton size="tiny" quaternary type="error" @click="emit('cancel', task)">
            取消
          </NButton>
        </span>
      </div>
    </div>
  </section>
</template>

<style scoped lang="scss">
.bg-task-panel {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 10px 16px;
}

.bg-task-panel__card--approval {
  padding: 10px 12px;
  border: 1px solid var(--noesis-color-border);
  border-left: 3px solid var(--noesis-color-warning, #f0a020);
  border-radius: var(--noesis-radius-md);
  background: var(--noesis-color-bg-elevated);
}

.bg-task-panel__head {
  display: flex;
  align-items: center;
  gap: 8px;
}

.bg-task-panel__desc {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  color: var(--noesis-color-text-secondary);
  font-size: 13px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.bg-task-panel__preview {
  margin: 8px 0;
  padding: 6px 8px;
  max-height: 120px;
  overflow: auto;
  border-radius: var(--noesis-radius-sm);
  background: var(--noesis-color-bg-muted);
  color: var(--noesis-color-text);
  font-size: 12px;
  white-space: pre-wrap;
}

.bg-task-panel__actions {
  display: flex;
  gap: 8px;
}

.bg-task-panel__list {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.bg-task-panel__row {
  display: flex;
  align-items: center;
  gap: 8px;
  min-height: 24px;
}
</style>
