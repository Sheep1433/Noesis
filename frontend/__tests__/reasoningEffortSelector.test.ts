// @vitest-environment happy-dom

import { flushPromises, mount } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'
import ReasoningEffortSelector from '@/components/Chat/ReasoningEffortSelector.vue'
import {
  isReasoningLevel,
  orderReasoningLevels,
  reasoningLevelLabel,
  reasoningLevelOptions,
} from '@/utils/reasoningLevels'

const getChatModels = vi.fn()

vi.mock('@/api/models', () => ({
  getChatModels: (...args: unknown[]) => getChatModels(...args),
}))

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
  it('orders declared levels in fixed order and drops invalid ones', () => {
    expect(orderReasoningLevels(['max', 'low', 'off'])).toEqual(['off', 'low', 'max'])
    expect(orderReasoningLevels(['xhigh', 'medium', 'medium'])).toEqual(['medium'])
    expect(orderReasoningLevels([])).toEqual([])
    expect(orderReasoningLevels(null)).toEqual([])
    expect(orderReasoningLevels('low')).toEqual([])
  })

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
    expect(reasoningLevelLabel('xhigh')).toBe('xhigh')
    expect(isReasoningLevel('low')).toBe(true)
    expect(isReasoningLevel('')).toBe(false)
    expect(isReasoningLevel('xhigh')).toBe(false)
  })
})

describe('reasoning effort selector', () => {
  it('hides when current model declares no reasoning levels', async () => {
    getChatModels.mockResolvedValue({
      models: [
        { id: 'm1', label: 'M1', model_type: 'openai', is_default: true, reasoning_levels: [] },
        { id: 'm2', label: 'M2', model_type: 'openai', is_default: false, reasoning_levels: ['low'] },
      ],
      default_id: 'm1',
    })
    const wrapper = mountSelector({ modelId: 'm1' })
    await flushPromises()
    expect(wrapper.find('button').exists()).toBe(false)

    // 同一选择器在声明了档位的模型上渲染触发按钮
    await wrapper.setProps({ modelId: 'm2' })
    await flushPromises()
    expect(wrapper.find('button').exists()).toBe(true)
    expect(wrapper.find('button').text()).toContain('自动')
  })

  it('shows the selected level on the trigger button', async () => {
    getChatModels.mockResolvedValue({
      models: [{
        id: 'm2', label: 'M2', model_type: 'openai', is_default: true,
        reasoning_levels: ['low', 'high', 'max'],
      }],
      default_id: 'm2',
    })
    const wrapper = mountSelector({ modelId: 'm2' })
    await flushPromises()
    await wrapper.setProps({ modelValue: 'high' })
    await flushPromises()
    expect(wrapper.find('button').text()).toContain('高')
  })

  it('resets to auto when model switches to one without the selected level', async () => {
    getChatModels.mockResolvedValue({
      models: [
        { id: 'm2', label: 'M2', model_type: 'openai', is_default: true, reasoning_levels: ['low', 'high'] },
        { id: 'm3', label: 'M3', model_type: 'openai', is_default: false, reasoning_levels: ['low'] },
      ],
      default_id: 'm2',
    })
    const wrapper = mountSelector({ modelId: 'm2' })
    await flushPromises()
    await wrapper.setProps({ modelValue: 'high' })
    await flushPromises()
    expect(wrapper.vm.modelValue as unknown as string).toBe('high')

    // 切到只声明 low 的模型 → 回退自动（下次发送不传参）
    await wrapper.setProps({ modelId: 'm3' })
    await flushPromises()
    expect(wrapper.vm.modelValue as unknown as string).toBe('')
  })
})
