/**
 * Chat API (v2.1)
 *
 * 封装所有 /api/chat/* 接口，参考 backend/api/chat_api.py
 */

import {
  authFetch,
  getAuthHeaders,
  parseAuthJson,
} from '@/utils/authHttp'
import { downloadFile } from '@/utils/download'

// ============================================================================
// Types
// ============================================================================

/** 消息内容片段 */
export interface MessagePart {
  type: 'text' | 'reasoning' | 'tool' | 'retrieval'
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
  finish_reason?: string
  error?: string
  /** 最近一次模型请求的上下文快照（与累计 usage 分开） */
  context?: ContextSnapshot
}

/**
 * 当前模型请求的上下文快照（context-update 事件 / 历史消息）。
 *  结构与 messageParts.ContextWindowSnapshot 对齐，避免循环 import。
 */
export interface ContextSnapshot {
  current_tokens: number
  max_tokens: number
  used_percentage: number
  updated_at?: string
}

export type AgentStopReason =
  | 'completed'
  | 'context_exhausted'
  | 'length_stop'
  | 'safety_stop'
  | 'partial_output'
  | 'empty_after_tools'
  | 'tool_loop_limit'
  | 'tool_call_limit'
  | 'subagent_concurrency_limit'
  | 'subagent_total_limit'
  | 'subagent_depth_limit'
  | 'retryable_error'
  | 'error'
  | (string & {})

