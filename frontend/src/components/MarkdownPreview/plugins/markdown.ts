import type StateCore from 'markdown-it/lib/rules_core/state_core.mjs'
import type Token from 'markdown-it/lib/token.mjs'
import type { CitationIndex } from '@/views/chat/citationRendering'
import type { RetrievalResultUi } from '@/views/chat/messageParts'
import MarkdownIt from 'markdown-it'
import markdownItHighlight from 'markdown-it-highlightjs'
import { buildCitationIndex, citationKey, safeWebUrl } from '@/views/chat/citationRendering'
import hljs from './highlight'
import { mermaidPlugin } from './mermaid'
import { preWrapperPlugin } from './preWrapper'

const md = new MarkdownIt({
  html: true,
  linkify: true,
  typographer: true,
})

interface CitationRenderEnv {
  citationIndex?: CitationIndex
  retrievalResults?: RetrievalResultUi[]
}

function webSupBadge(state: StateCore, number: number, title: string, href: string): Token {
  const badge = new state.Token('html_inline', '', 0)
  const titleEsc = md.utils.escapeHtml(title)
  badge.content = `<sup class="citation-badge citation-sup" data-citation-number="${number}" title="${titleEsc}"><a href="${md.utils.escapeHtml(href)}" target="_blank" rel="noopener noreferrer">${number}</a></sup>`
  return badge
}

function kbSupBadge(state: StateCore, number: number, title: string, kbRef: string): Token {
  const badge = new state.Token('html_inline', '', 0)
  const titleEsc = md.utils.escapeHtml(title)
  badge.content = `<sup class="citation-badge citation-badge--kb" data-citation-number="${number}" data-kb-ref="${md.utils.escapeHtml(kbRef)}" role="button" tabindex="0" title="${titleEsc}">${number}</sup>`
  return badge
}

function unnumberedBadge(state: StateCore, title: string, href: string | null, isKb: boolean): Token {
  const badge = new state.Token('html_inline', '', 0)
  const titleEsc = md.utils.escapeHtml(title)
  if (isKb && href) {
    badge.content = `<sup class="citation-badge citation-badge--kb" data-kb-ref="${md.utils.escapeHtml(href)}" role="button" tabindex="0" title="${titleEsc}">·</sup>`
  } else if (href) {
    badge.content = `<sup class="citation-badge citation-sup" title="${titleEsc}"><a href="${md.utils.escapeHtml(href)}" target="_blank" rel="noopener noreferrer">·</a></sup>`
  } else {
    badge.content = `<sup class="citation-badge citation-sup" title="${titleEsc}">·</sup>`
  }
  return badge
}

function lookupByRef(index: CitationIndex | undefined, ref: string): { number: number, result: RetrievalResultUi } | null {
  if (!index || index.size === 0) {
    return null
  }
  // markdown-it normalizeLink 会把 href 里的非 ASCII 百分号编码（中文文件名），
  // 解码后再匹配；ref 本身已是明文时 decodeURIComponent 原样返回。
  let decoded = ref
  try {
    decoded = decodeURIComponent(ref)
  } catch {
    decoded = ref
  }
  if (/^https?:\/\//i.test(decoded)) {
    const targetKey = citationKey({ url: decoded, source_type: 'web', evidence_id: decoded, title: '', excerpt: '' } as RetrievalResultUi)
    const direct = index.get(targetKey)
    if (direct) {
      return direct
    }
    for (const [, candidate] of index) {
      if (candidate.result.source_type !== 'web') {
        continue
      }
      if (citationKey(candidate.result) === targetKey) {
        return candidate
      }
    }
    return null
  }
  if (decoded.startsWith('kb:')) {
    const rest = decoded.slice(3)
    const slashIdx = rest.indexOf('/')
    if (slashIdx < 0) {
      return null
    }
    const collection = rest.slice(0, slashIdx)
    const file = rest.slice(slashIdx + 1)
    const direct = index.get(`kb:${collection}:${file}`)
    if (direct) {
      return direct
    }
    for (const [, candidate] of index) {
      if (candidate.result.source_type === 'web') {
        continue
      }
      if (candidate.result.title === file) {
        return candidate
      }
    }
    return null
  }
  return null
}

/** 未被 markdown-it 解析成链接的原始引用文本（如 file: 协议被 validateLink 黑名单拒绝）。 */
const RAW_CITATION_RE = /\[citation\s*:[^\]]*\]\([^)]*\)/gi

/**
 * 归一化模型可能编造的引用 ref。已知偏差：file:Collection/文件名 → kb:Collection/文件名
 * （file: 在 markdown-it validateLink 黑名单内，整条链接退化为原始文本）。
 */
function normalizeCitationRef(ref: string): string {
  if (/^file:/i.test(ref)) {
    return `kb:${ref.slice(5)}`
  }
  return ref
}

