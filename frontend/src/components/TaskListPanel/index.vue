<script setup lang="ts">
import type { SessionTaskEntry } from '@/api/chat'
import { ChevronDownOutline, GitNetworkOutline } from '@vicons/ionicons-v5'
import { NButton } from 'naive-ui'
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import SubagentConversationView from '@/components/SubagentConversationView/index.vue'
import SubagentSessionDrawer from '@/components/SubagentSessionDrawer/index.vue'
import { wireTimestampMs } from '@/utils/formatTime'
import { taskStatusLabel } from '@/utils/taskStatusLabels'
import { formatDurationMs } from '@/views/chat/messageParts'

const props = defineProps<{
  tasks: SessionTaskEntry[]
  focusTaskId?: string | null
}>()

const emit = defineEmits<{
  (e: 'decide', payload: { task: SessionTaskEntry, decisions: Array<{ type: 'approve' | 'reject' }> }): void
  (e: 'cancel', task: SessionTaskEntry): void
  (e: 'changed'): void
}>()

const show = defineModel<boolean>('show', { default: false })
const selectedTask = ref<SessionTaskEntry | null>(null)
const showDetail = ref(false)
const selectedTaskResolved = computed(() => {
  if (!selectedTask.value) {
    return null
  }
  return props.tasks.find((task) => task.task_id === selectedTask.value?.task_id) || selectedTask.value
})

const pending = computed(() => props.tasks.filter((t) => {
  return t.status === 'awaiting_approval'
}))
const running = computed(() => props.tasks.filter((t) => {
  return t.status === 'queued' || t.status === 'running'
}))
const finished = computed(() =>
  props.tasks
    .filter((t) => {
      return (
        t.status !== 'queued'
        && t.status !== 'running'
        && t.status !== 'awaiting_approval'
      )
    })
    .reverse(),
)
const ordered = computed(() => [...pending.value, ...running.value, ...finished.value])
// 已结束任务默认折叠：面板只保运行中/待审批的即时可见性，历史按需展开
const finishedExpanded = ref(false)
const taskSummary = computed(() => {
  if (!ordered.value.length) {
    return '任务进度会在这里实时更新'
  }
  const segments = []
  if (running.value.length) {
    segments.push(`${running.value.length} 个进行中`)
  }
  if (pending.value.length) {
    segments.push(`${pending.value.length} 个待处理`)
  }
  if (finished.value.length) {
    segments.push(`${finished.value.length} 个已结束`)
  }
  return segments.join(' · ')
})

// 运行中任务卡耗时：抽屉打开且有 running 任务时本地时钟跳动；
// 终态用落库起止值；等待审批不计耗时（等人时间不算执行时长）
const { now: clockNow, start: startClock, stop: stopClock } = useTicker()

function refreshClockTimer(): void {
  // 启动即刷新：抽屉关闭期间时钟冻结，重开第一帧不能用旧值
  if (show.value && running.value.length > 0) {
    startClock()
  } else {
    stopClock()
  }
}

watch([show, running], refreshClockTimer, { immediate: true })
onBeforeUnmount(() => {
  // 卸载无条件清理：refreshClockTimer 在抽屉开着且有活跃任务时
  // 会判定 need=true 而不停表
  stopClock()
})

function taskElapsed(task: SessionTaskEntry): string {
  const started = wireTimestampMs(task.started_at)
  if (!started || task.status === 'awaiting_approval') {
    return ''
  }
  if (task.status === 'running') {
    return formatDurationMs(Math.max(0, clockNow.value - started))
  }
  const finished = wireTimestampMs(task.completed_at)
  if (!finished) {
    return ''
  }
  return formatDurationMs(Math.max(0, finished - started))
}

function statusClass(status: SessionTaskEntry['status']): string {
  return status.replaceAll('_', '-')
}

function toggleExpand(task: SessionTaskEntry): void {
  selectedTask.value = task
  showDetail.value = true
}

function closeDetail(): void {
  showDetail.value = false
  selectedTask.value = null
}

function actionPreview(task: SessionTaskEntry): string {
  const first = task.interrupt?.action_requests?.[0]
  if (!first) {
    return task.description
  }
  const args = first.args ? JSON.stringify(first.args) : ''
  return `${first.name ?? 'tool'} ${args}`
}

watch(show, (open) => {
  if (!open) {
    showDetail.value = false
  }
})
watch([() => props.focusTaskId, () => props.tasks], ([taskId]) => {
  if (!taskId) {
    return
  }
  const task = props.tasks.find((item) => item.child_session_id === taskId || item.task_id === taskId)
  if (task && task.kind !== 'shell') {
    selectedTask.value = task
    showDetail.value = true
  }
})
</script>

