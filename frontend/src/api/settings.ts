import { authFetch, parseAuthJson } from '@/utils/authHttp'

export type SettingsCapabilities = {
  provider_models: boolean
  mcp_management: boolean
  automation_operations: boolean
  channel_operations: boolean
  agent_context: boolean
  observability: boolean
  import_export: boolean
}

export async function getSettingsCapabilities() {
  const res = await authFetch(
    new Request(`${location.origin}/api/user/settings/capabilities`, {
      credentials: 'include',
    }),
  )
  return parseAuthJson<SettingsCapabilities>(res)
}

async function settingsJson<T>(path: string, method = 'GET', body?: unknown) {
  const res = await authFetch(new Request(`${location.origin}${path}`, {
    method,
    credentials: 'include',
    headers: body === undefined ? undefined : { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  }))
  return parseAuthJson<T>(res)
}

export type MemoryFilePayload = {
  file: string
  content: string
  updated_at?: string
  size?: number
}

export async function getUserMemoryFile(file: 'USER.md' | 'AGENTS.md') {
  const res = await authFetch(
    new Request(`${location.origin}/api/user/memory/${encodeURIComponent(file)}`, {
      credentials: 'include',
    }),
  )
  return parseAuthJson<MemoryFilePayload>(res)
}

export async function putUserMemoryFile(file: 'USER.md' | 'AGENTS.md', content: string) {
  const res = await authFetch(
    new Request(`${location.origin}/api/user/memory/${encodeURIComponent(file)}`, {
      method: 'PUT',
      credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ content }),
    }),
  )
  return parseAuthJson<MemoryFilePayload>(res)
}

export type CortexMemoryPreference = {
  enabled: boolean
}

export type MachineMemoryType = 'decision' | 'experience' | 'workflow' | 'gotcha'
export type MachineMemoryStatus = 'candidate' | 'active' | 'superseded' | 'disabled' | 'invalidated' | 'needs_review'
export type MachineMemoryEvidence = {
  id: string
  source_kind: 'message' | 'tool' | 'artifact' | 'chunk' | 'user_revision'
  provenance: 'user' | 'assistant_derived' | 'tool_internal' | 'tool_external'
  created_at: string
}
export type MachineMemoryItem = {
  id: string
  memory_type: MachineMemoryType
  status: MachineMemoryStatus
  subject: string
  statement: string
  applicability: string
  scope_id: string
  scope_label: string
  effective_provenance: MachineMemoryEvidence['provenance']
  version: number
  valid_from: string
  valid_to?: string | null
  last_verified_at?: string | null
  user_revision: boolean
  evidence_count: number
  evidence: MachineMemoryEvidence[]
}
export type MachineMemorySource = {
  memory_id: string
  evidence_id: string
  availability: 'available' | 'source_deleted' | 'retention_expired'
  source_kind: MachineMemoryEvidence['source_kind']
  source_ref?: string | null
  excerpt?: string | null
  provenance?: MachineMemoryEvidence['provenance'] | null
  captured_at?: string | null
}
export type MachineMemoryHealth = {
  last_capture_at?: string | null
  last_consolidation_at?: string | null
  pending: number
  partial: number
  failed: number
  dead: number
  skipped: number
  workspace_pending: number
  index_pending: number
  workspace_failed: number
  index_failed: number
  derived_view_lag_seconds?: number | null
}

export function getCortexMemoryPreference() {
  return settingsJson<CortexMemoryPreference>('/api/user/memory/cortex/preferences')
}

export function updateCortexMemoryPreference(
  enabled: boolean,
) {
  return settingsJson<CortexMemoryPreference>('/api/user/memory/cortex/preferences', 'PUT', {
    enabled,
  })
}

export async function listMachineMemory(
  status?: MachineMemoryStatus,
  memoryType?: MachineMemoryType,
  scopeId?: string,
  query?: string,
) {
  const params = new URLSearchParams()
  if (status) {
    params.set('status', status)
  }
  if (memoryType) {
    params.set('memory_type', memoryType)
  }
  if (scopeId) {
    params.set('scope_id', scopeId)
  }
  if (query?.trim()) {
    params.set('query', query.trim())
  }
  const suffix = params.size ? `?${params.toString()}` : ''
  return (await settingsJson<{ items: MachineMemoryItem[] }>(`/api/user/memory/cortex/items${suffix}`)).items
}

