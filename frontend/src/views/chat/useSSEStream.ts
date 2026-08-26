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

function parseSseFrames(buffer: string): { frames: string[], rest: string } {
  const parts = buffer.split('\n\n')
  const rest = parts.pop() ?? ''
  return { frames: parts.filter(Boolean), rest }
}

/** 解析单条 SSE frame（event + 多行 data）并交给 dispatchFrame */
function parseAndDispatchFrame(frame: string, dispatchFrame: (eventName: string, dataStr: string) => void) {
  let eventName = 'message'
  const dataLines: string[] = []
  for (const line of frame.split('\n')) {
    if (line.startsWith('event:')) {
      eventName = line.slice(6).trim()
    } else if (line.startsWith('data:')) {
      dataLines.push(line.slice(5).trimStart())
    }
  }
  const dataStr = dataLines.join('\n')
  dispatchFrame(eventName, dataStr)
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
  let sequenceGap = false
  let terminalObserved = false
  /** 409 撞上「会话仍在生成」时排队的消息：本轮终态后自动重发 */
  let queuedSend: { sessionId: string, content: string, extra?: Record<string, unknown> } | null = null
  let signalSessionId: string | null = null
  let signalAbort: AbortController | null = null
  let signalRetryTimer: ReturnType<typeof setTimeout> | null = null

  const tool_name_by_call_id = new Map<string, string>()
  const tool_step_id_by_call_id = new Map<string, string | undefined>()

  function isCurrentStream(generation: number) {
    return generation === streamGeneration
  }

  function parentTaskCallId(data: Record<string, unknown>): string | undefined {
    const value = data.parent_task_call_id
    return typeof value === 'string' && value.trim() ? value.trim() : undefined
  }

  function handleRunSnapshot(snapshot: AgentRunSnapshot) {
    lastSequence = Number(snapshot.snapshot_sequence ?? 0)
    sequenceGap = false
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

  function handleToolInputStart(data: Record<string, unknown>) {
    const id = String(data.tool_call_id ?? '')
    if (!id) {
      return
    }
    tool_name_by_call_id.set(id, String(data.name ?? ''))
    tool_step_id_by_call_id.set(id, typeof data.step_id === 'string' ? data.step_id : undefined)
  }

  function handleToolInputAvailable(data: Record<string, unknown>) {
    const id = String(data.tool_call_id ?? '')
    const nameFromFrame = typeof data.name === 'string' ? data.name : ''
    const name = nameFromFrame || tool_name_by_call_id.get(id) || ''
    if (id && nameFromFrame) {
      tool_name_by_call_id.set(id, nameFromFrame)
    }
    const stepIdRaw = data.step_id
    let stepId: string | undefined
    if (typeof stepIdRaw === 'string' && stepIdRaw) {
      stepId = stepIdRaw
    } else if (id) {
      stepId = tool_step_id_by_call_id.get(id)
    }
    if (id && typeof stepIdRaw === 'string' && stepIdRaw) {
      tool_step_id_by_call_id.set(id, stepIdRaw)
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
      stepId = tool_step_id_by_call_id.get(id)
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

  function handleFinish(data: Record<string, unknown>) {
    const finishReason = String(data.finish_reason ?? 'stop')
    lastFinishReason = finishReason
    terminalObserved = finishReason !== 'hitl_pending'
    if (finishReason === 'hitl_pending') {
      onRunStatus?.('hitl_pending')
    } else if (finishReason === 'error') {
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
  const customEventTypes = new Set([
    'scenario-start',
    'testpoints-confirm-required',
    'scene-cases',
    'phase-start',
    'phase-delta',
    'phase-end',
    'hitl-required',
  ])

  function dispatchFrame(eventName: string, dataStr: string, generation = streamGeneration) {
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
        sequenceGap = true
        return
      }
      lastSequence = sequence
    }

    const handler = frameHandlers[type]
    if (handler) {
      handler(data)
      return
    }
    if (customEventTypes.has(type)) {
      handleCustomEvent(type, data)
      return
    }
    if (type === 'finish') {
      handleFinish(data)
      return
    }
    if (type === 'error') {
      terminalObserved = true
      settleFailure(String(data.error ?? '请求失败'))
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
    for (let retry = 0; retry <= 5; retry += 1) {
      if (!isCurrentStream(generation) || streamSettled || userAborted) {
        break
      }
      sequenceGap = false
      try {
        const res = await subscribeAgentRun(runId, lastSequence, abortController?.signal)
        if (!res.ok) {
          throw new Error(`连接失败（HTTP ${res.status}）`)
        }
        const reader = res.body?.getReader()
        if (!reader) {
          throw new Error('无法读取响应流')
        }
        const decoder = new TextDecoder()
        let rawBuffer = ''
        // 读超时：防止 TCP half-open 导致 reader.read() 永久挂住。
        // 超时后 break 出 while，让 followRun 重连查 getAgentRun 拿终态。
        const READ_TIMEOUT_MS = 45_000
        const READ_TIMEOUT = Symbol('read-timeout')
        while (true) {
          if (streamSettled || userAborted) {
            break
          }
          let timer: ReturnType<typeof setTimeout> | undefined
          const result = await Promise.race([
            reader.read().then((r) => {
              clearTimeout(timer)
              return r
            }),
            new Promise<typeof READ_TIMEOUT>((resolve) => {
              timer = setTimeout(() => resolve(READ_TIMEOUT), READ_TIMEOUT_MS)
            }),
          ])
          if (result === READ_TIMEOUT) {
            await reader.cancel()
            break
          }
          const { done, value } = result
          if (value) {
            rawBuffer += decoder.decode(value, { stream: true })
          }
          const { frames, rest } = parseSseFrames(rawBuffer)
          rawBuffer = rest
          for (const frame of frames) {
            parseAndDispatchFrame(
              frame,
              (eventName, dataStr) => dispatchFrame(eventName, dataStr, generation),
            )
            if (sequenceGap) {
              break
            }
          }
          if (done || sequenceGap) {
            if (sequenceGap) {
              await reader.cancel()
            }
            break
          }
        }
      } catch (streamError) {
        if (!isCurrentStream(generation) || userAborted) {
          break
        }
        if (retry >= 5) {
          throw streamError
        }
      }
      if (!isCurrentStream(generation) || streamSettled || userAborted) {
        break
      }
      const snapshot = await getAgentRun(runId)
      dispatchFrame(
        'run-snapshot',
        JSON.stringify({ type: 'run-snapshot', ...snapshot }),
        generation,
      )
      if (streamSettled) {
        break
      }
      if (retry < 5) {
        const delay = Math.min(8000, 500 * (2 ** retry)) + Math.floor(Math.random() * 250)
        await new Promise((resolve) => setTimeout(resolve, delay))
      }
    }
    if (isCurrentStream(generation) && !streamSettled && !userAborted) {
      throw new Error('连接已中断，请重新连接')
    }
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
    sequenceGap = false
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

    tool_name_by_call_id.clear()
    tool_step_id_by_call_id.clear()
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
    const snapshot = await stopAgentRun(runId)
    onSnapshot?.(snapshot)
    if (['completed', 'partial', 'error', 'interrupted'].includes(snapshot.status)) {
      terminalObserved = true
      if (snapshot.status === 'error') {
        settleFailure(snapshot.message ?? '生成失败')
      } else {
        settleSuccess(snapshot.finish_reason ?? 'stopped')
      }
      isLoading.value = false
      return
    }

    // cancel grace 内终态持久化尚未完成时，不能把 running snapshot 当作成功。
    // 建立新订阅，等待服务端权威 terminal transaction 后再收尾。
    userAborted = false
    abortController = new AbortController()
    onRunStatus?.('running', '正在停止')
    await followRun(runId, streamGeneration)
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
    void pumpSessionSignals(sessionId, 0)
  }

  function stopSessionSignals() {
    if (signalRetryTimer !== null) {
      clearTimeout(signalRetryTimer)
      signalRetryTimer = null
    }
    signalAbort?.abort()
    signalAbort = null
    signalSessionId = null
    queuedSend = null
  }

  async function pumpSessionSignals(sessionId: string, attempt: number) {
    if (signalSessionId !== sessionId) {
      return
    }
    const controller = new AbortController()
    signalAbort = controller
    try {
      const res = await subscribeSessionEvents(sessionId, controller.signal)
      if (!res.ok) {
        // 401/404：登录失效或会话已删，重连无意义，静默退出
        if (res.status === 401 || res.status === 404) {
          if (signalAbort === controller) {
            signalAbort = null
          }
          return
        }
        throw new Error(`连接失败（HTTP ${res.status}）`)
      }
      const reader = res.body?.getReader()
      if (!reader) {
        throw new Error('无法读取响应流')
      }
      const decoder = new TextDecoder()
      let rawBuffer = ''
      const READ_TIMEOUT_MS = 45_000
      const READ_TIMEOUT = Symbol('read-timeout')
      for (;;) {
        // 会话已切走：停止消费（signalSessionId 由 stopSessionSignals/watchSessionSignals 修改）
        if (signalSessionId !== sessionId) {
          break
        }
        let timer: ReturnType<typeof setTimeout> | undefined
        const result = await Promise.race([
          reader.read().then((r) => {
            clearTimeout(timer)
            return r
          }),
          new Promise<typeof READ_TIMEOUT>((resolve) => {
            timer = setTimeout(() => resolve(READ_TIMEOUT), READ_TIMEOUT_MS)
          }),
        ])
        if (result === READ_TIMEOUT) {
          await reader.cancel()
          break
        }
        const { done, value } = result
        if (done) {
          break
        }
        rawBuffer += decoder.decode(value, { stream: true })
        const { frames, rest } = parseSseFrames(rawBuffer)
        rawBuffer = rest
        for (const frame of frames) {
          parseAndDispatchFrame(frame, (eventName, dataStr) => {
            if (eventName !== 'session-signal' || !dataStr) {
              return
            }
            try {
              const signal = JSON.parse(dataStr) as { type?: string, run_id?: string }
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
            } catch {
              // 非 JSON 帧忽略：信令是 hint，坏帧无害
            }
          })
        }
      }
      // 只清理自己这一代的 controller：新一代 pump 可能已接管 signalAbort
      if (signalAbort === controller) {
        signalAbort = null
      }
    } catch {
      if (signalAbort === controller) {
        signalAbort = null
      }
    }
    // 断线重连：仍在该会话则退避重试（信令丢失靠 active-run 自愈，退避可放宽）
    if (signalSessionId === sessionId) {
      const delay = Math.min(30_000, 3_000 * 2 ** Math.min(attempt, 3))
      signalRetryTimer = setTimeout(() => {
        signalRetryTimer = null
        void pumpSessionSignals(sessionId, attempt + 1)
      }, delay)
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
