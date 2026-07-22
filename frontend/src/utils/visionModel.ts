/**
 * 上传图片时确保使用支持 Vision 的模型，或提示 VLM 兜底。
 */
import type { ChatModelCatalog } from '@/api/models'
import { ensureSession } from '@/api/chat'
import { getChatModels } from '@/api/models'

let catalogCache: ChatModelCatalog | null = null
let catalogPromise: Promise<ChatModelCatalog> | null = null

async function loadCatalog(): Promise<ChatModelCatalog> {
  if (catalogCache) {
    return catalogCache
  }
  if (!catalogPromise) {
    catalogPromise = getChatModels().then((data) => {
      catalogCache = data
      return data
    })
  }
  return catalogPromise
}

export function invalidateChatModelCatalogCache() {
  catalogCache = null
  catalogPromise = null
}

function modelSupportsVision(catalog: ChatModelCatalog, modelId: string): boolean {
  const hit = catalog.models?.find((m) => m.id === modelId)
  return Boolean(hit?.supports_vision)
}

/**
 * 图片上传成功后调用：必要时自动切换到 Vision 模型并持久化到会话。
 * @returns 是否发生了模型切换
 */
export async function ensureVisionModelForImageUpload(options: {
  sessionId: string
  selectedModelId: Ref<string>
  /** 仅 ACTIVE（或发送编排已物化）时写回 session.extra */
  persistSessionExtra?: boolean
}): Promise<'already' | 'switched' | 'vlm_fallback' | 'unsupported'> {
  const catalog = await loadCatalog()
  const currentId = options.selectedModelId.value || catalog.default_id

  if (modelSupportsVision(catalog, currentId)) {
    return 'already'
  }

  const visionId = catalog.first_vision_model_id
    || catalog.models?.find((m) => m.supports_vision)?.id

  if (visionId) {
    options.selectedModelId.value = visionId
    const label = catalog.models?.find((m) => m.id === visionId)?.label || visionId
    if (options.persistSessionExtra && options.sessionId) {
      try {
        await ensureSession(options.sessionId, { extra: { model_id: visionId } })
      } catch (e) {
        console.warn('保存 Vision 模型选择失败', e)
      }
    }
    window.$ModalMessage.info(`已切换为「${label}」以支持图片理解`)
    return 'switched'
  }

  if (catalog.vlm_fallback_available) {
    window.$ModalMessage.info('当前文本模型将通过 VLM 生成图片描述后作答')
    return 'vlm_fallback'
  }

  window.$ModalMessage.warning('当前未配置 Vision 模型或 VLM，上传的图片可能无法被理解')
  return 'unsupported'
}
