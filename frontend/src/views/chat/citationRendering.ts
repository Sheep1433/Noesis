export interface CitationSource {
  title: string
  rawRef: string
  sourceType: 'web' | 'kb'
  href: string | null
  domain: string
  count: number
  index: number
}

// [citation:标题](URL) 或 [citation:文件名](kb:Collection名/文件名)
const CITATION_LINK_RE = /\[citation: ([^\]]+)\]\(([^)]+)\)/gi

function safeWebUrl(raw: string | undefined): string | null {
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

function extractDomain(url: string): string {
  try {
    return new URL(url).hostname.replace(/^www\./i, '')
  } catch {
    return url
  }
}

/**
 * 从正文扫描所有 [citation:标题](ref) 内联引用。
 * ref 分两类：http(s):// 开头的 web 来源，kb: 开头的知识库来源。
 */
export function extractCitationSources(markdown: string): CitationSource[] {
  if (!markdown) {
    return []
  }
  const sourcesByUrl = new Map<string, CitationSource>()

  for (const match of markdown.matchAll(CITATION_LINK_RE)) {
    const rawTitle = (match[1] ?? '').trim()
    const rawRef = (match[2] ?? '').trim()
    const index = match.index ?? 0

    const isWeb = /^https?:\/\//i.test(rawRef)
    const isKb = rawRef.startsWith('kb:')
    if (!isWeb && !isKb) {
      continue
    }

    const href = isWeb ? safeWebUrl(rawRef) : null
    const domain = isWeb && href ? extractDomain(href) : '知识库'
    const sourceType: 'web' | 'kb' = isWeb ? 'web' : 'kb'
    const key = isWeb ? `web:${href}` : `kb:${rawRef}`

    const existing = sourcesByUrl.get(key)
    if (existing) {
      existing.count += 1
      continue
    }

    sourcesByUrl.set(key, {
      title: rawTitle || domain,
      rawRef,
      sourceType,
      href,
      domain,
      count: 1,
      index,
    })
  }

  return Array.from(sourcesByUrl.values())
}

export { safeWebUrl }
