<script setup lang="ts">
import type { DiscoveredModelRow } from '@/api/models'
import { NButton, NTag } from 'naive-ui'
import { computed, ref, watch } from 'vue'

/**
 * 发现列表共享面板：勾选多选 + 批量「添加所选」（平台组与自定义表单同一交互）。
 * 「只看免费」chip 仅在发现结果含免费模型时展示（默认激活）：免费 = id 含
 * -free / :free 片段，或 Provider 行原始字段标记免费（如 kilo 的 isFree）
 * ——通用约定，非平台特判；无免费模型的 Provider（如 deepseek）不显示 chip。
 */
const props = withDefaults(defineProps<{
  models: DiscoveredModelRow[]
  /** 已存在于目标 Provider / 草稿目录的 model_id：置灰并标「已添加」 */
  existingIds?: Set<string>
  /** 批量动作进行中 */
  adopting?: boolean
  /** 批量动作文案 */
  adoptLabel?: string
}>(), {
  existingIds: () => new Set<string>(),
  adopting: false,
  adoptLabel: '添加所选',
})

const emit = defineEmits<{
  (e: 'adopt', models: DiscoveredModelRow[]): void
}>()

/** 只看免费；关闭则平铺全部。初始随发现结果：含免费模型才默认开 */
const freeOnly = ref(false)
/** 勾选的 model_id 集（以组件外传入的 models 为准，含被筛选暂时隐藏的行） */
const picked = ref(new Set<string>())

function isFree(row: DiscoveredModelRow): boolean {
  return row.model_id.includes('-free')
    || row.model_id.includes(':free')
    || row.flags?.isFree === true
}

/** 结果含免费模型才展示筛选 chip（无免费模型的 Provider 平铺全部） */
const hasFree = computed(() => props.models.some(isFree))

const visibleModels = computed(() =>
  freeOnly.value ? props.models.filter(isFree) : props.models)

// 模型列表刷新（重新发现）时清空勾选并重算免费筛选默认值
watch(() => props.models, (models) => {
  picked.value = new Set()
  freeOnly.value = models.some(isFree)
}, { immediate: true })

function togglePick(modelId: string) {
  const next = new Set(picked.value)
  if (next.has(modelId)) {
    next.delete(modelId)
  } else {
    next.add(modelId)
  }
  picked.value = next
}

const pickedModels = computed(() => props.models.filter((m) => picked.value.has(m.model_id)))

function adoptPicked() {
  if (!pickedModels.value.length) {
    return
  }
  emit('adopt', pickedModels.value)
  picked.value = new Set()
}
</script>

<template>
  <div class="discovery-panel">
    <div class="discovery-toolbar">
      <div class="toolbar-left">
        <n-tag
          v-if="hasFree"
          size="small" checkable
          :checked="freeOnly"
          @update:checked="(checked: boolean) => freeOnly = checked"
        >
          只看免费
        </n-tag>
        <span class="muted">已选 {{ picked.size }} 项</span>
      </div>
      <n-button
        size="tiny" type="primary" :disabled="!picked.size" :loading="adopting"
        @click="adoptPicked"
      >
        {{ adoptLabel }}
      </n-button>
    </div>
    <div v-if="visibleModels.length" class="discovery-list">
      <div
        v-for="discovered in visibleModels" :key="discovered.model_id"
        class="discovery-row pick" :class="{ picked: picked.has(discovered.model_id) }"
        @click="existingIds.has(discovered.model_id) ? null : togglePick(discovered.model_id)"
      >
        <div class="discovery-model">
          <input
            type="checkbox" class="pick-box"
            :checked="picked.has(discovered.model_id)"
            :disabled="existingIds.has(discovered.model_id)"
            @click.stop="togglePick(discovered.model_id)"
          >
          <strong>{{ discovered.label }}</strong>
          <span class="muted">{{ discovered.model_id }}</span>
          <span class="muted">{{ discovered.context_window ? `${discovered.context_window} tokens` : '窗口未知' }}</span>
        </div>
        <n-tag v-if="existingIds.has(discovered.model_id)" size="small" type="success" :bordered="false">已添加</n-tag>
      </div>
    </div>
    <div v-else class="muted empty-hint">当前筛选条件下没有模型</div>
  </div>
</template>

<style scoped>
.muted { color: var(--noesis-color-text-secondary); font-size: 12px; }
.discovery-panel { margin-top: 4px; padding: 10px 12px; border-radius: 8px; background: var(--noesis-color-fill-subtle, rgba(0,0,0,.03)); }
.discovery-toolbar { display: flex; align-items: center; justify-content: space-between; gap: 8px; margin-bottom: 8px; }
.toolbar-left { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; min-width: 0; }
.discovery-list { display: grid; gap: 6px; margin-top: 8px; }
.discovery-row { display: flex; align-items: center; justify-content: space-between; gap: 12px; padding: 6px 0; border-top: 1px solid var(--noesis-color-border-subtle, rgba(0,0,0,.06)); }
.discovery-row.pick { cursor: pointer; border-radius: 6px; padding: 6px 6px; }
.discovery-row.pick.picked { background: var(--noesis-color-primary-bg-subtle); }
.discovery-model { display: flex; align-items: baseline; gap: 10px; min-width: 0; flex-wrap: wrap; }
.pick-box { accent-color: var(--noesis-color-primary); width: 14px; height: 14px; flex-shrink: 0; }
.empty-hint { margin-top: 8px; }
</style>
