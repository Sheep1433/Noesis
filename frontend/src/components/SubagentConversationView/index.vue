<script setup lang="ts">
import type { AgentRunSnapshot, ChatMessageResponse } from '@/api/chat'
import type { RunEventState } from '@/views/chat/runEventReducer'
import { useLocalStorage } from '@vueuse/core'
import { NFloatButton, NInput } from 'naive-ui'
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import {
  getSession,
  getSessionMessages,
  resumeAgentRunHitl,
  sendSubagentFollowup,
  stopAgentRun,
  subscribeAgentRun,
} from '@/api/chat'
import ModelSelector from '@/components/Chat/ModelSelector.vue'
import ReasoningEffortSelector from '@/components/Chat/ReasoningEffortSelector.vue'
import ContextWindowIndicator from '@/components/ContextWindowIndicator/index.vue'
import ConversationPartsRenderer from '@/components/ConversationPartsRenderer/index.vue'
import FollowupQueue from '@/components/FollowupQueue/index.vue'
import HitlApprovalCard from '@/components/HitlApprovalCard/index.vue'
import { getQueuedFollowups, setQueuedFollowups } from '@/components/SubagentConversationView/queuedFollowups'
import { useFollowupQueue } from '@/hooks/useFollowupQueue'
import { wireTimestampMs } from '@/utils/formatTime'
import { rebuildSessionStats } from '@/utils/sessionStats'
import { formatStatsLine } from '@/utils/statsFormat'
import { taskStatusLabel } from '@/utils/taskStatusLabels'
import {
  formatDurationMs,
  hasValidContextWindow,
  normalizeApiContent,
} from '@/views/chat/messageParts'
import {
  initialRunEventState,
  parseRunEvent,
  runEventReducer,

} from '@/views/chat/runEventReducer'

const props = withDefaults(defineProps<{
  sessionId: string
  runId?: string | null
  /** 可见性：由父级控制（抽屉外壳传 show；目录内嵌时挂载即 true） */
  active?: boolean
}>(), {
  runId: null,
  active: true,
})

const emit = defineEmits<{ (event: 'changed'): void }>()

// 同 runId 的活跃 run SSE 全局唯一：新订阅取代旧订阅（naive-ui 抽屉打开时
// 槽内容可能挂载两次；目录抽屉与消息流入口也可能同时打开同一子会话）
const activeRunStreams = new Map<string, AbortController>()

const messages = ref<ChatMessageResponse[]>([])
const loading = ref(false)
const followupInput = ref('')
const followupSending = ref(false)
const streamAbort = ref<AbortController | null>(null)
const activeRunId = ref<string | null>(props.runId)
let requestSerial = 0
const now = ref(Date.now())
let durationTimer: ReturnType<typeof setInterval> | null = null
/** followup 模型选择：初始取子会话 extra.model_id（ModelSelector 持久化），缺省目录默认 */
const selectedModelId = ref('')
/** run 事件消费单点状态（runEventReducer 持有唯一真相；run / contextSnapshot 为派生视图） */
const reducerState = ref<RunEventState>(initialRunEventState())
const run = computed<AgentRunSnapshot | null>(() => reducerState.value.run)
const contextSnapshot = computed<Record<string, unknown> | null>(() => reducerState.value.contextSnapshot)
/** followup 推理档位：与主 Agent 同款选择器（按 turn 覆盖） */
const selectedReasoningEffort = ref('')
/** 子会话统计条：与主会话同口径（assistant 消息 extra.usage 重建，随消息加载/终态更新） */
const sessionStats = computed(() => rebuildSessionStats(messages.value))
const statsLineTemplate = useLocalStorage('noesis:statsline-template', '')
const statsLine = computed(() => formatStatsLine(sessionStats.value, statsLineTemplate.value))

const assistantMessage = computed(() => messages.value.find((item) => item.id === run.value?.assistant_message_id))
const turnCount = computed(() => messages.value.filter((item) => item.role === 'user').length)
const stepCount = computed(() => messages.value.reduce((count, message) => {
  if (message.role !== 'assistant') {
    return count
  }
  return count + normalizeApiContent(message.content).parts.filter((part) => part.type === 'tool').length
}, 0))

