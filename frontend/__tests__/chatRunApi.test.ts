import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createAgentRun } from '@/api/chat'

const auth = vi.hoisted(() => ({
  authFetch: vi.fn(),
  getAuthHeaders: vi.fn(() => ({})),
  parseAuthJson: vi.fn(),
}))

vi.mock('@/utils/authHttp', () => auth)

describe('agent Run API', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    Object.defineProperty(globalThis, 'location', {
      configurable: true,
      value: new URL('http://localhost/'),
    })
  })

  it('创建成功只消费一次响应 body', async () => {
    const response = new Response(JSON.stringify({
      code: 200,
      data: {
        run_id: 'run-1',
        assistant_message_id: 'message-1',
        session_id: 'session-1',
        status: 'running',
        session_title: '会话',
      },
    }))
    const jsonSpy = vi.spyOn(response, 'json')
    auth.authFetch.mockResolvedValue(response)

    const created = await createAgentRun({
      session_id: 'session-1',
      content: 'hello',
      client_request_id: 'request-1',
    })

    expect(created.run_id).toBe('run-1')
    expect(jsonSpy).toHaveBeenCalledOnce()
  })

  it('409 暴露可加入的权威 run id', async () => {
    auth.authFetch.mockResolvedValue(new Response(JSON.stringify({
      code: 409,
      msg: '当前会话仍在生成',
      data: {
        run_id: 'run-existing',
        assistant_message_id: 'message-existing',
        session_id: 'session-1',
        status: 'running',
      },
    }), { status: 409 }))

    await expect(createAgentRun({
      session_id: 'session-1',
      content: 'hello',
      client_request_id: 'request-2',
    })).rejects.toMatchObject({ conflictRunId: 'run-existing' })
  })
})
