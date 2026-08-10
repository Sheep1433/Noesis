// @vitest-environment happy-dom

import { mount } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'
import ChatHistoryPanel from '@/views/chat/ChatHistoryPanel.vue'

vi.mock('vue-router', () => ({
  useRouter: () => ({ push: vi.fn() }),
}))

vi.mock('@/components/ThemeSwitcher/index.vue', () => ({
  default: { template: '<button />' },
}))

const stubs = {
  ChatModeSelector: { template: '<div><slot name="trigger" /></div>' },
  ThemeSwitcher: true,
  NButton: { template: '<button><slot name="icon" /><slot /></button>' },
  NIcon: { template: '<span><slot /></span>' },
  NInput: { template: '<input />' },
  NTooltip: { template: '<div><slot name="trigger" /><slot /></div>' },
  NDropdown: true,
  NDataTable: { template: '<div class="data-table" />' },
  NDivider: { template: '<hr />' },
  NPopover: { template: '<div><slot name="trigger" /><slot /></div>' },
}

describe('mobile chat history panel', () => {
  it('renders archived conversations inside a collapsible section', async () => {
    const wrapper = mount(ChatHistoryPanel, {
      props: {
        stylizingLoading: false,
        isFocusSearchChat: false,
        isLoadingHistory: false,
        searchText: '',
        tableData: [],
        archivedTableData: [{ uuid: 'archived-1', chat_id: 'archived-1', qa_type: 'COMMON_QA', key: '已归档' }],
        historySidebarColumns: [],
        sessionContextMenuShow: false,
        sessionContextMenuX: 0,
        sessionContextMenuY: 0,
        sessionContextMenuOptions: [],
        rowProps: () => ({}),
        showAccountActions: true,
      },
      global: { stubs },
    })

    expect(wrapper.find('[data-testid="archived-section"]').exists()).toBe(true)
    const toggle = wrapper.get('[data-testid="archive-section-toggle"]')
    expect(toggle.attributes('aria-expanded')).toBe('false')
    await toggle.trigger('click')
    expect(toggle.attributes('aria-expanded')).toBe('true')
    expect(wrapper.find('[data-testid="archive-toggle"]').exists()).toBe(false)
    wrapper.unmount()
  })
})