export function reviseMachineMemory(memoryId: string, statement: string, applicability: string) {
  return settingsJson<MachineMemoryItem>(
    `/api/user/memory/cortex/items/${encodeURIComponent(memoryId)}`,
    'PUT',
    { statement, applicability },
  )
}

export type MachineMemoryStateOperation = 'activate' | 'disable' | 'enable' | 'invalidate'

export function changeMachineMemoryState(memoryId: string, operation: MachineMemoryStateOperation) {
  return settingsJson<{ id: string, status: MachineMemoryStatus }>(
    `/api/user/memory/cortex/items/${encodeURIComponent(memoryId)}/${operation}`,
    'POST',
  )
}

export function deleteMachineMemory(memoryId: string) {
  return settingsJson<void>(`/api/user/memory/cortex/items/${encodeURIComponent(memoryId)}`, 'DELETE')
}

export function getMachineMemorySource(memoryId: string, evidenceId: string) {
  return settingsJson<MachineMemorySource>(
    `/api/user/memory/cortex/items/${encodeURIComponent(memoryId)}/evidence/${encodeURIComponent(evidenceId)}/source`,
  )
}

export function getMachineMemoryHealth() {
  return settingsJson<MachineMemoryHealth>('/api/user/memory/cortex/health')
}

export type ContextPreview = {
  profile: string
  sources: Array<{ id: string, label: string, injected: boolean, characters: number, token_estimate: number, content: string }>
  compiled_content: string
  characters: number
  token_estimate: number
}

export function getContextPreview(profile = 'super_agent') {
  return settingsJson<ContextPreview>(`/api/user/context/preview?profile=${encodeURIComponent(profile)}`)
}

export type ScheduledTask = {
  id: string
  name: string
  cron_expr: string
  summary?: string
  timezone: string
  enabled: boolean
  qa_type: string
  prompt: string
  session_binding: string
  delivery: string
  last_run_at?: number | null
  next_run_at?: number | null
  last_status?: string | null
  disabled_reason?: string | null
  run?: ScheduledTaskRun
}

export type ScheduledTaskRun = {
  id: string
  task_id: string
  status: 'queued' | 'running' | 'succeeded' | 'failed' | 'cancelled'
  trigger_source: 'schedule' | 'manual' | 'retry'
  retry_of?: string | null
  session_id?: string | null
  result_summary?: string | null
  error_category?: string | null
  error_message?: string | null
  delivery_result?: Record<string, unknown> | null
  started_at?: number | null
  finished_at?: number | null
  duration_ms?: number | null
  created_at: number
}

export async function listScheduledTasks() {
  const res = await authFetch(
    new Request(`${location.origin}/api/user/scheduled-tasks`, {
      credentials: 'include',
    }),
  )
  const data = await parseAuthJson<{ tasks: ScheduledTask[] }>(res)
  return data?.tasks || []
}

export async function createScheduledTask(payload: Partial<ScheduledTask>) {
  const res = await authFetch(
    new Request(`${location.origin}/api/user/scheduled-tasks`, {
      method: 'POST',
      credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    }),
  )
  return parseAuthJson<ScheduledTask>(res)
}

export async function updateScheduledTask(id: string, payload: Partial<ScheduledTask>) {
  const res = await authFetch(
    new Request(`${location.origin}/api/user/scheduled-tasks/${encodeURIComponent(id)}`, {
      method: 'PUT',
      credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    }),
  )
  return parseAuthJson<ScheduledTask>(res)
}

export async function setScheduledTaskEnabled(id: string, enabled: boolean) {
  const path = enabled ? 'enable' : 'disable'
  const res = await authFetch(
    new Request(`${location.origin}/api/user/scheduled-tasks/${encodeURIComponent(id)}/${path}`, {
      method: 'POST',
      credentials: 'include',
    }),
  )
  return parseAuthJson<ScheduledTask>(res)
}

export async function deleteScheduledTask(id: string) {
  const res = await authFetch(
    new Request(`${location.origin}/api/user/scheduled-tasks/${encodeURIComponent(id)}`, {
      method: 'DELETE',
      credentials: 'include',
    }),
  )
  await parseAuthJson(res)
}

