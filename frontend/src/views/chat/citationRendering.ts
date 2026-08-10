import type { RetrievalResultUi } from './messageParts'
import MarkdownIt from 'markdown-it'

export interface CitationTarget {
  href: string
  title: string
}

const REFERENCE_HEADING = /^#{1,6}\s+参考资料\s*$/m
const citationParser = new MarkdownIt()

export function citationBody(
  markdown: string,
  targets: Map<number, CitationTarget>,
  referencesComplete = true,
): string {
  const heading = REFERENCE_HEADING.exec(markdown)
  if (!heading || !referencesComplete) {
    return markdown
  }
  const referenceSection = markdown.slice(heading.index + heading[0].length)
  const references = parseReferenceLines(referenceSection)
  const numbers = new Set(references.map(([number]) => number))
  const allReferencesMatched = references.length > 0
    && numbers.size === references.length
    && references.every(([number]) => targets.has(number))
  if (allReferencesMatched && referenceSectionContainsOnlyReferences(referenceSection)) {
    return markdown.slice(0, heading.index).trimEnd()
  }
  return `${markdown.slice(0, heading.index + heading[0].length)}${formatReferenceLines(referenceSection)}`
}

function parseReferenceLine(rawLine: string): [number, string] | null {
  const line = rawLine.trim().replace(/^[-*]\s+/, '')
  const closingBracket = line.indexOf(']')
  if (!line.startsWith('[') || closingBracket < 2) {
    return null
  }
  const numberText = line.slice(1, closingBracket)
  const value = line.slice(closingBracket + 1).trim()
  return /^\d+$/.test(numberText) && value ? [Number(numberText), value] : null
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
  const heading = REFERENCE_HEADING.exec(markdown)
  if (!heading) {
    return new Map()
  }
  const references = markdown.slice(heading.index + heading[0].length)
  const bodyNumbers = bodyCitationNumbers(markdown.slice(0, heading.index))
  const targets = new Map<number, CitationTarget>()
  const ambiguousNumbers = new Set<number>()
  for (const [number, line] of parseReferenceLines(references)) {
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
          (result) => result.source_type === 'web' && safeWebUrl(result.url) === referenceUrl,
          (result) => safeWebUrl(result.url) || '',
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
