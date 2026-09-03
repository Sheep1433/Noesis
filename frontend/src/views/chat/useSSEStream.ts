/**
 * chat 页专用：创建可恢复 run，并解析带 sequence 的 Noesis SSE。
 * 通过回调与 chat.vue 现有 UI 逻辑对接。
 */

import type { AgentRunSnapshot, AgentStopReason, ContextSnapshot } from '@/api/chat'
import type { ToolLifecycleState } from '@/views/chat/messageParts'
import { ref } from 'vue'
import {
  createAgentRun,
  getActiveRun,
  getAgentRun,
  resumeAgentRunHitl,
  resumeAgentRunTestCase,
  stopAgentRun,
  subscribeAgentRun,
  subscribeSessionEvents,
} from '@/api/chat'
import { consumeRunStream } from './useRunStreamClient'

export interface SSEStreamOptions {
  onTitleUpdate?: (title: string) => void
  onContextUpdate?: (context: ContextSnapshot) => void
  onTextDelta?: (text: string, parent_task_call_id?: string) => void
  onRetrievalResults?: (part: Record<string, unknown>) => void
  onReasoningDelta?: (reasoning: string, parent_task_call_id?: string) => void
  onReasoningStart?: (data: Record<string, unknown>) => void
  onReasoningEnd?: (data: Record<string, unknown>) => void
  onToolCall?: (
    name: string,
    args: Record<string, unknown>,
    tool_call_id: string,
    parent_task_call_id?: string,
    step_id?: string,
  ) => void
  onToolResult?: (
    tool_call_id: string,
    payload: {
      output: string
      error?: string
      status: 'success' | 'error'
      duration_ms?: number
      errorCategory?: string
      state?: ToolLifecycleState
      outcome?: string
      exit_code?: number
      timed_out?: boolean
      truncated?: boolean
      step_id?: string
    },
  ) => void
  /** 测试用例等扩展 SSE（event 名与 data.type 一致） */
  onCustomEvent?: (eventType: string, data: Record<string, unknown>) => void
  /** message-start 帧（含 assistant_message_id、可选 langfuse_session_id） */
  onMessageStart?: (data: Record<string, unknown>) => void
  onSnapshot?: (snapshot: AgentRunSnapshot) => void
  onRunStatus?: (status: string, message?: string) => void
  onStatsUpdate?: (stats: Record<string, unknown>) => void
  onFinish?: (detail?: { finish_reason?: AgentStopReason }) => void
  onError?: (msg: string) => void
  /** 发送撞上「会话仍在生成」（409 加入已有 run）时通知宿主：消息已排队，本轮结束后自动重发 */
  onBusyConflict?: () => void
  /** 取某会话历史加载中的 promise；信令触发的加入须等历史就位再 apply snapshot，防止 patch 丢失 */
  historyReady?: (sessionId: string) => Promise<unknown> | null
}


function parentTaskCallId(data: Record<string, unknown>): string | undefined {
  const value = data.parent_task_call_id
  return typeof value === 'string' && value.trim() ? value.trim() : undefined
}

/**
 * 帧词汇 → 回调的共享分派表：主聊天 useSSEStream 与子会话视图共用同一
 * 份映射与工具元数据富集（tool-input-start 记名/step → available/output
 * 补齐）。跨流（新 run）须 reset() 清富集缓存。
 */
