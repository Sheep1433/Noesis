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

/** 平台 Provider 当前可用模型（OpenCode Zen 免费模型轮换时刷新用） */
export async function discoverPlatformModels() {
  const res = await authFetch(new Request(`${API_BASE}/discover-platform`, {
    method: 'POST',
    credentials: 'include',
  }))
  return parseAuthJson<{
    ok: boolean
    status: string
    models: { model_id: string, label: string, context_window: number }[]
    message: string
  }>(res)
}
