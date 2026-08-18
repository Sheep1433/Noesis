import type { RetrievalResultUi } from '@/views/chat/messageParts'
import { describe, expect, it } from 'vitest'
import MarkdownInstance from '@/components/MarkdownPreview/plugins/markdown'
import { citationBody, citationTargets } from '@/views/chat/citationRendering'

const results: RetrievalResultUi[] = [
  {
    evidence_id: 'web-1',
    source_type: 'web',
    url: 'https://example.com/report',
    title: 'Example report',
    excerpt: 'web excerpt',
  },
  {
    evidence_id: 'kb-1',
    source_type: 'knowledge_base',
    collection_name: 'requirement_docs',
    title: '登录需求.md',
    excerpt: '验证码五分钟内有效',
  },
]

const markdown = `Web 结论。[1]\n\nKB 结论。[2]\n\n### 参考资料\n\n[1] Example report — https://example.com/report\n[2] 登录需求.md — Collection: requirement_docs`

describe('numbered citation rendering', () => {
  it('renders a raw citation marker as a superscript', () => {
    const html = MarkdownInstance.render(
      '酒精可致胎儿中枢神经系统异常citation:2',
      { retrievalResults: results },
    )

    expect(html).toContain('class="citation-badge citation-badge--number"')
    expect(html).toContain('data-citation-number="2"')
    expect(html).toContain('>2</sup>')
    expect(html).not.toContain('异常citation:2')
  })

  it('renders the prompt citation link format as a source badge', () => {
    const html = MarkdownInstance.render('[citation:Example report](https://example.com/report)')

    expect(html).toContain('class="citation-badge"')
    expect(html).toContain('href="https://example.com/report"')
    expect(html).not.toContain('[citation:Example report]')
  })

  it('matches real Web and KB retrieval results and renders clickable superscripts', () => {
    const targets = citationTargets(
      markdown,
      results,
      (collection, file) => `/knowledgeBase/collection/${collection}?file=${encodeURIComponent(file)}`,
    )
    const html = MarkdownInstance.render(citationBody(markdown, targets), { citationTargets: targets })

    expect(targets.size).toBe(2)
    expect(html.match(/class="citation-sup"/g)).toHaveLength(2)
    expect(html).toContain('data-citation-number="1"')
    expect(html).toContain('data-citation-number="2"')
    expect(html).not.toContain('href="https://example.com/report"')
    expect(html).not.toContain('Example report —')
  })

  it('does not create a clickable citation for an unknown or ambiguous source', () => {
    const ambiguous = [...results, {
      ...results[0],
      evidence_id: 'web-2',
      url: 'https://example.com/report?mirror=1',
    }]
    const ambiguousMarkdown = markdown.replace(
      'https://example.com/report',
      'https://example.com/report and https://example.com/report?mirror=1',
    )
    const targets = citationTargets(ambiguousMarkdown, ambiguous, () => '/kb')
    const html = MarkdownInstance.render(citationBody(ambiguousMarkdown, targets), { citationTargets: targets })

    expect(targets.has(1)).toBe(false)
    expect(html).toContain('Web 结论。[1]')
    expect(html).toContain('Example report —')
    expect(targets.has(2)).toBe(true)
    expect(html.match(/class="citation-sup"/g)).toHaveLength(1)
  })

  it('deduplicates multiple KB segments from the same document', () => {
    const targets = citationTargets(
      markdown,
      [...results, { ...results[1], evidence_id: 'kb-2', segment_id: 'segment-2' }],
      () => '/kb',
    )

    expect(targets.get(2)?.href).toBe('/kb')
  })

  it('matches a KB citation followed by parenthesized locator text', () => {
    const withLocator = markdown.replace(
      '[2] 登录需求.md — Collection: requirement_docs',
      '[2] 登录需求.md — Collection: requirement_docs（第三页，登录流程）',
    )

    expect(citationTargets(withLocator, results, () => '/kb').get(2)?.href).toBe('/kb')
  })

  it('rejects an ambiguous parenthesized Collection identity', () => {
    const sameNameResults: RetrievalResultUi[] = [
      { ...results[1], evidence_id: 'kb-docs', collection_name: 'docs' },
      { ...results[1], evidence_id: 'kb-archive', collection_name: 'docs（archive）' },
    ]
    const archived = '归档结论。[1]\n\n### 参考资料\n\n[1] 登录需求.md — Collection: docs（archive）'

    expect(citationTargets(archived, sameNameResults, (collection) => `/kb/${collection}`).has(1))
      .toBe(false)
  })

  it('rejects unsafe Web URLs', () => {
    const unsafe = [{ ...results[0], url: 'javascript:alert(1)' }]
    expect(citationTargets(markdown, unsafe, () => '/kb')).toEqual(new Map())
  })

  it('rejects a duplicated reference number', () => {
    const duplicated = `${markdown}\n[1] 登录需求.md — Collection: requirement_docs`
    expect(citationTargets(duplicated, results, () => '/kb').has(1)).toBe(false)
  })

  it('rejects different numbers assigned to the same source', () => {
    const duplicatedSource = markdown
      .replace('KB 结论。[2]', 'Web 重复结论。[2]')
      .replace('[2] 登录需求.md — Collection: requirement_docs', '[2] Example report — https://example.com/report')
    const targets = citationTargets(duplicatedSource, results, () => '/kb')
    expect(targets.has(1)).toBe(false)
    expect(targets.has(2)).toBe(false)
  })

  it('does not cite a source that only appears in the reference list', () => {
    const withoutBodyMarker = markdown.replace('Web 结论。[1]', 'Web 结论。')
    expect(citationTargets(withoutBodyMarker, results, () => '/kb').has(1)).toBe(false)
  })

  it('does not treat a citation number inside code as a body citation', () => {
    const codeOnly = markdown.replace('Web 结论。[1]', 'Web 结论。`[1]`')
    expect(citationTargets(codeOnly, results, () => '/kb').has(1)).toBe(false)
  })

  it('matches a Web citation by canonical URL when the displayed title changes', () => {
    const rewrittenTitle = markdown.replace('Example report —', '模型改写后的标题 —')
    expect(citationTargets(rewrittenTitle, results, () => '/kb').has(1)).toBe(true)
  })

  it('matches a Web citation when publisher text appears before the trailing URL', () => {
    const withPublisher = markdown.replace(
      'Example report — https://example.com/report',
      'Example report — Example Publisher，https://example.com/report',
    )

    expect(citationTargets(withPublisher, results, () => '/kb').has(1)).toBe(true)
  })

  it('supports deep-research heading, numbered reference lines, and domain-prefixed markers', () => {
    const markdown = `结论：Mem0 和 Zep 的效果如文献所示[A3][A4]。\n\n## 参考资料（精选）\n\n3. Mem0 — https://arxiv.org/abs/2504.19413\n4. Zep — https://arxiv.org/abs/2501.13956`
    const results = [
      { evidence_id: 'mem0', source_type: 'web' as const, title: 'Mem0', excerpt: '', url: 'https://arxiv.org/abs/2504.19413' },
      { evidence_id: 'zep', source_type: 'web' as const, title: 'Zep', excerpt: '', url: 'https://arxiv.org/abs/2501.13956' },
    ]
    const targets = citationTargets(markdown, results, () => '/kb')
    const html = MarkdownInstance.render(citationBody(markdown, targets), { citationTargets: targets })

    expect(targets.has(3)).toBe(true)
    expect(targets.has(4)).toBe(true)
    expect(html).toContain('data-citation-number="3"')
    expect(html).toContain('data-citation-number="4"')
  })

  it('matches a Web citation when retrieval URL carries tracking query parameters', () => {
    const withQuery = markdown.replace(
      'https://example.com/report',
      'https://m.sohu.com/a/1062516673',
    ).replace('Example report', '搜狐报道')
    const resultsWithQuery = [{
      evidence_id: 'web-q',
      source_type: 'web' as const,
      url: 'https://m.sohu.com/a/1062516673?scm=10001.325_13-325_13.0.0-0-0-0-0.5_133',
      title: '搜狐报道',
      excerpt: '',
    }]
    expect(citationTargets(withQuery, resultsWithQuery, () => '/kb').has(1)).toBe(true)
  })

  it('renders each visible reference on its own line when matching is incomplete', () => {
    const unknownSource = markdown.replace('https://example.com/report', 'https://unknown.example/report')
    const targets = citationTargets(unknownSource, results, () => '/kb')
    const html = MarkdownInstance.render(citationBody(unknownSource, targets), { citationTargets: targets })
    expect(html).toContain('</p>\n<p>[2]')
  })

  it('keeps references while streaming or when prose follows them', () => {
    const targets = citationTargets(markdown, results, () => '/kb')
    expect(citationBody(markdown, targets, false)).toBe(markdown)
    const trailingProse = `${markdown}\n\n补充说明。`
    const displayed = citationBody(trailingProse, targets)
    expect(displayed).toContain('\n\n[2] 登录需求.md')
    expect(displayed).toContain('补充说明。')
  })
})
