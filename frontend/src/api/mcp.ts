/**
 * MCP 目录、状态与用户配置文件 API
 */
import { authFetch, parseAuthJson } from '@/utils/authHttp'

const API_BASE = `${location.origin}/api/mcp`

export type McpServerSource = 'platform' | 'user'
export type McpServerStatus = 'unknown' | 'ok' | 'error'

export interface McpServerCatalogItem {
  id: string
  source: McpServerSource
  transport: string
  url?: string | null
  display_name?: string | null
  enabled: boolean
}

export interface McpServerCatalogResponse {
  servers: McpServerCatalogItem[]
}

export interface McpServerStatusItem extends McpServerCatalogItem {
  status: McpServerStatus
  tool_count: number
  message: string
  checked_at?: number
  error_category?: string
  correlation_id?: string | null
}

export interface McpServerStatusResponse {
  servers: McpServerStatusItem[]
}

export interface McpConfigFile {
  content: string
  path_hint: string
  exists: boolean
}

export async function listMcpServers(scope: 'user' | 'all' = 'all'): Promise<McpServerCatalogResponse> {
  const response = await authFetch(
    `${API_BASE}/servers?scope=${encodeURIComponent(scope)}`,
    { method: 'GET' },
  )
  if (!response.ok) {
    throw new Error(`获取 MCP 目录失败: ${response.status}`)
  }
  return parseAuthJson<McpServerCatalogResponse>(response)
}

export async function listMcpServerStatus(
  probe = false,
  scope: 'user' | 'all' = 'user',
): Promise<McpServerStatusResponse> {
  const url
    = `${API_BASE}/servers/status`
      + `?probe=${probe ? 'true' : 'false'}&scope=${encodeURIComponent(scope)}`
  const response = await authFetch(url, { method: 'GET' })
  if (!response.ok) {
    let detail = `获取 MCP 状态失败: ${response.status}`
    try {
      const json = await response.json() as { msg?: string, detail?: string }
      detail = json.msg || json.detail || detail
    } catch {
      // ignore
    }
    throw new Error(detail)
  }
  return parseAuthJson<McpServerStatusResponse>(response)
}

export async function getMcpConfig(): Promise<McpConfigFile> {
  const response = await authFetch(`${API_BASE}/config`, { method: 'GET' })
  if (!response.ok) {
    throw new Error(`读取 MCP 配置失败: ${response.status}`)
  }
  return parseAuthJson<McpConfigFile>(response)
}

export async function saveMcpConfig(content: string): Promise<McpConfigFile> {
  const response = await authFetch(`${API_BASE}/config`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ content }),
  })
  if (!response.ok) {
    let detail = `保存失败: ${response.status}`
    try {
      const err = await response.json()
      detail = err.detail || err.msg || detail
    } catch {
      // ignore
    }
    throw new Error(detail)
  }
  return parseAuthJson<McpConfigFile>(response)
}

export async function probeMcpServer(serverId: string): Promise<{
  ok: boolean
  tool_count: number
  message: string
}> {
  const response = await authFetch(
    `${API_BASE}/servers/${encodeURIComponent(serverId)}/probe`,
    { method: 'POST' },
  )
  if (!response.ok) {
    throw new Error(`探测失败: ${response.status}`)
  }
  return parseAuthJson(response)
}

export type McpServerWrite = {
  transport: 'streamable_http' | 'sse'
  url: string
  display_name?: string
  enabled: boolean
  headers_action: 'keep' | 'replace' | 'clear'
  headers?: Record<string, string>
}

export async function upsertMcpServer(serverId: string, payload: McpServerWrite) {
  const response = await authFetch(`${API_BASE}/servers/${encodeURIComponent(serverId)}`, {
    method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
  })
  return parseAuthJson<McpServerCatalogItem>(response)
}

export async function deleteMcpServer(serverId: string) {
  const response = await authFetch(`${API_BASE}/servers/${encodeURIComponent(serverId)}`, { method: 'DELETE' })
  return parseAuthJson(response)
}

export async function setMcpServerEnabled(serverId: string, enabled: boolean) {
  const action = enabled ? 'enable' : 'disable'
  const response = await authFetch(`${API_BASE}/servers/${encodeURIComponent(serverId)}/${action}`, { method: 'POST' })
  return parseAuthJson<McpServerCatalogItem>(response)
}

export async function listMcpServerTools(serverId: string, refresh = false) {
  const response = await authFetch(`${API_BASE}/servers/${encodeURIComponent(serverId)}/tools?refresh=${refresh}`, { method: 'GET' })
  return parseAuthJson<{ tools: Array<{ name: string, description: string }> }>(response)
}