<template>
  <!-- 监控面板常开数分钟，默认黑遮罩会把主界面压暗成"换主题"的观感；透明遮罩保持主界面视觉不变 -->
  <SubagentSessionDrawer
    v-model:show="show"
    :title="showDetail && selectedTaskResolved ? selectedTaskResolved.description : '子 Agent 与后台命令'"
    transparent-mask
    flush-body
  >
    <!-- 详情态：单抽屉 master-detail，点开任务在原抽屉内切换，返回回列表 -->
    <template v-if="showDetail && selectedTaskResolved">
      <div class="bg-task-detail">
        <button type="button" class="bg-task-detail__back" @click="closeDetail">
          <span class="i-hugeicons:arrow-left-01" aria-hidden="true"></span>
          返回任务列表
        </button>
        <div v-if="selectedTaskResolved.kind === 'shell'" class="shell-task-detail">
          <div class="shell-task-detail__header">
            <strong>后台命令输出</strong>
            <NButton
              v-if="selectedTaskResolved.status === 'queued' || selectedTaskResolved.status === 'running'"
              size="small"
              type="error"
              quaternary
              @click="emit('cancel', selectedTaskResolved)"
            >
              停止命令
            </NButton>
          </div>
          <code class="shell-task-detail__command">{{ selectedTaskResolved.command || selectedTaskResolved.description }}</code>
          <pre v-if="selectedTaskResolved.result || selectedTaskResolved.error" class="shell-task-detail__output">{{ selectedTaskResolved.result || selectedTaskResolved.error }}</pre>
          <template v-else-if="selectedTaskResolved.output_tail">
            <span class="shell-task-detail__empty">运行中——最新输出尾部（持续更新）：</span>
            <pre class="shell-task-detail__output">{{ selectedTaskResolved.output_tail }}</pre>
          </template>
          <span v-else class="shell-task-detail__empty">命令仍在运行，输出完成后会显示在这里。</span>
        </div>
        <SubagentConversationView
          v-else
          :session-id="selectedTaskResolved.child_session_id || selectedTaskResolved.task_id"
          :run-id="selectedTaskResolved.run_id"
          @changed="emit('changed')"
        />
      </div>
    </template>

    <!-- 列表态 -->
    <template v-else>
      <div class="bg-task-overview">
        <span>{{ taskSummary || '运行状态会在这里实时更新' }}</span>
        <span v-if="ordered.length" class="bg-task-overview__count">共 {{ ordered.length }} 个</span>
      </div>
      <div class="bg-task-list">
        <div v-if="!ordered.length" class="bg-task-empty">
          <span class="bg-task-empty__icon"><n-icon size="20"><GitNetworkOutline /></n-icon></span>
          <strong>暂无后台子任务</strong>
          <span>Agent 创建后台任务后，进度会实时显示在这里</span>
        </div>

        <!-- 待审批固定展开，避免高风险操作藏在折叠层级里。 -->
        <div
          v-for="task in pending"
          :key="task.task_id"
          class="bg-task-card bg-task-card--approval"
        >
          <div class="bg-task-card__head">
            <span class="bg-task-status-dot bg-task-status-dot--awaiting-approval"></span>
            <div class="bg-task-card__content">
              <span class="bg-task-card__title">{{ task.description }}</span>
              <span class="bg-task-card__meta">等待确认后继续</span>
            </div>
          </div>
          <pre class="bg-task-card__preview">{{ actionPreview(task) }}</pre>
          <div class="bg-task-card__actions">
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

        <!-- 两行任务行：主信息、状态与指标分层，展开后再显示执行内容。
             已结束任务折叠为一行分组头（点按展开/收起），避免历史条目
             淹没运行中的即时状态。 -->
        <template v-for="task in running" :key="task.task_id">
          <div
            class="bg-task-card"
            :class="{ 'bg-task-card--open': showDetail && selectedTaskResolved?.task_id === task.task_id }"
          >
            <button type="button" class="bg-task-card__row" @click="toggleExpand(task)">
              <span class="bg-task-card__disclosure" :class="{ 'bg-task-card__disclosure--open': showDetail && selectedTaskResolved?.task_id === task.task_id }">
                <n-icon size="14"><ChevronDownOutline /></n-icon>
              </span>
              <span class="bg-task-status-dot" :class="`bg-task-status-dot--${statusClass(task.status)}`"></span>
              <span class="bg-task-card__content">
                <span class="bg-task-card__title">{{ task.description }}</span>
                <span class="bg-task-card__meta">
                  <span v-if="task.kind === 'shell'">后台命令</span>
                  <span v-else>子 Agent</span>
                  <span>·</span>
                  <span>{{ taskStatusLabel(task.status) }}</span>
                  <template v-if="taskElapsed(task)">
                    <span>·</span>
                    <span class="bg-task-card__elapsed">{{ taskElapsed(task) }}</span>
                  </template>
                </span>
              </span>
              <span v-if="task.progress_count ?? task.progress?.length" class="bg-task-card__metric">
                {{ task.progress_count ?? task.progress?.length }} 步
              </span>
            </button>
          </div>
        </template>

        <div v-if="finished.length" class="bg-task-finished">
          <button
            type="button"
            class="bg-task-finished__header"
            @click="finishedExpanded = !finishedExpanded"
          >
            <n-icon size="14" class="bg-task-finished__chevron" :class="{ 'bg-task-finished__chevron--open': finishedExpanded }">
              <ChevronDownOutline />
            </n-icon>
            <span>已结束</span>
            <span class="bg-task-finished__count">{{ finished.length }}</span>
          </button>
          <template v-if="finishedExpanded">
            <div
              v-for="task in finished"
              :key="task.task_id"
              class="bg-task-card"
              :class="{ 'bg-task-card--open': showDetail && selectedTaskResolved?.task_id === task.task_id }"
            >
              <button type="button" class="bg-task-card__row" @click="toggleExpand(task)">
                <span class="bg-task-card__disclosure" :class="{ 'bg-task-card__disclosure--open': showDetail && selectedTaskResolved?.task_id === task.task_id }">
                  <n-icon size="14"><ChevronDownOutline /></n-icon>
                </span>
                <span class="bg-task-status-dot" :class="`bg-task-status-dot--${statusClass(task.status)}`"></span>
                <span class="bg-task-card__content">
                  <span class="bg-task-card__title">{{ task.description }}</span>
                  <span class="bg-task-card__meta">
                    <span v-if="task.kind === 'shell'">后台命令</span>
                    <span v-else>子 Agent</span>
                    <span>·</span>
                    <span>{{ taskStatusLabel(task.status) }}</span>
                    <template v-if="taskElapsed(task)">
                      <span>·</span>
                      <span class="bg-task-card__elapsed">{{ taskElapsed(task) }}</span>
                    </template>
                  </span>
                </span>
                <span v-if="task.progress_count ?? task.progress?.length" class="bg-task-card__metric">
                  {{ task.progress_count ?? task.progress?.length }} 步
                </span>
              </button>
            </div>
          </template>
        </div>
      </div>
    </template>
  </SubagentSessionDrawer>
