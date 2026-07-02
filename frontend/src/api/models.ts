/**
 * 对话模型目录 API
 */
import { authFetch, parseAuthJson } from '@/utils/authHttp'

const API_BASE = `${location.origin}/api/models`

export interface ChatModelOption {
  id: string
  label: string
  model_name: string
  model_type: string
  is_default: boolean
}

export interface ChatModelCatalog {
  models: ChatModelOption[]
  default_id: string
}

export async function getChatModels(): Promise<ChatModelCatalog> {
  const res = await authFetch(API_BASE)
  return parseAuthJson<ChatModelCatalog>(res)
}
