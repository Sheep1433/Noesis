<script lang="ts" setup>
import type { ChatModelOption } from '@/api/models'
import { h } from 'vue'
import { useRouter } from 'vue-router'
import { ensureSession } from '@/api/chat'
import { getChatModels } from '@/api/models'

const props = defineProps<{
  sessionId: string
  disabled?: boolean
  /** ACTIVE 会话才写回 session.extra；COMPOSING 仅改本地 modelValue */
  persistSessionExtra?: boolean
}>()

const MANAGE_KEY = '__manage_models__'

const router = useRouter()

const modelValue = defineModel<string>({ default: '' })

const loading = ref(false)
const options = ref<ChatModelOption[]>([])

const optionsById = computed(() => new Map(options.value.map((item) => [item.id, item])))

const currentModel = computed(() => optionsById.value.get(modelValue.value))

/** 按 provider 分组：不知用的是谁家的模型就是事故 */
const groupedOptions = computed(() => {
  const groups = new Map<string, ChatModelOption[]>()
  for (const item of options.value) {
    const provider = item.provider || '其他'
    if (!groups.has(provider)) {
      groups.set(provider, [])
    }
    groups.get(provider)!.push(item)
  }
  return [...groups.entries()].map(([provider, items]) => ({ provider, items }))
})

/** 两级级联：一级 = 提供商（hover 展开子菜单），二级 = 模型；末尾「管理模型」跳设置页 */
const dropdownOptions = computed(() => [
  ...groupedOptions.value.map(({ provider, items }) => ({
    label: provider,
    key: `provider-${provider}`,
    children: items.map((item) => ({ label: item.label, key: item.id })),
  })),
  { type: 'divider' as const, key: 'manage-divider' },
  { label: '管理模型', key: MANAGE_KEY },
])

interface DropdownRawNode {
  label?: string | number
  key?: string | number
  children?: unknown[]
}

/** n-dropdown 官方 render-label：提供商行渲染「名称 + 当前勾选」，模型行渲染「名称 + 视觉标签 + 激活勾选」 */
function renderDropdownLabel(option: DropdownRawNode) {
  const key = String(option.key)
  const label = String(option.label ?? '')
  if (Array.isArray(option.children)) {
    const activeProvider = currentModel.value?.provider || '其他'
    return h('span', { class: 'composer-model-dropdown__item' }, [
      h('span', { class: 'composer-model-dropdown__label' }, label),
      activeProvider === label
        ? h('span', { class: 'i-carbon:checkmark composer-model-dropdown__check' })
        : null,
    ])
  }
  const item = optionsById.value.get(key)
  return h('span', { class: 'composer-model-dropdown__item' }, [
    h('span', { class: 'composer-model-dropdown__label' }, label),
    item?.supports_vision
      ? h('span', { class: 'composer-model-dropdown__tag' }, '视觉')
      : null,
    key === modelValue.value
      ? h('span', { class: 'i-carbon:checkmark composer-model-dropdown__check' })
      : null,
  ])
}

/** 触发按钮显示「提供商/模型」，与级联菜单的归属语义一致 */
const currentLabel = computed(() => {
  const hit = currentModel.value
  if (hit) {
    return hit.provider ? `${hit.provider}/${hit.label}` : hit.label
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
    } else if (!options.value.some((item) => item.id === modelValue.value)) {
      modelValue.value = catalog.default_id
    }
  } catch (e) {
    options.value = []
    console.warn('加载模型列表失败', e)
  } finally {
    loading.value = false
  }
}

async function persistModel(modelId: string) {
  if (!props.persistSessionExtra || !props.sessionId || !modelId) {
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

function onSelect(key: string | number) {
  if (key === MANAGE_KEY) {
    void router.push({ name: 'Settings', query: { s: 'models' } })
    return
  }
  const modelId = String(key)
  modelValue.value = modelId
  void persistModel(modelId)
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
  <!-- 桌面与移动端同一下拉：提供商 → 模型两级级联，触发按钮显示「提供商/模型」 -->
  <n-dropdown
    trigger="click"
    placement="top-start"
    :options="dropdownOptions"
    :render-label="renderDropdownLabel"
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

/* 移动端（≤768px，同 useBreakpoint md）：长模型名限幅截断，避免挤压右侧控件 */
@media (max-width: 768px) {
  .composer-model-trigger {
    max-width: 36vw;
  }
}
</style>

<style>
/* n-dropdown 内容渲染在 teleport 层，需全局样式 */
.composer-model-dropdown__item {
  display: inline-flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  min-width: 180px;
}
.composer-model-dropdown__label {
  flex: 1;
}
.composer-model-dropdown__tag {
  flex-shrink: 0;
  padding: 1px 6px;
  border-radius: 4px;
  background: var(--noesis-color-bg-muted, rgb(0 0 0 / 6%));
  color: var(--noesis-text-secondary, #6b7280);
  font-size: 11px;
  line-height: 1.4;
}
.composer-model-dropdown__check {
  flex-shrink: 0;
  color: var(--noesis-color-primary, #111);
  font-size: 14px;
}
</style>
