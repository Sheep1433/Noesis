/**
 * Skills 文件目录 API（平台预置 + 个人上传）
 */
import { authFetch, parseAuthJson } from '@/utils/authHttp'
import { downloadFile } from '@/utils/download'

const API_BASE = `${location.origin}/api/skills`

export type SkillSource = 'platform' | 'user'

/** Skills 目录树节点 */
export interface SkillFsTreeNode {
  key: string
  label: string
  isLeaf: boolean
  source: SkillSource
  children?: SkillFsTreeNode[]
}

/** 单源 Skills 目录摘要 */
export interface SkillFsSourceSection {
  root_exists: boolean
  writable: boolean
  skill_count: number
  tree: SkillFsTreeNode[]
}

/** Skills 目录树 */
export interface SkillFsTreeResponse {
  platform: SkillFsSourceSection
  user: SkillFsSourceSection
  tree: SkillFsTreeNode[]
}

/** Skills 目录下文件内容 */
export interface SkillFsFileContent {
  rel_path: string
  filename: string
  source: SkillSource
  content: string
}

function parseSourceFromKey(key: string): { source: SkillSource, path: string } {
  if (key.startsWith('user:')) {
    return { source: 'user', path: key.slice('user:'.length) }
  }
  if (key.startsWith('platform:')) {
    return { source: 'platform', path: key.slice('platform:'.length) }
  }
  return { source: 'platform', path: key }
}

/**
 * 获取当前用户可用 Skills 目录树
 */
export async function getSkillsFsTree(): Promise<SkillFsTreeResponse> {
  const url = `${API_BASE}/fs/tree`

  const response = await authFetch(url, {
    method: 'GET',
  })

  if (!response.ok) {
    throw new Error(`获取 Skills 目录失败: ${response.status}`)
  }

  return parseAuthJson<SkillFsTreeResponse>(response)
}

/**
 * 读取 Skills 目录下文件文本
 */
export async function getSkillsFsFile(
  key: string,
  source?: SkillSource,
): Promise<SkillFsFileContent> {
  const parsed = parseSourceFromKey(key)
  const effectiveSource = source ?? parsed.source
  const relPath = parsed.path
  const url = `${API_BASE}/fs/file?path=${encodeURIComponent(relPath)}&source=${effectiveSource}`

  const response = await authFetch(url, {
    method: 'GET',
  })

  if (!response.ok) {
    const err = await response.json().catch(() => ({ detail: response.statusText }))
    throw new Error(err.detail || err.msg || `读取文件失败: ${response.status}`)
  }

  return parseAuthJson<SkillFsFileContent>(response)
}

/**
 * 上传 skill：将 ZIP 解压到当前用户的私有目录
 */
export async function uploadSkillsFsZip(
  file: File,
): Promise<{ success: boolean, message: string }> {
  const url = `${API_BASE}/fs/upload-zip`

  const formData = new FormData()
  formData.append('file', file)

  const response = await authFetch(url, {
    method: 'POST',
    body: formData,
  })

  const json = await response.json().catch(() => ({}))
  if (!response.ok) {
    throw new Error(json.detail || json.msg || `上传失败: ${response.status}`)
  }

  return {
    success: Boolean(json.success),
    message: json.msg || '操作成功',
  }
}

/**
 * 删除个人技能顶层目录
 */
export async function deleteUserSkillPackage(
  packageName: string,
): Promise<{ success: boolean, message: string }> {
  const url = `${API_BASE}/fs/package?path=${encodeURIComponent(packageName)}`

  const response = await authFetch(url, {
    method: 'DELETE',
  })

  const json = await response.json().catch(() => ({}))
  if (!response.ok) {
    throw new Error(json.detail || json.msg || `删除失败: ${response.status}`)
  }

  return {
    success: Boolean(json.success),
    message: json.msg || '删除成功',
  }
}

export function isDeletableUserSkillPackage(node: SkillFsTreeNode): boolean {
  return isSkillPackageNode(node) && node.source === 'user'
}

