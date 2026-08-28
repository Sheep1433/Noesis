<script lang="ts" setup>
import type { ChatModelOption } from '@/api/models'
import { ensureSession } from '@/api/chat'
import { getChatModels } from '@/api/models'
import {
  orderReasoningLevels,
  reasoningLevelLabel,
} from '@/utils/reasoningLevels'

const props = defineProps<{
  sessionId: string
  /** 当前模型 id：按模型能力声明决定是否渲染与可选档位 */
  modelId?: string
  disabled?: boolean
  /** ACTIVE 会话才写回 session.extra；COMPOSING 仅改本地 modelValue */
  persistSessionExtra?: boolean
  embedded?: boolean
}>()

/** '' = 自动（不传参）；off/low/medium/high/max = 推理档位 */
const modelValue = defineModel<string>({ default: '' })

const options = ref<ChatModelOption[]>([])

/** 当前模型声明的档位（固定序）；未声明返回 null（不渲染控件） */
const declaredLevels = computed<string[] | null>(() => {
  const hit = options.value.find((item) => item.id === props.modelId)
  const levels = hit?.reasoning_levels
  if (!hit || !Array.isArray(levels) || levels.length === 0) {
    return null
  }
  const ordered = orderReasoningLevels(levels)
  return ordered.length > 0 ? ordered : null
})

/** 滑块索引 ↔ 档位值 */
const sliderIndex = computed<number>({
  get: () => {
    if (declaredLevels.value === null) {
      return 0
    }
    const index = declaredLevels.value.indexOf(modelValue.value)
    return index >= 0 ? index : 0
  },
  set: (index) => {
    if (declaredLevels.value === null) {
      return
    }
    const level = declaredLevels.value[index]
    if (level && level !== modelValue.value) {
      modelValue.value = level
      void persistEffort(level)
    }
  },
})

/** '' = 自动（不传参） */
const autoMode = computed<boolean>({
  get: () => {
    return !modelValue.value
  },
  set: (auto) => {
    const next = auto ? '' : defaultManualLevel()
    modelValue.value = next
    void persistEffort(next)
  },
})

/** 关闭自动时的落点：声明含 medium 取 medium，否则取中间档 */
function defaultManualLevel(): string {
  const levels = declaredLevels.value ?? []
  if (levels.includes('medium')) {
    return 'medium'
  }
  return levels[Math.floor((levels.length - 1) / 2)] ?? ''
}

const sliderMarks = computed<Record<number, string>>(() => {
  const marks: Record<number, string> = {}
  const levels = declaredLevels.value ?? []
  levels.forEach((level, index) => {
    marks[index] = reasoningLevelLabel(level)
  })
  return marks
})

const HINTS: Record<string, string> = {
  off: '关闭思考：直接回答，最快',
  low: '低：快速思考，适合简单问题',
  medium: '中：平衡思考与速度',
  high: '高：更深入的推理，更慢',
  max: '最高：最充分的思考，最慢',
}

const currentHint = computed(() => {
  if (autoMode.value) {
    return '自动：不干预，使用模型默认行为'
  }
  return HINTS[modelValue.value] ?? ''
})

const currentLabel = computed(() => {
  return reasoningLevelLabel(modelValue.value)
})

async function loadModels() {
  try {
    const catalog = await getChatModels()
    options.value = catalog.models ?? []
  } catch (e) {
    options.value = []
    console.warn('加载模型列表失败', e)
  }
}

async function persistEffort(level: string) {
  if (!props.persistSessionExtra || !props.sessionId) {
    return
  }
  try {
    await ensureSession(props.sessionId, {
      // ''（自动）写 null 清键：消费端 normalize 为「不传参」
      extra: { reasoning_effort: level || null },
    })
  } catch (e) {
    console.warn('保存推理档位失败', e)
  }
}

/** 模型切换后档位不再在声明内 → 回退自动（下次发送不传参） */
watch(
  () => [props.modelId, declaredLevels.value] as const,
  () => {
    if (declaredLevels.value !== null && modelValue.value && !declaredLevels.value.includes(modelValue.value)) {
      modelValue.value = ''
    }
  },
)

onMounted(() => {
  void loadModels()
})

watch(
  () => props.sessionId,
  () => {
    void loadModels()
  },
)
</script>

<template>
  <n-popover
    v-if="declaredLevels !== null"
    trigger="click"
    placement="top-start"
    :disabled="disabled"
    :show-arrow="true"
  >
    <template #trigger>
      <button
        type="button"
        class="composer-model-trigger"
        :class="{ 'composer-model-trigger--menu': embedded }"
        :disabled="disabled"
      >
        <span v-if="embedded" class="i-carbon:ideas composer-model-trigger__icon"></span>
        <span v-if="embedded" class="composer-model-trigger__title">思考</span>
        <span class="composer-model-trigger__label">{{ currentLabel }}</span>
        <span class="i-carbon:chevron-down text-12 opacity-60"></span>
      </button>
    </template>

    <div class="reasoning-panel">
      <div class="reasoning-panel__header">
        <span class="reasoning-panel__title">推理预算</span>
        <span class="reasoning-panel__auto">
          自动
          <n-switch v-model:value="autoMode" size="small" />
        </span>
      </div>
      <n-slider
        v-model:value="sliderIndex"
        :min="0"
        :max="(declaredLevels ?? []).length - 1"
        :step="1"
        :marks="sliderMarks"
        :disabled="autoMode"
        :tooltip="false"
        class="reasoning-panel__slider"
      />
      <div class="reasoning-panel__hint" :class="{ 'reasoning-panel__hint--muted': autoMode }">
        {{ currentHint }}
      </div>
    </div>
  </n-popover>
</template>

<style scoped>
.composer-model-trigger {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  max-width: 160px;
  padding: 4px 8px;
  border: none;
  border-radius: var(--noesis-radius-sm, 6px);
  background: transparent;
  color: var(--noesis-text-secondary, #6b7280);
  font-size: 12px;
  line-height: 1.4;
  cursor: pointer;
  transition: background-color 0.2s ease, color 0.2s ease;
}

.composer-model-trigger:hover:not(:disabled) {
  background: var(--noesis-color-primary-bg-subtle, rgb(0 0 0 / 4%));
  color: var(--noesis-text-primary, #111);
}

.composer-model-trigger:disabled {
  cursor: not-allowed;
  opacity: 0.55;
}

.composer-model-trigger__label {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.composer-model-trigger--menu {
  width: 100%;
  max-width: none;
  gap: 10px;
  padding: 8px 14px;
  border-radius: 0;
  color: var(--noesis-text-primary, #111);
  font-size: 13px;
  text-align: left;
}

.composer-model-trigger__icon {
  flex-shrink: 0;
  width: 16px;
  height: 16px;
  color: var(--noesis-text-secondary, #6b7280);
  font-size: 16px;
}

.composer-model-trigger__title {
  flex: 1;
}

.reasoning-panel {
  display: flex;
  flex-direction: column;
  gap: 14px;
  width: 260px;
  padding: 4px 2px;
}

.reasoning-panel__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.reasoning-panel__title {
  font-size: 13px;
  font-weight: 600;
  color: var(--noesis-text-primary, #111);
}

.reasoning-panel__auto {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: var(--noesis-text-secondary, #6b7280);
}

.reasoning-panel__slider {
  padding: 0 6px;
}

.reasoning-panel__hint {
  font-size: 12px;
  line-height: 1.5;
  color: var(--noesis-text-secondary, #6b7280);
}

.reasoning-panel__hint--muted {
  opacity: 0.7;
}
</style>
