// @vitest-environment happy-dom

import { mount } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'
import WorkspaceFileTreeNode from '@/views/chat/WorkspaceFileTreeNode.vue'

describe('workspace file tree', () => {
  it('does not render a download action on each file row', () => {
    const wrapper = mount(WorkspaceFileTreeNode, {
      props: {
        node: { key: 'workspace/AGENTS.md', label: 'AGENTS.md', isLeaf: true },
        depth: 0,
        selectedKey: '',
        isExpanded: () => false,
        toggleExpand: vi.fn(),
        onRowClick: vi.fn(),
        onContextMenu: vi.fn(),
      },
    })

    expect(wrapper.find('[aria-label="下载"]').exists()).toBe(false)
  })
})