/**
 * 用户消息取纯文本：主对话的用户气泡就是纯文本渲染，保持一致
 * （MarkdownPreview 在 fit-content 气泡里会因循环百分比按 max-content 溢出）
 */
function userText(message: ChatMessageResponse): string {
  return normalizeApiContent(message.content).parts.filter((part) => part.type === 'text' && typeof part.content === 'string').map((part) => part.content).join('')
}

const duration = computed(() => {
  const started = wireTimestampMs(assistantMessage.value?.run_started_at)
  if (!started) {
    return ''
  }
  const finished = wireTimestampMs(assistantMessage.value?.run_finished_at) ?? now.value
  return formatDurationMs(Math.max(0, finished - started))
})

/** run 进行中（含排队/待审批）：发送进入前端待发队列，终态后逐条自动提交 */
const runActive = computed(() => !!run.value && ['queued', 'running', 'stopping', 'hitl_pending'].includes(run.value.status))
const sendDisabled = computed(() => !followupInput.value.trim() || followupSending.value)

/**
 * 单按钮形态（与主 Agent 一致）：运行中且输入为空 → 停止当前 run；
 * 有内容 → 发送（运行中发送自动进入待发队列）
 */
const composerStopMode = computed(() => runActive.value && !followupInput.value.trim())

// ---- 前端待发队列（跨抽屉开关存活，见 queuedFollowups.ts；CRUD 走共享 composable） ----

const followupQueue = useFollowupQueue({
  get: () => getQueuedFollowups(props.sessionId),
  set: (list) => setQueuedFollowups(props.sessionId, list),
})
const queuedMessages = followupQueue.messages
/** 编辑：文本回到输入框，从队列移除 */
function editQueued(index: number): void {
  followupInput.value = followupQueue.edit(index)
}

/** 立即提交指定排队消息：空闲即开新 run；运行中由后端衔接为下一轮 */
async function submitQueuedNow(index: number): Promise<void> {
  const message = queuedMessages.value[index]
  if (!message || followupSending.value) {
    return
  }
  followupSending.value = true
  // 先出队再提交：同一子会话可能有多个视图实例（消息卡抽屉 + 任务目录），
  // 出队是同步操作，天然防止两个实例重复提交同一条消息
  followupQueue.remove(index)
  try {
    const task = await sendSubagentFollowup(
      props.sessionId,
      message,
      selectedModelId.value || undefined,
      selectedReasoningEffort.value || undefined,
    )
    activeRunId.value = task.run_id || activeRunId.value
    emit('changed')
    await loadConversation()
  } catch (error) {
    console.warn('[subagent] queued followup submit failed', error)
    const next = [...queuedMessages.value]
    next.splice(Math.min(index, next.length), 0, message)
    setQueuedFollowups(props.sessionId, next)
    window.$message?.error('发送失败')
  } finally {
    followupSending.value = false
  }
}

/** run 终态且有排队消息：提交队首（先出队，失败回插队首） */
async function flushNextQueued(): Promise<void> {
  const message = queuedMessages.value[0]
  if (!message || followupSending.value) {
    return
  }
  followupSending.value = true
  followupQueue.remove(0)
  try {
    const task = await sendSubagentFollowup(
      props.sessionId,
      message,
      selectedModelId.value || undefined,
      selectedReasoningEffort.value || undefined,
    )
    activeRunId.value = task.run_id || activeRunId.value
    emit('changed')
    await loadConversation()
  } catch (error) {
    // 任务失败/取消后 API 拒绝追加：消息回插队首，由用户编辑或删除
    console.warn('[subagent] queued followup flush failed', error)
    setQueuedFollowups(props.sessionId, [message, ...queuedMessages.value])
  } finally {
    followupSending.value = false
  }
}

