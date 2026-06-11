/**
 * chat 页专用：解析 Noesis 标准 SSE（见 docs/prd/platform/SSE流式数据设计.md）
 * 通过回调与 chat.vue 现有 UI 逻辑对接。
 */

import { ref } from 'vue'
import { applyRefreshToken, getAuthHeaders } from '@/utils/authHttp'

export interface SSEStreamOptions {
  onTitleUpdate?: (title: string) => void
  onUsageUpdate?: (usage: { input_tokens: number, output_tokens: number, total_tokens?: number }) => void
  onContextUpdate?: (context: { current_tokens: number, max_tokens: number, used_percentage: number }) => void
  onTextDelta?: (text: string, parent_task_call_id?: string) => void
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
    payload: { output: string, error?: string, status: 'success' | 'error', duration_ms?: number },
  ) => void
  /** 测试用例等扩展 SSE（event 名与 data.type 一致） */
  onCustomEvent?: (eventType: string, data: Record<string, unknown>) => void
  /** message-start 帧（含 assistant_message_id、可选 langfuse_session_id） */
  onMessageStart?: (data: Record<string, unknown>) => void
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
    onReasoningDelta,
    onReasoningStart,
    onReasoningEnd,
    onToolCall,
    onToolResult,
    onCustomEvent,
    onMessageStart,
    onFinish,
    onError,
  } = options

  const isLoading = ref(false)
  const error = ref<string | null>(null)
  const currentStopToken = ref<string | null>(null)
  let removeBeforeUnload: (() => void) | null = null
  let lastFinishReason: string | undefined
  let abortController: AbortController | null = null
  let userAborted = false

  const tool_name_by_call_id = new Map<string, string>()

  function setupBeforeUnload(sessionId: string, qaType: string) {
    cleanupBeforeUnload()
    const handleBeforeUnload = () => {
      if (isLoading.value && sessionId) {
        const payload = JSON.stringify({
          session_id: sessionId,
          qa_type: qaType,
          ...(currentStopToken.value ? { stop_token: currentStopToken.value } : {}),
        })
        const blob = new Blob([payload], { type: 'application/json' })
        navigator.sendBeacon(`/api/chat/sessions/${sessionId}/stop`, blob)
      }
    }
    window.addEventListener('beforeunload', handleBeforeUnload)
    removeBeforeUnload = () => window.removeEventListener('beforeunload', handleBeforeUnload)
  }

  function cleanupBeforeUnload() {
    removeBeforeUnload?.()
    removeBeforeUnload = null
  }

  function dispatchFrame(eventName: string, dataStr: string) {
    if (userAborted) {
      return
    }
    if (dataStr === '[DONE]') {
      settleSuccess()
      return
    }

    let data: Record<string, unknown>
    try {
      data = JSON.parse(dataStr) as Record<string, unknown>
    } catch {
      return
    }

    const t = (data.type as string) || eventName

    if (t === 'message-start') {
      const stopRaw = data.stop_token
      currentStopToken.value = typeof stopRaw === 'string' && stopRaw.trim()
        ? stopRaw.trim()
        : null
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
      onToolResult?.(id, {
        output: out,
        error: err || undefined,
        status: status === 'error' ? 'error' : 'success',
        duration_ms: duration_ms != null && !Number.isNaN(duration_ms) ? duration_ms : undefined,
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
    ) {
      onCustomEvent?.(t, data)
      return
    }
    if (t === 'finish') {
      const title = typeof data.title === 'string' ? data.title.trim() : ''
      if (title) {
        onTitleUpdate?.(title)
      }
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

  async function sendMessage(
    sessionId: string,
    content: string,
    extra?: Record<string, unknown>,
  ): Promise<void> {
    if (isLoading.value) {
      return
    }

    tool_name_by_call_id.clear()
    error.value = null
    currentStopToken.value = null
    streamSettled = false
    lastFinishReason = undefined
    userAborted = false
    abortController = new AbortController()
    isLoading.value = true

    const qaType = (extra?.qa_type as string) || 'COMMON_QA'
    setupBeforeUnload(sessionId, qaType)

    try {
      const res = await fetch('/api/chat/sessions/stream', {
        method: 'POST',
        credentials: 'include',
        signal: abortController.signal,
        headers: {
          'Content-Type': 'application/json',
          ...getAuthHeaders(),
        },
        body: JSON.stringify({
          session_id: sessionId,
          content,
          extra: extra || {},
        }),
      })
      applyRefreshToken(res)

      if (!res.ok) {
        const status = res.status
        let detail = `请求失败（HTTP ${status}）`
        if (status === 429) {
          detail = '请求过于频繁（429），请稍后再试'
        } else if (status === 401) {
          detail = '未授权（401），请重新登录'
        } else if (status === 503) {
          detail = '服务暂时不可用（503），请稍后再试'
        }
        throw new Error(detail)
      }

      const reader = res.body?.getReader()
      if (!reader) {
        throw new Error('无法读取响应流')
      }

      const decoder = new TextDecoder()
      let rawBuffer = ''

      while (true) {
        const { done, value } = await reader.read()
        if (value) {
          rawBuffer += decoder.decode(value, { stream: true })
        }
        const { frames, rest } = parseSseFrames(rawBuffer)
        rawBuffer = rest
        for (const frame of frames) {
          parseAndDispatchFrame(frame, dispatchFrame)
        }
        if (done) {
          break
        }
      }

      if (rawBuffer.trim()) {
        const flush = `${rawBuffer}\n\n`
        const { frames: tailFrames } = parseSseFrames(flush)
        for (const frame of tailFrames) {
          parseAndDispatchFrame(frame, dispatchFrame)
        }
        rawBuffer = ''
      }
      if (!userAborted) {
        settleSuccess()
      }
    } catch (err: unknown) {
      if (userAborted) {
        settleSuccess('stopped')
        return
      }
      const e = err as { message?: string, name?: string }
      error.value = e.message ?? '未知错误'
      settleFailure(e.message ?? '未知错误')
    } finally {
      isLoading.value = false
      abortController = null
      cleanupBeforeUnload()
    }
  }

  async function resumeTestCase(sessionId: string, selectedPointNames: string[]) {
    if (isLoading.value) {
      return
    }

    tool_name_by_call_id.clear()
    error.value = null
    currentStopToken.value = null
    streamSettled = false
    lastFinishReason = undefined
    userAborted = false
    abortController = new AbortController()
    isLoading.value = true

    const qaType = 'TEST_CASE_QA'
    setupBeforeUnload(sessionId, qaType)

    try {
      const res = await fetch(`/api/chat/sessions/${sessionId}/test-case/resume`, {
        method: 'POST',
        credentials: 'include',
        signal: abortController.signal,
        headers: {
          'Content-Type': 'application/json',
          ...getAuthHeaders(),
        },
        body: JSON.stringify({ selected_point_names: selectedPointNames }),
      })
      applyRefreshToken(res)

      if (!res.ok) {
        const status = res.status
        let detail = `请求失败（HTTP ${status}）`
        if (status === 429) {
          detail = '请求过于频繁（429），请稍后再试'
        } else if (status === 401) {
          detail = '未授权（401），请重新登录'
        } else if (status === 503) {
          detail = '服务暂时不可用（503），请稍后再试'
        }
        throw new Error(detail)
      }

      const reader = res.body?.getReader()
      if (!reader) {
        throw new Error('无法读取响应流')
      }

      const decoder = new TextDecoder()
      let rawBuffer = ''

      while (true) {
        const { done, value } = await reader.read()
        if (value) {
          rawBuffer += decoder.decode(value, { stream: true })
        }
        const { frames, rest } = parseSseFrames(rawBuffer)
        rawBuffer = rest
        for (const frame of frames) {
          parseAndDispatchFrame(frame, dispatchFrame)
        }
        if (done) {
          break
        }
      }

      if (rawBuffer.trim()) {
        const flush = `${rawBuffer}\n\n`
        const { frames: tailFrames } = parseSseFrames(flush)
        for (const frame of tailFrames) {
          parseAndDispatchFrame(frame, dispatchFrame)
        }
        rawBuffer = ''
      }
      if (!userAborted) {
        settleSuccess()
      }
    } catch (err: unknown) {
      if (userAborted) {
        settleSuccess('stopped')
        return
      }
      const e = err as { message?: string }
      error.value = e.message ?? '未知错误'
      settleFailure(e.message ?? '未知错误')
    } finally {
      isLoading.value = false
      abortController = null
      cleanupBeforeUnload()
    }
  }

  function abortStream() {
    if (!isLoading.value || userAborted) {
      return
    }
    userAborted = true
    abortController?.abort()
  }

  function getStopToken(): string | null {
    return currentStopToken.value
  }

  return {
    isLoading,
    error,
    sendMessage,
    resumeTestCase,
    abortStream,
    getStopToken,
  }
}
