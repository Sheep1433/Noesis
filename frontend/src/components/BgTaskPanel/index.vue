<script setup lang="ts">
import type { BgTask, BgTaskMessage } from '@/api/chat'
import { NButton, NDrawer, NDrawerContent, NInput, NTag } from 'naive-ui'
import { computed, ref } from 'vue'
import { getBgTaskMessages, sendBgTaskMessage } from '@/api/chat'

const props = defineProps<{
  tasks: BgTask[]
}>()

const emit = defineEmits<{
  (e: 'decide', payload: { task: BgTask, decisions: Array<{ type: 'approve' | 'reject' }> }): void
  (e: 'cancel', task: BgTask): void
  (e: 'changed'): void
}>()

const pending = computed(() => props.tasks.filter((t) => {
  return t.status === 'awaiting_approval'
}))
const running = computed(() => props.tasks.filter((t) => {
  return t.status === 'running'
}))
const finished = computed(() =>
  props.tasks
    .filter((t) => {
      return t.status !== 'running' && t.status !== 'awaiting_approval'
    })
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

// ---- 面板可见性：有活跃任务自动展示；否则单行入口可展开 ----
const panelOpen = ref(false)
const hasActive = computed(() =>
  pending.value.length > 0 || running.value.length > 0,
)
const showEntryOnly = computed(() => !hasActive.value && !panelOpen.value && finished.value.length > 0)

function togglePanel(): void {
  panelOpen.value = !panelOpen.value
}

// ---- 展开的步骤概览 ----
const expanded = ref(new Set<string>())

function toggleExpand(task: BgTask): void {
  if (detailTaskId.value === task.task_id) {
    return
  }
  const next = new Set(expanded.value)
  if (next.has(task.task_id)) {
    next.delete(task.task_id)
  } else {
    next.add(task.task_id)
  }
  expanded.value = next
}

function stepIcon(kind: string): string {
  if (kind === 'tool_call') {
    return '🔧'
  }
  if (kind === 'tool_result') {
    return '↩'
  }
  return '💬'
}

function stepText(step: NonNullable<BgTask['progress']>[number]): string {
  if (step.kind === 'text') {
    return step.preview || ''
  }
  if (step.kind === 'tool_result') {
    const status = step.status === 'error' ? ' ✗' : ''
    return `${step.name || 'tool'}${status} ${step.preview || ''}`.trim()
  }
  return step.name || 'tool'
}

// ---- 子会话详情抽屉 ----
const detailOpen = ref(false)
const detailTask = ref<BgTask | null>(null)
const detailMessages = ref<BgTaskMessage[]>([])
const detailLoading = ref(false)
const followupInput = ref('')
const followupSending = ref(false)

const canFollowup = computed(() => {
  const t = detailTask.value
  if (!t || t.kind === 'one_shot') {
    return false
  }
  return ['running', 'awaiting_approval', 'completed'].includes(t.status)
})

async function openDetail(task: BgTask): Promise<void> {
  detailOpen.value = true
  detailTask.value = task
  detailMessages.value = []
  followupInput.value = ''
  detailLoading.value = true
  try {
    detailMessages.value = await getBgTaskMessages(task.task_id)
  } catch (err) {
    console.warn('[bg-task] load messages failed', err)
  } finally {
    detailLoading.value = false
  }
}

async function refreshDetail(): Promise<void> {
  if (!detailTask.value) {
    return
  }
  try {
    detailMessages.value = await getBgTaskMessages(detailTask.value.task_id)
  } catch {
    // 忽略刷新失败
  }
}

async function sendFollowup(): Promise<void> {
  const task = detailTask.value
  const message = followupInput.value.trim()
  if (!task || !message || followupSending.value) {
    return
  }
  followupSending.value = true
  try {
    await sendBgTaskMessage(task.task_id, message)
    followupInput.value = ''
    window.$message?.success('已发送，子任务将作为新一轮执行')
    emit('changed')
    await refreshDetail()
  } catch (err) {
    console.warn('[bg-task] send message failed', err)
    window.$message?.error('发送失败')
  } finally {
    followupSending.value = false
  }
}

function messageLabel(message: BgTaskMessage): string {
  if (message.role === 'user') {
    return '任务指令'
  }
  if (message.role === 'tool') {
    return `${message.name || '工具'} 结果`
  }
  return '子 Agent'
}
</script>

<template>
  <section v-if="tasks.length" class="bg-task-panel" aria-label="后台任务">
    <button
      v-if="showEntryOnly"
      type="button"
      class="bg-task-panel__entry"
      @click="togglePanel"
    >
      <span class="i-hugeicons:ai-chat-01" aria-hidden="true"></span>
      子任务 {{ finished.length }}
      <span class="bg-task-panel__entry-chevron">›</span>
    </button>

    <template v-if="!showEntryOnly">
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

      <div v-if="running.length || finished.length" class="bg-task-panel__list">
        <div
          v-for="task in [...running, ...finished]"
          :key="task.task_id"
          class="bg-task-panel__item"
        >
          <div class="bg-task-panel__row" @click="toggleExpand(task)">
            <NTag size="small" :type="statusLabel[task.status]?.type ?? 'default'">
              {{ statusLabel[task.status]?.label ?? task.status }}
            </NTag>
            <span v-if="task.kind === 'one_shot'" class="bg-task-panel__kind">一次性</span>
            <span class="bg-task-panel__desc">{{ task.description }}</span>
            <span
              v-if="task.progress?.length"
              class="bg-task-panel__steps-hint"
            >
              {{ expanded.has(task.task_id) ? '收起' : `${task.progress.length} 步` }}
            </span>
            <span class="bg-task-panel__detail-btn" @click.stop>
              <NButton size="tiny" quaternary type="primary" @click="openDetail(task)">
                详情
              </NButton>
            </span>
            <span v-if="task.status === 'running'" class="bg-task-panel__cancel" @click.stop>
              <NButton size="tiny" quaternary type="error" @click="emit('cancel', task)">
                取消
              </NButton>
            </span>
          </div>
          <div v-if="expanded.has(task.task_id) && task.progress?.length" class="bg-task-panel__steps">
            <div
              v-for="(step, idx) in task.progress"
              :key="idx"
              class="bg-task-panel__step"
              :class="{ 'bg-task-panel__step--error': step.kind === 'tool_result' && step.status === 'error' }"
            >
              <span class="bg-task-panel__step-icon">{{ stepIcon(step.kind) }}</span>
              <span class="bg-task-panel__step-text">{{ stepText(step) }}</span>
            </div>
          </div>
          <div
            v-else-if="expanded.has(task.task_id)"
            class="bg-task-panel__steps bg-task-panel__steps--empty"
          >
            暂无执行过程
          </div>
        </div>
      </div>

      <n-drawer
        v-model:show="detailOpen"
        placement="right"
        width="min(520px, 94vw)"
      >
        <n-drawer-content :title="`子任务 · ${detailTask?.description ?? ''}`" closable>
          <div class="bg-task-detail">
            <div class="bg-task-detail__toolbar">
              <NTag size="small" :type="statusLabel[detailTask?.status ?? '']?.type ?? 'default'">
                {{ statusLabel[detailTask?.status ?? '']?.label ?? detailTask?.status }}
              </NTag>
              <NTag v-if="detailTask?.kind === 'one_shot'" size="small" :bordered="false">
                一次性
              </NTag>
              <span class="bg-task-detail__spacer"></span>
              <NButton size="tiny" quaternary :loading="detailLoading" @click="refreshDetail">
                刷新
              </NButton>
            </div>

            <div class="bg-task-detail__messages">
              <div
                v-for="(message, idx) in detailMessages"
                :key="idx"
                class="bg-task-detail__message"
                :class="`bg-task-detail__message--${message.role}`"
              >
                <div class="bg-task-detail__message-label">{{ messageLabel(message) }}</div>
                <div v-if="message.tool_calls?.length" class="bg-task-detail__calls">
                  <div v-for="(call, ci) in message.tool_calls" :key="ci" class="bg-task-detail__call">
                    🔧 {{ call.name }}
                  </div>
                </div>
                <div v-if="message.text" class="bg-task-detail__text">{{ message.text }}</div>
                <div v-if="message.role === 'tool' && message.status === 'error'" class="bg-task-detail__error">
                  ✗ 执行失败
                </div>
              </div>
              <div v-if="!detailMessages.length && !detailLoading" class="bg-task-detail__empty">
                暂无执行记录
              </div>
            </div>

            <div v-if="canFollowup" class="bg-task-detail__composer">
              <NInput
                v-model:value="followupInput"
                size="small"
                placeholder="向子任务追加指示（作为新一轮执行）"
                @keyup.enter="sendFollowup"
              />
              <NButton size="small" type="primary" :loading="followupSending" @click="sendFollowup">
                发送
              </NButton>
            </div>
            <div v-else-if="detailTask?.kind === 'one_shot'" class="bg-task-detail__hint">
              一次性任务不支持追加消息
            </div>
          </div>
        </n-drawer-content>
      </n-drawer>
    </template>

    <button
      v-if="panelOpen && !hasActive"
      type="button"
      class="bg-task-panel__entry"
      @click="togglePanel"
    >
      收起子任务
    </button>
  </section>
</template>

<style scoped lang="scss">
.bg-task-panel {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 10px 16px;
}

.bg-task-panel__entry {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  align-self: flex-start;
  margin: 0;
  padding: 3px 10px;
  border: 0;
  border-radius: var(--noesis-radius-pill);
  background: transparent;
  color: var(--noesis-color-text-secondary);
  font-size: 12px;
  cursor: pointer;
  transition: color 0.15s ease, background-color 0.15s ease;
}

.bg-task-panel__entry:hover {
  color: var(--noesis-color-primary);
  background: var(--noesis-color-primary-bg-subtle);
}

.bg-task-panel__entry span {
  font-size: 14px;
}

.bg-task-panel__entry-chevron {
  font-size: 12px;
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

.bg-task-panel__kind {
  flex-shrink: 0;
  color: var(--noesis-color-text-hint, #94a3b8);
  font-size: 11px;
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

.bg-task-panel__item {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.bg-task-panel__row {
  display: flex;
  align-items: center;
  gap: 8px;
  min-height: 24px;
  cursor: pointer;
  border-radius: var(--noesis-radius-sm);
}

.bg-task-panel__row:hover {
  background: var(--noesis-color-primary-bg-subtle);
}

.bg-task-panel__steps-hint,
.bg-task-panel__detail-btn,
.bg-task-panel__cancel {
  flex-shrink: 0;
}

.bg-task-panel__steps-hint {
  color: var(--noesis-color-text-hint, #94a3b8);
  font-size: 11px;
}

.bg-task-panel__steps {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 6px 8px 6px 12px;
  border-left: 2px solid var(--noesis-color-border-subtle);
  margin-left: 14px;
}

.bg-task-panel__steps--empty {
  color: var(--noesis-color-text-hint, #94a3b8);
  font-size: 12px;
}

.bg-task-panel__step {
  display: flex;
  align-items: baseline;
  gap: 6px;
  font-size: 12px;
  line-height: 1.5;
}

.bg-task-panel__step--error .bg-task-panel__step-text {
  color: var(--noesis-color-error, #e5484d);
}

.bg-task-panel__step-icon {
  flex-shrink: 0;
}

.bg-task-panel__step-text {
  min-width: 0;
  overflow: hidden;
  color: var(--noesis-color-text-secondary);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.bg-task-detail {
  display: flex;
  flex-direction: column;
  gap: 10px;
  height: 100%;
}

.bg-task-detail__toolbar {
  display: flex;
  align-items: center;
  gap: 8px;
}

.bg-task-detail__spacer {
  flex: 1;
}

.bg-task-detail__messages {
  display: flex;
  flex: 1;
  flex-direction: column;
  gap: 8px;
  min-height: 0;
  padding: 8px 0;
  overflow-y: auto;
}

.bg-task-detail__message {
  padding: 8px 10px;
  border-radius: var(--noesis-radius-sm);
  background: var(--noesis-color-bg-muted);
}

.bg-task-detail__message--user {
  background: var(--noesis-color-primary-bg-subtle);
}

.bg-task-detail__message-label {
  margin-bottom: 4px;
  color: var(--noesis-color-text-hint, #94a3b8);
  font-size: 11px;
}

.bg-task-detail__calls {
  display: flex;
  flex-direction: column;
  gap: 2px;
  margin-bottom: 4px;
  color: var(--noesis-color-text-secondary);
  font-size: 12px;
}

.bg-task-detail__text {
  color: var(--noesis-color-text);
  font-size: 13px;
  line-height: 1.6;
  word-break: break-word;
  white-space: pre-wrap;
}

.bg-task-detail__error {
  margin-top: 4px;
  color: var(--noesis-color-error, #e5484d);
  font-size: 12px;
}

.bg-task-detail__empty,
.bg-task-detail__hint {
  color: var(--noesis-color-text-hint, #94a3b8);
  font-size: 12px;
}

.bg-task-detail__composer {
  display: flex;
  gap: 8px;
  padding-top: 8px;
  border-top: 1px solid var(--noesis-color-border-subtle);
}
</style>