export async function runScheduledTask(id: string) {
  const res = await authFetch(
    new Request(`${location.origin}/api/user/scheduled-tasks/${encodeURIComponent(id)}/run`, {
      method: 'POST',
      credentials: 'include',
      headers: { 'Idempotency-Key': crypto.randomUUID() },
    }),
  )
  return parseAuthJson<ScheduledTask>(res)
}

export async function previewSchedule(cronExpr: string, timezone: string) {
  const params = new URLSearchParams({ cron_expr: cronExpr, timezone })
  const res = await authFetch(`${location.origin}/api/user/scheduled-tasks/preview?${params}`)
  return parseAuthJson<{ summary: string, next_run_at: number, timezone: string }>(res)
}

export type ScheduledTaskDraft = {
  name: string
  cron_expr: string
  timezone: string
  qa_type: string
  prompt: string
  session_binding: string
  delivery: string
  summary: string
  next_run_at: number
}

export async function parseScheduledTask(text: string) {
  const res = await authFetch(
    new Request(`${location.origin}/api/user/scheduled-tasks/parse`, {
      method: 'POST',
      credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text }),
    }),
  )
  return parseAuthJson<ScheduledTaskDraft>(res)
}

export async function listScheduledTaskRuns(taskId: string) {
  const res = await authFetch(`${location.origin}/api/user/scheduled-tasks/${encodeURIComponent(taskId)}/runs`)
  return parseAuthJson<{ items: ScheduledTaskRun[], total: number }>(res)
}

export async function retryScheduledTaskRun(runId: string) {
  const res = await authFetch(`${location.origin}/api/user/scheduled-task-runs/${encodeURIComponent(runId)}/retry`, {
    method: 'POST',
    headers: { 'Idempotency-Key': crypto.randomUUID() },
  })
  return parseAuthJson<ScheduledTaskRun>(res)
}

export type MessagingChannel = {
  channel_id: string
  type: string
  enabled: boolean
  display_name: string
  bot_token_masked?: string | null
  has_token?: boolean
  pairing_chat_id?: string | null
  pairing_user_id?: string | null
  default_qa_type?: string
  runtime_note?: string
  default_session_id?: string | null
  session_strategy?: 'persistent' | 'new_per_message'
  delivery_preference?: 'reply' | 'silent'
  health?: {
    status: 'healthy' | 'degraded' | 'unavailable' | 'unknown'
    checked_at: number
    last_inbound_at?: number | null
    last_inbound_status?: string | null
    last_outbound_at?: number | null
    last_outbound_status?: string | null
    error_category?: string | null
    message: string
    correlation_id?: string | null
  }
}

export async function listChannels() {
  const res = await authFetch(
    new Request(`${location.origin}/api/user/channels`, {
      credentials: 'include',
    }),
  )
  const data = await parseAuthJson<{ channels: MessagingChannel[] }>(res)
  return data?.channels || []
}

export async function createChannel(payload: Record<string, unknown>) {
  const res = await authFetch(
    new Request(`${location.origin}/api/user/channels`, {
      method: 'POST',
      credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    }),
  )
  return parseAuthJson<MessagingChannel>(res)
}

export async function updateChannel(id: string, payload: Record<string, unknown>) {
  const res = await authFetch(
    new Request(`${location.origin}/api/user/channels/${encodeURIComponent(id)}`, {
      method: 'PUT',
      credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    }),
  )
  return parseAuthJson<MessagingChannel>(res)
}

export async function deleteChannel(id: string) {
  const res = await authFetch(
    new Request(`${location.origin}/api/user/channels/${encodeURIComponent(id)}`, {
      method: 'DELETE',
      credentials: 'include',
    }),
  )
  await parseAuthJson(res)
}

export async function testChannelConnection(id: string) {
  const res = await authFetch(`${location.origin}/api/user/channels/${encodeURIComponent(id)}/test-connection`, { method: 'POST' })
  return parseAuthJson<{ ok: boolean, status: string, message: string, checked_at: number, correlation_id: string }>(res)
}

export async function testChannelDelivery(id: string) {
  const res = await authFetch(`${location.origin}/api/user/channels/${encodeURIComponent(id)}/test-delivery`, { method: 'POST' })
  return parseAuthJson<{ ok: boolean, status: string, message: string, delivered_at: number, correlation_id: string }>(res)
}

export type NotificationPreference = { event_type: string, delivery_surface: string, enabled: boolean, version: number, updated_at?: number | null }
export type DiagnosticItem = { key: string, status: 'healthy' | 'degraded' | 'unavailable' | 'unknown', checked_at: number, message: string, action_code?: string | null, correlation_id: string }

