// @vitest-environment happy-dom

import type { MachineMemoryHealth, MachineMemoryItem } from '@/api/settings'
import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import MemoryEditorSection from '@/views/settings/sections/MemoryEditorSection.vue'

const listMachineMemory = vi.fn()
const changeMachineMemoryState = vi.fn()
const deleteMachineMemory = vi.fn()
const reviseMachineMemory = vi.fn()

vi.mock('@/api/settings', () => ({
  listMachineMemory: (...args: unknown[]) => listMachineMemory(...args),
  changeMachineMemoryState: (...args: unknown[]) => changeMachineMemoryState(...args),
  deleteMachineMemory: (...args: unknown[]) => deleteMachineMemory(...args),
  reviseMachineMemory: (...args: unknown[]) => reviseMachineMemory(...args),
  getUserMemoryFile: vi.fn().mockResolvedValue({ content: '# user context' }),
  putUserMemoryFile: vi.fn(),
  getCortexMemoryPreference: vi.fn().mockResolvedValue({ enabled: false }),
  updateCortexMemoryPreference: vi.fn(),
  getContextPreview: vi.fn().mockResolvedValue({ preview: '' }),
  getMachineMemoryHealth: vi.fn(),
  getMachineMemorySource: vi.fn(),
}))

const messageSpy = { success: vi.fn(), error: vi.fn(), info: vi.fn() }
const dialogSpy = { warning: vi.fn(), success: vi.fn() }

vi.mock('naive-ui', () => {
  const passthrough = (tag: string) => ({
    name: tag,
    template: `<${tag}><slot /></${tag}>`,
  })
  return {
    NButton: {
      name: 'NButton',
      props: ['disabled', 'loading', 'type', 'ghost', 'size', 'quaternary'],
      emits: ['click'],
      template: '<button :disabled="disabled || loading" @click="$emit(\'click\')"><slot /></button>',
    },
    NInput: {
      name: 'NInput',
      props: ['value', 'type', 'placeholder', 'autosize', 'clearable'],
      emits: ['update:value'],
      template: '<textarea :placeholder="placeholder" :value="value" @input="$emit(\'update:value\', $event.target.value)" />',
    },
    NSelect: {
      name: 'NSelect',
      props: ['value', 'options', 'clearable'],
      emits: ['update:value'],
      template: '<select />',
    },
    NSwitch: {
      name: 'NSwitch',
      props: ['value', 'disabled', 'loading'],
      emits: ['update:value'],
      template: '<input type="checkbox" :checked="value" @change="$emit(\'update:value\', $event.target.checked)" />',
    },
    NTag: passthrough('span'),
    useDialog: () => dialogSpy,
    useMessage: () => messageSpy,
  }
})

vi.mock('@/components/MarkdownPreview/plugins/markdown', () => ({
  default: { render: (source: string) => source },
}))

vi.mock('@/hooks/useMermaidRender', () => ({
  useMermaidRender: () => ({}),
}))

function memoryItem(overrides: Partial<MachineMemoryItem> = {}): MachineMemoryItem {
  return {
    id: 'memory-1',
    memory_type: 'decision',
    status: 'active',
    subject: '构建工具选择',
    statement: '统一使用 pnpm。',
    applicability: '前端仓库',
    scope_id: 'scope-1',
    scope_label: '项目 demo',
    effective_provenance: 'user',
    version: 1,
    valid_from: '2026-08-24T00:00:00Z',
    valid_to: null,
    last_verified_at: '2026-08-24T00:00:00Z',
    user_revision: false,
    evidence_count: 1,
    evidence: [{ id: 'evidence-1', source_kind: 'message', provenance: 'user', created_at: '2026-08-24T00:00:00Z' }],
    ...overrides,
  } as MachineMemoryItem
}

