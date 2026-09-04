<script lang="ts" setup>
import { ensureSession } from '@/api/chat'
import {
  modelSupportsReasoningEffort,
  REASONING_LEVEL_ORDER,
  reasoningLevelLabel,
} from '@/utils/reasoningLevels'

const props = defineProps<{
  sessionId: string
  /** 当前模型 id：仅已知支持 reasoning_effort 的模型显示入口 */
  modelId?: string
  disabled?: boolean
  /** ACTIVE 会话才写回 session.extra；COMPOSING 仅改本地 modelValue */
  persistSessionExtra?: boolean
  embedded?: boolean
}>()

/** '' = 自动（不传参）；low/medium/high = 推理档位 */
const modelValue = defineModel<string>({ default: '' })

/** 滑块停靠点：自动（不发参数）+ 通用三档 */
const STOPS: string[] = ['', ...REASONING_LEVEL_ORDER]

const supported = computed(() => {
  return modelSupportsReasoningEffort(props.modelId ?? '')
})

/** 滑块索引 ↔ 档位值 */
const sliderIndex = computed<number>({
  get: () => {
    const index = STOPS.indexOf(modelValue.value)
    return index >= 0 ? index : 0
  },
  set: (index) => {
    const level = STOPS[index] ?? ''
    if (level !== modelValue.value) {
      modelValue.value = level
      void persistEffort(level)
    }
  },
})

const sliderMarks = computed<Record<number, string>>(() => {
  const marks: Record<number, string> = {}
  STOPS.forEach((level, index) => {
    marks[index] = reasoningLevelLabel(level)
  })
  return marks
})

const HINTS: Record<string, string> = {
  low: '低：快速思考，适合简单问题',
  medium: '中：平衡思考与速度',
  high: '高：更深入的推理，更慢',
}

const currentHint = computed(() => {
  if (!modelValue.value) {
    return '自动：不干预，使用模型默认行为'
  }
  return HINTS[modelValue.value] ?? ''
})

const currentLabel = computed(() => {
  return reasoningLevelLabel(modelValue.value)
})

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
</script>

<template>
  <n-popover
    v-if="supported"
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
      </div>
      <n-slider
        v-model:value="sliderIndex"
        :min="0"
        :max="STOPS.length - 1"
        :step="1"
        :marks="sliderMarks"
        :tooltip="false"
        class="reasoning-panel__slider"
      />
      <div class="reasoning-panel__hint" :class="{ 'reasoning-panel__hint--muted': !modelValue }">
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

/* 移动端（≤768px，同 useBreakpoint md）：档位标签限幅截断，避免与模型选择器互相挤压 */
@media (max-width: 768px) {
  .composer-model-trigger {
    max-width: 80px;
  }
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
  width: 240px;
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
