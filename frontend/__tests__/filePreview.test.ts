// @vitest-environment happy-dom

import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import FilePreview from '@/components/FilePreview/index.vue'

describe('file preview', () => {
  it('can hide the download action when embedded in a multi-file viewer', () => {
    const wrapper = mount(FilePreview, {
      props: {
        path: 'workspace/AGENTS.md',
        content: '# AGENTS',
        showDownload: false,
      },
      global: {
        stubs: {
          NButton: { template: '<button><slot /><slot name="icon" /></button>' },
          NButtonGroup: { template: '<div><slot /></div>' },
          NCode: { template: '<pre><slot /></pre>' },
          NIcon: { template: '<span><slot /></span>' },
          NSpin: { template: '<div><slot /></div>' },
        },
      },
    })

    expect(wrapper.find('[title="下载当前文件"]').exists()).toBe(false)
  })
})
