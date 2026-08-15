import type { RetrievalResultUi } from './messageParts'
import MarkdownIt from 'markdown-it'

export interface CitationTarget {
  href: string
  title: string
}

const HEADING_LINE = /^#{1,6} [^\r\n]+$/gmu
const REFERENCE_HEADING_NAMES = new Set(['参考资料', '参考文献', 'reference', 'references', 'source', 'sources'])
const citationParser = new MarkdownIt()

function findReferenceHeading(markdown: string): RegExpExecArray | null {
  HEADING_LINE.lastIndex = 0
  while (true) {
    const match = HEADING_LINE.exec(markdown)
    if (match === null) {
      return null
    }
    const title = match[0].replace(/^#{1,6} /, '').trim()
    const base = title.split(/[ \t（(：:]/, 1)[0].toLowerCase()
    if (REFERENCE_HEADING_NAMES.has(base)) {
      return match
    }
  }
}

export function citationBody(
  markdown: string,
  targets: Map<number, CitationTarget>,
  referencesComplete = true,
): string {
  const heading = findReferenceHeading(markdown)
  if (!heading || !referencesComplete) {
    return markdown
  }
  const referenceSection = markdown.slice(heading.index + heading[0].length)
  const references = parseReferenceLines(referenceSection)
  const normalizedBody = normalizeCitationAliases(markdown.slice(0, heading.index), references)
  const numbers = new Set(references.map(([number]) => number))
  const allReferencesMatched = references.length > 0
    && numbers.size === references.length
    && references.every(([number]) => targets.has(number))
  if (allReferencesMatched && referenceSectionContainsOnlyReferences(referenceSection)) {
    return normalizedBody.trimEnd()
  }
  return `${normalizedBody}${markdown.slice(heading.index, heading.index + heading[0].length)}${formatReferenceLines(referenceSection)}`
}

function parseReferenceLine(rawLine: string): [number, string] | null {
  const line = rawLine.trim().replace(/^[-*]\s+/, '')
  const match = line.match(/^(?:\[(\d+)\]|(\d+)\s*[.、)])/)
  if (!match) {
    return null
  }
  const numberText = match[1] || match[2]
  const value = line.slice(match[0].length).trim()
  return value ? [Number(numberText), value] : null
}

/**
 * Deep-research 报告有时使用 A3/B2 这类域前缀引用，但最终参考资料列表
 * 会被合并成 3/2 的全局编号。仅当对应的数字确实出现在参考资料列表时，
 * 才把别名前缀归一化，避免把普通方括号文本误变成引用。
 */
function normalizeCitationAliases(markdown: string, references: Array<[number, string]>): string {
  const referenceNumbers = new Set(references.map(([number]) => String(number)))
  if (referenceNumbers.size === 0) {
    return markdown
  }
  return markdown.replace(/\[([a-z]+)(\d+)\]/gi, (raw, _prefix: string, number: string) => {
    return referenceNumbers.has(number) ? `[${number}]` : raw
  })
}

function parseReferenceLines(markdown: string): Array<[number, string]> {
  const references: Array<[number, string]> = []
  for (const rawLine of markdown.split(/\r?\n/)) {
    const reference = parseReferenceLine(rawLine)
    if (reference) {
      references.push(reference)
    }
  }
  return references
}

function referenceSectionContainsOnlyReferences(markdown: string): boolean {
  return markdown.split(/\r?\n/).every((line) => !line.trim() || parseReferenceLine(line) !== null)
}

function formatReferenceLines(markdown: string): string {
  const lines = markdown.split(/\r?\n/)
  const formatted: string[] = []
  for (const line of lines) {
    if (parseReferenceLine(line) && formatted.at(-1)?.trim()) {
      formatted.push('')
    }
    formatted.push(line)
  }
  return formatted.join('\n')
}

function bodyCitationNumbers(markdown: string): Set<number> {
  const numbers = new Set<number>()
  for (const token of citationParser.parse(markdown, {})) {
    if (token.type !== 'inline') {
      continue
    }
    for (const child of token.children || []) {
      if (child.type !== 'text') {
        continue
      }
      for (const match of child.content.matchAll(/\[(\d+)\]/g)) {
        numbers.add(Number(match[1]))
      }
    }
  }
  return numbers
}

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
 * URL 匹配键：origin + pathname，忽略 query/hash。
 * retrieval 记录的 URL 常带 tracking query（如 sohu 的 ?scm=...），
 * 而模型在参考资料里写的 URL 不含 query，两者应判为同一来源。
 */
function webUrlMatchKey(raw: string | undefined): string | null {
  if (!raw) {
    return null
  }
  try {
    const url = new URL(raw)
    return ['http:', 'https:'].includes(url.protocol) && !url.username && !url.password
      ? `${url.origin}${url.pathname}`
      : null
  } catch {
    return null
  }
}

function uniqueSourceMatch(
  results: RetrievalResultUi[],
  predicate: (result: RetrievalResultUi) => boolean,
  key: (result: RetrievalResultUi) => string,
): RetrievalResultUi | null {
  const matches = results.filter(predicate)
  const sources = new Map(matches.map((result) => [key(result), result]))
  return sources.size === 1 ? [...sources.values()][0] : null
}

function matchesKbReference(line: string, result: RetrievalResultUi): boolean {
  if (!result.collection_name || !result.title) {
    return false
  }
  const identity = `${result.title} — Collection: ${result.collection_name}`
  if (line === identity) {
    return true
  }
  const locator = line.slice(identity.length).trimStart()
  return line.startsWith(identity) && /^[，,（(；;]/.test(locator)
}

function trailingWebUrl(line: string): string | null {
  const matches = [...line.matchAll(/https?:\/\/\S+/gu)]
  const raw = matches.length === 1 && matches[0].index! + matches[0][0].length === line.length
    ? matches[0][0]
    : undefined
  return safeWebUrl(raw)
}

export function citationTargets(
  markdown: string,
  results: RetrievalResultUi[],
  kbHref: (collectionName: string, fileName: string) => string,
): Map<number, CitationTarget> {
  const heading = findReferenceHeading(markdown)
  if (!heading) {
    return new Map()
  }
  const references = markdown.slice(heading.index + heading[0].length)
  const parsedReferences = parseReferenceLines(references)
  const bodyNumbers = bodyCitationNumbers(
    normalizeCitationAliases(markdown.slice(0, heading.index), parsedReferences),
  )
  const targets = new Map<number, CitationTarget>()
  const ambiguousNumbers = new Set<number>()
  for (const [number, line] of parsedReferences) {
    if (!bodyNumbers.has(number)) {
      continue
    }
    if (targets.has(number) || ambiguousNumbers.has(number)) {
      targets.delete(number)
      ambiguousNumbers.add(number)
      continue
    }
    const referenceUrl = trailingWebUrl(line)
    const web = referenceUrl
      ? uniqueSourceMatch(
          results,
          (result) => result.source_type === 'web' && webUrlMatchKey(result.url) === webUrlMatchKey(referenceUrl),
          (result) => webUrlMatchKey(result.url) || '',
        )
      : null
    if (web) {
      targets.set(number, {
        href: safeWebUrl(web.url)!,
        title: web.title || web.url || `来源 ${number}`,
      })
      continue
    }
    const kb = uniqueSourceMatch(
      results,
      (result) => result.source_type === 'knowledge_base' && matchesKbReference(line, result),
      (result) => `${result.collection_name}\0${result.title}`,
    )
    if (kb) {
      targets.set(number, {
        href: kbHref(kb.collection_name!, kb.title),
        title: kb.title,
      })
    }
  }
  const numbersByHref = new Map<string, number[]>()
  for (const [number, target] of targets) {
    numbersByHref.set(target.href, [...(numbersByHref.get(target.href) || []), number])
  }
  for (const numbers of numbersByHref.values()) {
    if (numbers.length > 1) {
      numbers.forEach((number) => targets.delete(number))
    }
  }
  return targets
}
