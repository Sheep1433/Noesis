import type MarkdownIt from 'markdown-it'

export interface Options {
  codeCopyButtonTitle: string
  hasSingleTheme: boolean
}

const LANG_LABELS: Record<string, string> = {
  bash: 'Bash',
  c: 'C',
  cpp: 'C++',
  css: 'CSS',
  go: 'Go',
  html: 'HTML',
  java: 'Java',
  javascript: 'JavaScript',
  js: 'JavaScript',
  json: 'JSON',
  markdown: 'Markdown',
  md: 'Markdown',
  python: 'Python',
  py: 'Python',
  rust: 'Rust',
  scss: 'SCSS',
  sh: 'Shell',
  shell: 'Shell',
  sql: 'SQL',
  ts: 'TypeScript',
  tsx: 'TSX',
  typescript: 'TypeScript',
  vue: 'Vue',
  xml: 'XML',
  yaml: 'YAML',
  yml: 'YAML',
}

function capitalizeFirstLetter(str: string) {
  return str.replace(/^\w/, (match) => match.toUpperCase())
}

function getBaseLanguageName(lang: string) {
  const key = lang.toLowerCase()
  return LANG_LABELS[key] || capitalizeFirstLetter(lang || 'markdown')
}

export function preWrapperPlugin(md: MarkdownIt, options: Options) {
  const fence = md.renderer.rules.fence!
  md.renderer.rules.fence = (...args) => {
    const [tokens, idx] = args
    const token = tokens[idx]

    token.info = token.info.replace(/\[.*\]/, '')

    const active = / active(?: |$)/.test(token.info) ? ' active' : ''
    token.info = token.info.replace(/ active$/, '').replace(/ active /, ' ')

    const lang = extractLang(token.info)

    const content = fence(...args)
    return (
      `
      <div class="markdown-code-wrapper flex language-${lang}${getAdaptiveThemeMarker(options)}${active}">
        <div class="markdown-code-header">
          <span class="markdown-code-lang">${getBaseLanguageName(lang)}</span>
          <button class="markdown-code-copy">
            <div class="markdown-copy-icon"></div>
            <span class="markdown-copy-text default">复制代码</span>
            <span class="markdown-copy-text done">已复制</span>
          </button>
        </div>
        ${content}
      </div>
      `
    )
  }
}

export function getAdaptiveThemeMarker(options: Options) {
  return options.hasSingleTheme ? '' : ' xx-adaptive-theme'
}

export function extractTitle(info: string, html = false) {
  if (html) {
    return (
      info.replace(/<!--[\s\S]*?-->/g, '').match(/data-title="(.*?)"/)?.[1] || ''
    )
  }
  return info.match(/\[(.*)\]/)?.[1] || extractLang(info) || 'txt'
}

function extractLang(info: string) {
  return info
    .trim()
    .replace(/=\d*/, '')
    .replace(/:(?:no-)?line-numbers(?:\{| |$|=?\d*).*/, '')
    .replace(/(?:-vue|\{| ).*$/, '')
    .replace(/^vue-html$/, 'template')
    .replace(/^ansi$/, '')
}
