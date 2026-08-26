// @vitest-environment happy-dom

import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import FilePreview from '@/components/FilePreview/index.vue'

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
})
