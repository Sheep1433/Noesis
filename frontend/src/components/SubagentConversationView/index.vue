<script setup lang="ts">
import type { AgentRunSnapshot, ChatMessageResponse } from '@/api/chat'
import { NButton, NInput } from 'naive-ui'
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import {
  getSession,
  getSessionMessages,
  resumeAgentRunHitl,
  sendSubagentFollowup,
  stopAgentRun,
  subscribeAgentRun,
} from '@/api/chat'
import ContextWindowIndicator from '@/components/ContextWindowIndicator/index.vue'
import ConversationPartsRenderer from '@/components/ConversationPartsRenderer/index.vue'
import HitlApprovalCard from '@/components/HitlApprovalCard/index.vue'
import {
  formatDurationMs,
  hasValidContextWindow,
  normalizeApiContent,
} from '@/views/chat/messageParts'

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
const run = ref<AgentRunSnapshot | null>(null)
const loading = ref(false)
const followupInput = ref('')
const followupSending = ref(false)
const streamAbort = ref<AbortController | null>(null)
const activeRunId = ref<string | null>(props.runId)
let requestSerial = 0
const now = ref(Date.now())
let durationTimer: ReturnType<typeof setInterval> | null = null
const contextSnapshot = ref<Record<string, unknown> | null>(null)

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

function timestampMs(value: number | null | undefined): number | undefined {
  if (value == null || !Number.isFinite(value)) {
    return undefined
  }
  return Math.abs(value) < 1e12 ? value * 1000 : value
}

const duration = computed(() => {
  const started = timestampMs(assistantMessage.value?.run_started_at)
  if (!started) {
    return ''
  }
  const finished = timestampMs(assistantMessage.value?.run_finished_at) ?? now.value
  return formatDurationMs(Math.max(0, finished - started))
})

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
  if (snapshot) {
    run.value = { ...(run.value ?? {} as AgentRunSnapshot), ...snapshot } as AgentRunSnapshot
  }
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

function applyEvent(event: string, payload: Record<string, unknown>) {
  if (event === 'run-snapshot') {
    run.value = payload as unknown as AgentRunSnapshot
    upsertAssistant(payload.content, payload as unknown as Partial<AgentRunSnapshot>)
    return
  }
  const sequence = Number(payload.sequence ?? 0)
  if (run.value && sequence > Number(run.value.snapshot_sequence ?? 0)) {
    run.value = { ...run.value, snapshot_sequence: sequence }
  }
  if (event === 'context-update' && payload.context && typeof payload.context === 'object') {
    contextSnapshot.value = { ...(payload.context as Record<string, unknown>) }
    return
  }
  if (event === 'message.updated') {
    upsertAssistant(payload.content)
    if (run.value) {
      run.value = { ...run.value, pending_hitl: null }
    }
  } else if (event === 'approval.required') {
    run.value = {
      ...(run.value as AgentRunSnapshot),
      status: 'hitl_pending',
      pending_hitl: payload.pending_hitl as AgentRunSnapshot['pending_hitl'],
    }
  } else if (event === 'approval.resumed') {
    if (run.value) {
      run.value = { ...run.value, status: 'running', pending_hitl: null }
    }
  } else if (event === 'run.finished') {
    if (run.value) {
      run.value = {
        ...run.value,
        status: String(payload.status || 'completed') as AgentRunSnapshot['status'],
        pending_hitl: null,
      }
    }
  }
}

async function loadContextSnapshot() {
  try {
    const session = await getSession(props.sessionId)
    if (hasValidContextWindow(session?.extra?.context)) {
      contextSnapshot.value = session.extra.context
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

async function sendFollowup() {
  const message = followupInput.value.trim()
  if (!message || followupSending.value) {
    return
  }
  followupSending.value = true
  try {
    const task = await sendSubagentFollowup(props.sessionId, message)
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

async function decideHitl(decision: { type: 'approve' | 'reject', grant_scope?: 'once' | 'session' }) {
  if (!run.value?.run_id || !run.value.pending_hitl?.interrupt_id) {
    return
  }
  try {
    run.value = await resumeAgentRunHitl(run.value.run_id, {
      interrupt_id: run.value.pending_hitl.interrupt_id,
      decisions: [decision],
      grant_scope: decision.grant_scope ?? 'once',
    })
    await loadConversation()
  } catch (error) {
    console.warn('[subagent] approval failed', error)
    window.$message?.error('审批提交失败')
  }
}

async function stopCurrentRun() {
  if (!run.value?.run_id) {
    return
  }
  try {
    run.value = await stopAgentRun(run.value.run_id)
    emit('changed')
  } catch (error) {
    console.warn('[subagent] stop failed', error)
    window.$message?.error('停止失败')
  }
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
watch(() => messages.value.length > 0, (has) => (has ? startDurationTimer() : stopDurationTimer()), { immediate: true })
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
      <span v-if="run">· {{ run.status }}</span>
      <span v-if="hasValidContextWindow(contextSnapshot)" class="subagent-conversation__context">
        <ContextWindowIndicator :context="contextSnapshot as any" />
      </span>
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
      <n-input
        v-model:value="followupInput"
        type="textarea"
        class="textarea-resize-none w-full text-15 [&_.n-input\\_\\_border]:hidden [&_.n-input\\_\\_state-border]:hidden [&_.n-input-wrapper]:p-0!"
        :style="{
          '--n-border-radius': '15px',
          'font-size': '16px',
          'line-height': '1.5',
        }"
        placeholder="继续向这个子 Agent 提问…"
        :autosize="{ minRows: 1, maxRows: 5 }"
        @keydown.enter.exact.prevent="sendFollowup"
      />
      <div class="subagent-conversation__composer-actions">
        <n-button
          v-if="run && ['running', 'hitl_pending'].includes(run.status)"
          quaternary
          type="error"
          size="small"
          @click="stopCurrentRun"
        >
          停止
        </n-button>
        <n-button type="primary" size="small" :loading="followupSending" @click="sendFollowup">
          发送
        </n-button>
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

.subagent-conversation__meta {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 12px;
  color: var(--noesis-color-text-hint);
  font-size: 12px;
}

.subagent-conversation__context {
  display: inline-flex;
  align-items: center;
  margin-left: auto;
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
  justify-content: flex-end;
  gap: 8px;
}
</style>
