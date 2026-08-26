// @vitest-environment happy-dom

import { mount } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'
import WorkspaceFileTree from '@/views/chat/WorkspaceFileTree.vue'
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

  it('starts with all folders collapsed', () => {
    const wrapper = mount(WorkspaceFileTree, {
      props: {
        nodes: [{
          key: 'users/1',
          label: 'users/1',
          isLeaf: false,
          children: [
            { key: 'users/1/AGENTS.md', label: 'AGENTS.md', isLeaf: true },
          ],
        }],
        selectedKey: '',
      },
    })

    expect(wrapper.findAll('.tree-row').map((row) => row.text())).toEqual(['›users/1'])
  })
})
