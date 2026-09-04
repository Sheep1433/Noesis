/**
 * 对话模型目录 API
 */
import { authFetch, parseAuthJson } from '@/utils/authHttp'

const API_BASE = `${location.origin}/api/models`

export interface ChatModelOption {
  id: string
  label: string
  provider?: string
  model_type: string
  is_default: boolean
  supports_vision?: boolean
  context_window?: number
  custom?: boolean
}

export interface ProviderPreset {
  id: string
  label: string
  base_url: string
  headers?: Record<string, string>
}

export interface PlatformProvider {
  id: string
  label: string
  base_url: string
}

/** 发现列表行（OpenAI /models 归一化 + 原始布尔/数值字段透传） */
export interface DiscoveredModelRow {
  model_id: string
  label: string
  context_window: number
  context_source?: 'provider' | 'unknown' | 'catalog' | string
  flags?: Record<string, boolean | number>
}

export interface ChatModelCatalog {
  models: ChatModelOption[]
  platform_provider?: PlatformProvider | null
  provider_presets?: ProviderPreset[]
  default_id: string
  first_vision_model_id?: string | null
  vlm_fallback_available?: boolean
}

export async function getChatModels(): Promise<ChatModelCatalog> {
  const res = await authFetch(API_BASE)
  return parseAuthJson<ChatModelCatalog>(res)
}
