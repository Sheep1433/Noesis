<script setup lang="ts">
import type { AgentRunSnapshot, ChatMessageResponse } from '@/api/chat'
import type { RetrievalResultUi } from '@/views/chat/messageParts'
import type { RunEventState } from '@/views/chat/runEventReducer'
import { useLocalStorage } from '@vueuse/core'
import { NFloatButton, NInput } from 'naive-ui'
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import {
  getActiveRun,
  getAgentRun,
  getSession,
  getSessionMessages,
  resumeAgentRunHitl,
  sendSubagentFollowup,
  stopAgentRun,
  subscribeAgentRun,
} from '@/api/chat'
import AssistantReplyToolbar from '@/components/AssistantReplyToolbar/index.vue'
import AssistantStreamingIndicator from '@/components/AssistantStreamingIndicator/index.vue'
import ModelSelector from '@/components/Chat/ModelSelector.vue'
import ReasoningEffortSelector from '@/components/Chat/ReasoningEffortSelector.vue'
import CitationSources from '@/components/CitationSources/index.vue'
import ContextWindowIndicator from '@/components/ContextWindowIndicator/index.vue'
import ConversationPartsRenderer from '@/components/ConversationPartsRenderer/index.vue'
import FollowupQueue from '@/components/FollowupQueue/index.vue'
import HitlApprovalCard from '@/components/HitlApprovalCard/index.vue'
import { getQueuedFollowups, setQueuedFollowups } from '@/components/SubagentConversationView/queuedFollowups'
import { langfuseUiOrigin } from '@/config'
import { useFollowupQueue } from '@/hooks/useFollowupQueue'
import { useToolDisplayMode } from '@/hooks/useToolDisplayMode'
import { formatHHmm, wireTimestampMs } from '@/utils/formatTime'
import { buildDisplayParts, lastTopLevelTextEntry } from '@/utils/groupAssistantParts'
import { rebuildSessionStats } from '@/utils/sessionStats'
import { formatStatsLine } from '@/utils/statsFormat'
import { citationKey } from '@/views/chat/citationRendering'
import { assistantPartsStillStreaming,
  extractLastTopLevelText,
  formatDurationMs,
  hasValidContextWindow,
  normalizeApiContent,
  shouldShowAssistantToolFailureBlocker } from '@/views/chat/messageParts'
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
/**
 * 运行中优先流式统计（executor 每次模型调用发布，与主 Agent 同口径含 tok/s）；
 *  终态后 reducer stats 清空，回落落库 usage 重建。
 */
const effectiveStats = computed(() => {
  if (runActive.value && reducerState.value.stats) {
    return reducerState.value.stats
  }
  // 失败终态不落 usage（后端缺口）时，DB 重建为空——保留终态前最后一次
  // 实时统计（bridge 真实累计，非估算），统计条不因此消失
  return sessionStats.value ?? reducerState.value.stats ?? null
})
const statsLine = computed(() => formatStatsLine(effectiveStats.value, statsLineTemplate.value))

/** 消息级检索结果：正文 badge 序号数据源（与主会话同构的 retrieval parts） */
function messageRetrievalResults(message: ChatMessageResponse): RetrievalResultUi[] {
  return normalizeApiContent(message.content).parts.filter((part) => part.type === 'retrieval').flatMap((part) => part.results)
}

/** 会话级来源面板：子会话全部落库 retrieval parts，按 canonical URL 去重 */
const sessionSources = computed<RetrievalResultUi[]>(() => {
  const seen = new Map<string, RetrievalResultUi>()
  for (const message of messages.value) {
    if (message.role !== 'assistant') {
      continue
    }
    for (const result of messageRetrievalResults(message)) {
      const key = citationKey(result)
      if (!seen.has(key)) {
        seen.set(key, result)
      }
    }
  }
  return [...seen.values()]
})

const assistantMessage = computed(() => messages.value.find((item) => item.id === run.value?.assistant_message_id))
/**
 * 用户消息取纯文本：主对话的用户气泡就是纯文本渲染，保持一致
 * （MarkdownPreview 在 fit-content 气泡里会因循环百分比按 max-content 溢出）
 */
function userText(message: ChatMessageResponse): string {
  return normalizeApiContent(message.content).parts.filter((part) => part.type === 'text' && typeof part.content === 'string').map((part) => part.content).join('')
}

/** run 进行中（含排队/待审批）：发送进入前端待发队列，终态后逐条自动提交 */
const runActive = computed(() => !!run.value && ['queued', 'running', 'stopping', 'hitl_pending'].includes(run.value.status))

/** 工具展示模式：与主 Agent 共享同一存储实例（useToolDisplayMode 模块级单例） */
const { mode: toolDisplayMode } = useToolDisplayMode()

