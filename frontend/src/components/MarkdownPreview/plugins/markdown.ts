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
