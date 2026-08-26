<script setup lang="ts">
import type { ContextPreview } from '@/api/settings'
import { NButton, NInput, NSelect, NTag } from 'naive-ui'
import { computed, onMounted, ref, watch } from 'vue'
import { getContextPreview, getUserMemoryFile, putUserMemoryFile } from '@/api/settings'
import FilePreview from '@/components/FilePreview/index.vue'
import MarkdownInstance from '@/components/MarkdownPreview/plugins/markdown'
import { useMermaidRender } from '@/hooks/useMermaidRender'

const props = defineProps<{
  file: 'USER.md' | 'AGENTS.md'
  title: string
  description: string
}>()

const emit = defineEmits<{
  (e: 'dirtyChange', dirty: boolean): void
}>()

const content = ref('')
const draft = ref('')
const updatedAt = ref<string>()
const saving = ref(false)
const loading = ref(false)
const editing = ref(false)
const preview = ref<ContextPreview>()
const previewProfile = ref('super_agent')

const dirty = computed(() => editing.value && draft.value !== content.value)
const formattedUpdatedAt = computed(() => {
  if (!updatedAt.value) {
    return undefined
  }
  const value = new Date(updatedAt.value)
  return Number.isNaN(value.getTime()) ? updatedAt.value : value.toLocaleString()
})
const profileOptions = [
  { label: '超级 Agent', value: 'super_agent' },
  { label: '通用问答', value: 'common_qa' },
  { label: '故障运维', value: 'fault_operation' },
]

async function load() {
  loading.value = true
  try {
    const data = await getUserMemoryFile(props.file)
    content.value = data?.content ?? ''
    draft.value = content.value
    updatedAt.value = data?.updated_at
    editing.value = !content.value.trim()
  } finally {
    loading.value = false
  }
}

async function loadContextData() {
  if (props.file !== 'AGENTS.md') {
    return
  }
  preview.value = await getContextPreview(previewProfile.value).catch(() => undefined)
}

async function refreshPreview() {
  preview.value = await getContextPreview(previewProfile.value).catch(() => undefined)
}

const compiledMarkdown = computed(() => {
  const source = preview.value?.compiled_content || ''
  return source ? MarkdownInstance.render(source) : ''
})
const previewRef = ref<HTMLElement | null>(null)
useMermaidRender(previewRef, compiledMarkdown, computed(() => !!compiledMarkdown.value))

function startEdit() {
  draft.value = content.value
  editing.value = true
}

function cancelEdit() {
  draft.value = content.value
  editing.value = false
}

async function save() {
  saving.value = true
  try {
    const data = await putUserMemoryFile(props.file, draft.value)
    content.value = draft.value
    updatedAt.value = data?.updated_at
    editing.value = false
    await loadContextData()
  } finally {
    saving.value = false
  }
}

watch(() => props.file, () => {
  void load()
  void loadContextData()
})
watch(dirty, (value) => emit('dirtyChange', value), { immediate: true })

onMounted(() => {
  void load()
  void loadContextData()
})
</script>

<template>
  <section class="pane">
    <h2>{{ title }}</h2>
    <p class="hint">
      {{ description }}
    </p>
    <p v-if="formattedUpdatedAt" class="meta">
      最近修改：{{ formattedUpdatedAt }}
    </p>

    <div class="editor-area" :class="{ 'editor-area--preview': !editing }">
      <n-input
        v-if="editing"
        v-model:value="draft"
        type="textarea"
        :autosize="{ minRows: 16, maxRows: 40 }"
        :disabled="loading || saving"
        placeholder="使用 Markdown 编写…"
      />
      <FilePreview
        v-else
        :path="file"
        :content="content"
        :loading="loading"
        :show-path="false"
        :show-toolbar="false"
        density="comfortable"
      />
    </div>

    <div class="pane-footer">
      <template v-if="editing">
        <n-button :disabled="saving" @click="cancelEdit">
          取消
        </n-button>
        <n-button type="primary" :loading="saving" @click="save">
          保存
        </n-button>
      </template>
      <n-button v-else type="primary" ghost :disabled="loading" @click="startEdit">
        编辑
      </n-button>
    </div>

    <template v-if="file === 'AGENTS.md'">
      <section class="context-panel">
        <h3>最终上下文预览</h3>
        <div class="inline-actions">
          <n-select v-model:value="previewProfile" :options="profileOptions" @update:value="refreshPreview" />
          <n-tag v-if="preview">
            约 {{ preview.token_estimate }} tokens
          </n-tag>
        </div>
        <div v-if="preview" ref="previewRef" class="compiled-preview markdown-body" v-html="compiledMarkdown"></div>
      </section>
    </template>
  </section>
</template>

<style scoped lang="scss">
.pane { padding: 8px 0 24px; }
.pane h2 { margin: 0 0 8px; font-size: 20px; }
.pane h3 { margin: 0 0 8px; }
.hint { color: var(--noesis-color-text-secondary); line-height: 1.6; }
.meta { color: var(--noesis-color-text-tertiary); font-size: 12px; }
.editor-area { max-width: 720px; margin-top: 16px; }
.editor-area--preview { min-height: 180px; }
.pane-footer { display: flex; justify-content: flex-end; gap: 8px; max-width: 720px; margin-top: 12px; }
.context-panel { max-width: 720px; margin-top: 28px; }
.inline-actions { display: flex; align-items: center; gap: 8px; margin-bottom: 10px; }
.inline-actions > :first-child { flex: 1; }
.compiled-preview { max-height: 480px; overflow: auto; padding: 16px; border: 1px solid var(--noesis-color-border-subtle); border-radius: var(--noesis-radius-md); }
</style>
