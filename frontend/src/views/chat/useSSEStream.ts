/**
 * chat 页专用：解析 Noesis 标准 SSE（见 docs/prd/platform/SSE流式数据设计.md）
 * 通过回调与 chat.vue 现有 UI 逻辑对接。
 */

import { ref } from 'vue'
import { useUserStore } from '@/store/business/userStore'

export interface SSEStreamOptions {
  onTitleUpdate?: (title: string) => void
  onUsageUpdate?: (usage: { input_tokens: number, output_tokens: number, total_tokens?: number }) => void
  onTextDelta?: (text: string, parentTaskCallId?: string) => void
  onReasoningDelta?: (reasoning: string, parentTaskCallId?: string) => void
  onReasoningStart?: (data: Record<string, unknown>) => void
  onReasoningEnd?: (data: Record<string, unknown>) => void
  onToolCall?: (
    name: string,
    args: Record<string, unknown>,
    toolCallId: string,
    parentTaskCallId?: string,
  ) => void
  onToolResult?: (
    toolCallId: string,
    payload: { output: string, error?: string, status: 'success' | 'error', durationMs?: number },
  ) => void
  /** 测试用例等扩展 SSE（event 名与 data.type 一致） */
  onCustomEvent?: (eventType: string, data: Record<string, unknown>) => void
  /** message-start 帧（含 assistantMessageId、可选 langfuseSessionId） */
  onMessageStart?: (data: Record<string, unknown>) => void
  onFinish?: () => void
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
  let abortController: AbortController | null = null
  let removeBeforeUnload: (() => void) | null = null

  const toolNameByCallId = new Map<string, string>()

  function setupBeforeUnload(sessionId: string, qaType: string) {
    cleanupBeforeUnload()
    const handleBeforeUnload = () => {
      if (isLoading.value && sessionId) {
        const payload = JSON.stringify({ qa_type: qaType })
        navigator.sendBeacon(`/api/chat/sessions/${sessionId}/stop`, payload)
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
      onMessageStart?.(data)
      return
    }
    if (t === 'text-delta' && typeof data.textDelta === 'string') {
      const parentTaskCallId = typeof data.parentTaskCallId === 'string' && data.parentTaskCallId.trim()
        ? data.parentTaskCallId.trim()
        : undefined
      onTextDelta?.(data.textDelta, parentTaskCallId)
      return
    }
    if (t === 'reasoning-start') {
      onReasoningStart?.(data)
      return
    }
    if (t === 'reasoning-delta' && typeof data.textDelta === 'string') {
      const parentTaskCallId = typeof data.parentTaskCallId === 'string' && data.parentTaskCallId.trim()
        ? data.parentTaskCallId.trim()
        : undefined
      onReasoningDelta?.(data.textDelta, parentTaskCallId)
      return
    }
    if (t === 'reasoning-end') {
      onReasoningEnd?.(data)
      return
    }
    if (t === 'tool-input-start') {
      const id = String(data.toolCallId ?? '')
      const name = String(data.toolName ?? '')
      if (id) {
        toolNameByCallId.set(id, name)
      }
      return
    }
    if (t === 'tool-input-available') {
      const id = String(data.toolCallId ?? '')
      const nameFromFrame = typeof data.toolName === 'string' ? data.toolName : ''
      const name = nameFromFrame || toolNameByCallId.get(id) || ''
      if (id && nameFromFrame) {
        toolNameByCallId.set(id, nameFromFrame)
      }
      const input = (data.input as Record<string, unknown>) || {}
      const parentTaskCallId = typeof data.parentTaskCallId === 'string' && data.parentTaskCallId.trim()
        ? data.parentTaskCallId.trim()
        : undefined
      onToolCall?.(name, input, id, parentTaskCallId)
      return
    }
    if (t === 'tool-output-available') {
      const id = String(data.toolCallId ?? '')
      const status = String(data.status ?? 'success')
      const out = typeof data.output === 'string' ? data.output : ''
      const err = data.error != null ? String(data.error) : ''
      const durationMs = data.durationMs != null ? Number(data.durationMs) : undefined
      onToolResult?.(id, {
        output: out,
        error: err || undefined,
        status: status === 'error' ? 'error' : 'success',
        durationMs: durationMs != null && !Number.isNaN(durationMs) ? durationMs : undefined,
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
      const finishReason = String(data.finishReason ?? data.finish_reason ?? 'stop')
      if (finishReason === 'error') {
        const errMsg = typeof data.error === 'string' && data.error.trim()
          ? data.error.trim()
          : '生成失败'
        settleFailure(errMsg)
        return
      }
      settleSuccess()
      return
    }
    if (t === 'error') {
      const msg = String(data.error ?? '请求失败')
      settleFailure(msg)
      return
    }
    if (t === 'abort') {
      settleSuccess()
    }
  }

  let streamSettled = false
  function settleSuccess() {
    if (streamSettled) {
      return
    }
    streamSettled = true
    onFinish?.()
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

    toolNameByCallId.clear()
    error.value = null
    streamSettled = false
    isLoading.value = true

    const qaType = (extra?.qa_type as string) || 'COMMON_QA'
    setupBeforeUnload(sessionId, qaType)
    abortController = new AbortController()

    try {
      const userStore = useUserStore()
      const token = userStore.getUserToken()

      const res = await fetch('/api/chat/sessions/stream', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${token ?? ''}`,
        },
        body: JSON.stringify({
          session_id: sessionId,
          content,
          extra: extra || {},
        }),
        signal: abortController.signal,
      })

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

      // 连接已关闭：若末尾帧没有以 \n\n 结束，会留在 rawBuffer 中从未被解析
      if (rawBuffer.trim()) {
        const flush = `${rawBuffer}\n\n`
        const { frames: tailFrames } = parseSseFrames(flush)
        for (const frame of tailFrames) {
          parseAndDispatchFrame(frame, dispatchFrame)
        }
        rawBuffer = ''
      }
      // 未收到 finish/abort/error/[DONE] 时仍结束加载（例如上游漏发 finish）
      settleSuccess()
    } catch (err: unknown) {
      const e = err as { name?: string, message?: string }
      if (e.name === 'AbortError') {
        settleSuccess()
      } else {
        error.value = e.message ?? '未知错误'
        settleFailure(e.message ?? '未知错误')
      }
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

    toolNameByCallId.clear()
    error.value = null
    streamSettled = false
    isLoading.value = true

    const qaType = 'TEST_CASE_QA'
    setupBeforeUnload(sessionId, qaType)
    abortController = new AbortController()

    try {
      const userStore = useUserStore()
      const token = userStore.getUserToken()

      const res = await fetch(`/api/chat/sessions/${sessionId}/test-case/resume`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${token ?? ''}`,
        },
        body: JSON.stringify({ selected_point_names: selectedPointNames }),
        signal: abortController.signal,
      })

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
      settleSuccess()
    } catch (err: unknown) {
      const e = err as { name?: string, message?: string }
      if (e.name === 'AbortError') {
        settleSuccess()
      } else {
        error.value = e.message ?? '未知错误'
        settleFailure(e.message ?? '未知错误')
      }
    } finally {
      isLoading.value = false
      abortController = null
      cleanupBeforeUnload()
    }
  }

  function stop() {
    abortController?.abort()
    abortController = null
  }

  return {
    isLoading,
    error,
    sendMessage,
    resumeTestCase,
    stop,
  }
}
