import { describe, expect, it } from 'vitest'
import {
  isChatRouteName,
  mainNavItems,
  mobileHistoryNavItems,
  mobileProductNavItems,
  shouldShowMobileBottomNav,
} from '../src/config/navigation'
import { composerPlaceholder } from '../src/config/subagents'
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
  it.each(['Settings', 'KnowledgeBase'])(
    'shows the bottom navigation on the %s page',
    (routeName) => {
      expect(shouldShowMobileBottomNav(routeName, true)).toBe(true)
    },
  )

  it('keeps nested knowledge base details focused on their local content', () => {
    expect(shouldShowMobileBottomNav('KnowledgeBaseDetail', true)).toBe(false)
  })

  it('preserves navigation on other mobile product pages', () => {
    expect(shouldShowMobileBottomNav('Extensions', true)).toBe(true)
  })

  it('never shows mobile navigation on desktop', () => {
    expect(shouldShowMobileBottomNav('Settings', false)).toBe(false)
  })
})

describe('public product navigation', () => {
  it('does not expose the retired test-case page', () => {
    expect(mainNavItems.map((item) => item.routeName)).not.toContain('TestCaseGenerate')
  })

  it('uses the four top-level product entries for mobile navigation', () => {
    expect(mobileProductNavItems.map((item) => item.routeName)).toEqual([
      'ChatIndex',
      'KnowledgeBase',
      'Extensions',
      'Settings',
    ])
  })

  it('keeps the history drawer shortcuts to three management entries', () => {
    expect(mobileHistoryNavItems.map((item) => item.routeName)).toEqual([
      'KnowledgeBase',
      'Extensions',
      'Settings',
    ])
  })
})

describe('composer placeholder', () => {
  it('describes shortcuts in product language', () => {
    expect(composerPlaceholder('SUPER_AGENT_QA', false)).toBe(
      '输入消息，使用 / 调用 Skill，使用 @ 引用文件或协作助手…',
    )
    expect(composerPlaceholder('FAULT_OPERATION_QA', false)).toBe(
      '输入消息，使用 @ 引用文件或协作助手…',
    )
    expect(composerPlaceholder('COMMON_QA', false)).toBe('输入消息…')
    expect(composerPlaceholder('COMMON_QA', true)).toBe('正在上传附件…')
  })
})
