import type { RetrievalResultUi } from './messageParts'
import { canonicalUrl } from '@/utils/canonicalUrl'

export function safeWebUrl(raw: string | undefined): string | null {
  if (!raw) {
    return null
  }
  try {
    const url = new URL(raw)
    return ['http:', 'https:'].includes(url.protocol) && !url.username && !url.password
      ? url.href
      : null
  } catch {
    return null
  }
}

/**
 * 去重 key：与来源面板的去重逻辑保持一致，保证正文 badge 序号与面板序号
 * 一一对应。web 用 canonical URL（去 tracking 参数、协议/host 归一，与后端
 * 共享规则），知识库用 collection + title。
 */
export function citationKey(result: RetrievalResultUi): string {
  if (result.source_type === 'web') {
    const url = canonicalUrl(result.url)
    if (url) {
      return `web:${url}`
    }
    return `web:${result.evidence_id}`
  }
  return `kb:${result.collection_name || ''}:${result.title}`
}

export interface CitationIndexEntry {
  number: number
  result: RetrievalResultUi
}

/**
 * 从本轮检索结果构建「来源 key → 序号」索引。按出现顺序去重分配 1-based 编号，
 * 正文 badge 与 CitationSources 来源面板共用同一份映射，确保点击 [2] 能定位到
 * 面板第 2 条。
 */
export function buildCitationIndex(results: RetrievalResultUi[]): Map<string, CitationIndexEntry> {
  const index = new Map<string, CitationIndexEntry>()
  let number = 0
  for (const result of results) {
    const key = citationKey(result)
    if (!index.has(key)) {
      number += 1
      index.set(key, { number, result })
    }
  }
  return index
}

/**
 * 用外部编号映射构建索引（弧聚合面板的引用优先编号：被引用 1..N、未引用接续），
 * 保证正文 badge 序号与来源面板编号一一对应。无编号的条目不进索引；
 * 单条消息内的默认编号仍用 buildCitationIndex。
 */
export function buildCitationIndexFromNumbers(results: RetrievalResultUi[], numbers: Map<string, number>): CitationIndex {
  const index = new Map<string, CitationIndexEntry>()
  for (const result of results) {
    const key = citationKey(result)
    const number = numbers.get(key)
    if (number !== undefined && !index.has(key)) {
      index.set(key, { number, result })
    }
  }
  return index
}

export type CitationIndex = Map<string, CitationIndexEntry>