</template>

<style scoped lang="scss">
.bg-task-detail {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
  padding: 0 16px 16px;
}

.bg-task-detail__back {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  align-self: flex-start;
  margin: 8px 0 4px;
  padding: 4px 8px;
  border: 0;
  border-radius: var(--noesis-radius-md);
  background: transparent;
  color: var(--noesis-color-text-secondary);
  font-size: 13px;
  cursor: pointer;
}

.bg-task-detail__back:hover {
  background: var(--noesis-color-bg-muted);
  color: var(--noesis-color-text);
}

:deep(.n-drawer-header__main) {
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
}

.bg-task-overview {
  display: flex;
  align-items: center;
  justify-content: space-between;
  min-height: 44px;
  padding: 0 20px;
  border-bottom: 1px solid var(--noesis-color-border-subtle);
  color: var(--noesis-color-text-secondary);
  font-size: 12px;
}

.bg-task-overview__count {
  color: var(--noesis-color-text-hint);
  font-variant-numeric: tabular-nums;
}

.bg-task-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 14px;
}

.bg-task-empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 6px;
  padding: 56px 16px;
  color: var(--noesis-color-text-hint);
  text-align: center;
}

.bg-task-empty strong {
  color: var(--noesis-color-text-secondary);
  font-size: 14px;
  font-weight: 500;
}

.bg-task-empty__icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 38px;
  height: 38px;
  margin-bottom: 4px;
  border-radius: 50%;
  background: var(--noesis-color-bg-muted);
  color: var(--noesis-color-text-hint);
}

.bg-task-card {
  border: 1px solid var(--noesis-color-border-subtle);
  border-radius: var(--noesis-radius-lg);
  background: var(--noesis-color-bg-elevated);
  overflow: hidden;
  transition: border-color 0.15s ease, box-shadow 0.15s ease;
}

.bg-task-card:hover,
.bg-task-card--open {
  border-color: var(--noesis-color-border);
  box-shadow: var(--noesis-shadow-sm);
}

