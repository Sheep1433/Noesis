// @vitest-environment happy-dom

import { flushPromises, mount } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'
import ReasoningEffortSelector from '@/components/Chat/ReasoningEffortSelector.vue'
import {
  isReasoningLevel,
  reasoningLevelLabel,
  reasoningLevelOptions,
} from '@/utils/reasoningLevels'

vi.mock('@/api/chat', () => ({
  ensureSession: vi.fn().mockResolvedValue({}),
}))

function mountSelector(props: Record<string, unknown> = {}) {
  return mount(ReasoningEffortSelector, {
    props: {
      sessionId: 'sess-1',
      ...props,
    },
  })
}

describe('reasoning level utils', () => {
  it('exposes select options and labels', () => {
    expect(reasoningLevelOptions()).toEqual([
      { label: '关', value: 'off' },
      { label: '低', value: 'low' },
      { label: '中', value: 'medium' },
      { label: '高', value: 'high' },
      { label: '最高', value: 'max' },
    ])
    expect(reasoningLevelLabel('')).toBe('自动')
    expect(reasoningLevelLabel('high')).toBe('高')
    expect(isReasoningLevel('low')).toBe(true)
    expect(isReasoningLevel('')).toBe(false)
  })
})

describe('reasoning effort selector', () => {
  it('always renders for any model (no capability gating)', async () => {
    // 无模型信息、无声明——控件常显
    const wrapper = mountSelector()
    await flushPromises()
    expect(wrapper.find('button').exists()).toBe(true)
    expect(wrapper.find('button').text()).toContain('自动')
  })

  it('entry label reflects each level and returns to auto', async () => {
    const wrapper = mountSelector()
    await flushPromises()
    for (const [level, label] of [['off', '关'], ['low', '低'], ['medium', '中'], ['high', '高'], ['max', '最高']]) {
      await wrapper.setProps({ modelValue: level })
      await flushPromises()
      expect(wrapper.find('button').text()).toContain(label)
    }
    await wrapper.setProps({ modelValue: '' })
    await flushPromises()
    expect(wrapper.find('button').text()).toContain('自动')
  })
})
