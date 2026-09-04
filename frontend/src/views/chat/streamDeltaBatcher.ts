/**
 * 流式 text/reasoning delta 批量应用器。
 *
 * 每个 delta 直接 patch 消息 parts 会触发整链全量重建（parts 克隆、content 字符串
 * 重建、消息数组重建、markdown 全量重解析），长 run 内数万 delta 的分配速率会把
 * 渲染进程堆推到 GB 级（见 docs/bug/chat-stream-hotpath-memory-bloat.md）。这里把
 * delta 缓冲后按 ~100ms 批量应用，整链开销按 flush 频率而非 token 频率计价。
 *
 * 顺序契约由调用方保证：任何会触碰消息 parts 或消息结构的帧回调（message-start、
 * tool、retrieval、snapshot、finish、error 等）先 flush 再处理本帧；会话整体重置
 * 点 clear。delta 在 push 时捕获 redacted-thinking 拆分开关，flush 时不再读共享
 * 标志，避免标志翻转改变已缓冲 delta 的语义。
 *
 * 用 setTimeout 而非 rAF：切后台后 rAF 不触发而 SSE 仍在推流，正是膨胀场景；后台
 * timer 节流（≥1s，隐藏 5 分钟后 ≥1min）只影响展示延迟，缓冲量由 maxPendingChars
 * 阈值兜底同步强刷。
 */

export type StreamDeltaKind = 'text' | 'reasoning'

/** 一条（或一批合并后的）流式 delta；redactedThinking 为 push 时捕获的 <think> 拆分开关 */
export interface StreamDelta {
  kind: StreamDeltaKind
  data: string
  parentTaskCallId?: string
  redactedThinking?: boolean
}

export interface StreamDeltaBatcher {
  /** 缓冲一条 delta；连续同签名（kind / parent / 拆分开关）合并进同一桶 */
  push(delta: StreamDelta): void
  /** 立即应用全部缓冲（结构性帧 / finish / error 回调前调用，保证顺序） */
  flush(): void
  /** 丢弃缓冲（会话切换等整体重置场景，内容由快照 / 历史重新对齐） */
  clear(): void
  /** 组件卸载：丢弃缓冲并停用 */
  dispose(): void
}

/** 同签名连续 delta 的累积桶；chunk 逐条存，flush 时一次 join，避免桶生长期的重复拷贝 */
interface DeltaBucket {
  kind: StreamDeltaKind
  parentTaskCallId?: string
  redactedThinking?: boolean
  chunks: string[]
}

const DEFAULT_FLUSH_INTERVAL_MS = 100
const DEFAULT_MAX_PENDING_CHARS = 128 * 1024

export function createStreamDeltaBatcher(
  apply: (deltas: StreamDelta[]) => void,
  options: { flushIntervalMs?: number, maxPendingChars?: number } = {},
): StreamDeltaBatcher {
  const flushIntervalMs = options.flushIntervalMs ?? DEFAULT_FLUSH_INTERVAL_MS
  const maxPendingChars = options.maxPendingChars ?? DEFAULT_MAX_PENDING_CHARS
  let buckets: DeltaBucket[] = []
  let pendingChars = 0
  let timer: ReturnType<typeof setTimeout> | null = null
  let disposed = false

  function cancelTimer(): void {
    if (timer !== null) {
      clearTimeout(timer)
      timer = null
    }
  }

  function flush(): void {
    cancelTimer()
    if (!buckets.length) {
      return
    }
    const deltas: StreamDelta[] = buckets.map((bucket) => ({
      kind: bucket.kind,
      data: bucket.chunks.join(''),
      parentTaskCallId: bucket.parentTaskCallId,
      redactedThinking: bucket.redactedThinking,
    }))
    buckets = []
    pendingChars = 0
    apply(deltas)
  }

  function clear(): void {
    cancelTimer()
    buckets = []
    pendingChars = 0
  }

  return {
    push(delta: StreamDelta): void {
      if (disposed) {
        return
      }
      const tail = buckets[buckets.length - 1]
      if (
        tail
        && tail.kind === delta.kind
        && tail.parentTaskCallId === delta.parentTaskCallId
        && tail.redactedThinking === delta.redactedThinking
      ) {
        tail.chunks.push(delta.data)
      } else {
        buckets.push({
          kind: delta.kind,
          parentTaskCallId: delta.parentTaskCallId,
          redactedThinking: delta.redactedThinking,
          chunks: [delta.data],
        })
      }
      pendingChars += delta.data.length
      if (pendingChars >= maxPendingChars) {
        flush()
        return
      }
      if (timer === null) {
        timer = setTimeout(() => {
          timer = null
          flush()
        }, flushIntervalMs)
      }
    },
    flush,
    clear,
    dispose(): void {
      disposed = true
      clear()
    },
  }
}
