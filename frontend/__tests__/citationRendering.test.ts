import type { RetrievalResultUi } from '@/views/chat/messageParts'
import { describe, expect, it } from 'vitest'
import MarkdownInstance from '@/components/MarkdownPreview/plugins/markdown'
import { buildCitationIndex, citationKey } from '@/views/chat/citationRendering'

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

const index = buildCitationIndex(results)

describe('citation index', () => {
  it('assigns 1-based numbers in first-seen order', () => {
    expect(index.get('web:https://example.com/report')?.number).toBe(1)
    expect(index.get('kb:requirement_docs:登录需求.md')?.number).toBe(2)
  })

  it('normalizes web URLs by origin+pathname (ignores tracking query)', () => {
    const key = citationKey({
      evidence_id: 'web-q',
      source_type: 'web',
      url: 'https://m.sohu.com/a/1062516673?scm=10001.325_13-325',
      title: '搜狐报道',
      excerpt: '',
    })
    expect(key).toBe('web:https://m.sohu.com/a/1062516673')
  })
})

describe('link-format citation rendering', () => {
  it('renders a web citation as a numbered superscript with safe link attributes', () => {
    const html = MarkdownInstance.render('[citation:Example report](https://example.com/report)', { citationIndex: index })

    expect(html).toContain('data-citation-number="1"')
    expect(html).toContain('href="https://example.com/report"')
    expect(html).toContain('target="_blank"')
    expect(html).toContain('rel="noopener noreferrer"')
    expect(html).not.toContain('[citation:Example report]')
  })

  it('renders a kb citation as a numbered kb badge', () => {
    const html = MarkdownInstance.render('[citation:登录需求.md](kb:requirement_docs/登录需求.md)', { citationIndex: index })

    expect(html).toContain('citation-badge--kb')
    expect(html).toContain('data-citation-number="2"')
    expect(html).toContain('data-kb-ref="kb:requirement_docs/登录需求.md"')
  })

  it('renders a kb citation by ref even when the label title differs', () => {
    const html = MarkdownInstance.render('[citation:登录需求](kb:requirement_docs/登录需求.md)', { citationIndex: index })

    expect(html).toContain('data-citation-number="2"')
  })

  it('falls back to an unnumbered badge for an unknown ref', () => {
    const html = MarkdownInstance.render('[citation:未知文档](kb:other/不存在.md)', { citationIndex: index })

    expect(html).toContain('citation-badge')
    expect(html).not.toContain('data-citation-number=')
    expect(html).not.toContain('[citation:未知文档]')
  })

  it('leaves non-citation links untouched', () => {
    const html = MarkdownInstance.render('[普通链接](https://example.com/report)')

    expect(html).toContain('<a href="https://example.com/report"')
    expect(html).not.toContain('citation-badge')
  })
})

describe('file: citation tolerance (model protocol hallucination)', () => {
  // 模型曾把知识库引用写成 [citation:文件名](file:Collection/文件名)。
  // file: 在 markdown-it validateLink 黑名单内，整条链接退化为原始文本，
  // 这里验证兜底：text token 内的原始引用文本归一为 kb: 后仍渲染成数字上标。
  it('renders a raw file:-scheme citation as a numbered kb badge', () => {
    const html = MarkdownInstance.render(
      '结论甲。[citation:登录需求.md](file:requirement_docs/登录需求.md)',
      { citationIndex: index },
    )

    expect(html).toContain('citation-badge--kb')
    expect(html).toContain('data-citation-number="2"')
    expect(html).toContain('data-kb-ref="kb:requirement_docs/登录需求.md"')
    expect(html).not.toContain('[citation:')
    expect(html).not.toContain('file:')
  })

  it('renders multiple raw file: citations inline and keeps surrounding prose', () => {
    const html = MarkdownInstance.render(
      '结论甲。[citation:登录需求.md](file:requirement_docs/登录需求.md) 结论乙。[citation:Example report](https://example.com/report)',
      { citationIndex: index },
    )

    expect(html).toContain('结论甲。')
    expect(html).toContain('结论乙。')
    expect(html).toContain('data-citation-number="2"')
    expect(html).toContain('data-citation-number="1"')
    expect(html).not.toContain('[citation:')
  })

  it('falls back to an unnumbered badge for a raw file: citation with unknown ref', () => {
    const html = MarkdownInstance.render('[citation:未知](file:other/不存在.md)', { citationIndex: index })

    expect(html).toContain('citation-badge')
    expect(html).not.toContain('data-citation-number=')
    expect(html).not.toContain('[citation:')
  })
})
