import MarkdownIt from 'markdown-it'
import markdownItHighlight from 'markdown-it-highlightjs'
import hljs from './highlight'
import { mermaidPlugin } from './mermaid'
import { preWrapperPlugin } from './preWrapper'

const md = new MarkdownIt({
  html: true,
  linkify: true,
  typographer: true,
})

// 拦截 [citation:标题](ref) 链接，渲染成 badge 样式
md.core.ruler.after('inline', 'citation-badges', (state) => {
  for (const token of state.tokens) {
    if (token.type !== 'inline' || !token.children) {
      continue
    }
    const children: typeof token.children = []
    for (const child of token.children) {
      if (child.type !== 'text') {
        children.push(child)
        continue
      }
      let cursor = 0
      const re = /\[citation: ([^\]]+)\]\(([^)]+)\)/gi
      for (const match of child.content.matchAll(re)) {
        if (match.index == null) {
          continue
        }
        const title = match[1].trim()
        const ref = match[2].trim()
        const isWeb = /^https?:\/\//i.test(ref)
        const isKb = ref.startsWith('kb:')
        if (!isWeb && !isKb) {
          continue
        }
        const display = isWeb ? extractDomain(ref) : title
        const href = isWeb ? ref : ''
        const titleEsc = md.utils.escapeHtml(title)

        if (match.index > cursor) {
          const before = new state.Token('text', '', 0)
          before.content = child.content.slice(cursor, match.index)
          children.push(before)
        }

        const badge = new state.Token('html_inline', '', 0)
        if (isWeb) {
          badge.content = `<sup class="citation-badge"><a href="${md.utils.escapeHtml(href)}" target="_blank" rel="noopener noreferrer" title="${titleEsc}">${md.utils.escapeHtml(display)}</a></sup>`
        } else {
          badge.content = `<sup class="citation-badge citation-badge--kb" data-kb-ref="${md.utils.escapeHtml(ref)}" role="button" tabindex="0" title="${titleEsc}">${md.utils.escapeHtml(display)}</sup>`
        }
        children.push(badge)
        cursor = match.index + match[0].length
      }
      if (cursor === 0) {
        children.push(child)
      } else if (cursor < child.content.length) {
        const after = new state.Token('text', '', 0)
        after.content = child.content.slice(cursor)
        children.push(after)
      }
    }
    token.children = children
  }
})

function extractDomain(url: string): string {
  try {
    return new URL(url).hostname.replace(/^www\./i, '')
  } catch {
    return url
  }
}

// Customize the image rendering rule
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

// 确保正确使用 hljs 实例
md.use(markdownItHighlight, {
  hljs,
  auto: true,
  code: true,
}).use(mermaidPlugin).use(preWrapperPlugin, {
  hasSingleTheme: true,
})
export default md
