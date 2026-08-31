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

/** 正文中的 URL → canonical 集合（引用归因判定） */
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

export interface ArcPanelData {
  /** 弧内去重来源（首见序；与 buildCitationIndex 的编号序一致） */
  entries: ArcSourceEntry[]
  /** 被交付正文引用（URL 归因）的条目 key 集合 */
  citedKeys: Set<string>
  /** 交付正文不含任何来源 URL（如文件交付）：引用子集不可判定，降级为仅「共检索 N」 */
  attributionUnavailable: boolean
}

/**
 * 某条消息的面板数据 = 其所属弧的全部消息 parts 合并去重 + 引用过滤的纯函数。
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
    const deliveryText = arcDeliveryText(arc.terminal)
    const urlsInText = canonicalUrlsInText(deliveryText)
    const citedKeys = new Set(
      entries.filter((entry) => entry.key.startsWith('web:') && urlsInText.has(entry.key.slice(4))).map((entry) => entry.key),
    )
    panels.set(key, {
      entries,
      citedKeys,
      attributionUnavailable: urlsInText.size === 0,
    })
  }
  return panels
}
