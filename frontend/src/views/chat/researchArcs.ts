/**
 * 研究弧来源聚合（research-source-provenance）：
 *
 * - 弧边界：一条真实用户消息（source_kind != bg_task_notice 的 user 消息）到
 *   下一条真实用户消息之间的全部消息；系统通知注入不构成边界（续跑多段消息同属一弧）。
 * - 弧内过程消息不渲染来源面板；末条 assistant 消息渲染该弧全部消息落库
 *   retrieval parts 的聚合面板（canonical URL 去重 + origin 分组）。
 * - 引用分层：交付正文与弧内写入文件中的完整 [citation:标题](ref) 标记，
 *   ref 精确命中（web 按 canonical URL、KB 按 kb ref）记为引用；裸 URL 与
 *   残缺标记不升格，归入其他检索来源。
 *
 * 全部为持久化消息数据的纯函数：同输入必同输出（刷新重算不变），
 * 相邻研究轮次互不渗透（去重作用域为弧内）。
 */

import type { MessageContentV1, RetrievalOrigin, RetrievalResultUi } from './messageParts'
import { canonicalUrl } from '@/utils/canonicalUrl'
import { citationKey } from './citationRendering'

export interface ArcMessage {
  uuid?: string
  message_id?: string
  role: 'user' | 'assistant'
  /** 系统注入消息标记（bg_task_notice 等渲染为系统通知条，不构成弧边界） */
  source_kind?: string
  messageContent?: MessageContentV1
}

/** 真实用户消息：弧边界判定唯一输入（确定性，无时间窗 / 运行时状态） */
export function isRealUserMessage(message: ArcMessage): boolean {
  return message.role === 'user' && message.source_kind !== 'bg_task_notice'
}

export function arcMessageKey(message: ArcMessage): string {
  return message.message_id || message.uuid || ''
}

export interface ResearchArc<T extends ArcMessage> {
  messages: T[]
  /** 弧内最后一条 assistant 消息（交付 / 被打断弧的末条消息）；无则 null */
  terminal: T | null
}

/** 按真实用户消息切分研究弧（首个真实用户消息之前的前导消息自成一弧） */
export function computeResearchArcs<T extends ArcMessage>(messages: T[]): ResearchArc<T>[] {
  const arcs: ResearchArc<T>[] = []
  let current: T[] = []
  for (const message of messages) {
    if (isRealUserMessage(message) && current.length > 0) {
      arcs.push({ messages: current, terminal: _lastAssistant(current) })
      current = []
    }
    current.push(message)
  }
  if (current.length > 0) {
    arcs.push({ messages: current, terminal: _lastAssistant(current) })
  }
  return arcs
}

function _lastAssistant<T extends ArcMessage>(messages: T[]): T | null {
  for (let i = messages.length - 1; i >= 0; i--) {
    if (messages[i].role === 'assistant') {
      return messages[i]
    }
  }
  return null
}

/** 弧聚合面板的单条来源：canonical URL 去重后的条目 + 完整 origin 列表 */
export interface ArcSourceEntry {
  key: string
  result: RetrievalResultUi
  /** 贡献者列表（去重；旧数据无 origin 按 main 归组） */
  origins: RetrievalOrigin[]
}

function _originKey(origin: RetrievalOrigin): string {
  return `${origin.kind}:${origin.label || ''}`
}

/** 收集弧内全部消息落库 retrieval parts 的来源（canonical URL 去重、保首见序、origin 合并） */
export function collectArcSources(messages: ArcMessage[]): ArcSourceEntry[] {
  const entries = new Map<string, ArcSourceEntry>()
  for (const message of messages) {
    const parts = message.messageContent?.parts || []
    for (const part of parts) {
      if (part.type !== 'retrieval') {
        continue
      }
      const origin: RetrievalOrigin = part.origin || { kind: 'main' }
      for (const result of part.results) {
        const key = citationKey(result)
        const existing = entries.get(key)
        if (!existing) {
          entries.set(key, { key, result, origins: [origin] })
          continue
        }
        if (!existing.origins.some((o) => _originKey(o) === _originKey(origin))) {
          existing.origins.push(origin)
        }
      }
    }
  }
  return [...entries.values()]
}

/** 交付消息正文（顶层 text parts；子 Agent 嵌套正文不参与引用归因） */
export function arcDeliveryText(message: ArcMessage | null): string {
  if (!message) {
    return ''
  }
  return (message.messageContent?.parts || [])
    .filter((part) => part.type === 'text' && !part.parent_task_call_id)
    .map((part) => (part.type === 'text' ? part.content : ''))
    .join('\n')
}

// ---- 结构化引用标记解析（与后端 CITATION_EXTENSION 协议、markdown.ts
// RAW_CITATION_RE 渲染层同一语法）：[citation:标题](ref)。ref 为 URL 或
// kb:Collection/文件名。引用判定只认完整标记的 ref 精确命中；裸 URL 与
// 无 ref 括号的残缺输出不参与（与 Perplexity「标记 prompt-dependent、
// 检索列表才是事实来源」同口径）。 ----

const CITATION_REF_RE = /\[citation\s*:[^\]]*\]\(([^()\s]+)\)/gi

/** 引用归因信号：完整标记 ref 的精确 key 集 */
export interface CitationSignal {
  /** 精确命中键：`web:<canonical url>` 与 `kb:<collection>:<file>`（与 citationKey 同构） */
  exactKeys: Set<string>
  /** 归因文本中是否出现过引用标记 */
  hasAnySignal: boolean
}

