import type { CitationTarget } from '@/views/chat/citationRendering'
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

md.core.ruler.after('inline', 'citation-superscripts', (state) => {
  const targets = (state.env?.citationTargets || new Map()) as Map<number, CitationTarget>
  if (targets.size === 0) {
    return
  }
  let referencesStarted = false
  for (let i = 0; i < state.tokens.length; i++) {
    const token = state.tokens[i]
    if (token.type === 'heading_open') {
      const heading = state.tokens[i + 1]
      if (heading?.type === 'inline' && heading.content.trim() === '参考资料') {
        referencesStarted = true
      }
    }
    if (referencesStarted || token.type !== 'inline' || !token.children) {
      continue
    }
    const children = []
    for (const child of token.children) {
      if (child.type !== 'text') {
        children.push(child)
        continue
      }
      let cursor = 0
      for (const match of child.content.matchAll(/\[(\d+)\]/g)) {
        const target = targets.get(Number(match[1]))
        if (!target || match.index == null) {
          continue
        }
        if (match.index > cursor) {
          const before = new state.Token('text', '', 0)
          before.content = child.content.slice(cursor, match.index)
          children.push(before)
        }
        const marker = new state.Token('html_inline', '', 0)
        const title = md.utils.escapeHtml(target.title)
        marker.content = `<sup class="citation-sup"><button type="button" class="citation-link" title="${title}" aria-label="查看引用 ${match[1]}：${title}" data-citation-number="${match[1]}">${match[1]}</button></sup>`
        children.push(marker)
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