async function sendFollowup() {
  const message = followupInput.value.trim()
  if (!message || followupSending.value) {
    return
  }
  if (runActive.value) {
    // run 进行中：只进前端队列，等待终态后自动提交（保持可编辑/删除/排序）
    followupInput.value = ''
    setQueuedFollowups(props.sessionId, [...queuedMessages.value, message])
    return
  }
  followupSending.value = true
  try {
    const task = await sendSubagentFollowup(
      props.sessionId,
      message,
      selectedModelId.value || undefined,
      selectedReasoningEffort.value || undefined,
    )
    activeRunId.value = task.run_id || activeRunId.value
    followupInput.value = ''
    emit('changed')
    await loadConversation()
  } catch (error) {
    console.warn('[subagent] followup failed', error)
    window.$message?.error('发送失败')
  } finally {
    followupSending.value = false
  }
}

function stopStream() {
  const controller = streamAbort.value
  streamAbort.value = null
  if (controller) {
    controller.abort()
    for (const [runId, entry] of activeRunStreams) {
      if (entry === controller) {
        activeRunStreams.delete(runId)
      }
    }
  }
}

function upsertAssistant(content: unknown, snapshot?: Partial<AgentRunSnapshot>) {
  const assistantId = snapshot?.assistant_message_id || run.value?.assistant_message_id
  if (!assistantId) {
    return
  }
  const normalized = normalizeApiContent(content)
  const index = messages.value.findIndex((item) => item.id === assistantId)
  if (index >= 0) {
    messages.value[index] = { ...messages.value[index], content: normalized }
    return
  }
  messages.value.push({
    id: assistantId,
    session_id: props.sessionId,
    parent_id: null,
    user_id: '',
    role: 'assistant',
    content: normalized,
    status: String(snapshot?.status || 'streaming'),
    message_sequence: Number.MAX_SAFE_INTEGER,
    created_at: Date.now(),
    run_started_at: undefined,
    run_finished_at: undefined,
  })
}

/**
 * run 事件消费收敛：wire 解析（parseRunEvent）→ 领域事件 → runEventReducer
 * （纯函数，主/子会话共用的唯一状态转移）→ 同步本视图 refs 与消息列表副作用。
 */
function applyEvent(event: string, payload: Record<string, unknown>) {
  const domain = parseRunEvent(event, payload)
  if (!domain) {
    return
  }
  const prev = reducerState.value
  const next = runEventReducer(prev, domain)
  reducerState.value = next
  if (next.assistantContent !== prev.assistantContent) {
    upsertAssistant(next.assistantContent, domain.type === 'run-snapshot' ? next.run ?? undefined : undefined)
  }
  // 终态时刻落进 assistant 消息：流式建出的合成消息没有 run_finished_at，
  // 不补的话 duration 会随 now 永远跳（「会话停了计时器还在跑」）
  if (next.finishedAt && next.finishedAt !== prev.finishedAt) {
    const assistantId = next.run?.assistant_message_id
    if (assistantId) {
      const index = messages.value.findIndex((item) => item.id === assistantId)
      if (index >= 0 && !messages.value[index].run_finished_at) {
        messages.value[index] = { ...messages.value[index], run_finished_at: next.finishedAt }
      }
    }
  }
  // 终态重载：落库后的 usage / 终态内容进入统计条与消息（流式终态对齐）
  if (domain.type === 'run-finished') {
    void loadConversation()
  }
}

async function loadContextSnapshot() {
  try {
    const session = await getSession(props.sessionId)
    if (hasValidContextWindow(session?.extra?.context)) {
      reducerState.value = { ...reducerState.value, contextSnapshot: session.extra.context }
    }
    // 恢复该子会话的模型选择（launch 时写入 worker 实际模型，切换时由
    // ModelSelector 持久化）。无条件覆盖：与 getSession 并发的
    // getChatModels 会把空值先回填成目录默认模型，条件恢复会输掉竞态。
    const sessionModel = session?.extra?.model_id
    if (typeof sessionModel === 'string' && sessionModel) {
      selectedModelId.value = sessionModel
    }
  } catch {
    // 上下文快照缺失只影响指示器，不影响会话展示
  }
}

