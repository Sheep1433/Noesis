// @vitest-environment happy-dom

import { mount } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'
import SideBar from '@/components/Navigation/SideBar.vue'

vi.mock('@/components/ThemeSwitcher/index.vue', () => ({
  default: { template: '<div data-testid="theme-switcher"></div>' },
}))

vi.mock('vue-router', async () => {
  const actual = await vi.importActual<typeof import('vue-router')>('vue-router')
  return {
    ...actual,
    useRoute: () => ({ name: 'ChatIndex' }),
    useRouter: () => ({ push: vi.fn() }),
  }
})

describe('desktop product navigation', () => {
  it('does not expose the retired test-case page', () => {
    const wrapper = mount(SideBar, {
      global: {
        stubs: {
          NButton: { template: '<button><slot /></button>' },
          NPopover: { template: '<div><slot name="trigger" /><slot /></div>' },
          ThemeSwitcher: true,
        },
      },
    })

    expect(wrapper.text()).not.toContain('测试用例')
    wrapper.unmount()
  })
})