// ---- compact 折叠（与主 Agent assistant-run-meta 同语义）----
const expandedRuns = ref(new Set<string>())

/** 已完结 + 有可折叠过程 + 有终稿 → compact 模式折叠为终稿（主 Agent 同判据） */
function shouldCollapseMessage(message: ChatMessageResponse): boolean {
  if (toolDisplayMode.value !== 'compact' || message.status === 'streaming') {
    return false
  }
  const parts = normalizeApiContent(message.content).parts
  if (!parts.some((part) => part.type === 'tool' || part.type === 'reasoning')) {
    return false
  }
  return lastTopLevelTextEntry(buildDisplayParts(parts)) !== null
}

function isMessageExpanded(message: ChatMessageResponse): boolean {
  return expandedRuns.value.has(message.id)
}

function toggleMessageCollapse(message: ChatMessageResponse) {
  if (!shouldCollapseMessage(message)) {
    return
  }
  const next = new Set(expandedRuns.value)
  if (next.has(message.id)) {
    next.delete(message.id)
  } else {
    next.add(message.id)
  }
  expandedRuns.value = next
}

/** 回复级耗时（主 Agent runElapsedText 同构）：run 起止毫秒 → 可读时长 */
function messageElapsedText(message: ChatMessageResponse): string {
  const started = wireTimestampMs(message.run_started_at)
  if (!started) {
    return ''
  }
  const finished = wireTimestampMs(message.run_finished_at) ?? now.value
  return `耗时 ${formatDurationMs(Math.max(0, finished - started))}`
}
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
    activeRunId.value = (await resolveActiveRunId(task.run_id)) || activeRunId.value
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
    activeRunId.value = (await resolveActiveRunId(task.run_id)) || activeRunId.value
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

/**
 * 冷恢复 run_id 竞态：send_followup 响应可能携带旧 run_id（新 run 在
 *  隔离 loop 异步创建，响应可先于创建完成返回）——以服务端 active-run
 *  发现为准，短重试覆盖创建窗口；发现不到回退响应值。
 */
async function resolveActiveRunId(fallbackRunId?: string | null): Promise<string | null> {
  for (let attempt = 0; attempt < 4; attempt += 1) {
    if (attempt > 0) {
      await new Promise((resolve) => setTimeout(resolve, 250))
    }
    try {
      const active = await getActiveRun(props.sessionId)
      if (active?.run_id) {
        return active.run_id
      }
    } catch {
      return fallbackRunId ?? null
    }
  }
  return fallbackRunId ?? null
}