.bg-task-card--approval {
  border-color: var(--noesis-color-warning);
  background: var(--noesis-color-primary-bg-subtle);
}

.bg-task-card__row {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  width: 100%;
  min-height: 58px;
  padding: 10px 12px;
  border: 0;
  background: transparent;
  font: inherit;
  text-align: left;
  cursor: pointer;
}

.bg-task-card__row:hover {
  background: var(--noesis-color-bg-muted);
}

.bg-task-card__disclosure {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  width: 16px;
  height: 18px;
  color: var(--noesis-color-text-hint);
  transform: rotate(-90deg);
  transition: transform 0.15s ease;
}

.bg-task-card__disclosure--open {
  transform: rotate(0);
}

.bg-task-status-dot {
  flex: 0 0 auto;
  width: 8px;
  height: 8px;
  margin-top: 5px;
  border: 2px solid var(--noesis-color-text-hint);
  border-radius: 50%;
}

.bg-task-status-dot--queued {
  border-color: var(--noesis-color-text-hint);
}

.bg-task-status-dot--running {
  border-color: var(--noesis-color-primary);
  box-shadow: 0 0 0 3px var(--noesis-color-primary-bg-subtle);
}

.bg-task-status-dot--awaiting-approval {
  border-color: var(--noesis-color-warning);
  box-shadow: 0 0 0 3px var(--noesis-color-primary-bg-subtle);
}

.bg-task-status-dot--completed {
  border-color: var(--noesis-color-success);
  background: var(--noesis-color-success);
}

.bg-task-status-dot--failed,
.bg-task-status-dot--timed-out {
  border-color: var(--noesis-color-danger);
  background: var(--noesis-color-danger);
}

.bg-task-card__content {
  display: flex;
  flex: 1;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}

.bg-task-card__title {
  overflow: hidden;
  color: var(--noesis-color-text);
  font-size: 13px;
  font-weight: 400;
  line-height: 18px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.bg-task-card__meta {
  display: flex;
  gap: 4px;
  color: var(--noesis-color-text-hint);
  font-size: 11px;
  line-height: 16px;
}

.bg-task-card__elapsed {
  font-variant-numeric: tabular-nums;
}

.bg-task-card__metric {
  flex-shrink: 0;
  margin-top: 18px;
  color: var(--noesis-color-text-hint);
  font-size: 11px;
  font-variant-numeric: tabular-nums;
}

.bg-task-card__head {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  padding: 12px 12px 0;
}

.bg-task-card__preview {
  margin: 10px 12px;
  padding: 8px 10px;
  max-height: 120px;
  overflow: auto;
  border-radius: var(--noesis-radius-sm);
  background: var(--noesis-color-bg-elevated);
  color: var(--noesis-color-text-secondary);
  font-family: inherit;
  font-size: 12px;
  line-height: 1.5;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}

.bg-task-card__actions {
  display: flex;
  gap: 8px;
  padding: 0 12px 12px 28px;
}

.shell-task-detail {
  display: flex;
  flex-direction: column;
  gap: 10px;
  margin: 0 14px 14px;
  padding: 12px;
  border: 1px solid var(--noesis-color-border-subtle);
  border-radius: var(--noesis-radius-md);
  background: var(--noesis-color-bg-muted);
}

.shell-task-detail__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  color: var(--noesis-color-text);
  font-size: 13px;
}

.shell-task-detail__command,
.shell-task-detail__output {
  margin: 0;
  padding: 8px;
  border-radius: var(--noesis-radius-sm);
  background: var(--noesis-color-bg-elevated);
  color: var(--noesis-color-text-secondary);
  font-size: 12px;
  line-height: 1.5;
  overflow-wrap: anywhere;
  white-space: pre-wrap;
}

.shell-task-detail__empty {
  color: var(--noesis-color-text-hint);
  font-size: 12px;
}
.bg-task-finished {
  display: flex;
  flex-direction: column;
}

.bg-task-finished__header {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 6px 8px;
  border: 0;
  border-radius: var(--noesis-radius-md);
  background: transparent;
  color: var(--noesis-color-text-secondary);
  font-size: 12px;
  cursor: pointer;
  text-align: left;

  &:hover {
    background: var(--noesis-color-fill-soft, rgba(128, 128, 128, 0.08));
  }
}

.bg-task-finished__chevron {
  transition: transform 0.15s ease;

  &--open {
    transform: rotate(180deg);
  }
}

.bg-task-finished__count {
  padding: 0 6px;
  border-radius: 999px;
  background: var(--noesis-color-fill-soft, rgba(128, 128, 128, 0.12));
  font-size: 11px;
  line-height: 18px;
}
</style>
