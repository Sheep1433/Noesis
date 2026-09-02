/**
 * 研究弧来源聚合（research-source-provenance）：
 *
 * - 弧边界：一条真实用户消息（source_kind != bg_task_notice 的 user 消息）到
 *   下一条真实用户消息之间的全部消息；系统通知注入不构成边界（续跑多段消息同属一弧）。
 * - 弧内过程消息不渲染来源面板；末条 assistant 消息渲染该弧全部消息落库
 *   retrieval parts 的聚合面板（canonical URL 去重 + origin 分组）。
 * - 引用分层：交付消息正文中出现的来源 URL（canonical 匹配）记为引用。
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

const URL_IN_TEXT_RE = /https?:\/\/[^\s<>()"'`）】。，、；！？]+/gi

/** 正文中的 URL → canonical 集合（引用归因兜底通道） */
export function canonicalUrlsInText(text: string): Set<string> {
  const out = new Set<string>()
  for (const match of text.matchAll(URL_IN_TEXT_RE)) {
    const trimmed = match[0].replace(/[.,;:!?…。；]+$/, '')
    const canonical = canonicalUrl(trimmed)
    if (canonical) {
      out.add(canonical)
    }
  }
  return out
}

// ---- 结构化引用标记解析（与后端 CITATION_EXTENSION 协议、markdown.ts
// RAW_CITATION_RE 渲染层同一语法）：[citation:标题](ref)。ref 为 URL 或
// kb:Collection/文件名；模型偶发输出无 ref 括号的残缺形态
// [citation:domain]，作宽容线索匹配。 ----

const CITATION_REF_RE = /\[citation\s*:[^\]]*\]\(([^()\s]+)\)/gi
const BARE_CITATION_RE = /\[citation\s*:([^\]]+)\](?!\()/gi

/** 引用归因信号：标记 ref 的精确 key 集 + 裸 URL 集 + 残缺标记线索 */
export interface CitationSignal {
  /** 精确命中键：`web:<canonical url>` 与 `kb:<collection>:<file>`（与 citationKey 同构） */
  exactKeys: Set<string>
  /** 残缺标记 token（domain / 文件名线索），宽容匹配用 */
  bareHints: string[]
  /** 归因文本中是否出现过任何信号（URL 或标记） */
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

/** 从归因文本提取引用信号（完整标记 ref 精确 key + 残缺标记线索 + 裸 URL） */
export function collectCitationSignals(text: string): CitationSignal {
  const exactKeys = new Set<string>()
  const bareHints: string[] = []
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
  for (const match of text.matchAll(BARE_CITATION_RE)) {
    const hint = match[1].trim()
    if (hint) {
      bareHints.push(hint)
    }
  }
  for (const canonical of canonicalUrlsInText(text)) {
    exactKeys.add(`web:${canonical}`)
  }
  return { exactKeys, bareHints, hasAnySignal: exactKeys.size > 0 || bareHints.length > 0 }
}

function hostOfCanonicalWebKey(webKey: string): string {
  try {
    return new URL(webKey.slice(4)).hostname.toLowerCase()
  } catch {
    return ''
  }
}

/** 条目是否被引用：精确 key 命中 → 残缺标记宽容匹配（host / 标题） */
function entryIsCited(entry: ArcSourceEntry, signal: CitationSignal): boolean {
  if (signal.exactKeys.has(entry.key)) {
    return true
  }
  if (entry.key.startsWith('web:')) {
    const host = hostOfCanonicalWebKey(entry.key)
    if (!host) {
      return false
    }
    return signal.bareHints.some((hint) => host === hint.toLowerCase() || host.endsWith(`.${hint.toLowerCase()}`))
  }
  if (entry.key.startsWith('kb:')) {
    const title = entry.result.title
    if (!title || title.length < 2) {
      return false
    }
    return signal.bareHints.some((hint) => hint.length >= 2 && (title.includes(hint) || hint.includes(title)))
  }
  return false
}

/**
 * 弧内写入文件的内容（write_file.content / edit_file.new_string）：报告本体，
 * 文件交付场景的引用归因文本（写入发生在弧内任何消息都算——文件是交付物）
 */
export function arcWrittenFileContents(messages: ArcMessage[]): string[] {
  const out: string[] = []
  for (const message of messages) {
    for (const part of message.messageContent?.parts || []) {
      if (part.type !== 'tool' || part.state === 'failed' || part.state === 'rejected' || part.state === 'cancelled') {
        continue
      }
      if (part.name === 'write_file') {
        const content = part.input?.content
        if (typeof content === 'string' && content) {
          out.push(content)
        }
      } else if (part.name === 'edit_file') {
        const content = part.input?.new_string
        if (typeof content === 'string' && content) {
          out.push(content)
        }
      }
    }
  }
  return out
}

export interface ArcPanelData {
  /** 弧内去重来源（首见序存储；展示序号见 numbers） */
  entries: ArcSourceEntry[]
  /** 被引用的条目 key 集合（结构化标记精确命中优先，URL 归因兜底） */
  citedKeys: Set<string>
  /**
   * 引用优先编号（key → 序号）：被引用条目按引用信号首现序编 1..N（残缺标记
   * 宽容命中无出现位置，排精确命中之后按首见序），未引用条目按首见序接续
   * N+1..M。正文 badge 与来源面板共用该映射。
   */
  numbers: Map<string, number>
  /** 归因文本无任何信号（无标记也无 URL）：引用子集不可判定，降级为仅「共检索 N」 */
  attributionUnavailable: boolean
}

/**
 * 引用优先编号：被引用条目编 1..N（精确命中按归因文本首现序——exactKeys 为
 * 有序 Set；宽容命中排其后按首见序），未引用条目按首见序接续 N+1..M，全局连续。
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
    const attributionText = [
      arcDeliveryText(arc.terminal),
      ...arcWrittenFileContents(arc.messages),
    ].join('\n')
    const signal = collectCitationSignals(attributionText)
    const citedKeys = new Set(entries.filter((entry) => entryIsCited(entry, signal)).map((entry) => entry.key))
    panels.set(key, {
      entries,
      citedKeys,
      numbers: assignCitationFirstNumbers(entries, citedKeys, signal),
      attributionUnavailable: !signal.hasAnySignal,
    })
  }
  return panels
}
