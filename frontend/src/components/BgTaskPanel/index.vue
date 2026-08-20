<script setup lang="ts">
import type { BgTask, BgTaskMessage } from '@/api/chat'
import { GitNetworkOutline } from '@vicons/ionicons-v5'
import { NButton, NDrawer, NDrawerContent, NInput, NTag } from 'naive-ui'
import { computed, ref, watch } from 'vue'
import { getBgTaskMessages, sendBgTaskMessage } from '@/api/chat'
import ToolCallCollapse from '@/components/ToolCallCollapse/index.vue'

const props = defineProps<{
  tasks: BgTask[]
}>()

const emit = defineEmits<{
  (e: 'decide', payload: { task: BgTask, decisions: Array<{ type: 'approve' | 'reject' }> }): void
  (e: 'cancel', task: BgTask): void
  (e: 'changed'): void
}>()

const show = defineModel<boolean>('show', { default: false })

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
const ordered = computed(() => [...pending.value, ...running.value, ...finished.value])

const statusLabel: Record<string, { label: string, type: 'default' | 'info' | 'success' | 'warning' | 'error' }> = {
  running: { label: '进行中', type: 'warning' },
  awaiting_approval: { label: '待审批', type: 'warning' },
  completed: { label: '已完成', type: 'success' },
  failed: { label: '失败', type: 'error' },
  cancelled: { label: '已取消', type: 'default' },
  timed_out: { label: '超时', type: 'error' },
}

// ---- 展开（加载子会话详情） ----
const expandedId = ref<string | null>(null)
const detailMessages = ref<BgTaskMessage[]>([])
const detailLoading = ref(false)
const followupInput = ref('')
const followupSending = ref(false)

function canFollowup(task: BgTask): boolean {
  if (task.kind === 'one_shot') {
    return false
  }
  return ['running', 'awaiting_approval', 'completed'].includes(task.status)
}

async function toggleExpand(task: BgTask): Promise<void> {
  if (expandedId.value === task.task_id) {
    expandedId.value = null
    return
  }
  expandedId.value = task.task_id
  followupInput.value = ''
  await loadDetail(task.task_id)
}

async function loadDetail(taskId: string): Promise<void> {
  detailLoading.value = true
  try {
    detailMessages.value = await getBgTaskMessages(taskId)
  } catch (err) {
    console.warn('[bg-task] load messages failed', err)
    detailMessages.value = []
  } finally {
    detailLoading.value = false
  }
}

/** 子会话消息 → ToolCallCollapse 兼容的条目（assistant 工具调用与 tool 结果按名配对） */
interface DetailItem {
  kind: 'instruction' | 'text' | 'tool'
  text: string
  name?: string
  arguments?: Record<string, unknown>
  result?: string
  status?: string
}

const detailItems = computed<DetailItem[]>(() => {
  const items: DetailItem[] = []
  const pendingCalls: Array<DetailItem & { kind: 'tool' }> = []
  for (const message of detailMessages.value) {
    if (message.role === 'user') {
      items.push({ kind: 'instruction', text: message.text || '' })
    } else if (message.role === 'assistant') {
      for (const call of message.tool_calls ?? []) {
        pendingCalls.push({
          kind: 'tool',
          text: '',
          name: call.name,
          arguments: call.args,
          status: 'running',
        })
      }
      if (message.text) {
        items.push({ kind: 'text', text: message.text })
      }
    } else if (message.role === 'tool') {
      const idx = pendingCalls.findIndex((c) => c.name === message.name)
      const target = idx >= 0 ? pendingCalls.splice(idx, 1)[0] : { kind: 'tool' as const, text: '', name: message.name }
      target.result = message.text || ''
      target.status = message.status === 'error' ? 'error' : 'success'
      items.push(target)
    }
  }
  for (const leftover of pendingCalls) {
    leftover.status = 'running'
    items.push(leftover)
  }
  return items
})

async function sendFollowup(task: BgTask): Promise<void> {
  const message = followupInput.value.trim()
  if (!message || followupSending.value) {
    return
  }
  followupSending.value = true
  try {
    await sendBgTaskMessage(task.task_id, message)
    followupInput.value = ''
    window.$message?.success('已发送，子任务将作为新一轮执行')
    emit('changed')
    await loadDetail(task.task_id)
  } catch (err) {
    console.warn('[bg-task] send message failed', err)
    window.$message?.error('发送失败')
  } finally {
    followupSending.value = false
  }
}

function actionPreview(task: BgTask): string {
  const first = task.interrupt?.action_requests?.[0]
  if (!first) {
    return task.description
  }
  const args = first.args ? JSON.stringify(first.args) : ''
  return `${first.name ?? 'tool'} ${args}`
}

// 抽屉打开时刷新当前展开项
watch(show, (open) => {
  if (open && expandedId.value) {
    void loadDetail(expandedId.value)
  }
})
</script>

