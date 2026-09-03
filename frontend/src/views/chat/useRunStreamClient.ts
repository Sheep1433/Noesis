/**
 * run 流传输内核：SSE 订阅消费 + 断流自愈。
 *
 * 主聊天 run 流（followRun）、会话信令流、子会话 run 流共用的唯一传输实现：
 * SSE 帧解析（CRLF / 多行 data / [DONE] / 注释帧）、读超时（半开连接保护）、
 * 有界退避重连、断流后权威快照收口钩子、代际失效检查。不含任何领域事件
 * 分派——事件词汇与终态判定由调用方经 onFrame 处理。
 */

export interface RunStreamTransportOptions {
  /** 建立订阅连接；内核在每次（重）连接时调用 */
  subscribe: (signal: AbortSignal) => Promise<Response>
  /**
   * 单帧分派：`data === null` 表示 [DONE] 流结束标记（终态判定只认领域
   * 终态事件）。返回 'stop' 立即断开当前连接进入恢复流程（sequence gap）。
   */
  onFrame: (event: string, data: Record<string, unknown> | null, dataStr: string) => 'stop' | void
  /** 是否仍应继续（代际 / 终态 / 用户中止）；false 时内核安静退出 */
  isActive: () => boolean
  /** 连接周期上限（含首次）；Infinity = 无限重连（信令流） */
  maxAttempts: number
  /** 第 attempt 次重连（0 起）前的退避毫秒 */
  backoffMs: (attempt: number) => number
  /**
   * 每次断流后、退避前调用：权威快照收口（内部自行经 onFrame 分派）。
   * 抛错向上传播、不重试——快照端点失败意味着恢复链路本身不可用。
   */
  resync?: () => Promise<void>
  /** 重试耗尽且仍 active 时的收口；缺省抛「连接已中断」 */
  onExhausted?: () => void
  /** 订阅返回这些状态码时永久退出（登录失效 / 会话已删，重连无意义） */
  fatalStatuses?: readonly number[]
  /** 读超时毫秒（半开连接保护），默认 45s */
  readTimeoutMs?: number
  /** 中止信号（用户切换 / 停止） */
  signal?: AbortSignal
}

/** 按 SSE 规范切帧：空行分隔（兼容 CRLF），保留半帧续传 */
export function parseSseFrames(buffer: string): { frames: string[], rest: string } {
  const parts = buffer.split(/\r?\n\r?\n/)
  const rest = parts.pop() ?? ''
  return { frames: parts.filter(Boolean), rest }
}

/** 解析单帧（event + 多行 data，注释行忽略）为 event 名与 data 字符串 */
export function parseSseFrame(frame: string): { event: string, dataStr: string } {
  let eventName = 'message'
  const dataLines: string[] = []
  for (const line of frame.split(/\r?\n/)) {
    if (line.startsWith('event:')) {
      eventName = line.slice(6).trim()
    } else if (line.startsWith('data:')) {
      dataLines.push(line.slice(5).trimStart())
    }
  }
  return { event: eventName, dataStr: dataLines.join('\n') }
}

const READ_TIMEOUT = Symbol('run-stream-read-timeout')

export async function consumeRunStream(options: RunStreamTransportOptions): Promise<void> {
  const {
    subscribe,
    onFrame,
    isActive,
    maxAttempts,
    backoffMs,
    resync,
    onExhausted,
    fatalStatuses,
    readTimeoutMs = 45_000,
    signal,
  } = options

  for (let attempt = 0; attempt < maxAttempts; attempt += 1) {
    if (!isActive()) {
      return
    }
    let stopConnection = false
    try {
      const res = await subscribe(signal ?? new AbortController().signal)
      if (!res.ok) {
        if (fatalStatuses?.includes(res.status)) {
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
      while (true) {
        if (!isActive()) {
          break
        }
        // 读超时：TCP 半开时 reader.read() 永久挂住；超时断开进入恢复流程
        let timer: ReturnType<typeof setTimeout> | undefined
        const result = await Promise.race([
          reader.read().then((r) => {
            clearTimeout(timer)
            return r
          }),
          new Promise<typeof READ_TIMEOUT>((resolve) => {
            timer = setTimeout(() => resolve(READ_TIMEOUT), readTimeoutMs)
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
          const { event, dataStr } = parseSseFrame(frame)
          if (dataStr === '[DONE]') {
            if (onFrame(event, null, '[DONE]') === 'stop') {
              stopConnection = true
              break
            }
            continue
          }
          let data: Record<string, unknown>
          try {
            data = JSON.parse(dataStr) as Record<string, unknown>
          } catch {
            continue
          }
          if (onFrame(event, data, dataStr) === 'stop') {
            stopConnection = true
            break
          }
        }
        if (stopConnection) {
          await reader.cancel()
          break
        }
        if (done) {
          break
        }
      }
    } catch (streamError) {
      if (!isActive()) {
        return
      }
      if (attempt >= maxAttempts - 1) {
        if (onExhausted) {
          onExhausted()
          return
        }
        throw streamError
      }
    }
    if (!isActive()) {
      return
    }
    if (resync) {
      await resync()
    }
    if (!isActive()) {
      return
    }
    if (attempt < maxAttempts - 1) {
      await new Promise<void>((resolve) => {
        const timer = setTimeout(resolve, backoffMs(attempt))
        signal?.addEventListener('abort', () => {
          clearTimeout(timer)
          resolve()
        }, { once: true })
      })
    }
  }
  if (isActive()) {
    if (onExhausted) {
      onExhausted()
      return
    }
    throw new Error('连接已中断，请重新连接')
  }
}
