// @vitest-environment happy-dom

import { flushPromises, mount } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'
import KnowledgeBase from '@/views/knowledge-base/KnowledgeBase.vue'

vi.mock('vue-router', () => ({
  useRouter: () => ({ push: vi.fn() }),
}))

vi.mock('@/hooks/useBreakpoint', () => ({
  useBreakpoint: () => ({ isMobile: { value: true } }),
}))

vi.mock('@/api/knowledgeBase', () => ({
  createCollection: vi.fn(),
  deleteCollection: vi.fn(),
  getCollections: vi.fn().mockResolvedValue([]),
  getKnowledgeBaseStatus: vi.fn().mockResolvedValue({
    connected: true,
    host: 'localhost',
    port: 6333,
  }),
}))

vi.mock('naive-ui', async () => {
  const actual = await vi.importActual<typeof import('naive-ui')>('naive-ui')
  return {
    ...actual,
    useDialog: () => ({ warning: vi.fn() }),
    useMessage: () => ({ error: vi.fn(), success: vi.fn() }),
  }
})

describe('mobile knowledge base empty state', () => {
  it('uses the compact empty-state layout', async () => {
    const wrapper = mount(KnowledgeBase)
    await flushPromises()

    const emptyState = wrapper.get('.state-block--empty')
    expect(emptyState.attributes('style')).toContain('flex-grow: 0')
    expect(emptyState.attributes('style')).toContain('flex-shrink: 0')
    expect(emptyState.attributes('style')).toContain('min-height: 0')
    wrapper.unmount()
  })
})