async function consumeStream(runId: string, serial: number) {
  stopStream()
  const previous = activeRunStreams.get(runId)
  if (previous) {
    previous.abort()
    activeRunStreams.delete(runId)
  }
  const controller = new AbortController()
  activeRunStreams.set(runId, controller)
  streamAbort.value = controller
  try {
    const response = await subscribeAgentRun(runId, Number(run.value?.snapshot_sequence ?? 0), controller.signal)
    if (!response.body) {
      return
    }
    const reader = response.body.getReader()
    const decoder = new TextDecoder()
    let buffer = ''
    let eventName = 'message'
    let dataLines: string[] = []
    const flush = () => {
      if (!dataLines.length) {
        return
      }
      try {
        const payload = JSON.parse(dataLines.join('\n')) as Record<string, unknown>
        applyEvent(eventName, payload)
      } catch (error) {
        console.warn('[subagent] event parse failed', error)
      }
      eventName = 'message'
      dataLines = []
    }
    while (true) {
      if (serial !== requestSerial) {
        break
      }
      const { value, done } = await reader.read()
      if (done) {
        flush()
        break
      }
      buffer += decoder.decode(value, { stream: true })
      const lines = buffer.split(/\r?\n/)
      buffer = lines.pop() || ''
      for (const line of lines) {
        if (!line) {
          flush()
        } else if (line.startsWith('event:')) {
          eventName = line.slice(6).trim()
        } else if (line.startsWith('data:')) {
          dataLines.push(line.slice(5).trim())
        }
      }
    }
  } catch (error) {
    if ((error as Error)?.name !== 'AbortError') {
      console.warn('[subagent] stream failed', error)
    }
  } finally {
    if (streamAbort.value === controller) {
      streamAbort.value = null
    }
    if (activeRunStreams.get(runId) === controller) {
      activeRunStreams.delete(runId)
    }
  }
}

async function loadConversation() {
  const serial = ++requestSerial
  loading.value = true
  try {
    const history = await getSessionMessages(props.sessionId, { limit: 500 })
    if (serial !== requestSerial) {
      return
    }
    messages.value = history.messages
    void loadContextSnapshot()
    if (activeRunId.value) {
      void consumeStream(activeRunId.value, serial)
    }
  } catch (error) {
    if (serial === requestSerial) {
      console.warn('[subagent] history load failed', error)
    }
  } finally {
    if (serial === requestSerial) {
      loading.value = false
    }
  }
}

async function decideHitl(decision: { type: 'approve' | 'reject', grant_scope?: 'once' | 'session' }) {
  if (!run.value?.run_id || !run.value.pending_hitl?.interrupt_id) {
    return
  }
  try {
    syncRunSnapshot(await resumeAgentRunHitl(run.value.run_id, {
      interrupt_id: run.value.pending_hitl.interrupt_id,
      decisions: [decision],
      grant_scope: decision.grant_scope ?? 'once',
    }))
    await loadConversation()
  } catch (error) {
    console.warn('[subagent] approval failed', error)
    window.$message?.error('审批提交失败')
  }
}

async function stopCurrentRun() {
  if (!run.value?.run_id || run.value.status === 'stopping') {
    // stopping 期间重复停止无意义（受理已发出，等静止边界收尾）
    return
  }
  try {
    syncRunSnapshot(await stopAgentRun(run.value.run_id))
    emit('changed')
  } catch (error) {
    console.warn('[subagent] stop failed', error)
    window.$message?.error('停止失败')
  }
}

/** 非事件路径的 run 快照（审批提交/停止的 API 响应）：同步进 reducer 状态，防止后续事件序号回退 */
function syncRunSnapshot(snapshot: AgentRunSnapshot) {
  reducerState.value = { ...reducerState.value, run: snapshot }
}

function startDurationTimer() {
  now.value = Date.now()
  if (durationTimer === null) {
    durationTimer = setInterval(() => {
      now.value = Date.now()
    }, 1000)
  }
}

function stopDurationTimer() {
  if (durationTimer !== null) {
    clearInterval(durationTimer)
    durationTimer = null
  }
}

