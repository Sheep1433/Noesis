/**
 * chat 页专用：创建可恢复 run，并解析带 sequence 的 Noesis SSE。
 * 通过回调与 chat.vue 现有 UI 逻辑对接。
 */

import type { AgentRunSnapshot } from '@/api/chat'
import type { ToolLifecycleState } from '@/views/chat/messageParts'
import { ref } from 'vue'
import {
  createAgentRun,
  getAgentRun,
  resumeAgentRunHitl,
  resumeAgentRunTestCase,
  stopAgentRun,
  subscribeAgentRun,
} from '@/api/chat'

export interface SSEStreamOptions {
  onTitleUpdate?: (title: string) => void
  onUsageUpdate?: (usage: { input_tokens: number, output_tokens: number, total_tokens?: number }) => void
  onContextUpdate?: (context: { current_tokens: number, max_tokens: number, used_percentage: number }) => void
  onTextDelta?: (text: string, parent_task_call_id?: string) => void
  onTextAnnotation?: (textPartId: string, annotation: Record<string, unknown>) => void
  onRetrievalResults?: (part: Record<string, unknown>) => void
  onReasoningDelta?: (reasoning: string, parent_task_call_id?: string) => void
  onReasoningStart?: (data: Record<string, unknown>) => void
  onReasoningEnd?: (data: Record<string, unknown>) => void
  onToolCall?: (
    name: string,
    args: Record<string, unknown>,
    tool_call_id: string,
    parent_task_call_id?: string,
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
    },
  ) => void
  /** 测试用例等扩展 SSE（event 名与 data.type 一致） */
  onCustomEvent?: (eventType: string, data: Record<string, unknown>) => void
  /** message-start 帧（含 assistant_message_id、可选 langfuse_session_id） */
  onMessageStart?: (data: Record<string, unknown>) => void
  onSnapshot?: (snapshot: AgentRunSnapshot) => void
  onRunStatus?: (status: string, message?: string) => void
  onFinish?: (detail?: { finish_reason?: string }) => void
  onError?: (msg: string) => void
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
    onUsageUpdate,
    onContextUpdate,
    onTextDelta,
    onTextAnnotation,
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
    onFinish,
    onError,
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

  const tool_name_by_call_id = new Map<string, string>()

  function isCurrentStream(generation: number) {
    return generation === streamGeneration
  }

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

    const t = (data.type as string) || eventName

    if (t === 'run-snapshot') {
      const snapshot = data as unknown as AgentRunSnapshot
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

    if (t === 'run-status') {
      const status = String(data.status ?? 'running')
      onRunStatus?.(status, typeof data.message === 'string' ? data.message : undefined)
      return
    }

    if (t === 'message-start') {
      onMessageStart?.(data)
      return
    }
    if (t === 'text-delta' && typeof data.text_delta === 'string') {
      const parent_task_call_id = typeof data.parent_task_call_id === 'string' && data.parent_task_call_id.trim()
        ? data.parent_task_call_id.trim()
        : undefined
      onTextDelta?.(data.text_delta, parent_task_call_id)
      return
    }
    if (t === 'text-annotation-added' && data.annotation && typeof data.annotation === 'object') {
      onTextAnnotation?.(String(data.text_part_id ?? ''), data.annotation as Record<string, unknown>)
      return
    }
    if (t === 'retrieval-results-available') {
      onRetrievalResults?.({ ...data, type: 'retrieval' })
      return
    }
    if (t === 'reasoning-start') {
      onReasoningStart?.(data)
      return
    }
    if (t === 'reasoning-delta' && typeof data.text_delta === 'string') {
      const parent_task_call_id = typeof data.parent_task_call_id === 'string' && data.parent_task_call_id.trim()
        ? data.parent_task_call_id.trim()
        : undefined
      onReasoningDelta?.(data.text_delta, parent_task_call_id)
      return
    }
    if (t === 'reasoning-end') {
      onReasoningEnd?.(data)
      return
    }
    if (t === 'tool-input-start') {
      const id = String(data.tool_call_id ?? '')
      const name = String(data.name ?? '')
      if (id) {
        tool_name_by_call_id.set(id, name)
      }
      return
    }
    if (t === 'tool-input-available') {
      const id = String(data.tool_call_id ?? '')
      const nameFromFrame = typeof data.name === 'string' ? data.name : ''
      const name = nameFromFrame || tool_name_by_call_id.get(id) || ''
      if (id && nameFromFrame) {
        tool_name_by_call_id.set(id, nameFromFrame)
      }
      const input = (data.input as Record<string, unknown>) || {}
      const parent_task_call_id = typeof data.parent_task_call_id === 'string' && data.parent_task_call_id.trim()
        ? data.parent_task_call_id.trim()
        : undefined
      onToolCall?.(name, input, id, parent_task_call_id)
      return
    }
    if (t === 'tool-output-available') {
      const id = String(data.tool_call_id ?? '')
      const status = String(data.status ?? 'success')
      const out = typeof data.output === 'string' ? data.output : ''
      const err = data.error != null ? String(data.error) : ''
      const duration_ms = data.duration_ms != null ? Number(data.duration_ms) : undefined
      const errorCategory = typeof data.errorCategory === 'string' && data.errorCategory.trim()
        ? data.errorCategory.trim()
        : undefined
      onToolResult?.(id, {
        output: out,
        error: err || undefined,
        status: status === 'error' ? 'error' : 'success',
        duration_ms: duration_ms != null && !Number.isNaN(duration_ms) ? duration_ms : undefined,
        errorCategory,
        state: typeof data.state === 'string' ? data.state as ToolLifecycleState : undefined,
        outcome: typeof data.outcome === 'string' ? data.outcome : undefined,
        exit_code: data.exit_code != null ? Number(data.exit_code) : undefined,
        timed_out: data.timed_out != null ? Boolean(data.timed_out) : undefined,
        truncated: data.truncated != null ? Boolean(data.truncated) : undefined,
      })
      return
    }
    if (t === 'usage-update') {
      const usage = data.usage as { input_tokens?: number, output_tokens?: number, total_tokens?: number } | undefined
      if (usage && (usage.input_tokens != null || usage.output_tokens != null)) {
        onUsageUpdate?.({
          input_tokens: Number(usage.input_tokens ?? 0),
          output_tokens: Number(usage.output_tokens ?? 0),
          total_tokens: usage.total_tokens != null ? Number(usage.total_tokens) : undefined,
        })
      }
      return
    }
    if (t === 'context-update') {
      const context = data.context as {
        current_tokens?: number
        max_tokens?: number
        used_percentage?: number
      } | undefined
      if (context && context.max_tokens != null && Number(context.max_tokens) > 0) {
        onContextUpdate?.({
          current_tokens: Number(context.current_tokens ?? 0),
          max_tokens: Number(context.max_tokens),
          used_percentage: Number(context.used_percentage ?? 0),
        })
      }
      return
    }
    if (
      t === 'scenario-start'
      || t === 'testpoints-confirm-required'
      || t === 'scene-cases'
      || t === 'phase-start'
      || t === 'phase-delta'
      || t === 'phase-end'
      || t === 'hitl-required'
    ) {
      onCustomEvent?.(t, {
        ...data,
        run_id: data.run_id ?? currentRunId,
        session_id: data.session_id ?? activeSessionId,
      })
      return
    }
    if (t === 'finish') {
      const usage = data.usage as { input_tokens?: number, output_tokens?: number, total_tokens?: number } | undefined
      if (usage && (usage.input_tokens != null || usage.output_tokens != null)) {
        onUsageUpdate?.({
          input_tokens: Number(usage.input_tokens ?? 0),
          output_tokens: Number(usage.output_tokens ?? 0),
          total_tokens: usage.total_tokens != null ? Number(usage.total_tokens) : undefined,
        })
      }
      const finish_reason = String(data.finish_reason ?? 'stop')
      lastFinishReason = finish_reason
      terminalObserved = finish_reason !== 'hitl_pending'
      if (finish_reason === 'hitl_pending') {
        onRunStatus?.('hitl_pending')
        return
      }
      if (finish_reason === 'error') {
        const errMsg = typeof data.error === 'string' && data.error.trim()
          ? data.error.trim()
          : '生成失败'
        settleFailure(errMsg)
        return
      }
      settleSuccess(finish_reason)
      return
    }
    if (t === 'error') {
      terminalObserved = true
      const msg = String(data.error ?? '请求失败')
      settleFailure(msg)
      return
    }
    if (t === 'abort') {
      // 等待后续 finish / [DONE]，不在此结束流
    }
  }

  let streamSettled = false
  function settleSuccess(finishReason?: string) {
    if (streamSettled) {
      return
    }
    streamSettled = true
    const reason = finishReason ?? lastFinishReason
    onFinish?.(reason ? { finish_reason: reason } : undefined)
  }
  function settleFailure(msg: string) {
    if (streamSettled) {
      return
    }
    streamSettled = true
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
        while (true) {
          if (streamSettled || userAborted) {
            break
          }
          const { done, value } = await reader.read()
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

  async function sendMessage(
    sessionId: string,
    content: string,
    extra?: Record<string, unknown>,
  ): Promise<void> {
    if (isLoading.value && activeSessionId === sessionId) {
      return
    }

    tool_name_by_call_id.clear()
    error.value = null
    const generation = beginStream(sessionId)
    const clientRequestId = crypto.randomUUID()

    try {
      let created
      try {
        created = await createAgentRun({
          session_id: sessionId,
          content,
          client_request_id: clientRequestId,
          extra: extra || {},
        })
      } catch {
        // 响应未知时只使用原幂等键重试一次，服务端会返回同一 run。
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
      if (isCurrentStream(generation)) {
        isLoading.value = false
        abortController = null
        if (streamSettled) {
          sessionStorage.removeItem(`noesis:active-run:${sessionId}`)
          currentRunId = null
        }
      }
    }
  }

  async function resumeActiveRun(sessionId: string): Promise<void> {
    if (isLoading.value && activeSessionId === sessionId) {
      return
    }
    const runId = sessionStorage.getItem(`noesis:active-run:${sessionId}`)
    if (!runId) {
      return
    }
    const generation = beginStream(sessionId)
    currentRunId = runId
    try {
      const snapshot = await getAgentRun(runId)
      dispatchFrame(
        'run-snapshot',
        JSON.stringify({ type: 'run-snapshot', ...snapshot }),
        generation,
      )
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
      if (isCurrentStream(generation)) {
        isLoading.value = false
        abortController = null
        if (streamSettled) {
          sessionStorage.removeItem(`noesis:active-run:${sessionId}`)
          currentRunId = null
        }
      }
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
    const runId = sessionStorage.getItem(`noesis:active-run:${sessionId}`)
      || (activeSessionId === sessionId ? currentRunId : null)
    if (!runId) {
      throw new Error('当前任务已中断，无法继续确认')
    }
    currentRunId = runId
    const snapshot = await resumeAgentRunHitl(runId, body)
    dispatchFrame('run-snapshot', JSON.stringify({ type: 'run-snapshot', ...snapshot }))
    // 审批时原订阅可能已因网络中断而退出。POST 只恢复 producer，不会自动
    // 恢复浏览器订阅，因此此处在没有活跃 followRun 时重新订阅。
    if ((!isLoading.value || activeSessionId !== sessionId) && !streamSettled) {
      void resumeActiveRun(sessionId)
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
    terminalObserved = true
    onSnapshot?.(snapshot)
    settleSuccess(snapshot.finish_reason ?? 'stopped')
    isLoading.value = false
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
  }
}
