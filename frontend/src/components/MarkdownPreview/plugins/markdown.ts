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
  if (/^https?:\/\//i.test(ref)) {
    const targetKey = citationKey({ url: ref, source_type: 'web', evidence_id: ref, title: '', excerpt: '' } as RetrievalResultUi)
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
  if (ref.startsWith('kb:')) {
    const rest = ref.slice(3)
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

md.core.ruler.after('inline', 'citation-badges', (state) => {
  const env = (state.env || {}) as CitationRenderEnv
  const index = env.citationIndex
  let referencesStarted = false
  for (let tokenIndex = 0; tokenIndex < state.tokens.length; tokenIndex++) {
    const token = state.tokens[tokenIndex]
    if (token.type === 'heading_open') {
      const heading = state.tokens[tokenIndex + 1]
      if (heading?.type === 'inline' && /^参考资料(?:（精选）|\s|$)/u.test(heading.content.trim())) {
        referencesStarted = true
      }
    }
    if (token.type !== 'inline' || !token.children) {
      continue
    }
    if (referencesStarted) {
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
          const matched = lookupByRef(index, href)
          if (matched) {
            const isKb = href.startsWith('kb:')
            if (isKb) {
              children.push(kbSupBadge(state, matched.number, title || matched.result.title, href))
            } else {
              const webHref = safeWebUrl(href) || safeWebUrl(matched.result.url) || ''
              children.push(webSupBadge(state, matched.number, title || matched.result.title, webHref))
            }
          } else {
            const isKb = href.startsWith('kb:')
            children.push(unnumberedBadge(state, title, safeWebUrl(href), isKb))
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
      children.push(child)
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
