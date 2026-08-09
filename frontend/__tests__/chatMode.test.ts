import { describe, expect, it } from 'vitest'
import { isChatRouteName, shouldShowMobileBottomNav } from '../src/config/navigation'
import {
  CHAT_MODE_OPTIONS,
  chatModeOption,
  isChatModeChange,
  qaTypeLabel,
} from '../src/utils/qaType'

describe('chat mode presentation', () => {
  it('maps product labels to the existing qa types', () => {
    expect(CHAT_MODE_OPTIONS.map(({ qaType, label }) => ({ qaType, label }))).toEqual([
      { qaType: 'COMMON_QA', label: '聊天' },
      { qaType: 'SUPER_AGENT_QA', label: '任务' },
      { qaType: 'FAULT_OPERATION_QA', label: '故障排查' },
    ])
    expect(qaTypeLabel('COMMON_QA')).toBe('聊天')
    expect(qaTypeLabel('SUPER_AGENT_QA')).toBe('任务')
  })

  it('keeps historical deep research sessions in task mode', () => {
    expect(chatModeOption('DEEP_RESEARCH_QA').qaType).toBe('SUPER_AGENT_QA')
    expect(qaTypeLabel('DEEP_RESEARCH_QA')).toBe('任务')
  })

  it('starts a new composing surface only when the mode changes', () => {
    expect(isChatModeChange('COMMON_QA', 'COMMON_QA')).toBe(false)
    expect(isChatModeChange('COMMON_QA', 'SUPER_AGENT_QA')).toBe(true)
    expect(isChatModeChange('DEEP_RESEARCH_QA', 'SUPER_AGENT_QA')).toBe(true)
  })
})

describe('immersive mobile chat routes', () => {
  it.each(['ChatRoot', 'ChatIndex', 'ChatNew', 'ChatSession'])('includes %s', (routeName) => {
    expect(isChatRouteName(routeName)).toBe(true)
  })

  it.each(['KnowledgeBase', 'Extensions', 'TestCaseGenerate', 'Settings'])('excludes %s', (routeName) => {
    expect(isChatRouteName(routeName)).toBe(false)
  })
})

describe('mobile bottom navigation', () => {
  it.each(['Settings', 'KnowledgeBase', 'KnowledgeBaseDetail'])(
    'stays hidden on the %s page',
    (routeName) => {
      expect(shouldShowMobileBottomNav(routeName, true)).toBe(false)
    },
  )

  it('preserves navigation on other mobile product pages', () => {
    expect(shouldShowMobileBottomNav('Extensions', true)).toBe(true)
  })

  it('never shows mobile navigation on desktop', () => {
    expect(shouldShowMobileBottomNav('Settings', false)).toBe(false)
  })
})