/** markdown-it normalizeLink 会对 href 里的非 ASCII 百分号编码，解码后再匹配/展示。 */
function decodeRef(ref: string): string {
  try {
    return decodeURIComponent(ref)
  } catch {
    return ref
  }
}

/** 把单个 [citation:标题](ref) 原始文本转成上标 badge token。 */
function pushBadgeFromRawCitation(state: StateCore, index: CitationIndex | undefined, label: string, refRaw: string, out: Token[]) {
  const title = label.replace(/^citation\s*:/i, '').trim()
  const ref = decodeRef(normalizeCitationRef(refRaw))
  const matched = lookupByRef(index, ref)
  if (matched) {
    const isKb = ref.startsWith('kb:')
    if (isKb) {
      out.push(kbSupBadge(state, matched.number, title || matched.result.title, ref))
    } else {
      const webHref = safeWebUrl(ref) || safeWebUrl(matched.result.url) || ''
      out.push(webSupBadge(state, matched.number, title || matched.result.title, webHref))
    }
    return
  }
  const isKb = ref.startsWith('kb:')
  out.push(unnumberedBadge(state, title, isKb ? null : safeWebUrl(ref), isKb))
}

function appendTextToken(state: StateCore, out: Token[], content: string) {
  const token = new state.Token('text', '', 0)
  token.content = content
  out.push(token)
}

md.core.ruler.after('inline', 'citation-badges', (state) => {
  const env = (state.env || {}) as CitationRenderEnv
  const index = env.citationIndex
  for (let tokenIndex = 0; tokenIndex < state.tokens.length; tokenIndex++) {
    const token = state.tokens[tokenIndex]
    if (token.type !== 'inline' || !token.children) {
      continue
    }
    const children: typeof token.children = []
    for (let i = 0; i < token.children.length; i++) {
      const child = token.children[i]
      if (child.type === 'link_open') {
        const label = token.children[i + 1]
        const close = token.children[i + 2]
        const href = child.attrGet('href') || ''
        if (label?.type === 'text' && close?.type === 'link_close' && /^citation\s*:/i.test(label.content)) {
          const title = label.content.replace(/^citation\s*:/i, '').trim()
          const ref = decodeRef(normalizeCitationRef(href))
          const matched = lookupByRef(index, ref)
          if (matched) {
            const isKb = ref.startsWith('kb:')
            if (isKb) {
              children.push(kbSupBadge(state, matched.number, title || matched.result.title, ref))
            } else {
              const webHref = safeWebUrl(ref) || safeWebUrl(matched.result.url) || ''
              children.push(webSupBadge(state, matched.number, title || matched.result.title, webHref))
            }
          } else {
            const isKb = ref.startsWith('kb:')
            children.push(unnumberedBadge(state, title, safeWebUrl(ref), isKb))
          }
          i += 2
          continue
        }
        children.push(child)
        continue
      }
      if (child.type !== 'text') {
        children.push(child)
        continue
      }
      // 防御：file: 等被 validateLink 拒绝的引用链接不会产生 link_open，
      // 整条 [citation:...](ref) 留在 text token 里，这里兜底转成上标 badge
      const raw = child.content
      if (!raw.includes('[citation:')) {
        children.push(child)
        continue
      }
      RAW_CITATION_RE.lastIndex = 0
      const matches = Array.from(raw.matchAll(RAW_CITATION_RE))
      if (matches.length === 0) {
        children.push(child)
        continue
      }
      let cursor = 0
      for (const m of matches) {
        const inner = /^\[citation\s*:([^\]]*)\]\(([^)]*)\)$/i.exec(m[0])
        if (!inner) {
          continue
        }
        const start = m.index ?? 0
        if (start > cursor) {
          appendTextToken(state, children, raw.slice(cursor, start))
        }
        pushBadgeFromRawCitation(state, index, inner[1], inner[2], children)
        cursor = start + m[0].length
      }
      if (cursor < raw.length) {
        appendTextToken(state, children, raw.slice(cursor))
      }
    }
    token.children = children
  }
})

md.renderer.rules.image = function (tokens, idx, options, env, self) {
  const token = tokens[idx]
  token.attrPush(['referrerpolicy', 'no-referrer'])
  return self.renderToken(tokens, idx, options)
}

const defaultLinkOpen = md.renderer.rules.link_open
md.renderer.rules.link_open = function (tokens, idx, options, env, self) {
  const token = tokens[idx]
  const href = token.attrGet('href') || ''
  if (/^https?:\/\//i.test(href)) {
    token.attrSet('target', '_blank')
    token.attrSet('rel', 'noopener noreferrer')
    token.attrSet('referrerpolicy', 'no-referrer')
  }
  return defaultLinkOpen
    ? defaultLinkOpen(tokens, idx, options, env, self)
    : self.renderToken(tokens, idx, options)
}

md.use(markdownItHighlight, {
  hljs,
  auto: true,
  code: true,
}).use(mermaidPlugin).use(preWrapperPlugin, {
  hasSingleTheme: true,
})
export default md
export { buildCitationIndex }
