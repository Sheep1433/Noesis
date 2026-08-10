// @vitest-environment happy-dom

import { flushPromises, mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import ContextWindowIndicator from '@/components/ContextWindowIndicator/index.vue'

describe('context window indicator', () => {
  it('shows context usage percentage and token total', async () => {
    const wrapper = mount(ContextWindowIndicator, {
      props: {
        context: {
          current_tokens: 24_800,
          max_tokens: 200_000,
          used_percentage: 12.4,
        },
      },
    })

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
})
