import type { RetrievalResultUi } from './messageParts'

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
 * 去重 key：与 CitationSources 的去重逻辑保持一致，保证正文 badge 序号
 * 与底部来源面板序号一一对应。web 用 origin+pathname 归一（忽略 query/fragment），
 * 知识库用 collection + title。
 */
export function citationKey(result: RetrievalResultUi): string {
  if (result.source_type === 'web') {
    const url = safeWebUrl(result.url)
    if (url) {
      try {
        const u = new URL(url)
        return `web:${u.origin}${u.pathname}`
      } catch {
        return `web:${result.evidence_id}`
      }
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

export type CitationIndex = Map<string, CitationIndexEntry>
