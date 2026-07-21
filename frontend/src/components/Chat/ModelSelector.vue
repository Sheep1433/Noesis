<script lang="ts" setup>
import type { ChatModelOption } from '@/api/models'
import { ensureSession } from '@/api/chat'
import { getChatModels } from '@/api/models'

const props = defineProps<{
  sessionId: string
  disabled?: boolean
}>()

const modelValue = defineModel<string>({ default: '' })

const loading = ref(false)
const options = ref<ChatModelOption[]>([])

const dropdownOptions = computed(() =>
  options.value.map((item) => ({
    label: item.label,
    key: item.id,
  })),
)

const currentLabel = computed(() => {
  const hit = options.value.find((item) => item.id === modelValue.value)
  if (hit) {
    return hit.label
  }
  if (loading.value) {
    return '加载中…'
  }
  return '选择模型'
})

async function loadModels() {
  loading.value = true
  try {
    const catalog = await getChatModels()
    options.value = catalog.models ?? []
    if (!modelValue.value) {
      modelValue.value = catalog.default_id
      await persistModel(catalog.default_id)
    } else if (!options.value.some((item) => item.id === modelValue.value)) {
      modelValue.value = catalog.default_id
      await persistModel(catalog.default_id)
    }
  } catch (e) {
    options.value = []
    console.warn('加载模型列表失败', e)
  } finally {
    loading.value = false
  }
}

async function persistModel(modelId: string) {
  if (!props.sessionId || !modelId) {
    return
  }
  try {
    await ensureSession(props.sessionId, {
      extra: { model_id: modelId },
    })
  } catch (e) {
    console.warn('保存模型选择失败', e)
  }
}

async function onSelect(key: string) {
  modelValue.value = key
  await persistModel(key)
}

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
    trigger="click"
    placement="top-start"
    :options="dropdownOptions"
    :disabled="disabled || loading || dropdownOptions.length === 0"
    @select="onSelect"
  >
    <button
      type="button"
      class="composer-model-trigger"
      :disabled="disabled || loading || dropdownOptions.length === 0"
    >
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
  max-width: 220px;
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
</style>