/** 会话响应 */
export interface ChatSessionResponse {
  id: string
  parent_id: string | null
  kind?: 'root' | 'subagent' | string
  created_by_run_id?: string | null
  created_by_tool_call_id?: string | null
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

export interface ChildSessionCatalogItem {
  session_id: string
  parent_id: string
  created_by_tool_call_id?: string | null
  title: string
  profile_id: string
  run_id?: string | null
  status: AgentRunStatus | 'awaiting_approval' | 'failed' | 'cancelled' | 'timed_out' | string
  turn_count: number
  step_count: number
  started_at?: number | null
  finished_at?: number | null
  interrupt?: TaskCatalogEntry['interrupt'] | null
}

export interface ChildSessionCatalogResponse {
  sessions: ChildSessionCatalogItem[]
  total: number
}

/** 消息响应 */
export interface ChatMessageResponse {
  id: string
  session_id: string
  parent_id: string | null
  role: 'user' | 'assistant'
  content: MessageContent
  extra?: MessageMetadata
  status: string
  message_sequence: number
  created_at: number
  /** 关联 Agent run 的启动/终态时间（Unix 毫秒）；仅 assistant 且有 run 时有值 */
  run_started_at?: number | null
  run_finished_at?: number | null
}

/** 消息列表响应 */
export interface MessageListResponse {
  messages: ChatMessageResponse[]
  total: number
}

export type AgentRunStatus =
  | 'queued'
  | 'running'
  | 'stopping'
  | 'retrying'
  | 'hitl_pending'
  | 'completed'
  | 'partial'
  | 'error'
  | 'interrupted'

export interface AgentRunCreated {
  run_id: string
  assistant_message_id: string
  session_id: string
  status: AgentRunStatus
  session_title: string
  /** 命中斜杠命令时的 ephemeral 回复（不建 run、不落库）。存在时其余字段可缺省。 */
  command_reply?: string
}

/** 命中斜杠命令时的 ephemeral 响应（字段与 AgentRunCreated 不同，故独立判别）。 */
export interface CommandReplyResult {
  command_reply: string
  session_id: string
}

export interface AgentRunSnapshot {
  run_id: string
  assistant_message_id: string
  session_id: string
  qa_type: string
  origin: string
  status: AgentRunStatus
  snapshot_sequence: number
  attempt_id: number
  content: MessageContent
  finish_reason?: AgentStopReason | null
  error_code?: string | null
  message?: string | null
  pending_hitl?: {
    interrupt_id?: string
    kind?: string
    action_requests?: Array<{ tool_call_id?: string, name?: string, args?: Record<string, unknown> }>
    review_configs?: unknown[]
    expires_at?: number
  } | null
}

export interface CreateAgentRunParams {
  session_id: string
  content: string
  client_request_id: string
  extra?: Record<string, unknown>
}

export interface ResumeAgentRunHitlParams {
  interrupt_id: string
  decisions: Array<{ type: string, message?: string }>
  grant_scope?: 'once' | 'session' | null
}

/** 创建会话请求参数 */
export interface CreateSessionParams {
  title?: string
  parent_id?: string
  kind?: 'root' | 'subagent'
  extra?: Record<string, unknown>
}

/** 更新会话标题参数 */
export interface UpdateSessionTitleParams {
  title: string
}

/** 获取消息历史参数 */
export interface GetSessionMessagesParams {
  limit?: number
  before_id?: string
}

/** 会话附件响应 */
export interface ChatAttachmentResponse {
  attachment_id: string
  file_name: string
  kind: 'document' | 'image'
  mime_type?: string | null
  status: string
  char_count: number
  preview?: string | null
  virtual_path: string
  artifact_url?: string | null
  preview_base64?: string | null
  parse_error?: string | null
}

export interface ChatAttachmentListResponse {
  attachments: ChatAttachmentResponse[]
  total: number
}

/** 工作区 / 附件上下文树节点 */
export interface SessionFsTreeNode {
  key: string
  label: string
  isLeaf: boolean
  children?: SessionFsTreeNode[]
}

export interface SessionContextResponse {
  tree: SessionFsTreeNode[]
  session_root_path: string
}

export interface WorkspaceFileContent {
  path: string
  content: string
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
  extraHeaders: Record<string, string> = {},
): Request {
  return new Request(url, {
    mode: 'cors',
    credentials: 'include',
    method,
    headers: {
      'Content-Type': 'application/json',
      ...getAuthHeaders(extraHeaders),
    },
    body: body != null ? JSON.stringify(body) : undefined,
  })
}

/** 解析响应 JSON，提取 data 字段 */
async function parseResponse<T>(res: Response): Promise<T> {
  return parseAuthJson<T>(res)
}

// ============================================================================
// Slash commands
// ============================================================================

export interface SlashCommand {
  name: string
  description: string
}

/** 列出可用控制命令（skill 命令由 skills fs-tree 提供）。 */
export async function getSlashCommands(): Promise<SlashCommand[]> {
  const req = makeRequest('GET', `${location.origin}${BASE}/commands`)
  return parseResponse<SlashCommand[]>(await authFetch(req))
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
  return parseResponse<SessionListResponse>(await authFetch(req))
}

/**
 * 创建新会话
 * POST /api/chat/sessions
 */
export async function createSession(params: CreateSessionParams = {}): Promise<ChatSessionResponse> {
  const req = makeRequest('POST', `${location.origin}${BASE}/sessions`, params)
  return parseResponse<ChatSessionResponse>(await authFetch(req))
}

export interface EnsureSessionParams {
  title?: string
  extra?: Record<string, unknown>
}

/**
 * 幂等物化会话 PUT /api/chat/sessions/{sessionId}/ensure
 */
export async function ensureSession(
  sessionId: string,
  params: EnsureSessionParams = {},
): Promise<ChatSessionResponse> {
  const req = makeRequest(
    'PUT',
    `${location.origin}${BASE}/sessions/${encodeURIComponent(sessionId)}/ensure`,
    params,
  )
  return parseResponse<ChatSessionResponse>(await authFetch(req))
}

export async function createAgentRun(params: CreateAgentRunParams): Promise<AgentRunCreated | CommandReplyResult> {
  const req = makeRequest('POST', `${location.origin}${BASE}/runs`, params)
  const res = await authFetch(req)
  const json = await res.json() as { code?: number, msg?: string, data?: AgentRunCreated & CommandReplyResult & { run_id?: string, assistant_message_id?: string, session_id?: string, status?: string } }
  // 409 冲突：返回可加入的已有 Run 信息，不当作普通失败
  if (json.code === 409 && json.data?.run_id) {
    const conflict = new Error(json.msg ?? '当前会话仍在生成') as Error & { conflictRunId?: string, conflictData?: unknown }
    conflict.conflictRunId = json.data.run_id
    conflict.conflictData = json.data
    throw conflict
  }
  if (json.code !== 200 || !json.data) {
    throw new Error(json.msg ?? `API error: ${json.code}`)
  }
  // 命中斜杠命令：ephemeral 回复，无 run_id
  if (json.data.command_reply) {
    return { command_reply: json.data.command_reply, session_id: json.data.session_id ?? params.session_id }
  }
  return json.data
}

export async function getAgentRun(runId: string): Promise<AgentRunSnapshot> {
  const req = makeRequest('GET', `${location.origin}${BASE}/runs/${encodeURIComponent(runId)}`)
  return parseResponse<AgentRunSnapshot>(await authFetch(req))
}

export async function getActiveRun(sessionId: string): Promise<AgentRunSnapshot | null> {
  const req = makeRequest(
    'GET',
    `${location.origin}${BASE}/sessions/${encodeURIComponent(sessionId)}/active-run`,
  )
  const data = await parseResponse<AgentRunSnapshot | null>(await authFetch(req))
  return data
}

export async function subscribeAgentRun(
  runId: string,
  afterSequence: number,
  signal?: AbortSignal,
): Promise<Response> {
  const url = new URL(`${location.origin}${BASE}/runs/${encodeURIComponent(runId)}/stream`)
  url.searchParams.set('after_sequence', String(Math.max(0, afterSequence)))
  return authFetch(new Request(url, {
    method: 'GET',
    credentials: 'include',
    headers: getAuthHeaders(),
    signal,
  }))
}

export async function stopAgentRun(runId: string): Promise<AgentRunSnapshot> {
  const req = makeRequest('POST', `${location.origin}${BASE}/runs/${encodeURIComponent(runId)}/stop`)
  return parseResponse<AgentRunSnapshot>(await authFetch(req))
}

export async function stopShellTask(sessionId: string, taskId: string): Promise<TaskCatalogEntry> {
  const req = makeRequest(
    'POST',
    `${location.origin}${BASE}/sessions/${encodeURIComponent(sessionId)}/shell-jobs/${encodeURIComponent(taskId)}/stop`,
  )
  return parseResponse<TaskCatalogEntry>(await authFetch(req))
}

/** 订阅会话级信令流（跨窗口发现活跃 run）；帧为 event: session-signal 的轻量定位符 */
export async function subscribeSessionEvents(sessionId: string, signal?: AbortSignal): Promise<Response> {
  const url = new URL(`${location.origin}${BASE}/sessions/${encodeURIComponent(sessionId)}/events`)
  return authFetch(new Request(url, {
    method: 'GET',
    credentials: 'include',
    headers: getAuthHeaders(),
    signal,
  }))
}

/** 后台子 Agent 任务（含待审批） */
export interface TaskCatalogEntry {
  task_id: string
  session_id: string
  child_session_id?: string | null
  created_by_tool_call_id?: string | null
  run_id?: string | null
  assistant_message_id?: string | null
  description: string
  /** kind=shell 的原始命令；subagent 任务为空 */
  command?: string | null
  kind?: 'subagent' | 'shell'
  status: 'queued' | 'running' | 'stopping' | 'awaiting_approval' | 'completed' | 'failed' | 'cancelled' | 'timed_out' | 'partial' | 'error' | 'interrupted'
  result?: string | null
  error?: string | null
  /** 协作停止受理原因（cancelled / timed_out）；status=stopping 时非空 */
  stop_reason?: 'cancelled' | 'timed_out' | null
  interrupt?: {
    interrupt_id: string
    action_requests: Array<{ tool_call_id?: string, name?: string, args?: Record<string, unknown> }>
    kind?: string
  } | null
  started_at?: number
  completed_at?: number | null
  progress?: Array<{
    kind: 'tool_call' | 'tool_result' | 'text'
    name?: string
    status?: string
    preview?: string
    ts?: number
  }>
  /** SSE/列表负载已裁掉 progress 明细时的步数 */
  progress_count?: number
}

export async function listSessionTaskCatalog(sessionId: string): Promise<{ tasks: TaskCatalogEntry[], pending_approvals: TaskCatalogEntry[] }> {
  const req = makeRequest('GET', `${location.origin}${BASE}/sessions/${encodeURIComponent(sessionId)}/children/catalog`)
  return parseResponse(await authFetch(req))
}

export async function resumeAgentRunHitl(
  runId: string,
  params: ResumeAgentRunHitlParams,
): Promise<AgentRunSnapshot> {
  const req = makeRequest(
    'POST',
    `${location.origin}${BASE}/runs/${encodeURIComponent(runId)}/hitl/resume`,
    params,
  )
  return parseResponse<AgentRunSnapshot>(await authFetch(req))
}

export async function resumeAgentRunTestCase(
  runId: string,
  selectedPointNames: string[],
): Promise<AgentRunSnapshot> {
  const req = makeRequest(
    'POST',
    `${location.origin}${BASE}/runs/${encodeURIComponent(runId)}/test-case/resume`,
    { selected_point_names: selectedPointNames },
  )
  return parseResponse<AgentRunSnapshot>(await authFetch(req))
}

/**
 * 获取会话详情
 * GET /api/chat/sessions/{id}
 */
export async function getSession(id: string): Promise<ChatSessionResponse> {
  const req = makeRequest('GET', `${location.origin}${BASE}/sessions/${encodeURIComponent(id)}`)
  return parseResponse<ChatSessionResponse>(await authFetch(req))
}

/**
 * 删除会话（软删）
 * DELETE /api/chat/sessions/{id}
 */
export async function deleteSession(id: string): Promise<void> {
  const req = makeRequest('DELETE', `${location.origin}${BASE}/sessions/${id}`)
  await parseResponse<void>(await authFetch(req))
}

/**
 * 更新会话标题
 * PUT /api/chat/sessions/{id}/title
 */
export async function updateSessionTitle(
  id: string,
  params: UpdateSessionTitleParams,
): Promise<ChatSessionResponse> {
  const req = makeRequest('PUT', `${location.origin}${BASE}/sessions/${id}/title`, params)
  return parseResponse<ChatSessionResponse>(await authFetch(req))
}

/** 更新会话置顶 / 归档状态参数 */
export interface UpdateSessionMetaParams {
  pinned?: boolean | null
  archived?: boolean | null
}

/**
 * 更新会话置顶 / 归档状态
 * PUT /api/chat/sessions/{id}/meta
 */
export async function updateSessionMeta(
  id: string,
  params: UpdateSessionMetaParams,
): Promise<ChatSessionResponse> {
  const req = makeRequest('PUT', `${location.origin}${BASE}/sessions/${id}/meta`, params)
  return parseResponse<ChatSessionResponse>(await authFetch(req))
}

/**
 * 标记会话已读
 * PUT /api/chat/sessions/{id}/read
 */
export async function markSessionRead(id: string): Promise<void> {
  const req = makeRequest('PUT', `${location.origin}${BASE}/sessions/${id}/read`)
  await parseResponse<void>(await authFetch(req))
}

/**
 * 获取子会话列表
 * GET /api/chat/sessions/{id}/children
 */
export async function getSessionChildren(id: string): Promise<ChildSessionCatalogResponse> {
  const req = makeRequest('GET', `${location.origin}${BASE}/sessions/${id}/children`)
  return parseResponse<ChildSessionCatalogResponse>(await authFetch(req))
}

// ============================================================================
// Session Attachments API
// ============================================================================

function makeUploadRequest(url: string, formData: FormData): Request {
  return new Request(url, {
    mode: 'cors',
    credentials: 'include',
    method: 'POST',
    headers: getAuthHeaders(),
    body: formData,
  })
}

/** 上传会话附件 POST /api/chat/sessions/{sessionId}/attachments */
export async function uploadSessionAttachment(
  sessionId: string,
  file: File,
): Promise<ChatAttachmentResponse> {
  const formData = new FormData()
  formData.append('file', file)
  const req = makeUploadRequest(
    `${location.origin}${BASE}/sessions/${encodeURIComponent(sessionId)}/attachments`,
    formData,
  )
  const res = await authFetch(req)
  const json = await res.json()
  if (json.code !== 200) {
    throw new Error(json.msg ?? `上传失败（${json.code}）`)
  }
  return json.data as ChatAttachmentResponse
}

/** 列出会话附件 GET /api/chat/sessions/{sessionId}/attachments */
export async function listSessionAttachments(
  sessionId: string,
): Promise<ChatAttachmentListResponse> {
  const req = makeRequest('GET', `${location.origin}${BASE}/sessions/${encodeURIComponent(sessionId)}/attachments`)
  return parseResponse<ChatAttachmentListResponse>(await authFetch(req))
}

/** 删除会话附件 DELETE /api/chat/sessions/{sessionId}/attachments/{attachmentId} */
export async function deleteSessionAttachment(
  sessionId: string,
  attachmentId: string,
): Promise<void> {
  const req = makeRequest(
    'DELETE',
    `${location.origin}${BASE}/sessions/${encodeURIComponent(sessionId)}/attachments/${encodeURIComponent(attachmentId)}`,
  )
  await parseResponse<void>(await authFetch(req))
}

/** 会话 usage 汇总（主+子合并口径） GET /api/chat/sessions/{sessionId}/usage-summary */
/** 无 usage 数据时返回 null（data 为 null），前端回退本地重建 */
export async function getSessionUsageSummary(
  sessionId: string,
): Promise<Record<string, number> | null> {
  const req = makeRequest(
    'GET',
    `${location.origin}${BASE}/sessions/${encodeURIComponent(sessionId)}/usage-summary`,
  )
  const res = await authFetch(req)
  if (res.status === 404) {
    return null
  }
  return parseAuthJson<Record<string, number> | null>(res)
}

/** 会话上下文（工作区 + 附件） GET /api/chat/sessions/{sessionId}/context */
/** 会话尚未物化时返回 null（HTTP 404），不视为错误 */
export async function getSessionContext(sessionId: string): Promise<SessionContextResponse | null> {
  const req = makeRequest(
    'GET',
    `${location.origin}${BASE}/sessions/${encodeURIComponent(sessionId)}/context`,
  )
  const res = await authFetch(req)
  if (res.status === 404) {
    return null
  }
  return parseAuthJson<SessionContextResponse>(res)
}

/** 读取工作区文件 GET /api/chat/sessions/{sessionId}/workspace/file */
export async function getWorkspaceFile(
  sessionId: string,
  path: string,
): Promise<WorkspaceFileContent> {
  const url = new URL(
    `${location.origin}${BASE}/sessions/${encodeURIComponent(sessionId)}/workspace/file`,
  )
  url.searchParams.set('path', path)
  const req = makeRequest('GET', url.toString())
  return parseAuthJson<WorkspaceFileContent>(await authFetch(req))
}

/** 保存工作区文件 PUT /api/chat/sessions/{sessionId}/workspace/file */
export async function saveWorkspaceFile(
  sessionId: string,
  path: string,
  content: string,
): Promise<WorkspaceFileContent> {
  const req = makeRequest(
    'PUT',
    `${location.origin}${BASE}/sessions/${encodeURIComponent(sessionId)}/workspace/file`,
    { path, content },
  )
  return parseAuthJson<WorkspaceFileContent>(await authFetch(req))
}

function parseContentDispositionFilename(header: string | null): string | null {
  if (!header) {
    return null
  }
  const utf8Match = header.match(/filename\*=UTF-8''([^;]+)/i)
  if (utf8Match?.[1]) {
    try {
      return decodeURIComponent(utf8Match[1].trim())
    } catch {
      return utf8Match[1].trim()
    }
  }
  const plainMatch = header.match(/filename="?([^";]+)"?/i)
  return plainMatch?.[1]?.trim() ?? null
}

