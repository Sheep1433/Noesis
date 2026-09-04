// @vitest-environment happy-dom

import type { AgentRunSnapshot, ChatMessageResponse, TaskCatalogEntry } from '@/api/chat'
import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import BackgroundSubagentCollapse from '@/components/BackgroundSubagentCollapse/index.vue'
import SubagentConversationDrawer from '@/components/SubagentConversationDrawer/index.vue'
import { clearQueuedFollowups, setQueuedFollowups } from '@/components/SubagentConversationView/queuedFollowups'
import { activateChildCatalogSession, createChildCatalogEventSource } from '@/views/chat/childCatalogStream'

const api = vi.hoisted(() => ({
  getSession: vi.fn(),
  getSessionMessages: vi.fn(),
  getAgentRun: vi.fn(),
  resumeAgentRunHitl: vi.fn(),
  sendSubagentFollowup: vi.fn(),
  stopAgentRun: vi.fn(),
  subscribeAgentRun: vi.fn(),
}))

// mock 门面必须覆盖 SubagentConversationView 消费的全部值导出：缺导出时
// 断流自愈路径（resync → getAgentRun）会抛 vitest mock 错误，退避重试的
// rejection 落在用例结束后成为未处理拒绝（全量跑时序敏感）
vi.mock('@/api/chat', () => ({
  getSession: api.getSession,
  getSessionMessages: api.getSessionMessages,
  getAgentRun: api.getAgentRun,
  resumeAgentRunHitl: api.resumeAgentRunHitl,
  sendSubagentFollowup: api.sendSubagentFollowup,
  stopAgentRun: api.stopAgentRun,
  subscribeAgentRun: api.subscribeAgentRun,
}))

// ModelSelector 经 api/models → authHttp → router 拉起全部视图与 pinia
// devtools（happy-dom 无可用 localStorage，收集阶段即崩）；这里 mock 掉目录 API
// ChatComposerToolbar 经 vue-router→devtools→localStorage 传递链在 happy-dom
// 收集期崩溃（已知坑：组件级 stub 断链）
vi.mock('@/components/Chat/ChatComposerToolbar.vue', () => ({
  default: {
    name: 'ChatComposerToolbarStub',
    template: '<div class="composer-toolbar-stub"><slot name="right" /></div>',
  },
}))

