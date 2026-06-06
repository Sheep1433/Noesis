/**
 * Chat API (v2.1)
 *
 * 封装所有 /api/chat/* 接口，参考 backend/api/chat_api.py
 */

import { useUserStore } from '@/store/business/userStore'
import { downloadFile } from '@/utils/request'

// ============================================================================
// Types
// ============================================================================

/** 消息内容片段 */
export interface MessagePart {
  type: 'text' | 'reasoning' | 'tool'
  content?: string
  tool?: string
  input?: Record<string, unknown>
  output?: string
}

/** 消息内容（multipart 格式） */
export interface MessageContent {
  parts: MessagePart[]
}

/** 消息元数据 */
export interface MessageMetadata {
  model?: string
  input_tokens?: number
  output_tokens?: number
  finish_reason?: string
  error?: string
}

/** 会话响应 */
export interface ChatSessionResponse {
  id: string
  parent_id: string | null
  user_id: string
  title: string
  extra: Record<string, unknown> | null
  created_at: number
  updated_at: number
  deleted_at: number | null
}

/** 会话列表响应 */
export interface SessionListResponse {
  sessions: ChatSessionResponse[]
  total: number
}

/** 消息响应 */
export interface ChatMessageResponse {
  id: string
  session_id: string
  parent_id: string | null
  user_id: string
  role: 'user' | 'assistant'
  content: MessageContent
  extra?: MessageMetadata
  status: string
  created_at: number
}

/** 消息列表响应 */
export interface MessageListResponse {
  messages: ChatMessageResponse[]
  total: number
}

/** 发送消息响应 */
export interface SendMessageResponse {
  message_id: string
  session_id: string
  status: string
}

/** 创建会话请求参数 */
export interface CreateSessionParams {
  title?: string
  parent_id?: string
  extra?: Record<string, unknown>
}

/** 更新会话标题参数 */
export interface UpdateSessionTitleParams {
  title: string
}

/** 发送消息参数 */
export interface SendMessageParams {
  content: string
  parent_id?: string
  role?: 'user' | 'assistant'
  extra?: Record<string, unknown>
}

/** 流式发送消息参数（带 qa_type 和 file_dict） */
export interface StreamSendMessageParams extends SendMessageParams {
  extra?: SendMessageParams['extra'] & {
    qa_type?: string
    file_dict?: Record<string, string>
  }
}

/** 获取消息历史参数 */
export interface GetSessionMessagesParams {
  limit?: number
  before_id?: string
}

// ============================================================================
// Internal helpers
// ============================================================================

const BASE = '/api/chat'

/** 构造带认证的 Request */
function makeRequest(
  method: string,
  url: string,
  body?: unknown,
): Request {
  const userStore = useUserStore()
  const token = userStore.getUserToken()
  return new Request(url, {
    mode: 'cors',
    method,
    headers: {
      'Content-Type': 'application/json',
      'Authorization': `Bearer ${token ?? ''}`,
    },
    body: body != null ? JSON.stringify(body) : undefined,
  })
}

/** 解析响应 JSON，提取 data 字段 */
async function parseResponse<T>(res: Response): Promise<T> {
  const json = await res.json()
  if (json.code !== 200) {
    throw new Error(json.message ?? `API error: ${json.code}`)
  }
  return json.data as T
}

// ============================================================================
// Session API
// ============================================================================

/**
 * 获取当前用户的会话列表
 * GET /api/chat/sessions
 */
export async function getChatSessions(status?: string): Promise<SessionListResponse> {
  const url = new URL(`${location.origin}${BASE}/sessions`)
  if (status) {
    url.searchParams.set('status', status)
  }
  const req = makeRequest('GET', url.toString())
  return parseResponse<SessionListResponse>(await fetch(req))
}

/**
 * 创建新会话
 * POST /api/chat/sessions
 */
export async function createSession(params: CreateSessionParams = {}): Promise<ChatSessionResponse> {
  const req = makeRequest('POST', `${location.origin}${BASE}/sessions`, params)
  return parseResponse<ChatSessionResponse>(await fetch(req))
}

/**
 * 删除会话（软删）
 * DELETE /api/chat/sessions/{id}
 */
export async function deleteSession(id: string): Promise<void> {
  const req = makeRequest('DELETE', `${location.origin}${BASE}/sessions/${id}`)
  await parseResponse<void>(await fetch(req))
}

/**
 * 更新会话标题
 * PATCH /api/chat/sessions/{id}/title
 */
