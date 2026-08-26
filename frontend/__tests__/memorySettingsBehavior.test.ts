// @vitest-environment happy-dom

import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import MemoryEditorSection from '@/views/settings/sections/MemoryEditorSection.vue'

const getMemorySettings = vi.fn()
const putMemorySettings = vi.fn()
const getMemoryTree = vi.fn()
const getMemoryEntry = vi.fn()
const putMemoryEntry = vi.fn()
const deleteMemoryEntry = vi.fn()

vi.mock('@/api/settings', () => ({
  getUserMemoryFile: vi.fn().mockResolvedValue({ content: '# agents' }),
  putUserMemoryFile: vi.fn(),
  getMemorySettings: (...args: unknown[]) => getMemorySettings(...args),
  putMemorySettings: (...args: unknown[]) => putMemorySettings(...args),
  getMemoryTree: (...args: unknown[]) => getMemoryTree(...args),
  getMemoryEntry: (...args: unknown[]) => getMemoryEntry(...args),
  putMemoryEntry: (...args: unknown[]) => putMemoryEntry(...args),
  deleteMemoryEntry: (...args: unknown[]) => deleteMemoryEntry(...args),
  getContextPreview: vi.fn().mockResolvedValue({ compiled_content: '', token_estimate: 0 }),
}))

vi.mock('naive-ui', async (importOriginal) => {
  const original = await importOriginal()
  return {
    ...original,
    useMessage: () => ({ success: vi.fn(), error: vi.fn() }),
    useDialog: () => ({ warning: vi.fn() }),
  }
})

const TREE = {
  entries: [
    {
      memory_type: 'preference',
      type_label: '偏好',
      slug: 'doc-format',
      rel_path: 'preference/doc-format.md',
      label: '文档格式',
      description: '一律表格、简体中文',
    },
    {
      memory_type: 'goal',
      type_label: '目标',
      slug: 'nodejs',
      rel_path: 'goal/nodejs.md',
      label: '学习路线',
      description: '在学 Node.js',
    },
  ],
  corrupt_lines: 0,
  over_budget: false,
  journal_days: ['2026-08-26'],
}

async function mountSection() {
  const wrapper = mount(MemoryEditorSection, {
    props: {
      file: 'AGENTS.md' as const,
      title: '记忆',
      description: '长期记忆',
    },
    global: {
      stubs: { FilePreview: true, teleport: true },
    },
  })
  await flushPromises()
  return wrapper
}

beforeEach(() => {
  vi.clearAllMocks()
  getMemorySettings.mockResolvedValue({ enabled: true })
  getMemoryTree.mockResolvedValue(TREE)
  getMemoryEntry.mockResolvedValue({ content: '# 文档格式\n\n一律表格' })
  putMemorySettings.mockResolvedValue({ enabled: false })
})

describe('memory settings behavior', () => {
  it('renders toggle with server state and grouped entries', async () => {
    const wrapper = await mountSection()

    expect(getMemorySettings).toHaveBeenCalled()
    expect(getMemoryTree).toHaveBeenCalled()
    expect(wrapper.text()).toContain('记忆')
    expect(wrapper.text()).toContain('文档格式')
    expect(wrapper.text()).toContain('学习路线')
    expect(wrapper.text()).toContain('偏好')
    expect(wrapper.text()).toContain('目标')
  })

  it('toggles memory off via the single switch', async () => {
    const wrapper = await mountSection()
    const switchEl = wrapper.find('.n-switch')
    expect(switchEl.exists()).toBe(true)
    await switchEl.trigger('click')
    await flushPromises()

    expect(putMemorySettings).toHaveBeenCalledWith(false)
  })

  it('shows empty hint when no entries exist', async () => {
    getMemoryTree.mockResolvedValue({ entries: [], corrupt_lines: 0, over_budget: false, journal_days: [] })
    const wrapper = await mountSection()

    expect(wrapper.text()).toContain('还没有长期记忆')
  })

  it('opens an entry and saves edits', async () => {
    const wrapper = await mountSection()
    putMemoryEntry.mockResolvedValue({})

    const item = wrapper.findAll('.memory-item').find((node) => node.text().includes('文档格式'))
    expect(item).toBeTruthy()
    await item!.trigger('click')
    await flushPromises()

    expect(getMemoryEntry).toHaveBeenCalledWith('preference', 'doc-format')
    const editor = wrapper.find('.entry-editor')
    expect(editor.exists()).toBe(true)
    expect(editor.text()).toContain('文档格式')
  })

  it('uses plain business wording for the toggle', async () => {
    const wrapper = await mountSection()
    const text = wrapper.text()
    expect(text).toContain('会话结束后自动整理记忆')
    expect(text).not.toContain('capture')
    expect(text).not.toContain('consolidation')
    expect(text).not.toContain('Bulletin')
  })
})
