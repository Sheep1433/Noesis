/**
 * 知识库管理 API
 */
import { useUserStore } from '@/store/business/userStore'

const API_BASE = `${location.origin}/api/knowledge_base`

export type KbSearchMode = 'vector' | 'bm25' | 'hybrid'

export interface KbSearchFilters {
  file_name?: string
  source_name?: string
  Header_1?: string
  Header_2?: string
  Header_3?: string
  Header_4?: string
  header_path_prefix?: string
  [key: string]: unknown
}

export interface KbQueryParams {
  limit?: number
  score_threshold?: number | null
  search_mode?: KbSearchMode
  rrf_k?: number
  [key: string]: unknown
}

export const KB_DEFAULT_QUERY: KbQueryParams = {
  limit: 10,
  score_threshold: null,
}

interface CollectionInfo {
  name: string
  vector_dimension: number
  documents_count: number
  points_count: number
  created_at: string | null
}

export interface CollectionDetail extends CollectionInfo {
  status?: string | null
}

interface KnowledgeBaseStatus {
  connected: boolean
  host: string
  port: number
  collections_count: number
}

interface DocumentInfo {
  file_name: string
  shard_count: number
  uploaded_at: string | null
}

interface ShardInfo {
  id: string
  content: string
  char_length: number
  created_at: string | null
  header_path?: string | null
  chunk_index?: number | null
}

interface ShardDetail {
  id: string
  content: string
  char_length: number
  vector_dimension: number
  created_at: string | null
  header_path?: string | null
  Header_1?: string | null
  Header_2?: string | null
  Header_3?: string | null
  chunk_index?: number | null
}

interface DeleteResponse {
  success: boolean
  message: string
  deleted_count: number
}

interface CreateCollectionRequest {
  name: string
  vector_dimension: number
  description?: string
}

export interface SearchCollectionRequest {
  query: string
  limit?: number
  score_threshold?: number | null
  search_mode?: KbSearchMode
  filters?: KbSearchFilters
  rrf_k?: number
}

export interface SearchResult {
  id: string
  score: number
  content: string
  file_name: string
  search_mode?: string
  header_path?: string | null
}

interface CreateCollectionResponse {
  success: boolean
  message: string
  name: string
}

async function throwKnowledgeBaseError(response: Response, fallback: string): Promise<never> {
  const body = await response.json().catch(() => null) as
    | { msg?: string, detail?: string | string[] }
    | null
  let serverMsg = ''
  if (body && typeof body === 'object') {
    if (typeof body.msg === 'string' && body.msg.trim()) {
      serverMsg = body.msg.trim()
    } else if (typeof body.detail === 'string' && body.detail.trim()) {
      serverMsg = body.detail.trim()
    } else if (Array.isArray(body.detail) && body.detail.length) {
      serverMsg = String(body.detail[0])
    }
  }
  throw new Error(serverMsg || fallback)
}

/**
 * 获取知识库连接状态
 */
export async function getKnowledgeBaseStatus(): Promise<KnowledgeBaseStatus> {
  const userStore = useUserStore()
  const token = userStore.getUserToken()
  const url = `${API_BASE}/status`

  const response = await fetch(url, {
    method: 'GET',
    headers: {
      Authorization: `Bearer ${token}`,
    },
  })

  if (!response.ok) {
    await throwKnowledgeBaseError(response, '获取知识库状态失败')
  }

  return response.json()
}

/**
 * 获取 Collection 列表
 */
export async function getCollections(): Promise<CollectionInfo[]> {
  const userStore = useUserStore()
  const token = userStore.getUserToken()
  const url = `${API_BASE}/collections`

  const response = await fetch(url, {
    method: 'GET',
    headers: {
      Authorization: `Bearer ${token}`,
    },
  })

  if (!response.ok) {
    await throwKnowledgeBaseError(response, '获取 Collection 列表失败')
  }

  return response.json()
}

/**
 * 获取 Collection 详情
 */
export async function getCollection(name: string): Promise<CollectionDetail> {
  const userStore = useUserStore()
  const token = userStore.getUserToken()
  const url = `${API_BASE}/collections/${encodeURIComponent(name)}`

  const response = await fetch(url, {
    method: 'GET',
    headers: {
      Authorization: `Bearer ${token}`,
    },
  })

  if (!response.ok) {
    await throwKnowledgeBaseError(response, '获取 Collection 详情失败')
  }

  return response.json()
}

/**
 * 获取 Collection 下的文档列表
 */
export async function getDocuments(collectionName: string): Promise<DocumentInfo[]> {
  const userStore = useUserStore()
  const token = userStore.getUserToken()
  const url = `${API_BASE}/collections/${encodeURIComponent(collectionName)}/documents`

  const response = await fetch(url, {
    method: 'GET',
    headers: {
      Authorization: `Bearer ${token}`,
    },
  })

  if (!response.ok) {
    await throwKnowledgeBaseError(response, '获取文档列表失败')
  }

  return response.json()
}

/**
 * 获取文档的分片列表
 */