/** 顶层技能包目录（可下载 ZIP） */
export function isSkillPackageNode(node: SkillFsTreeNode): boolean {
  if (node.isLeaf || node.key === 'platform:' || node.key === 'user:') {
    return false
  }
  const { path } = parseSourceFromKey(node.key)
  return path.length > 0 && !path.includes('/')
}

/**
 * 下载顶层技能目录 ZIP
 */
export async function downloadSkillPackageArchive(
  packageName: string,
  source: SkillSource,
): Promise<void> {
  const url =
    `${API_BASE}/fs/package/archive` +
    `?path=${encodeURIComponent(packageName)}&source=${encodeURIComponent(source)}`
  const response = await authFetch(url, { method: 'GET' })
  if (!response.ok) {
    const err = await response.json().catch(() => ({ detail: response.statusText }))
    throw new Error(err.detail || err.msg || `下载失败: ${response.status}`)
  }
  const blob = await response.blob()
  downloadFile(blob, `${packageName}.zip`)
}

/** skills.sh 市场条目 */
export interface SkillMarketItem {
  id: string
  skill_id: string
  name: string
  source: string
  installs: number
  market_url: string
  installed: boolean
  install_match: 'none' | 'exact' | 'name_conflict'
}

export interface SkillMarketListResponse {
  items: SkillMarketItem[]
  query: string
  total: number
}

export interface SkillMarketDetailResponse {
  item: SkillMarketItem
  skill_md: string
  skill_md_path: string
}

export type SkillMarketSort = 'all_time' | 'trending'

export async function browseSkillsMarket(
  sort: SkillMarketSort = 'trending',
  limit = 20,
  offset = 0,
): Promise<SkillMarketListResponse> {
  const url = `${API_BASE}/market/browse?sort=${encodeURIComponent(sort)}&limit=${limit}&offset=${offset}`
  const response = await authFetch(url, { method: 'GET' })
  if (!response.ok) {
    throw new Error(`获取榜单失败: ${response.status}`)
  }
  return parseAuthJson<SkillMarketListResponse>(response)
}

export async function searchSkillsMarket(
  q: string,
  limit = 20,
  offset = 0,
): Promise<SkillMarketListResponse> {
  const url = `${API_BASE}/market/search?q=${encodeURIComponent(q)}&limit=${limit}&offset=${offset}`
  const response = await authFetch(url, { method: 'GET' })
  if (!response.ok) {
    const err = await response.json().catch(() => ({ detail: response.statusText }))
    throw new Error(err.detail || err.msg || `搜索失败: ${response.status}`)
  }
  return parseAuthJson<SkillMarketListResponse>(response)
}

export async function getSkillsMarketDetail(
  source: string,
  skillId: string,
): Promise<SkillMarketDetailResponse> {
  const url = `${API_BASE}/market/detail?source=${encodeURIComponent(source)}&skill_id=${encodeURIComponent(skillId)}`
  const response = await authFetch(url, { method: 'GET' })
  if (!response.ok) {
    const err = await response.json().catch(() => ({ detail: response.statusText }))
    throw new Error(err.detail || err.msg || `获取详情失败: ${response.status}`)
  }
  return parseAuthJson<SkillMarketDetailResponse>(response)
}

export async function installSkillsMarketPackage(params: {
  source: string
  skill_id: string
  overwrite?: boolean
}): Promise<{ success: boolean, message: string }> {
  const url = `${API_BASE}/market/install`
  const response = await authFetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      source: params.source,
      skill_id: params.skill_id,
      overwrite: Boolean(params.overwrite),
    }),
  })
  const json = await response.json().catch(() => ({}))
  if (!response.ok) {
    throw new Error(json.detail || json.msg || `安装失败: ${response.status}`)
  }
  return {
    success: Boolean(json.success ?? true),
    message: json.msg || '安装成功',
  }
}

export { parseSourceFromKey }
