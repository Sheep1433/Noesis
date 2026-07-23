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
}

export interface McpServerCatalogResponse {
  servers: McpServerCatalogItem[]
}

export interface McpServerStatusItem extends McpServerCatalogItem {
  status: McpServerStatus
  tool_count: number
  message: string
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
  const url =
    `${API_BASE}/servers/status` +
    `?probe=${probe ? 'true' : 'false'}&scope=${encodeURIComponent(scope)}`
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