export async function getDocumentShards(collectionName: string, fileName: string): Promise<ShardInfo[]> {
  const userStore = useUserStore()
  const token = userStore.getUserToken()
  const url = `${API_BASE}/collections/${encodeURIComponent(collectionName)}/documents/${encodeURIComponent(fileName)}/shards`

  const response = await fetch(url, {
    method: 'GET',
    headers: {
      Authorization: `Bearer ${token}`,
    },
  })

  if (!response.ok) {
    await throwKnowledgeBaseError(response, '获取分片列表失败')
  }

  return response.json()
}

/**
 * 获取分片详情
 */
export async function getShardDetail(collectionName: string, shardId: string): Promise<ShardDetail> {
  const userStore = useUserStore()
  const token = userStore.getUserToken()
  const url = `${API_BASE}/collections/${encodeURIComponent(collectionName)}/shards/${encodeURIComponent(shardId)}`

  const response = await fetch(url, {
    method: 'GET',
    headers: {
      Authorization: `Bearer ${token}`,
    },
  })

  if (!response.ok) {
    await throwKnowledgeBaseError(response, '获取分片详情失败')
  }

  return response.json()
}

/**
 * 删除文档
 */
export async function deleteDocument(collectionName: string, fileName: string): Promise<DeleteResponse> {
  const userStore = useUserStore()
  const token = userStore.getUserToken()
  const url = `${API_BASE}/collections/${encodeURIComponent(collectionName)}/documents/${encodeURIComponent(fileName)}`

  const response = await fetch(url, {
    method: 'DELETE',
    headers: {
      Authorization: `Bearer ${token}`,
    },
  })

  if (!response.ok) {
    await throwKnowledgeBaseError(response, '删除文档失败')
  }

  return response.json()
}

/**
 * 创建 Collection
 */
export async function createCollection(request: CreateCollectionRequest): Promise<CreateCollectionResponse> {
  const userStore = useUserStore()
  const token = userStore.getUserToken()
  const url = `${API_BASE}/collections`

  const response = await fetch(url, {
    method: 'POST',
    headers: {
      'Authorization': `Bearer ${token}`,
      'Content-Type': 'application/json',
    },
    body: JSON.stringify(request),
  })

  if (!response.ok) {
    await throwKnowledgeBaseError(response, '创建 Collection 失败')
  }

  return response.json()
}

/**
 * 删除 Collection
 */
export async function deleteCollection(collectionName: string): Promise<{ success: boolean, message: string }> {
  const userStore = useUserStore()
  const token = userStore.getUserToken()
  const url = `${API_BASE}/collections/${encodeURIComponent(collectionName)}`

  const response = await fetch(url, {
    method: 'DELETE',
    headers: {
      Authorization: `Bearer ${token}`,
    },
  })

  if (!response.ok) {
    await throwKnowledgeBaseError(response, '删除 Collection 失败')
  }

  return response.json()
}

/** 上传文档（平台固定 Markdown 标题分块） */
export async function uploadDocument(
  collectionName: string,
  file: File,
): Promise<{ success: boolean, message: string, shards_created?: number, extracted_markdown?: string | null }> {
  const userStore = useUserStore()
  const token = userStore.getUserToken()
  const url = `${API_BASE}/collections/${encodeURIComponent(collectionName)}/upload`

  const formData = new FormData()
  formData.append('file', file)

  const response = await fetch(url, {
    method: 'POST',
    headers: {
      Authorization: `Bearer ${token}`,
    },
    body: formData,
  })

  if (!response.ok) {
    await throwKnowledgeBaseError(response, '上传文档失败')
  }

  return response.json() as Promise<{
    success: boolean
    message: string
    shards_created?: number
    extracted_markdown?: string | null
  }>
}

/**
 * 知识库检索；不传 limit / score_threshold 时使用集合默认值
 */
export async function searchCollection(collectionName: string, body: SearchCollectionRequest): Promise<SearchResult[]> {
  const userStore = useUserStore()
  const token = userStore.getUserToken()
  const url = `${API_BASE}/collections/${encodeURIComponent(collectionName)}/search`

  const payload: Record<string, unknown> = {
    query: body.query,
    search_mode: body.search_mode ?? 'vector',
  }
  if (body.limit !== undefined && body.limit !== null) {
    payload.limit = body.limit
  }
  if (body.score_threshold !== undefined) {
    payload.score_threshold = body.score_threshold
  }
  if (body.filters && Object.keys(body.filters).length > 0) {
    payload.filters = body.filters
  }
  if (body.rrf_k !== undefined && body.rrf_k !== null) {
    payload.rrf_k = body.rrf_k
  }

  const response = await fetch(url, {
    method: 'POST',
    headers: {
      'Authorization': `Bearer ${token}`,
      'Content-Type': 'application/json',
    },
    body: JSON.stringify(payload),
  })

  if (!response.ok) {
    await throwKnowledgeBaseError(response, '检索失败')
  }

  const json = await response.json() as { data?: SearchResult[] } | SearchResult[]
  if (Array.isArray(json)) {
    return json
  }
  if (json && Array.isArray(json.data)) {
    return json.data
  }
  return []
}

export type {
  CollectionDetail,
  CollectionInfo,
  CreateCollectionRequest,
  CreateCollectionResponse,
  DeleteResponse,
  DocumentInfo,
  KnowledgeBaseStatus,
  SearchResult,
  ShardDetail,
  ShardInfo,
}
