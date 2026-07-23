import { authFetch, parseAuthJson } from '@/utils/authHttp'

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
    }),
  )
  return parseAuthJson<ScheduledTask>(res)
}

export type MessagingChannel = {
  channel_id: string
  type: string
  enabled: boolean
  display_name: string
  bot_token_masked?: string | null
  has_token?: boolean
  pairing_chat_id?: string | null
  default_qa_type?: string
  runtime_note?: string
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
