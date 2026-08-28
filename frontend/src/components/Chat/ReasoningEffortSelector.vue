<script lang="ts" setup>
import type { ChatModelOption } from '@/api/models'
import { h } from 'vue'
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

const dropdownOptions = computed(() => {
  if (declaredLevels.value === null) {
    return []
  }
  return [
    { label: '自动', key: '' },
    ...declaredLevels.value.map((level) => {
      return { label: reasoningLevelLabel(level), key: level }
    }),
  ]
})

function renderDropdownLabel(option: { label?: string | number, key?: string | number }) {
  const label = String(option.label ?? '')
  const active = String(option.key) === modelValue.value
  return h('span', { class: 'composer-model-dropdown__item' }, [
    h('span', { class: 'composer-model-dropdown__label' }, label),
    active ? h('span', { class: 'i-carbon:checkmark composer-model-dropdown__check' }) : null,
  ])
}

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

async function onSelect(key: string) {
  modelValue.value = key
  await persistEffort(key)
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
  <n-dropdown
    v-if="declaredLevels !== null"
    trigger="click"
    placement="top-start"
    :options="dropdownOptions"
    :render-label="renderDropdownLabel"
    :disabled="disabled || dropdownOptions.length === 0"
    @select="onSelect"
  >
    <button
      type="button"
      class="composer-model-trigger"
      :class="{ 'composer-model-trigger--menu': embedded }"
      :disabled="disabled || dropdownOptions.length === 0"
    >
      <span v-if="embedded" class="i-carbon:ideas composer-model-trigger__icon"></span>
      <span v-if="embedded" class="composer-model-trigger__title">思考</span>
      <span class="composer-model-trigger__label">{{ currentLabel }}</span>
      <span class="i-carbon:chevron-down text-12 opacity-60"></span>
    </button>
  </n-dropdown>
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
</style>