vi.mock('@/api/models', () => ({
  getChatModels: vi.fn().mockResolvedValue({ models: [], default_id: '' }),
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

/** 断流自愈的权威快照（默认终态：run 流安静退出不再重试） */
function agentRunSnapshot(status: AgentRunSnapshot['status'] = 'completed'): AgentRunSnapshot {
  return {
    run_id: 'run-1',
    assistant_message_id: 'am-1',
    session_id: 'child-session-1',
    qa_type: 'SUPER_AGENT_QA',
    origin: 'subagent',
    status,
    snapshot_sequence: 1,
    attempt_id: 1,
    content: { version: 1, parts: [] },
  }
}

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

// 模块级待发队列被所有视图实例共享：测试间必须卸载残留组件并清空队列，
// 否则上一个用例的 watcher 仍会消费新用例种子数据
const mountedWrappers: Array<{ unmount: () => void }> = []

function mountDrawer(show = true) {
  const wrapper = mount(SubagentConversationDrawer, {
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
  mountedWrappers.push(wrapper)
  return wrapper
}

/** 可控 SSE 流：push 注入一个事件块，read 挂起等待（模拟长连接） */
function controllableSseStream() {
  const encoder = new TextEncoder()
  type ReadResult = { value?: Uint8Array, done: boolean }
  const pendingReads: Array<{ resolve: (result: ReadResult) => void }> = []
  const chunks: ReadResult[] = []

  function pump() {
    while (pendingReads.length > 0 && chunks.length > 0) {
      pendingReads.shift()!.resolve(chunks.shift()!)
    }
  }

  return {
    push(chunk: string) {
      chunks.push({ value: encoder.encode(chunk), done: false })
      pump()
    },
    response: {
      ok: true,
      status: 200,
      body: {
        getReader: () => ({
          read: () => new Promise<ReadResult>((resolve) => {
            pendingReads.push({ resolve })
            pump()
          }),
        }),
      },
    },
  }
}

describe('子 Agent 标准会话展示', () => {
  beforeEach(() => {
    api.getSessionMessages.mockReset()
    api.subscribeAgentRun.mockReset()
    api.sendSubagentFollowup.mockReset()
    api.getAgentRun.mockReset()
    api.subscribeAgentRun.mockResolvedValue({ body: null })
    api.sendSubagentFollowup.mockResolvedValue(runningTask)
    api.getAgentRun.mockResolvedValue(agentRunSnapshot())
    clearQueuedFollowups('child-session-1')
  })

  afterEach(() => {
    while (mountedWrappers.length > 0) {
      mountedWrappers.pop()!.unmount()
    }
    clearQueuedFollowups('child-session-1')
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

  it('断流自愈拿到终态快照后不再重订阅（run-finished 重载不得回到订阅循环）', async () => {
    api.getSessionMessages.mockResolvedValue({ messages: [], total: 0 })
    const wrapper = mountDrawer(true)
    await flushPromises()

    // 订阅失败（body: null）→ resync 终态快照 → run-finished → 重载恰好一轮。
    // 若重载再次订阅同一终态 run，会形成无退避的微任务死循环——各环节都是
    // 已 resolve 的 mock promise，宏任务（含 vitest 超时）被饿死，套件表现为
    // 永久挂死。抽屉双挂载属编排细节，上限放宽到 2 实例 × 各一轮。
    expect(api.subscribeAgentRun.mock.calls.length).toBeLessThanOrEqual(2)
    expect(api.getSessionMessages.mock.calls.length).toBeLessThanOrEqual(4)
    await wrapper.setProps({ show: false })
  })

  it('补充要求走标准 child session followup API', async () => {
    api.getSessionMessages.mockResolvedValue({ messages: [], total: 0 })
    const wrapper = mountDrawer(true)
    await flushPromises()
    const textarea = wrapper.find('textarea')
    await textarea.setValue('请补充来源')
    await textarea.trigger('keydown.enter')
    await flushPromises()

    expect(api.sendSubagentFollowup).toHaveBeenCalledWith('child-session-1', '请补充来源', undefined, undefined)
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
    expect(wrapper.text()).toContain('子智能体')
    expect(wrapper.text()).toContain('检索')
  })

  it('任务卡是静态入口：不展示目录状态、步数与耗时', () => {
    const wrapper = mount(BackgroundSubagentCollapse, {
      props: {
        toolPart: { id: 'tool-1', type: 'tool', name: 'start_task', input: { description: '检索' }, output: '', status: 'success', state: 'succeeded' },
        task: { ...runningTask, status: 'queued', progress_count: 3 },
      },
      global: { stubs: { SubagentConversationDrawer: { template: '<div />' } } },
    })
    expect(wrapper.text()).toContain('检索')
    expect(wrapper.text()).not.toContain('排队中')
    expect(wrapper.text()).not.toContain('步')
    expect(wrapper.find('.subagent-card__status').exists()).toBe(false)
  })

  it('下发失败的任务卡显示失败提示且不可打开', () => {
    const wrapper = mount(BackgroundSubagentCollapse, {
      props: {
        toolPart: { id: 'tool-1', type: 'tool', name: 'start_task', input: { description: '检索' }, output: '启动失败：并发超限', status: 'error', state: 'failed' },
      },
      global: { stubs: { SubagentConversationDrawer: { template: '<div />' } } },
    })
    expect(wrapper.find('.subagent-card__failed').text()).toBe('启动失败')
    expect(wrapper.find('.subagent-card').attributes('disabled')).toBeDefined()
  })

  it('run 运行中发送进入前端排队，run 终态后自动提交队首', async () => {
    api.getSessionMessages.mockResolvedValue({ messages: [], total: 0 })
    const sse = controllableSseStream()
    api.subscribeAgentRun.mockReturnValue(sse.response)
    const wrapper = mountDrawer(true)
    await flushPromises()

    sse.push(`event: run-snapshot\ndata: ${JSON.stringify({
      type: 'run-snapshot',
      run_id: 'run-1',
      assistant_message_id: 'a1',
      status: 'running',
      content: { version: 1, parts: [] },
    })}\n\n`)
    await flushPromises()

    // 运行中：Enter 只进前端队列，不调 API
    await wrapper.find('textarea').setValue('排队消息 A')
    await wrapper.find('textarea').trigger('keydown.enter')
    await flushPromises()
    expect(api.sendSubagentFollowup).not.toHaveBeenCalled()
    expect(wrapper.find('[data-testid="followup-queue-item"]').text()).toContain('排队消息 A')

    // run 终态：自动提交队首并清空队列
    sse.push(`event: run.finished\ndata: ${JSON.stringify({
      type: 'run.finished',
      status: 'completed',
      finished_at: 1,
    })}\n\n`)
    await flushPromises()
    expect(api.sendSubagentFollowup).toHaveBeenCalledWith('child-session-1', '排队消息 A', undefined, undefined)
    expect(wrapper.find('[data-testid="followup-queue-item"]').exists()).toBe(false)
  })

  it('composer 单按钮：运行中输入为空呈停止态，输入内容后呈发送态', async () => {
    api.getSessionMessages.mockResolvedValue({ messages: [], total: 0 })
    const sse = controllableSseStream()
    api.subscribeAgentRun.mockReturnValue(sse.response)
    const wrapper = mountDrawer(true)
    await flushPromises()

    sse.push(`event: run-snapshot\ndata: ${JSON.stringify({
      type: 'run-snapshot',
      run_id: 'run-1',
      assistant_message_id: 'a1',
      status: 'running',
      content: { version: 1, parts: [] },
    })}\n\n`)
    await flushPromises()

    // 运行中且输入为空：唯一按钮是停止
    expect(wrapper.find('[data-testid="subagent-stop-button"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="subagent-send-button"]').exists()).toBe(false)

    // 输入内容后：同一按钮切换为发送（运行中发送进入排队）
    await wrapper.find('textarea').setValue('追问')
    await flushPromises()
    expect(wrapper.find('[data-testid="subagent-send-button"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="subagent-stop-button"]').exists()).toBe(false)
  })

  it('排队消息支持删除、编辑回填与立即提交', async () => {
    setQueuedFollowups('child-session-1', ['先问 A', '再问 B'])
    api.getSessionMessages.mockResolvedValue({ messages: [], total: 0 })
    // 队列 CRUD 场景要求 run 仍活跃：终态 run + 队列会触发队首自动提交
    // （watcher 契约，见「run 终态后自动提交队首」用例）。流端点故障
    // （body: null）下 resync 拿 running 快照 → 后台退避重连，不影响 CRUD。
    api.getAgentRun.mockResolvedValue(agentRunSnapshot('running'))
    const wrapper = mountDrawer(true)
    await flushPromises()

    const queueButton = (item: ReturnType<typeof wrapper.find>, title: string) =>
      item.findAll('button').find((button) => button.attributes('title') === title)

    let items = wrapper.findAll('[data-testid="followup-queue-item"]')
    expect(items).toHaveLength(2)

    // 删除第二条
    await queueButton(items[1], '删除')!.trigger('click')
    items = wrapper.findAll('[data-testid="followup-queue-item"]')
    expect(items).toHaveLength(1)
    expect(items[0].text()).toContain('先问 A')

    // 编辑：文本回填输入框并出队
    await queueButton(items[0], '编辑后重新排队')!.trigger('click')
    expect((wrapper.find('textarea').element as HTMLTextAreaElement).value).toBe('先问 A')
    expect(wrapper.find('[data-testid="followup-queue-item"]').exists()).toBe(false)

    // 立即提交：直接调 followup API 并出队
    setQueuedFollowups('child-session-1', ['立即这条'])
    await flushPromises()
    const item = wrapper.find('[data-testid="followup-queue-item"]')
    await queueButton(item, '立即发送：空闲时立即开跑，运行中衔接为当前轮后的下一轮')!.trigger('click')
    await flushPromises()
    expect(api.sendSubagentFollowup).toHaveBeenCalledWith('child-session-1', '立即这条', undefined, undefined)
    expect(wrapper.find('[data-testid="followup-queue-item"]').exists()).toBe(false)
  })
})