export async function updateSessionTitle(
  id: string,
  params: UpdateSessionTitleParams,
): Promise<ChatSessionResponse> {
  const req = makeRequest('PATCH', `${location.origin}${BASE}/sessions/${id}/title`, params)
  return parseResponse<ChatSessionResponse>(await fetch(req))
}

/**
 * 获取子会话列表
 * GET /api/chat/sessions/{id}/children
 */
export async function getSessionChildren(id: string): Promise<SessionListResponse> {
  const req = makeRequest('GET', `${location.origin}${BASE}/sessions/${id}/children`)
  return parseResponse<SessionListResponse>(await fetch(req))
}

// ============================================================================
// Message API
// ============================================================================

/**
 * 获取会话消息历史（按 created_at 升序排序，支持分页）
 * GET /api/chat/sessions/{sessionId}/messages
 */
export async function getSessionMessages(
  sessionId: string,
  params: GetSessionMessagesParams = {},
): Promise<MessageListResponse> {
  const url = new URL(`${location.origin}${BASE}/sessions/${sessionId}/messages`)
  if (params.limit != null) {
    url.searchParams.set('limit', String(params.limit))
  }
  if (params.before_id) {
    url.searchParams.set('before_id', params.before_id)
  }
  const req = makeRequest('GET', url.toString())
  return parseResponse<MessageListResponse>(await fetch(req))
}

/**
 * 发送消息（创建用户消息）
 * POST /api/chat/sessions/{sessionId}/messages
 */
export async function sendMessage(
  sessionId: string,
  params: SendMessageParams,
): Promise<SendMessageResponse> {
  const req = makeRequest('POST', `${location.origin}${BASE}/sessions/${sessionId}/messages`, params)
  return parseResponse<SendMessageResponse>(await fetch(req))
}

/**
 * 获取单条消息详情
 * GET /api/chat/messages/{messageId}
 */
export async function getMessage(messageId: string): Promise<ChatMessageResponse> {
  const req = makeRequest('GET', `${location.origin}${BASE}/messages/${messageId}`)
  return parseResponse<ChatMessageResponse>(await fetch(req))
}

/**
 * 停止对话
 * POST /api/chat/sessions/{sessionId}/stop
 */
export async function stopChat(sessionId: string, qaType: string): Promise<void> {
  const req = makeRequest('POST', `${location.origin}${BASE}/sessions/${sessionId}/stop`, {
    session_id: sessionId,
    qa_type: qaType,
  })
  await parseResponse<void>(await fetch(req))
}

/** 导出用例条目（与后端 TestCaseExportCaseItem 对齐） */
export interface TestCaseExportCaseItem {
  point_name: string
  case_id?: string
  point_level?: string
  point_type?: string
  scene_name?: string
  preconditions?: string[]
  test_steps?: string[]
  expected_results?: string[]
}

export interface TestCaseExportParams {
  test_cases?: TestCaseExportCaseItem[]
  query?: string
}

function parseContentDispositionFilename(disposition: string | null): string | null {
  if (!disposition) {
    return null
  }
  const star = disposition.match(/filename\*=UTF-8''([^;]+)/i)
  if (star?.[1]) {
    try {
      return decodeURIComponent(star[1].trim())
    } catch {
      return star[1].trim()
    }
  }
  const plain = disposition.match(/filename="?([^";]+)"?/i)
  return plain?.[1]?.trim() || null
}

/**
 * 导出测试用例 Markdown 并触发浏览器下载
 * POST /api/chat/sessions/{sessionId}/test-case/export
 */
export async function exportTestCaseMarkdown(
  sessionId: string,
  params: TestCaseExportParams = {},
): Promise<void> {
  const req = makeRequest(
    'POST',
    `${location.origin}${BASE}/sessions/${sessionId}/test-case/export`,
    params,
  )
  const res = await fetch(req)
  if (res.status === 404) {
    throw new Error('暂无可导出的测试用例，请先生成用例')
  }
  if (!res.ok) {
    let msg = `导出失败（${res.status}）`
    try {
      const json = await res.json()
      if (json?.msg || json?.detail) {
        msg = String(json.msg || json.detail)
      }
    } catch {
      // ignore
    }
    throw new Error(msg)
  }
  const blob = await res.blob()
  const filename = parseContentDispositionFilename(res.headers.get('content-disposition'))
    || '测试用例报告.md'
  downloadFile(blob, filename, 'text/markdown;charset=utf-8')
}