export function createFrameHandlerTable(handlers: SSEStreamOptions) {
  const {
    onRunStatus,
    onMessageStart,
    onTextDelta,
    onRetrievalResults,
    onReasoningStart,
    onReasoningDelta,
    onReasoningEnd,
    onToolCall,
    onToolResult,
    onContextUpdate,
    onStatsUpdate,
  } = handlers
  const toolNameByCallId = new Map<string, string>()
  const toolStepIdByCallId = new Map<string, string | undefined>()

  function handleToolInputStart(data: Record<string, unknown>) {
    const id = String(data.tool_call_id ?? '')
    if (!id) {
      return
    }
    toolNameByCallId.set(id, String(data.name ?? ''))
    toolStepIdByCallId.set(id, typeof data.step_id === 'string' ? data.step_id : undefined)
  }

  function handleToolInputAvailable(data: Record<string, unknown>) {
    const id = String(data.tool_call_id ?? '')
    const nameFromFrame = typeof data.name === 'string' ? data.name : ''
    const name = nameFromFrame || toolNameByCallId.get(id) || ''
    if (id && nameFromFrame) {
      toolNameByCallId.set(id, nameFromFrame)
    }
    const stepIdRaw = data.step_id
    let stepId: string | undefined
    if (typeof stepIdRaw === 'string' && stepIdRaw) {
      stepId = stepIdRaw
    } else if (id) {
      stepId = toolStepIdByCallId.get(id)
    }
    if (id && typeof stepIdRaw === 'string' && stepIdRaw) {
      toolStepIdByCallId.set(id, stepIdRaw)
    }
    onToolCall?.(
      name,
      (data.input as Record<string, unknown>) || {},
      id,
      parentTaskCallId(data),
      stepId,
    )
  }

  function handleToolOutput(data: Record<string, unknown>) {
    const id = String(data.tool_call_id ?? '')
    const duration = data.duration_ms != null ? Number(data.duration_ms) : undefined
    const errorCategory = typeof data.errorCategory === 'string' && data.errorCategory.trim()
      ? data.errorCategory.trim()
      : undefined
    const stepIdRaw = data.step_id
    let stepId: string | undefined
    if (typeof stepIdRaw === 'string' && stepIdRaw) {
      stepId = stepIdRaw
    } else if (id) {
      stepId = toolStepIdByCallId.get(id)
    }
    onToolResult?.(id, {
      output: typeof data.output === 'string' ? data.output : '',
      error: data.error != null ? String(data.error) || undefined : undefined,
      status: String(data.status ?? 'success') === 'error' ? 'error' : 'success',
      duration_ms: duration != null && !Number.isNaN(duration) ? duration : undefined,
      errorCategory,
      state: typeof data.state === 'string' ? data.state as ToolLifecycleState : undefined,
      outcome: typeof data.outcome === 'string' ? data.outcome : undefined,
      exit_code: data.exit_code != null ? Number(data.exit_code) : undefined,
      timed_out: data.timed_out != null ? Boolean(data.timed_out) : undefined,
      truncated: data.truncated != null ? Boolean(data.truncated) : undefined,
      step_id: stepId,
    })
  }

  const frameHandlers: Record<string, (data: Record<string, unknown>) => void> = {
    'run-status': (data) => {
      const message = typeof data.message === 'string' ? data.message : undefined
      onRunStatus?.(String(data.status ?? 'running'), message)
    },
    'message-start': (data) => onMessageStart?.(data),
    'text-delta': (data) => {
      if (typeof data.text_delta === 'string') {
        onTextDelta?.(data.text_delta, parentTaskCallId(data))
      }
    },
    'retrieval-results-available': (data) => onRetrievalResults?.({ ...data, type: 'retrieval' }),
    'reasoning-start': (data) => onReasoningStart?.(data),
    'reasoning-delta': (data) => {
      if (typeof data.text_delta === 'string') {
        onReasoningDelta?.(data.text_delta, parentTaskCallId(data))
      }
    },
    'reasoning-end': (data) => onReasoningEnd?.(data),
    'tool-input-start': handleToolInputStart,
    'tool-input-available': handleToolInputAvailable,
    'tool-output-available': handleToolOutput,
    'context-update': (data) => {
      const context = data.context as ContextSnapshot | undefined
      if (context && context.max_tokens != null && Number(context.max_tokens) > 0) {
        onContextUpdate?.({
          current_tokens: Number(context.current_tokens ?? 0),
          max_tokens: Number(context.max_tokens),
          used_percentage: Number(context.used_percentage ?? 0),
        })
      }
    },
    'stats-update': (data) => {
      const stats = data as Record<string, unknown>
      if (stats && typeof stats.steps === 'number') {
        onStatsUpdate?.(stats)
      }
    },
  }

  return {
    /** 分派一帧（未知类型静默忽略）；返回是否命中已知帧 */
    dispatch(type: string, data: Record<string, unknown>): boolean {
      const handler = frameHandlers[type]
      if (handler) {
        handler(data)
        return true
      }
      return false
    },
    /** 新 run/新流开始：清工具元数据富集缓存 */
    reset() {
      toolNameByCallId.clear()
      toolStepIdByCallId.clear()
    },
  }
}