async function sendFollowup() {
  const message = followupInput.value.trim()
  if (!message || followupSending.value) {
    return
  }
  if (run.value?.status === 'stopping') {
    // 与按钮禁用同口径：stopping 期间不可发送（后端也拒绝），两路径不分叉
    window.$message?.warning('任务正在停止，无法发送')
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
    activeRunId.value = (await resolveActiveRunId(task.run_id)) || activeRunId.value
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
  // 流式正文增量（瞬态）：直改合成消息的末尾 part（O(1) 追加），边界
  // message-updated 会以权威投影整体收口，此处不进 reducer
  if (domain.type === 'content-delta') {
    appendStreamingDelta(domain.kind, domain.text)
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
    runCollapseSignal.value += 1
    void loadConversation()
  }
}

/**
 * 回合结束收起脉冲：与主视图 runCollapseSignal 同机制——run 终态时 +1，
 *  广播让本回合工具卡（ToolCallCollapse / 并行组）自动收起。
 */
const runCollapseSignal = ref(0)

/**
 * 生成中标记：与主 Agent 同口径——由 run 活动驱动（模型思考/工具执行阶段
 *  也常亮），而非仅文本 delta 到达时刻点亮；标签区分首段/续段。此前仅
 *  delta 到达时点亮，工具批与长模型调用期间无任何指示，观感即「卡住」。
 */
const runGenerating = computed(() => run.value?.status === 'running')
const assistantHasParts = computed(() =>
  messages.value.some((m) => m.role === 'assistant' && normalizeApiContent(m.content).parts.length > 0),
)
/** 追加流式增量到合成 assistant 消息的末尾同类型 part（无则新建）。 */
function appendStreamingDelta(kind: 'text' | 'reasoning', text: string) {
  const assistantId = run.value?.assistant_message_id
  if (!assistantId) {
    return
  }
  const index = messages.value.findIndex((item) => item.id === assistantId)
  if (index === -1) {
    // 首个 delta 先于任何边界投影：建流式骨架（upsertAssistant 的合成路径）
    upsertAssistant({ version: 1, parts: [] })
  }
  const i = messages.value.findIndex((item) => item.id === assistantId)
  if (i === -1) {
    return
  }
  const content = messages.value[i].content as { version: number, parts: Array<Record<string, unknown>> }
  if (!Array.isArray(content.parts)) {
    content.parts = []
  }
  const partType = kind === 'text' ? 'text' : 'reasoning'
  let last = content.parts[content.parts.length - 1]
  if (!last || last.type !== partType || typeof last.content !== 'string') {
    last = { id: `part-stream-${partType}-${content.parts.length}`, type: partType, content: '', status: 'streaming' }
    content.parts.push(last)
  }
  last.content = (last.content as string) + text
  if (kind === 'text') {
    last.status = 'streaming'
  }
  messages.value[i] = { ...messages.value[i], content: { ...content, parts: [...content.parts] } }
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

async function consumeStream(runId: string, serial: number, attempt = 0) {
  stopStream()
  const previous = activeRunStreams.get(runId)
  if (previous) {
    previous.abort()
    activeRunStreams.delete(runId)
  }
  const controller = new AbortController()
  activeRunStreams.set(runId, controller)
  streamAbort.value = controller
  let streamEndedNormally = false
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
        if (payload.type === 'run.finished') {
          streamEndedNormally = true
        }
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
    // 断流自愈：流结束但未见过终态事件且 run 仍非终态——连接中断会把
    // 「运行中」永久卡死（终态事件随断连丢失，此前无任何补救路径：
    // 对话已结束仍显示生成中标记与停止按钮）
    if (!streamEndedNormally && serial === requestSerial && runActive.value) {
      void resyncRunAfterStreamEnd(runId, serial, attempt)
    }
  }
}

/** 断流后重取权威 run 状态：终态则按快照+终态收口；仍在跑则重订阅（有界退避）。 */
async function resyncRunAfterStreamEnd(runId: string, serial: number, attempt: number) {
  if (serial !== requestSerial || attempt >= 6) {
    return
  }
  await new Promise((resolve) => setTimeout(resolve, Math.min(800 * (attempt + 1), 4000)))
  if (serial !== requestSerial) {
    return
  }
  try {
    const snapshot = await getAgentRun(runId)
    if (serial !== requestSerial) {
      return
    }
    const status = String(snapshot.status || '')
    if (['completed', 'error', 'partial', 'interrupted'].includes(status)) {
      applyEvent('run-snapshot', { type: 'run-snapshot', ...snapshot })
      applyEvent('run.finished', {
        type: 'run.finished',
        status,
        finished_at: snapshot.finished_at ?? null,
      })
      return
    }
  } catch {
    // 权威状态暂不可达：直接重订阅再试
  }
  void consumeStream(runId, serial, attempt + 1)
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
    <div v-if="loading" class="subagent-conversation__empty">正在加载对话…</div>
    <div v-else class="subagent-conversation__body">
      <template v-for="message in messages" :key="message.id">
        <div v-if="message.role === 'user'" class="subagent-conversation__user">
          <span class="subagent-conversation__avatar i-my-svg:user-avatar" aria-hidden="true"></span>
          <div class="subagent-conversation__user-text">{{ userText(message) }}</div>
        </div>
        <div v-else class="subagent-conversation__assistant">
          <!-- 回复级元信息（主 Agent assistant-run-meta 同构）：耗时在回复上方，
               compact 可折叠轮为展开开关 -->
          <div v-if="messageElapsedText(message)" class="subagent-conversation__run-meta">
            <button
              v-if="shouldCollapseMessage(message)"
              type="button"
              class="subagent-conversation__run-meta-toggle"
              :aria-expanded="isMessageExpanded(message)"
              @click="toggleMessageCollapse(message)"
            >
              <span>{{ messageElapsedText(message) }}</span>
              <span
                class="subagent-conversation__run-meta-chevron"
                :class="{ 'subagent-conversation__run-meta-chevron--expanded': isMessageExpanded(message) }"
                aria-hidden="true"
              >›</span>
            </button>
            <span v-else class="subagent-conversation__run-meta-elapsed">{{ messageElapsedText(message) }}</span>
          </div>
          <ConversationPartsRenderer
            :content="message.content"
            appearance="light"
            :retrieval-results="messageRetrievalResults(message)"
            :qa-type="message.qa_type || 'SUPER_AGENT_QA'"
            :collapse-signal="runCollapseSignal"
            :compact-tools="toolDisplayMode === 'compact'"
            :collapsed="shouldCollapseMessage(message) && !isMessageExpanded(message)"
            :live-streaming="runGenerating && message.status === 'streaming'"
          />
          <div
            v-if="shouldShowAssistantToolFailureBlocker(normalizeApiContent(message.content).parts, runGenerating && message.status === 'streaming')"
            class="subagent-conversation__failure-blocker"
            role="status"
          >
            <span class="subagent-conversation__failure-blocker-icon" aria-hidden="true">!</span>
            <span>本轮未完成</span>
          </div>
          <div
            v-if="normalizeApiContent(message.content).parts.length > 0 && !assistantPartsStillStreaming(normalizeApiContent(message.content).parts)"
            class="subagent-conversation__message-actions"
          >
            <AssistantReplyToolbar
              :bordered="false"
              :qa-type="message.qa_type || 'SUPER_AGENT_QA'"
              :copy-text="extractLastTopLevelText(normalizeApiContent(message.content).parts)"
              :time-text="formatHHmm(wireTimestampMs(message.run_finished_at ?? message.created_at) || message.created_at)"
              :langfuse-session-id="sessionId"
              :langfuse-ui-origin="langfuseUiOrigin"
            />
          </div>
        </div>
      </template>
      <div v-if="!messages.length" class="subagent-conversation__empty">暂无对话内容</div>
      <!-- 生成中标记：消息流末尾（与主 Agent 的消息内同位置语义，而非
           消息区与输入框之间的独立悬浮行） -->
      <AssistantStreamingIndicator
        v-if="runGenerating"
        section
        :divided="assistantHasParts"
        :label="assistantHasParts ? '正在继续生成' : '正在生成'"
      />
      <!-- 子会话来源面板：与主 Agent 同位置（回复末尾而非底部统计行）；
           基于落库 retrieval parts，会话内 canonical URL 去重 -->
      <div v-if="sessionSources.length" class="subagent-conversation__sources">
        <CitationSources :results="sessionSources" />
      </div>
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
    </div>
    <!-- 子会话统计行：与主 Agent 同位置（输入框下方）。usage 统计与主会话同口径
         （extra.usage 重建，终态随消息重载更新）；运行中尚无 usage 时以轮对话/
         步数/时长兜底；任务状态同区。置于输入框容器外，避免继承消息框底色 -->
    <div
      v-if="statsLine"
      class="subagent-conversation__stats"
      role="status"
    >
      {{ statsLine }}
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

/* 回复工具条容器：主视图 assistant-message-actions 同构 */
.subagent-conversation__message-actions {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  box-sizing: border-box;
  width: 100%;
  margin-top: -8px;
}

/* 本轮未完成 blocker：主视图 assistant-tool-failure-blocker 同构 */
.subagent-conversation__failure-blocker {
  display: flex;
  align-items: center;
  gap: 7px;
  margin: 7px 2px 3px;
  color: var(--noesis-color-text-secondary);
  font-size: 12px;
  line-height: 1.4;
}

.subagent-conversation__failure-blocker-icon {
  display: inline-flex;
  flex: 0 0 16px;
  align-items: center;
  justify-content: center;
  width: 16px;
  height: 16px;
  color: var(--noesis-color-warning);
  font-size: 11px;
  font-weight: 700;
  border: 1px solid currentColor;
  border-radius: 50%;
}

/* 头部元信息：主 Agent assistant-run-meta 同构（耗时 + 状态在对话上方） */
.subagent-conversation__run-meta {
  display: flex;
  align-items: center;
  min-height: 24px;
  margin-bottom: 6px;
  padding: 0 2px;
  font-size: 13px;
  line-height: 1.4;
  color: var(--noesis-color-text-hint);
  letter-spacing: 0.01em;
}

.subagent-conversation__run-meta-toggle {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 0;
  border: 0;
  background: transparent;
  color: var(--noesis-color-text-muted);
  font-size: 13px;
  line-height: 1.5;
  cursor: pointer;
  transition: color 0.15s ease;
}

.subagent-conversation__run-meta-toggle:hover {
  color: var(--noesis-color-text);
}

.subagent-conversation__run-meta-chevron {
  display: inline-block;
  font-size: 16px;
  line-height: 12px;
  transform: translateY(-1px);
  transition: transform 0.15s ease;
}

.subagent-conversation__run-meta-chevron--expanded {
  transform: translateY(-1px) rotate(90deg);
}

.subagent-conversation__run-meta-status {
  margin-left: 4px;
  color: var(--noesis-color-text-secondary);
  font-variant-numeric: tabular-nums;
}

.subagent-conversation__stats {
  display: flex;
  align-items: center;
  justify-content: center;
  flex-wrap: wrap;
  gap: 4px;
  margin-top: 2px;
  color: var(--noesis-color-text-hint);
  font-size: 11px;
  line-height: 1.4;
  font-variant-numeric: tabular-nums;
}

/* 来源面板：回复末尾（与主 Agent 的回复工具栏 meta 区同位置语义） */
.subagent-conversation__sources {
  display: flex;
  justify-content: flex-start;
  padding: 4px 2px 0;
}


@keyframes subagent-generating-pulse {
  0%, 100% { opacity: 0.35; }
  50% { opacity: 1; }
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
