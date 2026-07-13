/**
 * Skills 文件目录 API（平台预置 + 个人上传）
 */
import { authFetch } from '@/utils/authHttp'

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

  return response.json()
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
    throw new Error(err.detail || `读取文件失败: ${response.status}`)
  }

  return response.json()
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
  if (node.isLeaf || node.source !== 'user') {
    return false
  }
  const { path } = parseSourceFromKey(node.key)
  return path.length > 0 && !path.includes('/')
}

export { parseSourceFromKey }