/** 下载目录（ZIP）或单文件 GET /api/chat/sessions/{sessionId}/workspace/archive */
export async function downloadWorkspaceArchive(
  sessionId: string,
  path: string,
): Promise<void> {
  const url = new URL(
    `${location.origin}${BASE}/sessions/${encodeURIComponent(sessionId)}/workspace/archive`,
  )
  url.searchParams.set('path', path)
  const req = makeRequest('GET', url.toString())
  const res = await authFetch(req)
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error((err as { msg?: string }).msg || `下载失败: ${res.status}`)
  }
  const blob = await res.blob()
  const contentType = res.headers.get('Content-Type') || 'application/octet-stream'
  const isZip = contentType.includes('zip')
  const fallback = isZip
    ? `${path.split('/').filter(Boolean).pop() || 'archive'}.zip`
    : (path.split('/').filter(Boolean).pop() || 'download')
  const filename = parseContentDispositionFilename(res.headers.get('Content-Disposition')) || fallback
  downloadFile(blob, filename, contentType)
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
  return parseResponse<MessageListResponse>(await authFetch(req))
}

/** 向已有 child session 追加下一轮对话；modelId/reasoningEffort 缺省沿用当前值。 */
export async function sendSubagentFollowup(
  sessionId: string,
  message: string,
  modelId?: string,
  reasoningEffort?: string,
): Promise<TaskCatalogEntry> {
  const body: Record<string, string> = { message }
  if (modelId) {
    body.model_id = modelId
  }
  if (reasoningEffort) {
    body.reasoning_effort = reasoningEffort
  }
  const req = makeRequest(
    'POST',
    `${location.origin}${BASE}/sessions/${encodeURIComponent(sessionId)}/subagent-followup`,
    body,
  )
  return parseResponse<TaskCatalogEntry>(await authFetch(req))
}

/**
 * 获取单条消息详情
 * GET /api/chat/messages/{messageId}
 */
export async function getMessage(messageId: string): Promise<ChatMessageResponse> {
  const req = makeRequest('GET', `${location.origin}${BASE}/messages/${messageId}`)
  return parseResponse<ChatMessageResponse>(await authFetch(req))
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
  const res = await authFetch(req)
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