<template>
  <n-drawer v-model:show="show" placement="right" width="min(520px, 94vw)">
    <n-drawer-content title="后台子任务" closable>
      <div class="bg-task-list">
        <div v-if="!ordered.length" class="bg-task-empty">
          当前会话暂无后台子任务
        </div>

        <!-- 待审批（固定展开样式的卡片） -->
        <div
          v-for="task in pending"
          :key="task.task_id"
          class="bg-task-card bg-task-card--approval"
        >
          <div class="bg-task-card__head">
            <n-tag type="warning" size="small" round>待审批</n-tag>
            <span class="bg-task-card__title">{{ task.description }}</span>
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

        <!-- 任务卡片（SubagentCollapse 同形态：可折叠卡片） -->
        <div
          v-for="task in [...running, ...finished]"
          :key="task.task_id"
          class="bg-task-card"
          :class="{ 'bg-task-card--open': expandedId === task.task_id }"
        >
          <button type="button" class="bg-task-card__row" @click="toggleExpand(task)">
            <span class="bg-task-card__icon"><n-icon size="15"><GitNetworkOutline /></n-icon></span>
            <span class="bg-task-card__title">{{ task.description }}</span>
            <span v-if="task.kind === 'one_shot'" class="bg-task-card__kind">一次性</span>
            <n-tag :type="statusLabel[task.status]?.type ?? 'default'" size="small" round>
              {{ statusLabel[task.status]?.label ?? task.status }}
            </n-tag>
            <span
              v-if="task.status === 'running' && (task.progress_count ?? task.progress?.length)"
              class="bg-task-card__chevron"
            >{{ task.progress_count ?? task.progress?.length }} 步</span>
            <span v-if="task.status === 'running'" class="bg-task-card__cancel" @click.stop>
              <NButton size="tiny" quaternary type="error" @click="emit('cancel', task)">
                取消
              </NButton>
            </span>
          </button>

          <div v-if="expandedId === task.task_id" class="bg-task-card__body">
            <div v-if="detailLoading" class="bg-task-detail__empty">加载中…</div>
            <template v-else-if="detailItems.length">
              <div
                v-for="(item, idx) in detailItems"
                :key="idx"
                class="bg-task-detail__item"
              >
                <div v-if="item.kind === 'instruction'" class="bg-task-detail__instruction">
                  {{ item.text }}
                </div>
                <div v-else-if="item.kind === 'text'" class="bg-task-detail__text">
                  {{ item.text }}
                </div>
                <ToolCallCollapse
                  v-else
                  :name="item.name || 'tool'"
                  :arguments="item.arguments"
                  :result="item.result"
                  :status="item.status || 'success'"
                  :default-open="false"
                />
              </div>
            </template>
            <div v-else class="bg-task-detail__empty">暂无执行记录</div>

            <div v-if="canFollowup(task)" class="bg-task-detail__composer">
              <NInput
                v-model:value="followupInput"
                size="small"
                placeholder="向子任务追加指示（作为新一轮执行）"
                @keyup.enter="sendFollowup(task)"
              />
              <NButton size="small" type="primary" :loading="followupSending" @click="sendFollowup(task)">
                发送
              </NButton>
            </div>
            <div v-else-if="task.kind === 'one_shot'" class="bg-task-detail__hint">
              一次性任务不支持追加消息
            </div>
          </div>
        </div>
      </div>
    </n-drawer-content>
  </n-drawer>
</template>

<style scoped lang="scss">
.bg-task-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.bg-task-empty,
.bg-task-detail__empty,
.bg-task-detail__hint {
  color: var(--noesis-color-text-hint, #94a3b8);
  font-size: 13px;
}

/* 卡片形态对齐 SubagentCollapse（light 外观） */
.bg-task-card {
  background: var(--noesis-block-light-bg, var(--noesis-color-bg-muted));
  border: 1px solid var(--noesis-color-border-subtle);
  border-radius: var(--noesis-radius-md, 10px);
  overflow: hidden;
}

.bg-task-card--approval {
  border-left: 3px solid var(--noesis-color-warning, #f0a020);
}

.bg-task-card__row {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  padding: 8px 12px;
  border: 0;
  background: transparent;
  font: inherit;
  text-align: left;
  cursor: pointer;
}

.bg-task-card__row:hover {
  background: var(--noesis-color-primary-bg-subtle);
}

.bg-task-card__icon {
  display: inline-flex;
  flex-shrink: 0;
  color: var(--noesis-color-primary);
}

.bg-task-card__title {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  color: var(--noesis-color-text);
  font-size: 13px;
  font-weight: 500;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.bg-task-card__kind {
  flex-shrink: 0;
  color: var(--noesis-color-text-hint, #94a3b8);
  font-size: 11px;
}

.bg-task-card__chevron {
  flex-shrink: 0;
  color: var(--noesis-color-text-hint, #94a3b8);
  font-size: 11px;
}

.bg-task-card__cancel {
  flex-shrink: 0;
}

.bg-task-card__head {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 12px 0;
}

.bg-task-card__preview {
  margin: 8px 12px;
  padding: 6px 8px;
  max-height: 120px;
  overflow: auto;
  border-radius: var(--noesis-radius-sm);
  background: var(--noesis-color-bg-muted);
  color: var(--noesis-color-text);
  font-size: 12px;
  white-space: pre-wrap;
}

.bg-task-card__actions {
  display: flex;
  gap: 8px;
  padding: 0 12px 10px;
}

.bg-task-card__body {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 4px 12px 10px;
  border-top: 1px solid var(--noesis-color-border-subtle);
}

.bg-task-detail__instruction {
  padding: 6px 8px;
  border-radius: var(--noesis-radius-sm);
  background: var(--noesis-color-primary-bg-subtle);
  color: var(--noesis-color-text-secondary);
  font-size: 13px;
}

.bg-task-detail__text {
  color: var(--noesis-color-text);
  font-size: 13px;
  line-height: 1.6;
  white-space: pre-wrap;
  word-break: break-word;
}

.bg-task-detail__composer {
  display: flex;
  gap: 8px;
  padding-top: 6px;
}
</style>
