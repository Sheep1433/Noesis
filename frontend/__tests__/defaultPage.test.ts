// @vitest-environment happy-dom

import { mount } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'
import DefaultPage from '@/views/DefaultPage.vue'

vi.hoisted(() => {
  const values = new Map<string, string>()
  Object.defineProperty(globalThis, 'localStorage', {
    configurable: true,
    value: {
      clear: () => values.clear(),
      getItem: (key: string) => values.get(key) ?? null,
      key: (index: number) => [...values.keys()][index] ?? null,
      get length() {
        return values.size
      },
      removeItem: (key: string) => values.delete(key),
      setItem: (key: string, value: string) => values.set(key, String(value)),
    },
  })
})

vi.mock('@/hooks/useBreakpoint', () => ({
  useBreakpoint: () => ({ isMobile: { value: true } }),
}))

describe('mobile chat welcome', () => {
  it.each([
    {
      qaType: 'COMMON_QA',
      title: '智能问答',
      subtitle: '基于 RAG 与向量检索的通用智能问答',
      points: [
        '① RAG 检索增强，结合知识库精准作答',
        '② 向量检索提升相关片段召回质量',
      ],
    },
    {
      qaType: 'SUPER_AGENT_QA',
      title: '智能体',
      subtitle: '通用超级智能体：调研、检索、分析与多步任务编排',
      points: [
        '① 网络检索与多源信息综合',
        '② 适合调研、对比与事实核查类问题',
      ],
    },
    {
      qaType: 'FAULT_OPERATION_QA',
      title: '故障运维',
      subtitle: '面向故障诊断、排查与恢复的专项助手',
      points: [
        '① 多步骤分析定位故障根因',
        '② 结合 MCP 工具读日志、执行运维指令',
      ],
    },
    {
      qaType: 'DEEP_RESEARCH_QA',
      title: '智能体',
      subtitle: '通用超级智能体：调研、检索、分析与多步任务编排',
      points: [
        '① 网络检索与多源信息综合',
        '② 适合调研、对比与事实核查类问题',
      ],
    },
  ])('keeps $qaType capability copy in a compact initial view', ({ qaType, title, subtitle, points }) => {
    const wrapper = mount(DefaultPage, { props: { qaType } })

    expect(wrapper.get('.mobile-intro__title').text()).toBe(title)
    expect(wrapper.get('.mobile-intro__subtitle').text()).toBe(subtitle)
    expect(wrapper.findAll('.mobile-intro__point').map((item) => item.text())).toEqual(points)
    expect(wrapper.get('.mobile-intro').attributes('style')).toContain('background')
    expect(wrapper.find('.welcome-header').exists()).toBe(false)
    wrapper.unmount()
  })
})
