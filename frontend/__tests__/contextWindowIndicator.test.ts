// @vitest-environment happy-dom

import { flushPromises, mount } from '@vue/test-utils'
import { NPopover } from 'naive-ui'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { ref } from 'vue'
import ContextWindowIndicator from '@/components/ContextWindowIndicator/index.vue'

vi.mock('@/hooks/useBreakpoint', () => ({
  useBreakpoint: vi.fn(),
}))

const { useBreakpoint } = await import('@/hooks/useBreakpoint')

function mountIndicator() {
  return mount(ContextWindowIndicator, {
    props: {
      context: {
        current_tokens: 24_800,
        max_tokens: 200_000,
        used_percentage: 12.4,
      },
    },
  })
}

describe('context window indicator', () => {
  beforeEach(() => {
    document.body.innerHTML = ''
  })

  it('shows context usage percentage and token total', async () => {
    vi.mocked(useBreakpoint).mockReturnValue({ isMobile: ref(false) })
    const wrapper = mountIndicator()

    expect(wrapper.text()).toContain('12%')
    await wrapper.get('.context-window-indicator').trigger('click')
    await flushPromises()
    expect(document.body.textContent).toContain('Context Usage')
    expect(document.body.textContent).toContain('12% Full')
    expect(document.body.textContent).toContain('24.8K / 200K Tokens')
    // 无分项行
    expect(document.body.querySelector('.context-usage-panel__row')).toBeNull()
    // 进度条只有单色填充，无 segment
    expect(document.querySelectorAll('.context-usage-panel__bar-segment')).toHaveLength(0)
    expect(document.querySelector('.context-usage-panel__bar-fill')).not.toBeNull()
  })

  it('desktop keeps top-end placement with full-width panel', async () => {
    vi.mocked(useBreakpoint).mockReturnValue({ isMobile: ref(false) })
    const wrapper = mountIndicator()

    expect(wrapper.findComponent(NPopover).props('placement')).toBe('top-end')
    await wrapper.get('.context-window-indicator').trigger('click')
    await flushPromises()
    expect(document.querySelector('.context-usage-panel')?.classList.contains('context-usage-panel--mobile')).toBe(false)
  })

  it('mobile uses top placement and narrowed panel to avoid left-edge overflow', async () => {
    vi.mocked(useBreakpoint).mockReturnValue({ isMobile: ref(true) })
    const wrapper = mountIndicator()

    // 移动端触发器贴右缘，top-end 会让面板左端溢出屏幕，须居中对齐并收窄宽度
    expect(wrapper.findComponent(NPopover).props('placement')).toBe('top')
    await wrapper.get('.context-window-indicator').trigger('click')
    await flushPromises()
    expect(document.querySelector('.context-usage-panel')?.classList.contains('context-usage-panel--mobile')).toBe(true)
  })
})
