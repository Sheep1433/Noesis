// @vitest-environment happy-dom

import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import FilePreview from '@/components/FilePreview/index.vue'
import { buildCitationIndex, citationKey } from '@/views/chat/citationRendering'

const STUBS = {
  NButton: { template: '<button><slot /><slot name="icon" /></button>' },
  NButtonGroup: { template: '<div><slot /></div>' },
  NCode: { template: '<pre><slot /></pre>' },
  NIcon: { template: '<span><slot /></span>' },
  NSpin: { template: '<div><slot /></div>' },
}

describe('file preview', () => {
  it('can hide the download action when embedded in a multi-file viewer', () => {
    const wrapper = mount(FilePreview, {
      props: {
        path: 'workspace/AGENTS.md',
        content: '# AGENTS',
        showDownload: false,
      },
      global: {
        stubs: STUBS,
      },
    })

    expect(wrapper.find('[title="下载当前文件"]').exists()).toBe(false)
  })

  it('renders html files in a sandboxed iframe and can switch to source', async () => {
    const wrapper = mount(FilePreview, {
      props: {
        path: 'workspace/report.html',
        content: '<!DOCTYPE html><html><body><h1>报告</h1></body></html>',
      },
      global: {
        stubs: STUBS,
      },
    })

    const iframe = wrapper.find('iframe.file-preview__html')
    expect(iframe.exists()).toBe(true)
    // 沙箱仅允许脚本：无 allow-same-origin，无法访问应用 cookie/localStorage
    expect(iframe.attributes('sandbox')).toBe('allow-scripts')
    const srcdoc = iframe.attributes('srcdoc') ?? ''
    expect(srcdoc).toContain('<h1>报告</h1>')
    // 垫片：锚点点击改为同文档滚动，避免沙箱下 #fragment 导航被拦导致白屏
    expect(srcdoc).toContain('scrollIntoView')
    expect(srcdoc.endsWith('</script>')).toBe(true)

    // 切到源码视图：iframe 消失，源码块出现
    await wrapper.findAll('button').find((b) => b.text() === '源码')!.trigger('click')
    expect(wrapper.find('iframe.file-preview__html').exists()).toBe(false)
    expect(wrapper.find('.file-preview__code').exists()).toBe(true)
  })

  it('renders unnumbered citation badges without a citation index', () => {
    const wrapper = mount(FilePreview, {
      props: {
        path: 'workspace/report.md',
        content: '结论 [citation:来源](https://example.com/a)',
      },
      global: { stubs: STUBS },
    })
    const badge = wrapper.find('.citation-badge')
    expect(badge.exists()).toBe(true)
    // 无索引：无编号「·」上标，不编造序号
    expect(badge.text()).toBe('·')
    expect(badge.attributes('data-citation-number')).toBeUndefined()
  })

  it('renders numbered citation badges when the arc citation index is provided', () => {
    const result = { evidence_id: 'w1', source_type: 'web', url: 'https://example.com/a', title: '来源', excerpt: 'excerpt' }
    const index = buildCitationIndex([result as never])
    const wrapper = mount(FilePreview, {
      props: {
        path: 'workspace/report.md',
        content: '结论 [citation:来源](https://example.com/a)',
        citationIndex: index,
      },
      global: { stubs: STUBS },
    })
    const badge = wrapper.find('[data-citation-number="1"]')
    expect(badge.exists()).toBe(true)
    expect(badge.text()).toBe('1')
    // KB 徽章携带 data-kb-ref，点击由 onContentClick 路由
    const kbResult = { evidence_id: 'kb-1', source_type: 'knowledge_base', collection_name: 'docs', title: '登录需求.md', excerpt: 'excerpt' }
    const kbIndex = buildCitationIndex([kbResult as never])
    const kbWrapper = mount(FilePreview, {
      props: {
        path: 'workspace/report.md',
        content: '要求 [citation:登录需求.md](kb:docs/登录需求.md)',
        citationIndex: kbIndex,
      },
      global: { stubs: STUBS },
    })
    expect(kbWrapper.find('.citation-badge--kb').attributes('data-kb-ref')).toBe('kb:docs/登录需求.md')
    expect(citationKey(result as never)).toBe('web:https://example.com/a')
  })
})