function kbRefToKey(ref: string): string | null {
  if (!ref.startsWith('kb:')) {
    return null
  }
  let decoded = ref
  try {
    decoded = decodeURIComponent(ref)
  } catch {
    decoded = ref
  }
  const rest = decoded.slice(3)
  const slashIdx = rest.indexOf('/')
  if (slashIdx < 0) {
    return null
  }
  const collection = rest.slice(0, slashIdx)
  const file = rest.slice(slashIdx + 1)
  return collection && file ? `kb:${collection}:${file}` : null
}

/** 从归因文本提取引用信号（完整标记 ref 精确 key；裸 URL 与残缺标记不参与） */
export function collectCitationSignals(text: string): CitationSignal {
  const exactKeys = new Set<string>()
  for (const match of text.matchAll(CITATION_REF_RE)) {
    const ref = match[1]
    if (/^https?:\/\//i.test(ref)) {
      const canonical = canonicalUrl(ref)
      if (canonical) {
        exactKeys.add(`web:${canonical}`)
      }
    } else {
      const kbKey = kbRefToKey(ref)
      if (kbKey) {
        exactKeys.add(kbKey)
      }
    }
  }
  return { exactKeys, hasAnySignal: exactKeys.size > 0 }
}

/**
 * 弧内写入文件（write_file / edit_file）：报告本体（内容）与产物路径。
 * 引用归因按写入正文判定；文件预览编号按路径归属弧。
 */
export function arcWrittenFiles(messages: ArcMessage[]): { paths: string[], contents: string[] } {
  const paths: string[] = []
  const contents: string[] = []
  for (const message of messages) {
    for (const part of message.messageContent?.parts || []) {
      if (part.type !== 'tool' || part.state === 'failed' || part.state === 'rejected' || part.state === 'cancelled') {
        continue
      }
      if (part.name === 'write_file') {
        if (typeof part.input?.file_path === 'string' && part.input.file_path) {
          paths.push(part.input.file_path)
        }
        const content = part.input?.content
        if (typeof content === 'string' && content) {
          contents.push(content)
        }
      } else if (part.name === 'edit_file') {
        if (typeof part.input?.file_path === 'string' && part.input.file_path) {
          paths.push(part.input.file_path)
        }
        const content = part.input?.new_string
        if (typeof content === 'string' && content) {
          contents.push(content)
        }
      }
    }
  }
  return { paths, contents }
}

export interface ArcPanelData {
  /** 弧内去重来源（首见序存储；展示序号见 numbers） */
  entries: ArcSourceEntry[]
  /** 被引用的条目 key 集合（完整标记 ref 精确命中） */
  citedKeys: Set<string>
  /**
   * 引用优先编号（key → 序号）：被引用条目按引用标记首现序编 1..N，
   * 未引用条目按首见序接续 N+1..M，全局连续。正文 badge 与来源面板共用该映射。
   */
  numbers: Map<string, number>
  /** 归因文本无完整引用标记：引用子集不可判定，降级为仅「共检索 N」 */
  attributionUnavailable: boolean
  /** 弧内 write_file / edit_file 写入的文件路径（报告产物；文件预览编号归属依据） */
  writtenFilePaths: string[]
}

/**
 * 引用优先编号：被引用条目编 1..N（按归因文本中标记 ref 首现序——exactKeys 为
 * 有序 Set），未引用条目按首见序接续 N+1..M，全局连续。
 */
function assignCitationFirstNumbers(entries: ArcSourceEntry[], citedKeys: Set<string>, signal: CitationSignal): Map<string, number> {
  const numbers = new Map<string, number>()
  let next = 1
  for (const key of signal.exactKeys) {
    if (citedKeys.has(key) && !numbers.has(key)) {
      numbers.set(key, next++)
    }
  }
  for (const entry of entries) {
    if (citedKeys.has(entry.key) && !numbers.has(entry.key)) {
      numbers.set(entry.key, next++)
    }
  }
  for (const entry of entries) {
    if (!citedKeys.has(entry.key) && !numbers.has(entry.key)) {
      numbers.set(entry.key, next++)
    }
  }
  return numbers
}

/**
 * 某条消息的面板数据 = 其所属弧的全部消息 parts 合并去重 + 引用过滤的纯函数。
 * 引用判定文本 = 交付消息顶层正文 + 弧内写入文件内容（报告本体）；
 * 过程消息的叙述正文不参与（避免进度叙事误标）。
 * 返回 Map（key = arcMessageKey）：仅含「弧末条 assistant 消息且弧内有来源」的条目。
 */
export function computeArcPanels<T extends ArcMessage>(messages: T[]): Map<string, ArcPanelData> {
  const panels = new Map<string, ArcPanelData>()
  for (const arc of computeResearchArcs(messages)) {
    const entries = collectArcSources(arc.messages)
    if (!arc.terminal || entries.length === 0) {
      continue
    }
    const key = arcMessageKey(arc.terminal)
    if (!key) {
      continue
    }
    const written = arcWrittenFiles(arc.messages)
    const attributionText = [arcDeliveryText(arc.terminal), ...written.contents].join('\n')
    const signal = collectCitationSignals(attributionText)
    const citedKeys = new Set(entries.filter((entry) => signal.exactKeys.has(entry.key)).map((entry) => entry.key))
    panels.set(key, {
      entries,
      citedKeys,
      numbers: assignCitationFirstNumbers(entries, citedKeys, signal),
      attributionUnavailable: !signal.hasAnySignal,
      writtenFilePaths: written.paths,
    })
  }
  return panels
}
