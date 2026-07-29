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

export type DailyMemoryItem = { date: string, size: number, updated_at: string }
export type MemorySourceRef = { session_id: string, message_id: string }
export type DailyMemoryMatch = { id: string, date: string, category: string, summary: string, keywords: string[], score: number, sources: MemorySourceRef[] }
export type MemoryDreamResult = { date: string, timezone: string, entries: number, status: string }

export async function listDailyMemory() {
  return (await settingsJson<{ items: DailyMemoryItem[] }>('/api/user/memory/daily/list')).items
}

export async function searchDailyMemory(query: string) {
  return (await settingsJson<{ items: DailyMemoryMatch[] }>(`/api/user/memory/daily/entries/search?q=${encodeURIComponent(query)}`)).items
}

export function runMemoryDream(date: string, timezone = 'Asia/Shanghai') {
  return settingsJson<MemoryDreamResult>('/api/user/memory/dream', 'POST', { date, timezone })
}

export type ContextPreview = {
  profile: string
  sources: Array<{ id: string, label: string, priority: number, injected: boolean, characters: number, token_estimate: number, content: string }>
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
