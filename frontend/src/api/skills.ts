/**
 * Skills 文件目录 API（磁盘 backend/skills，见配置 skills_filesystem_root）
 */
import { useUserStore } from '@/store/business/userStore'

const API_BASE = `${location.origin}/api/skills`

/** Skills 文件目录树节点 */
export interface SkillFsTreeNode {
  key: string
  label: string
  isLeaf: boolean
  children?: SkillFsTreeNode[]
}

/** Skills 文件目录树 */
export interface SkillFsTreeResponse {
  root_path: string
  root_exists: boolean
  tree: SkillFsTreeNode[]
}

/** Skills 目录下文件内容 */
export interface SkillFsFileContent {
  path: string
  content: string
}

/**
 * 获取 Skills 文件目录树（默认仓库 backend/skills，可用配置 skills_filesystem_root 覆盖）
 */
export async function getSkillsFsTree(): Promise<SkillFsTreeResponse> {
  const userStore = useUserStore()
  const token = userStore.getUserToken()
  const url = `${API_BASE}/fs/tree`

  const response = await fetch(url, {
    method: 'GET',
    headers: {
      Authorization: `Bearer ${token}`,
    },
  })

  if (!response.ok) {
    throw new Error(`获取 Skills 目录失败: ${response.status}`)
  }

  return response.json()
}

/**
 * 读取 Skills 目录下文件文本
 */
export async function getSkillsFsFile(relPath: string): Promise<SkillFsFileContent> {
  const userStore = useUserStore()
  const token = userStore.getUserToken()
  const url = `${API_BASE}/fs/file?path=${encodeURIComponent(relPath)}`

  const response = await fetch(url, {
    method: 'GET',
    headers: {
      Authorization: `Bearer ${token}`,
    },
  })

  if (!response.ok) {
    const err = await response.json().catch(() => ({ detail: response.statusText }))
    throw new Error(err.detail || `读取文件失败: ${response.status}`)
  }

  return response.json()
}

/**
 * 上传 skill：将 ZIP 解压到当前 Skills 根目录
 */
export async function uploadSkillsFsZip(
  file: File,
): Promise<{ success: boolean, message: string }> {
  const userStore = useUserStore()
  const token = userStore.getUserToken()
  const url = `${API_BASE}/fs/upload-zip`

  const formData = new FormData()
  formData.append('file', file)

  const response = await fetch(url, {
    method: 'POST',
    headers: {
      Authorization: `Bearer ${token}`,
    },
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
