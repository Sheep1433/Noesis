// @vitest-environment happy-dom

import { flushPromises, mount } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'
import SubagentCollapse from '@/components/SubagentCollapse/index.vue'

vi.mock('@/components/MarkdownPreview/index.vue', () => ({
  default: { props: ['content'], template: '<div class="md-stub">{{ content }}</div>' },
}))

function mountCollapse(overrides: Record<string, unknown> = {}) {
  return mount(SubagentCollapse, {
    props: {
      input: { description: '调研 Agent Eval 框架', subagent_type: 'general-purpose', prompt: '任务指令正文' },
      output: 'Task Succeeded. Result: ok',
      durationMs: 281_000,
      ...overrides,
    },
  })
}

describe('subagent collapse flat presentation', () => {
  it('header renders flat row with kind, type, description and plain status (no tags)', () => {
    const wrapper = mountCollapse()
    const header = wrapper.find('.subagent-header')
    expect(header.text()).toContain('子智能体')
    expect(header.find('.subagent-header__type').text()).toBe('general-purpose')
    expect(header.find('.subagent-header__desc').text()).toBe('调研 Agent Eval 框架')
    expect(header.find('.subagent-header__duration').text()).toBe('4m 41s')
    expect(header.find('.subagent-header__status').text()).toBe('已完成')
    // 去药丸化：状态不再用 n-tag 渲染
    expect(wrapper.find('.n-tag').exists()).toBe(false)
  })

  it('chevron rotates and content expands on click', async () => {
    const wrapper = mountCollapse()
    expect(wrapper.find('.subagent-header__chevron--open').exists()).toBe(false)

    await wrapper.find('.subagent-header').trigger('click')
    await flushPromises()
    expect(wrapper.find('.subagent-header__chevron--open').exists()).toBe(true)
    expect(wrapper.text()).toContain('任务指令正文')
  })

  it('same-description subagents expand independently', async () => {
    const first = mountCollapse()
    const second = mountCollapse()

    await first.find('.subagent-header').trigger('click')
    await flushPromises()

    expect(first.find('.subagent-header__chevron--open').exists()).toBe(true)
    expect(second.find('.subagent-header__chevron--open').exists()).toBe(false)
  })
})
