import type MarkdownIt from 'markdown-it'

export function mermaidPlugin(md: MarkdownIt) {
  const defaultFence = md.renderer.rules.fence!
  md.renderer.rules.fence = (tokens, idx, options, env, self) => {
    const token = tokens[idx]
    const info = token.info.trim().split(/\s+/g)[0]
    if (info === 'mermaid') {
      const code = md.utils.escapeHtml(token.content.trim())
      return `<div class="mermaid">${code}</div>\n`
    }
    return defaultFence(tokens, idx, options, env, self)
  }
}
