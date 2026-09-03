<script setup lang="ts">
import type { AgentRunSnapshot, ChatMessageResponse } from '@/api/chat'
import type { RetrievalResultUi, UiPart } from '@/views/chat/messageParts'
import type { RunEventState } from '@/views/chat/runEventReducer'
import { useLocalStorage } from '@vueuse/core'
import { NInput } from 'naive-ui'
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import {
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
import AssistantToolFailureBlocker from '@/components/AssistantToolFailureBlocker/index.vue'
import ChatComposerToolbar from '@/components/Chat/ChatComposerToolbar.vue'
import ContextWindowIndicator from '@/components/ContextWindowIndicator/index.vue'
import ConversationPartsRenderer from '@/components/ConversationPartsRenderer/index.vue'
import FollowupQueue from '@/components/FollowupQueue/index.vue'
import HitlComposerPanel from '@/components/HitlComposerPanel/index.vue'
import ResearchSourcesPanel from '@/components/ResearchSourcesPanel/index.vue'
import RunMetaLine from '@/components/RunMetaLine/index.vue'
import SessionStatsLine from '@/components/SessionStatsLine/index.vue'
import StopSendButton from '@/components/StopSendButton/index.vue'
import { getQueuedFollowups, setQueuedFollowups } from '@/components/SubagentConversationView/queuedFollowups'
import { langfuseUiOrigin } from '@/config'
import { useFollowupQueue } from '@/hooks/useFollowupQueue'
import { useTicker } from '@/hooks/useTicker'
import { useToolDisplayMode } from '@/hooks/useToolDisplayMode'
import { formatHHmm, wireTimestampMs } from '@/utils/formatTime'
import { buildDisplayParts, lastTopLevelTextEntry } from '@/utils/groupAssistantParts'
import { rebuildSessionStats } from '@/utils/sessionStats'
import { formatStatsLine } from '@/utils/statsFormat'
import { citationKey } from '@/views/chat/citationRendering'
import {
  appendReasoningDelta,
  appendRetrievalPart,
  appendTextDelta,
  applyToolOutput,
  assistantPartsStillStreaming,
  extractLastTopLevelText,
  formatDurationMs,
  hasValidContextWindow,
  normalizeApiContent,
  shouldShowAssistantToolFailureBlocker,
  upsertToolInputPart,
} from '@/views/chat/messageParts'
import {
  initialRunEventState,
  parseRunEvent,
  runEventReducer,
} from '@/views/chat/runEventReducer'
import { consumeRunStream } from '@/views/chat/useRunStreamClient'
import { createFrameHandlerTable } from '@/views/chat/useSSEStream'

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
const { now, start: startDurationTimer, stop: stopDurationTimer } = useTicker()
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
  // 审批挂起携带权威投影（中断点内容 + pending_hitl）：同步合成消息
  if (domain.type === 'approval-required' && domain.content) {
    upsertAssistant(domain.content)
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
    streamFailed.value = false
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
/**
 * 流式帧游标（durable 事件单调推进；快照重置）——与主聊天 lastSequence
 * 同一记账职责，作为重订阅的 after_sequence。
 */
const streamCursor = ref(0)
/** 断流自愈重试耗尽的可见失败（重连或新 run 清除；消灭静默卡「生成中」） */
const streamFailed = ref(false)
/**
 * 本流会话内已见终态（run.finished / 终态快照置位，活跃快照清除）：
 * 读循环不再因 runActive 为假提前退出——快照本身来自流首帧；
 * 终态后仅用于阻止无意义重连。
 */
let terminalSeen = false
const LIFECYCLE_EVENTS = new Set(['run.started', 'approval.required', 'approval.resumed', 'run.finished'])

/** 对流式 assistant 消息的 parts 应用一次投影变换（appenders 纯函数族）。 */
function mutateStreamingParts(apply: (parts: UiPart[]) => UiPart[], createSkeleton = false) {
  const assistantId = run.value?.assistant_message_id
  if (!assistantId) {
    return
  }
  let index = messages.value.findIndex((item) => item.id === assistantId)
  if (index === -1) {
    if (!createSkeleton) {
      return
    }
    // 首个帧先于任何边界投影：建流式骨架（upsertAssistant 的合成路径）
    upsertAssistant({ version: 1, parts: [] })
    index = messages.value.findIndex((item) => item.id === assistantId)
    if (index === -1) {
      return
    }
  }
  const parts = apply(normalizeApiContent(messages.value[index].content).parts)
  messages.value[index] = { ...messages.value[index], content: { version: 1, parts } }
}

/**
 * 共享帧分派表：帧词汇 → messageParts appenders（与主聊天 useSSEStream
 * 同一份映射与工具元数据富集）。内容投影与主聊天同一实现。
 */
const frameTable = createFrameHandlerTable({
  onTextDelta: (text, parent) => mutateStreamingParts((parts) => appendTextDelta(parts, text, parent), true),
  onReasoningDelta: (text, parent) => mutateStreamingParts((parts) => appendReasoningDelta(parts, text, parent), true),
  onToolCall: (name, args, toolCallId, parent, stepId) =>
    mutateStreamingParts((parts) => upsertToolInputPart(parts, toolCallId, name, args, parent, stepId), true),
  onToolResult: (toolCallId, payload) =>
    mutateStreamingParts((parts) => applyToolOutput(parts, toolCallId, payload)),
  onRetrievalResults: (part) => mutateStreamingParts((parts) => appendRetrievalPart(parts, part), true),
  onStatsUpdate: (stats) => applyEvent('stats-update', stats as unknown as Record<string, unknown>),
})

/** 快照重置：reducer run-snapshot + 内容整体 replace + 游标对齐。 */
function handleSnapshot(snapshot: AgentRunSnapshot) {
  const domain = parseRunEvent('run-snapshot', snapshot as unknown as Record<string, unknown>)
  if (domain) {
    reducerState.value = runEventReducer(reducerState.value, domain)
  }
  upsertAssistant(snapshot.content, snapshot)
  streamCursor.value = Number(snapshot.snapshot_sequence ?? 0)
  frameTable.reset()
  terminalSeen = ['completed', 'partial', 'error', 'interrupted'].includes(snapshot.status)
}

/**
 * 单帧分派（内核 onFrame）：transient 直接应用；durable 做序号记账与
 * gap 检测（gap → 'stop' 交内核走快照恢复）；生命周期事件走 reducer，
 * 其余帧走共享分派表（appenders）。
 */
function dispatchRunFrame(event: string, data: Record<string, unknown> | null, dataStr: string): 'stop' | void {
  if (data === null) {
    // [DONE] 传输层结束标记；终态判定只认 run.finished
    return
  }
  if (data.transient) {
    const type = String(data.type ?? event)
    frameTable.dispatch(type, data)
    return
  }
  const sequence = Number(data.sequence ?? 0)
  if (sequence > 0) {
    if (sequence <= streamCursor.value) {
      return
    }
    if (streamCursor.value > 0 && sequence !== streamCursor.value + 1) {
      // sequence gap：停止当前连接，内核经权威快照恢复
      return 'stop'
    }
    streamCursor.value = sequence
  }
  const type = String(data.type ?? event)
  if (type === 'run-snapshot') {
    handleSnapshot(data as unknown as AgentRunSnapshot)
    return
  }
  if (LIFECYCLE_EVENTS.has(type)) {
    if (type === 'run.finished') {
      terminalSeen = true
    }
    applyEvent(type, data)
    return
  }
  frameTable.dispatch(type, data)
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
  streamFailed.value = false
  terminalSeen = false
  try {
    await consumeRunStream({
      subscribe: (signal) => subscribeAgentRun(runId, streamCursor.value, signal),
      onFrame: dispatchRunFrame,
      // 代际失效或已见终态即安静退出（读循环不依赖 runActive——快照来自流首帧）
      isActive: () => serial === requestSerial && !terminalSeen,
      maxAttempts: 6,
      backoffMs: (attempt) => Math.min(800 * (attempt + 1), 4000),
      // 断流自愈：权威快照收口（终态即就地收尾），否则退避重订阅
      resync: async () => {
        const snapshot = await getAgentRun(runId)
        handleSnapshot(snapshot)
        if (['completed', 'partial', 'error', 'interrupted'].includes(snapshot.status)) {
          applyEvent('run.finished', {
            type: 'run.finished',
            status: snapshot.status,
            finished_at: snapshot.updated_at ?? Date.now(),
          })
        }
      },
      // 重试耗尽：可见失败（不再静默卡「生成中」），手动重连入口
      onExhausted: () => {
        if (serial === requestSerial) {
          streamFailed.value = true
        }
      },
      signal: controller.signal,
    })
  } catch (error) {
    if ((error as Error)?.name !== 'AbortError' && serial === requestSerial) {
      console.warn('[subagent] stream failed', error)
      streamFailed.value = true
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

/** 手动重连（可见失败后的重试入口）。 */
function retryStream() {
  if (activeRunId.value) {
    void consumeStream(activeRunId.value, requestSerial)
  }
}

async function decideHitl(payload: {
  decisions: Array<{ type: 'approve' | 'reject', message?: string }>
  grant_scope?: 'once' | 'session' | null
}) {
  if (!run.value?.run_id || !run.value.pending_hitl?.interrupt_id) {
    return
  }
  try {
    syncRunSnapshot(await resumeAgentRunHitl(run.value.run_id, {
      interrupt_id: run.value.pending_hitl.interrupt_id,
      decisions: payload.decisions,
      grant_scope: payload.grant_scope ?? 'once',
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
          <RunMetaLine
            v-if="messageElapsedText(message)"
            :elapsed="messageElapsedText(message)"
            :collapsible="shouldCollapseMessage(message)"
            :expanded="isMessageExpanded(message)"
            @toggle="toggleMessageCollapse(message)"
          />
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
          <AssistantToolFailureBlocker
            v-if="shouldShowAssistantToolFailureBlocker(normalizeApiContent(message.content).parts, runGenerating && message.status === 'streaming')"
          />
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
      <!-- 断流自愈重试耗尽的可见失败（不再静默卡「生成中」）：手动重连入口 -->
      <div v-if="streamFailed" class="subagent-conversation__stream-failed">
        <span>连接已中断，未能自动恢复</span>
        <button type="button" class="subagent-conversation__retry" @click="retryStream">重新连接</button>
      </div>
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
        <ResearchSourcesPanel :results="sessionSources" />
      </div>
      <!-- 审批卡：与主 Agent 同一 HitlComposerPanel（子会话恒可选会话级放行） -->
      <HitlComposerPanel
        v-if="run?.pending_hitl?.action_requests?.length"
        kind="approval"
        :action-requests="run.pending_hitl.action_requests"
        session-grant-policy="always"
        @submit="decideHitl"
      />
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
      <!-- 复用主 Agent 的 composer 容器：收窄为纯模型/档位工具栏（无附件/KB/MCP/Skills）；
           右槽组上下文环与单按钮（运行中且输入为空 = 停止；有内容 = 发送/入队） -->
      <ChatComposerToolbar
        v-model:model-id="selectedModelId"
        v-model:reasoning-effort="selectedReasoningEffort"
        qa-type="SUPER_AGENT_QA"
        :session-id="sessionId"
        :persist-session-extra="true"
        :disabled="followupSending"
        :show-tools-menu="false"
      >
        <template #right>
          <ContextWindowIndicator
            v-if="hasValidContextWindow(contextSnapshot)"
            :context="contextSnapshot as any"
          />
          <StopSendButton
            :stop-mode="composerStopMode"
            :send-disabled="sendDisabled"
            :stopping="run?.status === 'stopping'"
            testid-prefix="subagent-"
            @action="(kind) => (kind === 'stop' ? stopCurrentRun() : sendFollowup())"
          />
        </template>
      </ChatComposerToolbar>
    </div>
    <!-- 子会话统计行：与主 Agent 同位置（输入框下方）。usage 统计与主会话同口径
         （extra.usage 重建，终态随消息重载更新）；运行中尚无 usage 时以轮对话/
         步数/时长兜底；任务状态同区。置于输入框容器外，避免继承消息框底色 -->
    <SessionStatsLine
      v-if="statsLine"
      class="subagent-conversation__stats"
      :line="statsLine"
    />
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

.subagent-conversation__stats {
  margin-top: 2px;
}

/* 来源面板：回复末尾（与主 Agent 的回复工具栏 meta 区同位置语义） */
.subagent-conversation__sources {
  display: flex;
  justify-content: flex-start;
  padding: 4px 2px 0;
}


.subagent-conversation__stream-failed {
  display: flex;
  align-items: center;
  gap: 8px;
  justify-content: center;
  padding: 8px 0;
  color: var(--noesis-color-error, #d03050);
  font-size: 12px;
}

.subagent-conversation__retry {
  border: none;
  background: none;
  padding: 0;
  color: var(--noesis-color-primary, #18a058);
  font-size: 12px;
  cursor: pointer;
}

.subagent-conversation__retry:hover {
  text-decoration: underline;
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


/* 左右分区（与主 Agent ChatComposerToolbar 同构）：模型选择器靠左，
   上下文圆环与发送/停止按钮靠右 */


/* 停止态与主 Agent 同款：主色圆钮 + 白色方块 + 光环 */
@media (max-width: $bp-md) {
}
</style>