export function useSSEStream(options: SSEStreamOptions = {}) {
  const {
    onTitleUpdate,
    onContextUpdate,
    onTextDelta,
    onRetrievalResults,
    onReasoningDelta,
    onReasoningStart,
    onReasoningEnd,
    onToolCall,
    onToolResult,
    onCustomEvent,
    onMessageStart,
    onSnapshot,
    onRunStatus,
    onStatsUpdate,
    onFinish,
    onError,
    onBusyConflict,
    historyReady,
  } = options

  const isLoading = ref(false)
  const error = ref<string | null>(null)
  let lastFinishReason: string | undefined
  let abortController: AbortController | null = null
  let activeSessionId: string | null = null
  let streamGeneration = 0
  let userAborted = false
  let currentRunId: string | null = null
  let lastSequence = 0
  let terminalObserved = false
  /** 409 撞上「会话仍在生成」时排队的消息：本轮终态后自动重发 */
  let queuedSend: { sessionId: string, content: string, extra?: Record<string, unknown> } | null = null
  let signalSessionId: string | null = null
  let signalAbort: AbortController | null = null

  const frameTable = createFrameHandlerTable(options)

  function isCurrentStream(generation: number) {
    return generation === streamGeneration
  }

  function handleRunSnapshot(snapshot: AgentRunSnapshot) {
    lastSequence = Number(snapshot.snapshot_sequence ?? 0)
    onSnapshot?.(snapshot)
    onRunStatus?.(snapshot.status, snapshot.message ?? undefined)
    if (snapshot.status === 'hitl_pending' && snapshot.pending_hitl) {
      onCustomEvent?.('hitl-required', {
        type: 'hitl-required',
        ...snapshot.pending_hitl,
        run_id: snapshot.run_id,
        session_id: snapshot.session_id,
      })
    }
    if (['completed', 'partial', 'error', 'interrupted'].includes(snapshot.status)) {
      terminalObserved = true
      if (snapshot.status === 'error') {
        settleFailure(snapshot.message || '生成失败')
      } else {
        settleSuccess(snapshot.finish_reason ?? snapshot.status)
      }
    }
  }

  function handleRunFinished(data: Record<string, unknown>) {
    // 终态词汇统一：run.finished（status=completed/interrupted/error）为唯一
    // 流终止标记；hitl 分段暂停走 run-status（非终态）
    const status = String(data.status ?? 'completed')
    const finishReason = String(data.finish_reason ?? 'stop')
    lastFinishReason = finishReason
    if (status === 'hitl_pending') {
      onRunStatus?.('hitl_pending')
      return
    }
    terminalObserved = true
    if (status === 'error') {
      const error = typeof data.error === 'string' && data.error.trim() ? data.error.trim() : '生成失败'
      settleFailure(error)
    } else if (['context_exhausted', 'retryable_error'].includes(finishReason)) {
      settleFailure(finishReason)
    } else {
      settleSuccess(finishReason)
    }
  }

  function handleCustomEvent(type: string, data: Record<string, unknown>) {
    onCustomEvent?.(type, {
      ...data,
      run_id: data.run_id ?? currentRunId,
      session_id: data.session_id ?? activeSessionId,
    })
  }

  const customEventTypes = new Set([
    'scenario-start',
    'testpoints-confirm-required',
    'scene-cases',
    'phase-start',
    'phase-delta',
    'phase-end',
    'hitl-required',
  ])

  function dispatchFrame(eventName: string, dataStr: string, generation = streamGeneration): 'stop' | void {
    if (!isCurrentStream(generation) || userAborted) {
      return
    }
    if (dataStr === '[DONE]') {
      if (terminalObserved) {
        settleSuccess()
      }
      return
    }

    let data: Record<string, unknown>
    try {
      data = JSON.parse(dataStr) as Record<string, unknown>
    } catch {
      return
    }

    const type = (data.type as string) || eventName
    if (type === 'run-snapshot') {
      handleRunSnapshot(data as unknown as AgentRunSnapshot)
      return
    }
    const sequence = Number(data.sequence ?? 0)
    if (sequence > 0) {
      if (sequence <= lastSequence) {
        return
      }
      if (lastSequence > 0 && sequence !== lastSequence + 1) {
        // sequence gap：停止当前连接读取，交给内核走快照恢复流程
        return 'stop'
      }
      lastSequence = sequence
    }

    if (frameTable.dispatch(type, data)) {
      return
    }
    if (customEventTypes.has(type)) {
      handleCustomEvent(type, data)
      return
    }
    if (type === 'run.finished') {
      handleRunFinished(data)
    }
  }

  let streamSettled = false
  function settleSuccess(finishReason?: string) {
    if (streamSettled) {
      return
    }
    streamSettled = true
    isLoading.value = false
    const reason = finishReason ?? lastFinishReason
    onFinish?.(reason ? { finish_reason: reason } : undefined)
  }
  function settleFailure(msg: string) {
    if (streamSettled) {
      return
    }
    streamSettled = true
    isLoading.value = false
    onError?.(msg)
  }

  async function followRun(runId: string, generation: number): Promise<void> {
    await consumeRunStream({
      subscribe: (signal) => subscribeAgentRun(runId, lastSequence, signal),
      onFrame: (event, _data, dataStr) => dispatchFrame(event, dataStr, generation),
      isActive: () => isCurrentStream(generation) && !streamSettled && !userAborted,
      maxAttempts: 6,
      backoffMs: (attempt) => Math.min(8000, 500 * (2 ** attempt)) + Math.floor(Math.random() * 250),
      // 断流后先经权威快照收口（run-snapshot 终态即就地 settle），再退避重连
      resync: async () => {
        const snapshot = await getAgentRun(runId)
        dispatchFrame(
          'run-snapshot',
          JSON.stringify({ type: 'run-snapshot', ...snapshot }),
          generation,
        )
      },
      signal: abortController?.signal,
    })
  }

  function detachSubscription() {
    streamGeneration += 1
    const controller = abortController
    abortController = null
    activeSessionId = null
    currentRunId = null
    isLoading.value = false
    userAborted = false
    controller?.abort()
  }

  function beginStream(sessionId: string) {
    detachSubscription()
    activeSessionId = sessionId
    streamSettled = false
    lastFinishReason = undefined
    userAborted = false
    abortController = new AbortController()
    isLoading.value = true
    lastSequence = 0
    terminalObserved = false
    return streamGeneration
  }

  function finalizeSubscription(sessionId: string, generation: number) {
    if (!isCurrentStream(generation)) {
      return
    }
    isLoading.value = false
    abortController = null
    if (streamSettled) {
      sessionStorage.removeItem(`noesis:active-run:${sessionId}`)
      currentRunId = null
    }
  }

  async function sendMessage(
    sessionId: string,
    content: string,
    extra?: Record<string, unknown>,
  ): Promise<void> {
    if (isLoading.value && activeSessionId === sessionId) {
      return
    }

    frameTable.reset()
    error.value = null
    const generation = beginStream(sessionId)
    const clientRequestId = crypto.randomUUID()
    // 409 加入路径的排队消息是否待重发（见 finally）
    let pendingFlush = false

    try {
      let created
      try {
        created = await createAgentRun({
          session_id: sessionId,
          content,
          client_request_id: clientRequestId,
          extra: extra || {},
        })
      } catch (createErr) {
        // 409 冲突：同 session 已有 active Run，加入它而非当失败
        const conflictErr = createErr as Error & { conflictRunId?: string }
        if (conflictErr.conflictRunId) {
          currentRunId = conflictErr.conflictRunId
          sessionStorage.setItem(`noesis:active-run:${sessionId}`, conflictErr.conflictRunId)
          // 本条消息不会进入本轮 run（服务端未落库）；排队待本轮终态后自动重发
          queuedSend = { sessionId, content, extra }
          onBusyConflict?.()
          // 从服务端获取已有 Run 的 snapshot 并 replace
          const snapshot = await getAgentRun(conflictErr.conflictRunId)
          if (!isCurrentStream(generation)) {
            return
          }
          dispatchFrame(
            'run-snapshot',
            JSON.stringify({ type: 'run-snapshot', ...snapshot }),
            generation,
          )
          if (!streamSettled) {
            await followRun(conflictErr.conflictRunId, generation)
          }
          // 不能在这里直接 flush：isLoading 尚为 true（finalize 在 finally），
          // 内层 sendMessage 的防重守卫会静默吞掉重发。标记后在 finally 里执行。
          pendingFlush = true
          return
        }
        // 非 409：响应未知时只使用原幂等键重试一次，服务端会返回同一 run。
        created = await createAgentRun({
          session_id: sessionId,
          content,
          client_request_id: clientRequestId,
          extra: extra || {},
        })
      }
      if (!isCurrentStream(generation)) {
        return
      }
      // 命中斜杠命令：ephemeral 回复（不建 run、不落库），直接渲染文本后结束流。
      if ('command_reply' in created && created.command_reply) {
        onTextDelta?.(created.command_reply)
        settleSuccess('stop')
        return
      }
      currentRunId = created.run_id
      if (typeof created.session_title === 'string' && created.session_title.trim()) {
        onTitleUpdate?.(created.session_title.trim())
      }
      sessionStorage.setItem(`noesis:active-run:${sessionId}`, created.run_id)
      onMessageStart?.({
        type: 'message-start',
        run_id: created.run_id,
        assistant_message_id: created.assistant_message_id,
      })

      await followRun(created.run_id, generation)
    } catch (err: unknown) {
      if (!isCurrentStream(generation) || userAborted) {
        return
      }
      const e = err as { message?: string, name?: string }
      error.value = e.message ?? '未知错误'
      if (currentRunId && !terminalObserved) {
        // 订阅失败不等于 Agent 失败。保留 active run 与当前 parts，交给手动重连恢复。
        onRunStatus?.('disconnected', '连接已中断，可重新连接')
      } else {
        settleFailure(e.message ?? '未知错误')
      }
    } finally {
      finalizeSubscription(sessionId, generation)
      if (pendingFlush) {
        // 本轮已终态且流已收尾（isLoading=false）：重发排队消息。
        // followRun 抛异常（网络断）不 flush——本轮 run 服务端仍在跑，立即重发只会再 409。
        await flushQueuedSend()
      }
    }
  }

  async function resumeActiveRun(
    sessionId: string,
    beforeSnapshotApply?: Promise<unknown>,
  ): Promise<void> {
    if (isLoading.value && activeSessionId === sessionId) {
      return
    }
    const generation = beginStream(sessionId)
    // 优先从服务端发现 active Run（新 Tab、刷新页、断线恢复的权威来源）
    let snapshot: AgentRunSnapshot | null = null
    try {
      snapshot = await getActiveRun(sessionId)
      await beforeSnapshotApply
    } catch (err) {
      if (!isCurrentStream(generation)) {
        return
      }
      const message = err instanceof Error ? err.message : '连接恢复失败'
      error.value = message
      onRunStatus?.('disconnected', '连接已中断，可稍后重试')
      isLoading.value = false
      return
    }
    if (!isCurrentStream(generation)) {
      return
    }
    let runId: string | null
    if (snapshot) {
      runId = snapshot.run_id
      // 同步 sessionStorage 作为当前 Tab hint
      sessionStorage.setItem(`noesis:active-run:${sessionId}`, runId)
    } else {
      // 服务端确认无活跃 run：清除历史加载写入的 hint，避免残留 hint
      // 让已中断的未完成轮被误判为运行中
      sessionStorage.removeItem(`noesis:active-run:${sessionId}`)
      isLoading.value = false
      abortController = null
      return
    }
    currentRunId = runId
    try {
      if (snapshot) {
        dispatchFrame(
          'run-snapshot',
          JSON.stringify({ type: 'run-snapshot', ...snapshot }),
          generation,
        )
      }
      if (!streamSettled) {
        await followRun(runId, generation)
      }
    } catch (err) {
      if (!isCurrentStream(generation)) {
        return
      }
      const message = err instanceof Error ? err.message : '连接恢复失败'
      error.value = message
      onRunStatus?.('disconnected', '连接已中断，可稍后重试')
    } finally {
      finalizeSubscription(sessionId, generation)
    }
  }
  async function resumeTestCase(sessionId: string, selectedPointNames: string[]) {
    const runId = sessionStorage.getItem(`noesis:active-run:${sessionId}`)
      || (activeSessionId === sessionId ? currentRunId : null)
    if (!runId) {
      throw new Error('当前任务已中断，无法继续生成')
    }
    currentRunId = runId
    const snapshot = await resumeAgentRunTestCase(runId, selectedPointNames)
    dispatchFrame('run-snapshot', JSON.stringify({ type: 'run-snapshot', ...snapshot }))
    if ((!isLoading.value || activeSessionId !== sessionId) && !streamSettled) {
      void resumeActiveRun(sessionId)
    }
  }

  async function resumeHitl(
    sessionId: string,
    body: {
      interrupt_id: string
      decisions: Array<{ type: string, message?: string }>
      grant_scope?: 'once' | 'session' | null
    },
  ) {
    const active = await getActiveRun(sessionId)
    const runId = active?.run_id
      ?? (activeSessionId === sessionId ? currentRunId : null)
    if (!runId) {
      throw new Error('当前任务已中断，无法继续确认')
    }
    const needsNewSubscription = !isLoading.value || activeSessionId !== sessionId
    const generation = needsNewSubscription ? beginStream(sessionId) : streamGeneration
    currentRunId = runId
    const snapshot = await resumeAgentRunHitl(runId, body)
    dispatchFrame(
      'run-snapshot',
      JSON.stringify({ type: 'run-snapshot', ...snapshot }),
      generation,
    )
    // 审批时原订阅可能已因网络中断而退出。POST 只恢复 producer，不会自动
    // 恢复浏览器订阅，因此此处在没有活跃 followRun 时重新订阅。
    if (needsNewSubscription && !streamSettled) {
      void (async () => {
        try {
          await followRun(runId, generation)
        } catch (err) {
          if (!isCurrentStream(generation)) {
            return
          }
          const message = err instanceof Error ? err.message : '连接恢复失败'
          error.value = message
          onRunStatus?.('disconnected', '连接已中断，可稍后重试')
        } finally {
          finalizeSubscription(sessionId, generation)
        }
      })()
    }
  }

  function abortStream() {
    if (!isLoading.value || userAborted) {
      return
    }
    userAborted = true
    abortController?.abort()
  }

  async function stopCurrentRun() {
    if (!currentRunId) {
      abortStream()
      return
    }
    const runId = currentRunId
    userAborted = true
    abortController?.abort()
    // 乐观收尾：点击即终态——后端 stop 实测 1ms 内落 partial，API 往返
    // 是唯一延迟。不等 API：点击后立即让 UI 进入终态（消息收尾 + 停止
    // 标注），API 后台执行；失败时服务端协程仍在跑，用户可用停止重试
    terminalObserved = true
    settleSuccess('stopped')
    isLoading.value = false
    stopAgentRun(runId).catch(() => {})
  }

  /** 本轮终态后重发排队的消息；用户已切走会话或手动停止则丢弃 */
  async function flushQueuedSend() {
    const queued = queuedSend
    queuedSend = null
    if (!queued) {
      return
    }
    if (userAborted || queued.sessionId !== activeSessionId) {
      return
    }
    await sendMessage(queued.sessionId, queued.content, queued.extra)
  }

  /**
   * 会话信令流：run-started 时自动加入活跃 run（跨窗口/跨浏览器/跨设备发现）。
   *
   * 信令是 hint：连接建立时服务端先下发当前 active run，断线自动重连；
   * 收到 run-started 后经 resumeActiveRun 从权威端点取状态，本窗口正在
   * 流式中（isLoading 守卫）或已是同一 run 时跳过，不会重复订阅。
   */
  function watchSessionSignals(sessionId: string) {
    if (sessionId === signalSessionId) {
      return
    }
    stopSessionSignals()
    signalSessionId = sessionId
    void pumpSessionSignals(sessionId)
  }

  function stopSessionSignals() {
    // 无需清理重连 timer：传输内核的退避等待可被 abort 打断
    signalAbort?.abort()
    signalAbort = null
    signalSessionId = null
    queuedSend = null
  }

  async function pumpSessionSignals(sessionId: string) {
    const controller = new AbortController()
    signalAbort = controller
    try {
      await consumeRunStream({
        subscribe: (signal) => subscribeSessionEvents(sessionId, signal),
        onFrame: (event, data) => {
          if (event !== 'session-signal' || !data) {
            return
          }
          const signal = data as { type?: string, run_id?: string }
          if (
            signal.type === 'run-started'
            && typeof signal.run_id === 'string'
            && signal.run_id
            && signal.run_id !== currentRunId
            && !(isLoading.value && activeSessionId === sessionId)
          ) {
            // 该会话历史仍在加载时，等其就位再 apply snapshot（与刷新页路径一致），
            // 否则 patchAssistantPartsAt 找不到目标行，整轮内容静默丢失
            void resumeActiveRun(sessionId, historyReady?.(sessionId) ?? undefined)
          }
        },
        isActive: () => signalSessionId === sessionId,
        // 信令丢失靠 active-run 自愈，重连无限、退避放宽（封顶 30s）
        maxAttempts: Infinity,
        backoffMs: (attempt) => Math.min(30_000, 3_000 * 2 ** Math.min(attempt, 3)),
        // 401/404：登录失效或会话已删，重连无意义，静默退出
        fatalStatuses: [401, 404],
        signal: controller.signal,
      })
    } finally {
      if (signalAbort === controller) {
        signalAbort = null
      }
    }
  }

  return {
    isLoading,
    error,
    sendMessage,
    resumeTestCase,
    resumeHitl,
    abortStream,
    stopCurrentRun,
    detachSubscription,
    resumeActiveRun,
    watchSessionSignals,
    stopSessionSignals,
  }
}
