// @vitest-environment happy-dom

import { mount } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'
import SettingsShell from '@/views/settings/SettingsShell.vue'

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

const router = {
  push: vi.fn(),
  replace: vi.fn(),
}

vi.mock('vue-router', async () => {
  const actual = await vi.importActual<typeof import('vue-router')>('vue-router')
  return {
    ...actual,
    useRoute: () => ({ query: {} }),
    useRouter: () => router,
    onBeforeRouteLeave: vi.fn(),
  }
})

vi.mock('naive-ui', async () => {
  const actual = await vi.importActual<typeof import('naive-ui')>('naive-ui')
  return {
    ...actual,
    useDialog: () => ({ warning: vi.fn() }),
  }
})

describe('settings navigation', () => {
  it('uses the global navigation instead of a page-level back button', () => {
    const wrapper = mount(SettingsShell, {
      global: {
        stubs: {
          SettingsNav: true,
          OverviewSection: true,
          PlatformModelsSection: true,
          MemoryEditorSection: true,
          CapabilitiesSection: true,
          AutomationSection: true,
          ChannelsSection: true,
          DiagnosticsSection: true,
          AccountSection: true,
        },
      },
    })

    expect(wrapper.find('[data-testid="settings-back"]').exists()).toBe(false)
    expect(router.push).not.toHaveBeenCalled()
    wrapper.unmount()
  })
})
