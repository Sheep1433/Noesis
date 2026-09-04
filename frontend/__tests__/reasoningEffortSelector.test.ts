// @vitest-environment happy-dom

import { flushPromises, mount } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'
import ReasoningEffortSelector from '@/components/Chat/ReasoningEffortSelector.vue'
import {
  isReasoningLevel,
  modelSupportsReasoningEffort,
  reasoningLevelLabel,
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
  it('exposes universal three levels', () => {
    expect(isReasoningLevel('low')).toBe(true)
    expect(isReasoningLevel('medium')).toBe(true)
    expect(isReasoningLevel('high')).toBe(true)
    // 收窄掉的档位
    expect(isReasoningLevel('off')).toBe(false)
    expect(isReasoningLevel('max')).toBe(false)
    expect(isReasoningLevel('')).toBe(false)
  })

  it('labels include auto', () => {
    expect(reasoningLevelLabel('')).toBe('自动')
    expect(reasoningLevelLabel('low')).toBe('低')
    expect(reasoningLevelLabel('medium')).toBe('中')
    expect(reasoningLevelLabel('high')).toBe('高')
  })

  it('gates entry by known-supporting model names', () => {
    // 支持系
    expect(modelSupportsReasoningEffort('deepseek-v4-flash')).toBe(true)
    expect(modelSupportsReasoningEffort('glm-5.2')).toBe(true)
    expect(modelSupportsReasoningEffort('kimi-k3')).toBe(true)
    expect(modelSupportsReasoningEffort('gpt-5.1')).toBe(true)
    expect(modelSupportsReasoningEffort('o4-mini')).toBe(true)
    // 自定义复合身份（slug/model_id）也命中
    expect(modelSupportsReasoningEffort('volcano/deepseek-v4-flash')).toBe(true)
    // 不支持系
    expect(modelSupportsReasoningEffort('qwen-plus')).toBe(false)
    expect(modelSupportsReasoningEffort('kilo-auto/free')).toBe(false)
    expect(modelSupportsReasoningEffort('claude-opus-4-8')).toBe(false)
    expect(modelSupportsReasoningEffort('glm-4.6')).toBe(false)
    expect(modelSupportsReasoningEffort('')).toBe(false)
  })
})

describe('reasoning effort selector', () => {
  it('shows entry for supported models, hides for others', async () => {
    const wrapper = mountSelector({ modelId: 'qwen-plus' })
    await flushPromises()
    expect(wrapper.find('button').exists()).toBe(false)

    await wrapper.setProps({ modelId: 'deepseek-v4-flash' })
    await flushPromises()
    expect(wrapper.find('button').exists()).toBe(true)
    expect(wrapper.find('button').text()).toContain('自动')
  })

  it('entry label reflects each slider stop and returns to auto', async () => {
    const wrapper = mountSelector({ modelId: 'deepseek-v4-flash' })
    await flushPromises()
    // 停靠点：自动（默认，不发参数）→ 低 → 中 → 高
    for (const [value, label] of [['low', '低'], ['medium', '中'], ['high', '高']]) {
      await wrapper.setProps({ modelValue: value })
      await flushPromises()
      expect(wrapper.find('button').text()).toContain(label)
    }
    await wrapper.setProps({ modelValue: '' })
    await flushPromises()
    expect(wrapper.find('button').text()).toContain('自动')
  })
})
