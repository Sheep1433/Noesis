/**
 * 知识库管理 API
 */
import { authFetch } from '@/utils/authHttp'

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
  final_top_k?: number
  recall_top_k?: number
  use_reranker?: boolean
  score_threshold?: number | null
  search_mode?: KbSearchMode
  rrf_k?: number
  [key: string]: unknown
}

export const KB_DEFAULT_QUERY: KbQueryParams = {
  final_top_k: 10,
  recall_top_k: 50,
  search_mode: 'hybrid',
  use_reranker: true,
  score_threshold: null,
  rrf_k: 60,
}

export interface KbProcessingParams {
  chunk_preset_id?: string
  chunk_template_id?: string
  parser_id?: string
  chunk_parser_config?: {
    chunk_size?: number
    chunk_overlap?: number
  }
}

export interface CollectionConfig {
  collection_name: string
  processing_params: KbProcessingParams
  query_params: KbQueryParams
}

export interface CollectionInfo {
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
  file_hash?: string | null
}

interface ShardInfo {
  id: string
  content: string
  char_length: number
  created_at: string | null
  header_path?: string | null
  chunk_index?: number | null
}

export interface ShardDetail {
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
  effective_processing_params?: Record<string, unknown> | null
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
  final_top_k?: number
  recall_top_k?: number
  use_reranker?: boolean
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
  recall_score?: number | null
  rerank_score?: number | null
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

async function kbRequest(url: string, init?: RequestInit): Promise<Response> {
  const response = await authFetch(url, init)
  if (!response.ok) {
    await throwKnowledgeBaseError(response, '请求失败')
  }
  return response
}

/**
 * 获取知识库连接状态
 */
export async function getKnowledgeBaseStatus(): Promise<KnowledgeBaseStatus> {
  const response = await kbRequest(`${API_BASE}/status`, { method: 'GET' })
  return response.json()
}

/**
 * 获取 Collection 列表
 */
export async function getCollections(): Promise<CollectionInfo[]> {
  const response = await kbRequest(`${API_BASE}/collections`, { method: 'GET' })
  return response.json()
}

/**
 * 获取 Collection 详情
 */
export async function getCollection(name: string): Promise<CollectionDetail> {
  const response = await kbRequest(
    `${API_BASE}/collections/${encodeURIComponent(name)}`,
    { method: 'GET' },
  )
  return response.json()
}

/**
 * 获取 Collection 下的文档列表
 */
export async function getDocuments(collectionName: string): Promise<DocumentInfo[]> {
  const response = await kbRequest(
    `${API_BASE}/collections/${encodeURIComponent(collectionName)}/documents`,
    { method: 'GET' },
  )
  return response.json()
}

/**
 * 获取文档的分片列表
 */
export async function getDocumentShards(collectionName: string, fileName: string): Promise<ShardInfo[]> {
  const response = await kbRequest(
    `${API_BASE}/collections/${encodeURIComponent(collectionName)}/documents/${encodeURIComponent(fileName)}/shards`,
    { method: 'GET' },
  )
  return response.json()
}

/**
 * 获取分片详情
 */
export async function getShardDetail(collectionName: string, shardId: string): Promise<ShardDetail> {
  const response = await kbRequest(
    `${API_BASE}/collections/${encodeURIComponent(collectionName)}/shards/${encodeURIComponent(shardId)}`,
    { method: 'GET' },
  )
  return response.json()
}

/**
 * 删除文档
 */
export async function deleteDocument(collectionName: string, fileName: string): Promise<DeleteResponse> {
  const response = await kbRequest(
    `${API_BASE}/collections/${encodeURIComponent(collectionName)}/documents/${encodeURIComponent(fileName)}`,
    { method: 'DELETE' },
  )
  return response.json()
}

/**
 * 创建 Collection
 */
export async function createCollection(request: CreateCollectionRequest): Promise<CreateCollectionResponse> {
  const response = await kbRequest(`${API_BASE}/collections`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(request),
  })
  return response.json()
}

/**
 * 删除 Collection
 */
export async function deleteCollection(collectionName: string): Promise<{ success: boolean, message: string }> {
  const response = await kbRequest(
    `${API_BASE}/collections/${encodeURIComponent(collectionName)}`,
    { method: 'DELETE' },
  )
  return response.json()
}

/** 上传文档（解析分块后写入知识库） */
export async function uploadDocument(
  collectionName: string,
  file: File,
  processingParams?: KbProcessingParams,
): Promise<{ success: boolean, message: string, file_name?: string, shards_created?: number, extracted_markdown?: string | null }> {
  const formData = new FormData()
  formData.append('file', file)
  if (processingParams && Object.keys(processingParams).length > 0) {
    formData.append('processing_params', JSON.stringify(processingParams))
  }

  const response = await kbRequest(
    `${API_BASE}/collections/${encodeURIComponent(collectionName)}/upload`,
    { method: 'POST', body: formData },
  )

  return response.json() as Promise<{
    success: boolean
    message: string
    file_name?: string
    shards_created?: number
    extracted_markdown?: string | null
  }>
}

/**
 * 知识库检索；不传 limit / score_threshold 时使用集合默认值
 */
export async function searchCollection(collectionName: string, body: SearchCollectionRequest): Promise<SearchResult[]> {
  const payload: Record<string, unknown> = {
    query: body.query,
  }
  if (body.search_mode) {
    payload.search_mode = body.search_mode
  }
  if (body.final_top_k !== undefined && body.final_top_k !== null) {
    payload.final_top_k = body.final_top_k
  } else if (body.limit !== undefined && body.limit !== null) {
    payload.limit = body.limit
  }
  if (body.recall_top_k !== undefined && body.recall_top_k !== null) {
    payload.recall_top_k = body.recall_top_k
  }
  if (body.use_reranker !== undefined) {
    payload.use_reranker = body.use_reranker
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

  const response = await kbRequest(
    `${API_BASE}/collections/${encodeURIComponent(collectionName)}/search`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    },
  )

  const json = await response.json() as { data?: SearchResult[] } | SearchResult[]
  if (Array.isArray(json)) {
    return json
  }
  if (json && Array.isArray(json.data)) {
    return json.data
  }
  return []
}

export async function getCollectionConfig(collectionName: string): Promise<CollectionConfig> {
  const response = await kbRequest(
    `${API_BASE}/collections/${encodeURIComponent(collectionName)}/config`,
    { method: 'GET' },
  )
  return response.json()
}

export async function patchCollectionConfig(
  collectionName: string,
  patch: { processing_params?: KbProcessingParams, query_params?: KbQueryParams },
): Promise<CollectionConfig> {
  const response = await kbRequest(
    `${API_BASE}/collections/${encodeURIComponent(collectionName)}/config`,
    {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(patch),
    },
  )
  return response.json()
}

export type {
  CollectionConfig,
  CollectionDetail,
  CollectionInfo,
  CreateCollectionRequest,
  CreateCollectionResponse,
  DeleteResponse,
  DocumentInfo,
  KnowledgeBaseStatus,
  SearchResult,
  ShardInfo,
}
