<script lang="ts" setup>
import { ensureSession } from '@/api/chat'
import { getCollections } from '@/api/knowledgeBase'

const props = defineProps<{
  sessionId: string
  disabled?: boolean
}>()

const modelValue = defineModel<string[]>({ default: () => [] })

const loading = ref(false)
const loadError = ref<string | null>(null)
const options = ref<Array<{ label: string, value: string, disabled?: boolean }>>([])

const emptyHint = computed(() => {
  if (loading.value) {
    return ''
  }
  if (loadError.value) {
    return loadError.value
  }
  if (options.value.length === 0) {
    return '暂无知识库，请先在「知识库管理」中创建并上传文档'
  }
  if (options.value.every((o) => o.disabled)) {
    return '已有知识库但尚无已入库文档，上传文档后可在此限定检索范围'
  }
  return ''
})

async function loadCollections() {
  loading.value = true
  loadError.value = null
  try {
    const cols = await getCollections()
    options.value = cols.map((c) => {
      const searchable = (c.points_count ?? 0) > 0
      return {
        value: c.name,
        label: searchable
          ? `${c.name}（${c.documents_count ?? 0} 篇）`
          : `${c.name}（暂无文档，不可检索）`,
        disabled: !searchable,
      }
    })
  } catch (e) {
    options.value = []
    loadError.value = e instanceof Error ? e.message : '加载知识库列表失败'
  } finally {
    loading.value = false
  }
}

async function persistScope(names: string[]) {
  if (!props.sessionId) {
    return
  }
  try {
    await ensureSession(props.sessionId, {
      extra: { kb_collections: names },
    })
  } catch (e) {
    console.warn('保存知识库范围失败', e)
  }
}

async function onUpdate(value: string[]) {
  modelValue.value = value
  await persistScope(value)
}

onMounted(() => {
  void loadCollections()
})

watch(
  () => props.sessionId,
  () => {
    void loadCollections()
  },
)
</script>

<template>
  <div class="kb-scope-selector flex flex-col gap-4 min-w-0">
    <div class="flex items-center gap-6 min-w-0">
      <span class="kb-scope-label shrink-0 text-12 opacity-70">知识库</span>
      <n-select
        :value="modelValue"
        :options="options"
        :loading="loading"
        :disabled="disabled || !sessionId"
        multiple
        clearable
        filterable
        size="small"
        class="kb-scope-select flex-1 min-w-0"
        placeholder="不选则检索全部可用库"
        max-tag-count="responsive"
        @update:value="onUpdate"
      />
    </div>
    <p
      v-if="emptyHint"
      class="kb-scope-hint m-0 text-12 opacity-70"
    >
      {{ emptyHint }}
    </p>
  </div>
</template>

<style scoped>
.kb-scope-select {
  max-width: 320px;
}

.kb-scope-label,
.kb-scope-hint {
  color: var(--noesis-text-secondary, #6b7280);
}
</style>