export async function listNotificationPreferences() {
  return (await settingsJson<{ items: NotificationPreference[] }>('/api/user/settings/notifications')).items
}

export function updateNotificationPreference(item: NotificationPreference, enabled: boolean) {
  return settingsJson<NotificationPreference>('/api/user/settings/notifications', 'PUT', { ...item, enabled })
}

export function getSettingsDiagnostics() {
  return settingsJson<{ status: string, checked_at: number, items: DiagnosticItem[] }>('/api/user/settings/diagnostics')
}

export function exportSettings() {
  return settingsJson<Record<string, unknown>>('/api/user/settings/export')
}

export function previewSettingsImport(manifest: Record<string, unknown>) {
  return settingsJson<{ preview_id: string, changes: Array<{ domain: string, action: string }> }>('/api/user/settings/import/preview', 'POST', { manifest })
}

export function applySettingsImport(manifest: Record<string, unknown>, previewId: string) {
  return settingsJson<{ applied: string[] }>('/api/user/settings/import/apply', 'POST', { manifest, preview_id: previewId })
}

export function resetSettings() {
  return settingsJson<{ reset: string[] }>('/api/user/settings/reset', 'POST')
}

// ---------- 用户自定义对话模型 ----------

export type UserLLMProvider = {
  provider_id: string
  name: string
  api_type: string
  base_url: string
  enabled: boolean
  has_key: boolean
  api_key_masked?: string | null
}

export type UserLLMModel = {
  entry_id: string
  provider_id: string
  api_type?: string | null
  model_id: string
  label: string
  temperature?: number | null
  context_window: number
}

export type UserLLMProviderPayload = {
  name: string
  api_type: string
  base_url: string
  enabled: boolean
  api_key?: string
  api_key_action: 'keep' | 'replace' | 'clear'
}

export async function listLLMProviders() {
  const data = await settingsJson<{ providers: UserLLMProvider[] }>('/api/user/llm/providers')
  return data?.providers || []
}

export function createLLMProvider(payload: UserLLMProviderPayload) {
  return settingsJson<UserLLMProvider>('/api/user/llm/providers', 'POST', payload)
}

export function updateLLMProvider(id: string, payload: UserLLMProviderPayload) {
  return settingsJson<UserLLMProvider>(`/api/user/llm/providers/${encodeURIComponent(id)}`, 'PUT', payload)
}

export function deleteLLMProvider(id: string) {
  return settingsJson<null>(`/api/user/llm/providers/${encodeURIComponent(id)}`, 'DELETE')
}

export function testLLMProvider(id: string) {
  return settingsJson<UserLLMDiscoveryResult>(`/api/user/llm/providers/${encodeURIComponent(id)}/test`, 'POST')
}

export type UserLLMDiscoveredModel = {
  model_id: string
  label: string
  owned_by?: string | null
  context_window: number
  context_source: 'provider' | 'unknown'
}

export type UserLLMDiscoveryResult = {
  ok: boolean
  status: 'discovered' | 'missing_key' | 'authentication_error' | 'unsupported' | 'network_error' | 'provider_error' | 'invalid_response'
  provider_reachable: boolean
  discovery_supported: boolean
  models: UserLLMDiscoveredModel[]
  message: string
}

export function discoverLLMProvider(id: string) {
  return settingsJson<UserLLMDiscoveryResult>(`/api/user/llm/providers/${encodeURIComponent(id)}/discover`, 'POST')
}

export async function listLLMModels() {
  const data = await settingsJson<{ models: UserLLMModel[] }>('/api/user/llm/models')
  return data?.models || []
}

export function createLLMModel(payload: Omit<UserLLMModel, 'entry_id' | 'api_type'>) {
  return settingsJson<UserLLMModel>('/api/user/llm/models', 'POST', payload)
}

export function updateLLMModel(id: string, payload: Omit<UserLLMModel, 'entry_id' | 'api_type'>) {
  return settingsJson<UserLLMModel>(`/api/user/llm/models/${encodeURIComponent(id)}`, 'PUT', payload)
}

export function deleteLLMModel(id: string) {
  return settingsJson<null>(`/api/user/llm/models/${encodeURIComponent(id)}`, 'DELETE')
}
