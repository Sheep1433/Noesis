// @vitest-environment happy-dom

import { mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import SlotCenterPanel from '@/components/Layout/SlotCenterPanel.vue'

const testState = vi.hoisted(() => ({
  route: { name: 'Settings' },
}))

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

vi.mock('vue-router', async () => {
  const actual = await vi.importActual<typeof import('vue-router')>('vue-router')
  return {
    ...actual,
    useRoute: () => testState.route,
  }
})

vi.mock('@/hooks/useBreakpoint', () => ({
  useBreakpoint: () => ({ isMobile: { value: true } }),
}))

vi.mock('@/store/hooks/useAppStore', () => ({
  useAppStore: () => ({ areaLoading: false }),
}))

const globalStubs = {
  LayoutSlotFrame: { template: '<div><slot name="center" /><slot name="bottom" /></div>' },
  NSpin: { template: '<div><slot /></div>' },
  LayoutDefault: true,
  NavigationNavFooter: true,
  MobileBottomNav: { template: '<nav data-testid="mobile-bottom-nav"></nav>' },
}

describe('mobile layout navigation', () => {
  beforeEach(() => {
    vi.useFakeTimers()
  })

  afterEach(() => {
    vi.runOnlyPendingTimers()
    vi.useRealTimers()
  })

  it.each(['Settings', 'KnowledgeBase'])(
    'shows the bottom navigation and reserves its space on %s',
    (routeName) => {
      testState.route.name = routeName
      const wrapper = mount(SlotCenterPanel, { global: { stubs: globalStubs } })

      expect(wrapper.find('[data-testid="mobile-bottom-nav"]').exists()).toBe(true)
      expect(wrapper.get('.app-shell__main').classes()).not.toContain('app-shell__main--mobile-no-nav')
      wrapper.unmount()
    },
  )

  it('hides the bottom navigation on nested knowledge base details', () => {
    testState.route.name = 'KnowledgeBaseDetail'
    const wrapper = mount(SlotCenterPanel, { global: { stubs: globalStubs } })

    expect(wrapper.find('[data-testid="mobile-bottom-nav"]').exists()).toBe(false)
    expect(wrapper.get('.app-shell__main').classes()).toContain('app-shell__main--mobile-no-nav')
    wrapper.unmount()
  })

  it('keeps the bottom navigation on other mobile product pages', () => {
    testState.route.name = 'Extensions'
    const wrapper = mount(SlotCenterPanel, { global: { stubs: globalStubs } })

    expect(wrapper.find('[data-testid="mobile-bottom-nav"]').exists()).toBe(true)
    expect(wrapper.get('.app-shell__main').classes()).not.toContain('app-shell__main--mobile-no-nav')
    wrapper.unmount()
  })
})
