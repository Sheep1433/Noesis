// @vitest-environment happy-dom

import type { ChatMessageResponse, TaskCatalogEntry } from '@/api/chat'
import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import BackgroundSubagentCollapse from '@/components/BackgroundSubagentCollapse/index.vue'
import SubagentConversationDrawer from '@/components/SubagentConversationDrawer/index.vue'
import { activateChildCatalogSession, createChildCatalogEventSource } from '@/views/chat/childCatalogStream'

const api = vi.hoisted(() => ({
  getSessionMessages: vi.fn(),
  subscribeAgentRun: vi.fn(),
  sendSubagentFollowup: vi.fn(),
}))

vi.mock('@/api/chat', () => ({
  getSessionMessages: api.getSessionMessages,
  subscribeAgentRun: api.subscribeAgentRun,
  sendSubagentFollowup: api.sendSubagentFollowup,
}))

vi.mock('@/components/MarkdownPreview/index.vue', () => ({
  default: {
    props: ['content'],
    template: '<div class="markdown-preview-stub">{{ content }}</div>',
  },
}))

vi.mock('@/components/ReasoningBlock/index.vue', () => ({
  default: { template: '<div class="reasoning-stub" />' },
}))

vi.mock('@/components/ToolCallCollapse/index.vue', () => ({
  default: { template: '<div class="tool-call-stub" />' },
}))

const runningTask: TaskCatalogEntry = {
  task_id: 'child-session-1',
  session_id: 'parent-session-1',
  run_id: 'run-1',
  description: '检索并整理资料',
  kind: 'subagent',
  status: 'running',
  started_at: Date.now() / 1000,
  progress_count: 1,
}

function message(
  id: string,
  role: 'user' | 'assistant',
  text: string,
  sequence: number,
): ChatMessageResponse {
  return {
    id,
    session_id: 'child-session-1',
    parent_id: null,
    user_id: 'user-1',
    role,
    content: { parts: [{ type: 'text', content: text }] },
    status: 'completed',
    message_sequence: sequence,
    created_at: Date.now(),
  }
}

function mountDrawer(show = true) {
  return mount(SubagentConversationDrawer, {
    props: { show, sessionId: 'child-session-1', runId: 'run-1', title: '检索并整理资料' },
    global: {
      stubs: {
        teleport: true,
        NDrawer: { props: ['show'], template: '<div v-if="show"><slot /></div>' },
        NDrawerContent: { template: '<div><slot /></div>' },
        NInput: { template: '<textarea />' },
        NButton: { template: '<button><slot /></button>' },
      },
    },
  })
}

describe('子 Agent 标准会话展示', () => {
  beforeEach(() => {
    api.getSessionMessages.mockReset()
    api.subscribeAgentRun.mockReset()
    api.sendSubagentFollowup.mockReset()
    api.subscribeAgentRun.mockResolvedValue({ body: null })
    api.sendSubagentFollowup.mockResolvedValue(runningTask)
  })

  it('首次物化后立即接收父会话的子 Agent 状态事件', () => {
    const listeners = new Map<string, (event: MessageEvent) => void>()
    const onTask = vi.fn()
    const factory = vi.fn((url: string) => ({
      url,
      close: vi.fn(),
      addEventListener: (type: string, listener: (event: MessageEvent) => void) => listeners.set(type, listener),
    }))
    let currentSessionId: string | null = null

    activateChildCatalogSession({
      sessionId: 'parent-session-1',
      currentSessionId,
      hasStream: false,
      setCurrentSession: (sessionId) => {
        currentSessionId = sessionId
      },
      openStream: (sessionId) => {
        createChildCatalogEventSource(sessionId, { onTask, onContinuation: vi.fn() }, factory)
      },
    })
    listeners.get('bg-task')?.({ data: JSON.stringify({ event: 'started', task: runningTask }) } as MessageEvent)

    expect(currentSessionId).toBe('parent-session-1')
    expect(factory).toHaveBeenCalledWith(expect.stringContaining('/sessions/parent-session-1/children/stream'))
    expect(onTask).toHaveBeenCalledWith(runningTask)
  })

  it('详情打开时加载标准消息并建立 run SSE，关闭时释放订阅', async () => {
    api.getSessionMessages.mockResolvedValue({
      messages: [
        message('u1', 'user', '首轮任务要求', 1),
        message('a1', 'assistant', '### 正在检索', 2),
        message('u2', 'user', '补充要求', 3),
      ],
      total: 3,
    })
    const wrapper = mountDrawer(true)
    await flushPromises()

    expect(api.getSessionMessages).toHaveBeenCalledWith('child-session-1', { limit: 500 })
    expect(api.subscribeAgentRun).toHaveBeenCalledWith('run-1', 0, expect.any(AbortSignal))
    expect(wrapper.findAll('.subagent-conversation__user')).toHaveLength(2)
    expect(wrapper.find('.subagent-conversation__assistant .markdown-preview-stub').text()).toContain('### 正在检索')

    // naive-ui 抽屉打开编排可能双挂载槽内容——订阅次数是实现细节；
    // 契约是「打开时建立订阅、关闭时释放最新订阅」（全局同 run 去重）
    expect(api.subscribeAgentRun.mock.calls.length).toBeGreaterThanOrEqual(1)
    const signals = api.subscribeAgentRun.mock.calls.map((call) => call[2] as AbortSignal)
    const lastSignal = signals[signals.length - 1]
    expect(lastSignal.aborted).toBe(false)

    await wrapper.setProps({ show: false })
    expect(lastSignal.aborted).toBe(true)
  })

  it('补充要求走标准 child session followup API', async () => {
    api.getSessionMessages.mockResolvedValue({ messages: [], total: 0 })
    const wrapper = mountDrawer(true)
    await flushPromises()
    const textarea = wrapper.find('textarea')
    await textarea.setValue('请补充来源')
    await textarea.trigger('keydown.enter')
    await flushPromises()

    expect(api.sendSubagentFollowup).toHaveBeenCalledWith('child-session-1', '请补充来源')
  })

  it('父 Agent 中每次子 Agent 调用仍是独立卡片，并指向标准 run', async () => {
    const wrapper = mount(BackgroundSubagentCollapse, {
      props: { toolPart: { id: 'tool-1', type: 'tool', name: 'start_task', input: { description: '检索' }, output: '', status: 'running', state: 'running' }, task: runningTask },
      global: {
        stubs: {
          SubagentConversationDrawer: {
            props: ['show', 'sessionId', 'runId', 'title'],
            template: '<div class="drawer-stub" />',
          },
          NIcon: { template: '<span><slot /></span>' },
        },
      },
    })
    await wrapper.find('.subagent-card').trigger('click')
    expect(wrapper.find('.drawer-stub').exists()).toBe(true)
    expect(wrapper.text()).toContain('检索')
  })
})