// 挂载即加载（可见性由父级挂载/卸载控制）；多源 watch 逐源比较，
// 避免「数组 getter 每次新数组恒不等」造成的多余触发
watch([() => props.sessionId, () => props.runId], () => {
  activeRunId.value = props.runId
  void loadConversation()
}, { immediate: true })
// 计时器只在「还有未终态的 run」或「终态时刻未落」时跳动；
// completed 且 run_finished_at 已补齐 → 停表（不再空转）
const needsTicker = computed(() => {
  if (!messages.value.length) {
    return false
  }
  if (run.value && ['queued', 'running', 'hitl_pending'].includes(run.value.status)) {
    return true
  }
  return !assistantMessage.value?.run_finished_at
})
watch(needsTicker, (active) => (active ? startDurationTimer() : stopDurationTimer()), { immediate: true })
// 终态 + 有排队消息 → 逐条提交（首条成功开新 run，其余继续等它终态）；
// 仅 run 权威快照确认终态才触发，避免 run 未加载完成时误发
watch(
  [runActive, () => queuedMessages.value.length],
  ([active, count]) => {
    if (run.value && !active && count > 0) {
      void flushNextQueued()
    }
  },
  { immediate: true },
)
onBeforeUnmount(() => {
  requestSerial += 1
  stopStream()
  stopDurationTimer()
})
</script>

<template>
  <div class="subagent-conversation">
    <div class="subagent-conversation__meta">
      <span>{{ turnCount }} 轮对话</span>
      <span>·</span>
      <span>{{ stepCount }} 步</span>
      <span v-if="duration">· {{ duration }}</span>
      <span v-if="run">· {{ taskStatusLabel(run.status) }}</span>
    </div>
    <div v-if="loading" class="subagent-conversation__empty">正在加载对话…</div>
    <div v-else class="subagent-conversation__body">
      <template v-for="message in messages" :key="message.id">
        <div v-if="message.role === 'user'" class="subagent-conversation__user">
          <span class="subagent-conversation__avatar i-my-svg:user-avatar" aria-hidden="true"></span>
          <div class="subagent-conversation__user-text">{{ userText(message) }}</div>
        </div>
        <div v-else class="subagent-conversation__assistant">
          <ConversationPartsRenderer :content="message.content" appearance="light" />
        </div>
      </template>
      <div v-if="!messages.length" class="subagent-conversation__empty">暂无对话内容</div>
      <template v-if="run?.pending_hitl?.action_requests?.length">
        <HitlApprovalCard
          v-for="request in run.pending_hitl.action_requests"
          :key="request.tool_call_id || request.name"
          :tool-name="request.name || 'tool'"
          :command="JSON.stringify(request.args || {})"
          allow-session-grant
          @decide="decideHitl"
        />
      </template>
    </div>
    <div class="subagent-conversation__composer chat-composer">
      <!-- 前端待发队列：run 进行中发送的消息在此排队，终态后逐条自动提交 -->
      <FollowupQueue
        :messages="queuedMessages"
        @remove="followupQueue.remove"
        @edit="editQueued"
        @send-now="submitQueuedNow"
        @reorder="followupQueue.reorder"
      />
      <n-input
        v-model:value="followupInput"
        type="textarea"
        class="textarea-resize-none w-full text-15 [&_.n-input\\_\\_border]:hidden [&_.n-input\\_\\_state-border]:hidden [&_.n-input-wrapper]:p-0!"
        :style="{
          '--n-border-radius': '15px',
          'font-size': '16px',
          'line-height': '1.5',
        }"
        :placeholder="runActive ? '继续输入以排队后续消息…' : '继续向这个子 Agent 提问…'"
        :autosize="{ minRows: 1, maxRows: 5 }"
        @keydown.enter.exact.prevent="sendFollowup"
      />
      <div class="subagent-conversation__composer-actions">
        <div class="subagent-conversation__composer-left">
          <ModelSelector
            v-model="selectedModelId"
            :session-id="sessionId"
            persist-session-extra
          />
          <ReasoningEffortSelector
            v-model="selectedReasoningEffort"
            :session-id="sessionId"
            :model-id="selectedModelId"
            persist-session-extra
          />
        </div>
        <div class="subagent-conversation__composer-right">
          <ContextWindowIndicator
            v-if="hasValidContextWindow(contextSnapshot)"
            :context="contextSnapshot as any"
          />
          <!-- 单按钮（与主 Agent 一致）：运行中且输入为空 = 停止；有内容 = 发送（运行中入队） -->
          <div class="subagent-conversation__send-btn-wrap">
            <n-float-button
              position="relative"
              :width="36"
              :height="36"
              :disabled="(!composerStopMode && sendDisabled) || run?.status === 'stopping'"
              :type="composerStopMode ? 'primary' : 'default'"
              :data-testid="composerStopMode ? 'subagent-stop-button' : 'subagent-send-button'"
              class="subagent-conversation__send-btn"
              :class="{ 'subagent-conversation__send-btn--stop': composerStopMode }"
              @click.stop="composerStopMode ? stopCurrentRun() : sendFollowup()"
            >
              <span
                v-if="composerStopMode"
                class="subagent-conversation__stop-icon"
                aria-label="停止生成"
              ></span>
              <div
                v-else
                class="flex items-center justify-center i-mingcute:send-fill text-20 cursor-pointer"
              ></div>
            </n-float-button>
          </div>
        </div>
      </div>
      <!-- 子会话统计条：与主会话同口径（extra.usage 重建，终态随消息重载更新） -->
      <div v-if="statsLine" class="subagent-conversation__stats" role="status">
        {{ statsLine }}
      </div>
    </div>
  </div>