const health: MachineMemoryHealth = {
  last_capture_at: '2026-08-24T01:00:00Z',
  last_consolidation_at: '2026-08-24T01:30:00Z',
  pending: 3,
  partial: 1,
  failed: 1,
  dead: 1,
  skipped: 2,
  workspace_pending: 0,
  index_pending: 0,
  workspace_failed: 0,
  index_failed: 0,
  derived_view_lag_seconds: 0,
} as MachineMemoryHealth

async function mountSection() {
  const wrapper = mount(MemoryEditorSection, {
    props: { file: 'AGENTS.md', title: 'Agent 上下文', description: '手动维护' },
    global: {
      stubs: { FilePreview: { template: '<div />' } },
    },
  })
  await flushPromises()
  return wrapper
}

beforeEach(() => {
  vi.clearAllMocks()
  listMachineMemory.mockResolvedValue([])
  changeMachineMemoryState.mockResolvedValue({ id: 'memory-1', status: 'active' })
  deleteMachineMemory.mockResolvedValue(undefined)
})

describe('machine memory settings behavior', () => {
  it('lists governed memories and exposes candidate confirmation only for candidates', async () => {
    listMachineMemory.mockResolvedValue([
      memoryItem({ id: 'memory-candidate', status: 'candidate', subject: '部署命令' }),
      memoryItem({ id: 'memory-active', subject: '构建工具选择' }),
    ])

    const wrapper = await mountSection()
    const text = wrapper.text()

    expect(text).toContain('部署命令')
    expect(text).toContain('待确认')
    expect(text).toContain('可使用')

    const buttons = wrapper.findAll('button')
    const confirmButtons = buttons.filter((button) => button.text() === '确认适用')
    expect(confirmButtons).toHaveLength(1)

    await confirmButtons[0].trigger('click')
    await flushPromises()
    expect(changeMachineMemoryState).toHaveBeenCalledWith('memory-candidate', 'activate')
    expect(listMachineMemory.mock.calls.length).toBeGreaterThan(1)
    wrapper.unmount()
  })

  it('confirms deletion with regeneration semantics before deleting', async () => {
    listMachineMemory.mockResolvedValue([memoryItem()])

    const wrapper = await mountSection()
    const deleteButton = wrapper.findAll('button').find((button) => button.text() === '删除')
    expect(deleteButton).toBeTruthy()
    await deleteButton!.trigger('click')

    expect(dialogSpy.warning).toHaveBeenCalledTimes(1)
    const dialog = dialogSpy.warning.mock.calls[0][0] as { content: string, onPositiveClick: () => Promise<void> }
    expect(dialog.content).toContain('再次整理出相似经验')
    expect(deleteMachineMemory).not.toHaveBeenCalled()

    await dialog.onPositiveClick()
    await flushPromises()
    expect(deleteMachineMemory).toHaveBeenCalledWith('memory-1')
    expect(messageSpy.success).toHaveBeenCalledWith('已删除')
    wrapper.unmount()
  })

  it('renders processing health in business wording', async () => {
    const { getMachineMemoryHealth } = await import('@/api/settings')
    vi.mocked(getMachineMemoryHealth).mockResolvedValue(health)
    listMachineMemory.mockResolvedValue([memoryItem()])

    const wrapper = await mountSection()
    const text = wrapper.text()

    expect(text).toContain('等待处理 3')
    expect(text).toContain('部分完成 1')
    expect(text).toContain('处理失败 2')
    expect(text).toContain('已跳过 2')
    expect(text).toContain('最近记录')
    expect(text).toContain('最近整理')
    wrapper.unmount()
  })

  it('surfaces user-facing error copy when the memory list fails', async () => {
    listMachineMemory.mockRejectedValue(new Error('会话已过期'))

    const wrapper = await mountSection()
    expect(messageSpy.error).toHaveBeenCalledWith('会话已过期')

    listMachineMemory.mockRejectedValue('boom')
    wrapper.findAll('button').find((button) => button.text() === '搜索')!.trigger('click')
    await flushPromises()
    expect(messageSpy.error).toHaveBeenCalledWith('经验记忆加载失败')
    wrapper.unmount()
  })
})
