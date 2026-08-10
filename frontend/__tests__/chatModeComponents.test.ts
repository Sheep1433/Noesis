// @vitest-environment happy-dom

import { flushPromises, mount } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'
import ChatComposerToolbar from '@/components/Chat/ChatComposerToolbar.vue'
import ChatModeSelector from '@/components/Chat/ChatModeSelector.vue'

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
    useRouter: () => ({ push: vi.fn() }),
  }
})

vi.mock('@/api/mcp', () => ({ listMcpServers: vi.fn().mockResolvedValue({ servers: [] }) }))
vi.mock('@/api/skills', () => ({ getSkillsFsTree: vi.fn().mockResolvedValue({}) }))
vi.mock('@/api/chat', () => ({ ensureSession: vi.fn() }))

describe('chat mode selector', () => {
  it('shows product modes and emits the selected qa type', async () => {
    const wrapper = mount(ChatModeSelector, {
      props: { qaType: 'COMMON_QA' },
      attachTo: document.body,
    })

    expect(wrapper.text()).toContain('聊天')
    await wrapper.get('.chat-mode-trigger').trigger('click')
    await flushPromises()
    expect(document.body.textContent).toContain('任务')
    expect(document.body.textContent).toContain('故障排查')

    const taskButton = document.querySelectorAll<HTMLButtonElement>('.chat-mode-option')[1]
    taskButton.click()
    await flushPromises()
    expect(wrapper.emitted('select')).toEqual([['SUPER_AGENT_QA']])
    wrapper.unmount()
  })
})

describe('compact composer tools', () => {
  it('does not render a session-files button in the composer toolbar', () => {
    const wrapper = mount(ChatComposerToolbar, {
      props: {
        qaType: 'COMMON_QA',
        sessionId: 'session-1',
      },
      global: {
        stubs: {
          NButton: { template: '<button><slot /></button>' },
          NCheckbox: true,
          ModelSelector: { template: '<button data-testid="model-selector">模型</button>' },
          KbScopeSelector: true,
        },
      },
    })

    expect(wrapper.find('[data-testid="session-files-trigger"]').exists()).toBe(false)
    wrapper.unmount()
  })

  it('keeps the model switch beside plus and task tools inside the compact menu', async () => {
    const wrapper = mount(ChatComposerToolbar, {
      props: {
        qaType: 'SUPER_AGENT_QA',
        sessionId: 'session-1',
      },
      attachTo: document.body,
      global: {
        stubs: {
          NButton: { template: '<button><slot /></button>' },
          NCheckbox: true,
          ModelSelector: { template: '<button data-testid="model-selector">模型</button>' },
          KbScopeSelector: true,
        },
      },
    })

    const toolbarButtons = wrapper.get('.composer-toolbar__left').findAll('button')
    expect(toolbarButtons[0].classes()).toContain('composer-plus-btn')
    expect(toolbarButtons[1].attributes('data-testid')).toBe('model-selector')
    await wrapper.get('.composer-plus-btn').trigger('click')
    await flushPromises()
    const toolsPanel = document.querySelector<HTMLElement>('.composer-tools-panel')!
    expect(toolsPanel.textContent).not.toContain('会话文件')
    expect(toolsPanel.textContent).toContain('上传文件')
    expect(toolsPanel.textContent).toContain('上传图片')
    expect(toolsPanel.textContent).toContain('知识库')
    expect(toolsPanel.textContent).toContain('MCP')
    expect(toolsPanel.textContent).toContain('Skills')
    expect(toolsPanel.querySelector('[data-testid="model-selector"]')).toBeNull()
    wrapper.unmount()
  })
})