</template>

<style scoped lang="scss">
.subagent-conversation {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
}

.subagent-conversation__stats {
  margin-top: 6px;
  color: var(--noesis-color-text-hint);
  font-size: 12px;
  font-variant-numeric: tabular-nums;
  text-align: center;
}

.subagent-conversation__meta {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 12px;
  color: var(--noesis-color-text-hint);
  font-size: 12px;
}

.subagent-conversation__empty {
  padding: 32px 0;
  color: var(--noesis-color-text-hint);
  font-size: 13px;
  text-align: center;
}

.subagent-conversation__body {
  display: flex;
  flex-direction: column;
  gap: 14px;
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding-bottom: 16px;
}

.subagent-conversation__user {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  align-self: flex-end;
  max-width: 88%;
  box-sizing: border-box;
  padding: 8px 14px;
  border: 1px solid var(--noesis-color-primary-border-soft);
  border-radius: var(--noesis-radius-lg) var(--noesis-radius-lg) var(--noesis-radius-sm) var(--noesis-radius-lg);
  background: var(--noesis-color-primary-bg-subtle);
}

.subagent-conversation__user-text {
  color: var(--noesis-color-text);
  font-size: 14px;
  line-height: 1.6;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}

.subagent-conversation__avatar {
  flex: none;
  width: 20px;
  height: 20px;
  color: var(--noesis-color-primary);
}

.subagent-conversation__assistant {
  max-width: 100%;
  min-width: 0;
}

.subagent-conversation__composer {
  display: flex;
  flex-direction: column;
  gap: 6px;
  flex: none;
  margin-top: 8px;
  padding: 10px 12px;
  border: 1px solid var(--noesis-color-border);
  border-radius: var(--noesis-radius-composer);
  background: var(--noesis-color-bg-composer);
}

.subagent-conversation__composer-actions {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

/* 左右分区（与主 Agent ChatComposerToolbar 同构）：模型选择器靠左，
   上下文圆环与发送/停止按钮靠右 */
.subagent-conversation__composer-left,
.subagent-conversation__composer-right {
  display: flex;
  align-items: center;
  gap: 4px;
  min-width: 0;
}

.subagent-conversation__composer-right {
  flex-shrink: 0;
}

.subagent-conversation__send-btn-wrap {
  z-index: 1;
  display: flex;
  align-items: center;
}

.subagent-conversation__send-btn-wrap :deep(.n-float-button) {
  position: relative !important;
  inset: auto !important;
}

/* 停止态与主 Agent 同款：主色圆钮 + 白色方块 + 光环 */
.subagent-conversation__send-btn--stop {
  box-shadow: 0 0 0 2px var(--noesis-color-primary-ring);
}

.subagent-conversation__stop-icon {
  display: block;
  width: 12px;
  height: 12px;
  background-color: var(--noesis-color-bg-elevated);
  border-radius: 2px;
}

@media (max-width: $bp-md) {
  .subagent-conversation__composer-actions {
    flex-wrap: wrap;
    row-gap: 6px;
  }
}
</style>
