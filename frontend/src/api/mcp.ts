/**
 * MCP 目录与用户配置 API
 */
import { authFetch, parseAuthJson } from '@/utils/authHttp'

const API_BASE = `${location.origin}/api/mcp`

export type McpServerSource = 'platform' | 'user'

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

export interface McpServerUpsertBody {
  transport: 'streamable_http' | 'sse'
  url: string
  display_name?: string
  headers?: Record<string, string>
}

export async function listMcpServers(): Promise<McpServerCatalogResponse> {
  const response = await authFetch(`${API_BASE}/servers`, { method: 'GET' })
  if (!response.ok) {
    throw new Error(`获取 MCP 目录失败: ${response.status}`)
  }
  return response.json()
}

export async function upsertMcpServer(
  serverId: string,
  body: McpServerUpsertBody,
): Promise<McpServerCatalogItem> {
  const response = await authFetch(
    `${API_BASE}/servers/${encodeURIComponent(serverId)}`,
    {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    },
  )
  const data = await parseAuthJson<{ data?: McpServerCatalogItem }>(response)
  if (!response.ok) {
    throw new Error(`保存 MCP 失败: ${response.status}`)
  }
  return data.data as McpServerCatalogItem
}

export async function deleteMcpServer(serverId: string): Promise<void> {
  const response = await authFetch(
    `${API_BASE}/servers/${encodeURIComponent(serverId)}`,
    { method: 'DELETE' },
  )
  if (!response.ok) {
    throw new Error(`删除 MCP 失败: ${response.status}`)
  }
}
