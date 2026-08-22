<script setup lang="ts">
import type { AgentRunSnapshot, ChatMessageResponse } from '@/api/chat'
import { NButton, NDrawer, NDrawerContent, NInput } from 'naive-ui'
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import {
  getSessionMessages,
  resumeAgentRunHitl,
  sendSubagentFollowup,
  stopAgentRun,
  subscribeAgentRun,
} from '@/api/chat'
import ConversationPartsRenderer from '@/components/ConversationPartsRenderer/index.vue'
import HitlApprovalCard from '@/components/HitlApprovalCard/index.vue'
import MarkdownPreview from '@/components/MarkdownPreview/index.vue'
import { useResponsiveDrawerWidth } from '@/hooks/useResponsiveDrawerWidth'
import {
  formatDurationMs,
  normalizeApiContent,
} from '@/views/chat/messageParts'

const props = withDefaults(defineProps<{
  sessionId: string
  runId?: string | null
  title?: string
}>(), {
  runId: null,
  title: '子 Agent 对话',
})

const emit = defineEmits<{ (event: 'changed'): void }>()
const show = defineModel<boolean>('show', { default: false })
const { drawerWidth } = useResponsiveDrawerWidth({ max: 760, mobileRatio: 0.96 })

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

const assistantMessage = computed(() => messages.value.find((item) => item.id === run.value?.assistant_message_id))
const turnCount = computed(() => messages.value.filter((item) => item.role === 'user').length)
const stepCount = computed(() => messages.value.reduce((count, message) => {
  if (message.role !== 'assistant') {
    return count
  }
  return count + normalizeApiContent(message.content).parts.filter((part) => part.type === 'tool').length
}, 0))

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
  streamAbort.value?.abort()
  streamAbort.value = null
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

async function consumeStream(runId: string, serial: number) {
  stopStream()
  const controller = new AbortController()
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

watch(show, (open) => {
  if (open) {
    now.value = Date.now()
    if (!durationTimer) {
      durationTimer = setInterval(() => {
        now.value = Date.now()
      }, 1000)
    }
    void loadConversation()
  } else {
    stopStream()
    if (durationTimer) {
      clearInterval(durationTimer)
      durationTimer = null
    }
  }
}, { immediate: true })
watch(() => [props.sessionId, props.runId], () => {
  activeRunId.value = props.runId
  if (show.value) {
    void loadConversation()
  }
})
onBeforeUnmount(() => {
  requestSerial += 1
  stopStream()
  if (durationTimer) {
    clearInterval(durationTimer)
  }
})
</script>

<template>
  <n-drawer v-model:show="show" placement="right" :width="drawerWidth">
    <n-drawer-content :title="props.title" closable>
      <div class="subagent-conversation__meta">
        <span>{{ turnCount }} 轮对话</span>
        <span>·</span>
        <span>{{ stepCount }} 步</span>
        <span v-if="duration">· {{ duration }}</span>
        <span v-if="run">· {{ run.status }}</span>
      </div>
      <div v-if="loading" class="subagent-conversation__empty">正在加载对话…</div>
      <div v-else class="subagent-conversation__body">
        <template v-for="message in messages" :key="message.id">
          <div v-if="message.role === 'user'" class="subagent-conversation__user">
            <span class="subagent-conversation__avatar i-my-svg:user-avatar" aria-hidden="true"></span>
            <MarkdownPreview
              :content="message.content.parts.filter((part) => part.type === 'text').map((part) => part.content).join('')"
              :show-action-bar="false"
              variant="segment"
            />
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
      <div class="subagent-conversation__composer">
        <n-input
          v-model:value="followupInput"
          type="textarea"
          :autosize="{ minRows: 1, maxRows: 5 }"
          placeholder="继续向这个子 Agent 提问…"
          @keydown.enter.exact.prevent="sendFollowup"
        />
        <n-button type="primary" :loading="followupSending" @click="sendFollowup">发送</n-button>
        <n-button
          v-if="run && ['running', 'hitl_pending'].includes(run.status)"
          quaternary
          type="error"
          @click="stopCurrentRun"
        >
          停止
        </n-button>
      </div>
    </n-drawer-content>
  </n-drawer>
</template>

<style scoped lang="scss">
.subagent-conversation__meta {
  display: flex;
  gap: 6px;
  margin-bottom: 12px;
  color: var(--noesis-color-text-hint);
  font-size: 12px;
  font-variant-numeric: tabular-nums;
}
.subagent-conversation__body {
  display: flex;
  flex-direction: column;
  gap: 14px;
  min-height: 240px;
  padding-bottom: 16px;
}
.subagent-conversation__user {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  align-self: flex-end;
  width: fit-content;
  max-width: 88%;
  box-sizing: border-box;
  padding: 8px 14px;
  border: 1px solid var(--noesis-color-primary-border-soft);
  border-radius: var(--noesis-radius-lg) var(--noesis-radius-lg) var(--noesis-radius-sm) var(--noesis-radius-lg);
  background: var(--noesis-color-primary-bg-subtle);
  color: var(--noesis-color-text);
  line-height: 1.55;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
.subagent-conversation__avatar {
  flex: 0 0 auto;
  width: 20px;
  height: 20px;
  color: var(--noesis-color-primary);
}
.subagent-conversation__user :deep(.markdown-wrapper) {
  margin: 0;
  padding: 0;
  background: transparent;
  width: auto !important;
  max-width: 100%;
  font-size: inherit;
  line-height: inherit;
}
.subagent-conversation__user :deep(.n-spin),
.subagent-conversation__user :deep(.n-spin-container),
.subagent-conversation__user :deep(.n-spin-content),
.subagent-conversation__user :deep(.n-spin-content > div),
.subagent-conversation__user :deep(.n-spin-content > div > div),
.subagent-conversation__user :deep(.markdown-preview__body) {
  width: auto !important;
  height: auto !important;
  flex: none !important;
  min-height: 0 !important;
  overflow: visible !important;
}
.subagent-conversation__user :deep(.markdown-preview__body) {
  padding: 0 !important;
}
.subagent-conversation__user :deep(.markdown-wrapper p) {
  margin: 0 0 8px;
}
.subagent-conversation__user :deep(.markdown-wrapper > :last-child) {
  margin-bottom: 0;
}
.subagent-conversation__assistant {
  display: flex;
  flex-direction: column;
  gap: 8px;
  min-width: 0;
}
.subagent-conversation__empty {
  padding: 24px 8px;
  color: var(--noesis-color-text-hint);
  text-align: center;
}
.subagent-conversation__composer {
  display: flex;
  gap: 8px;
  padding-top: 10px;
  border-top: 1px solid var(--noesis-color-border-subtle);
}
.subagent-conversation__composer :deep(.n-input),
.subagent-conversation__composer :deep(.n-input-wrapper) {
  background: var(--noesis-color-bg-elevated) !important;
}
</style>
