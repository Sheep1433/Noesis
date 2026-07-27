<script setup lang="ts">
import type { ContextPreview, DailyMemoryItem, DailyMemoryMatch } from '@/api/settings'
import { NButton, NDatePicker, NInput, NSelect, NTag, useMessage } from 'naive-ui'
import { computed, onMounted, ref, watch } from 'vue'
import { getContextPreview, getUserMemoryFile, listDailyMemory, putUserMemoryFile, runMemoryDream, searchDailyMemory } from '@/api/settings'
import FilePreview from '@/components/FilePreview/index.vue'

const props = defineProps<{
  file: 'USER.md' | 'AGENTS.md'
  title: string
  description: string
}>()

const emit = defineEmits<{
  (e: 'dirtyChange', dirty: boolean): void
}>()

const message = useMessage()
const content = ref('')
const draft = ref('')
const updatedAt = ref<string | undefined>()
const saving = ref(false)
const loading = ref(false)
const editing = ref(false)
const dirty = computed(() => editing.value && draft.value !== content.value)
const dailyItems = ref<DailyMemoryItem[]>([])
const dailyMatches = ref<DailyMemoryMatch[]>([])
const dailyQuery = ref('')
const dreamDate = ref(Date.now())
const dreaming = ref(false)
const preview = ref<ContextPreview>()
const previewProfile = ref('super_agent')
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
  } catch (e) {
    message.error(e instanceof Error ? e.message : '加载失败')
  } finally {
    loading.value = false
  }
}

async function loadContextData() {
  if (props.file !== 'AGENTS.md') {
    return
  }
  try {
    dailyItems.value = await listDailyMemory()
    preview.value = await getContextPreview(previewProfile.value)
  } catch (e) {
    message.error(e instanceof Error ? e.message : '上下文信息加载失败')
  }
}

async function runDailySearch() {
  if (!dailyQuery.value.trim()) {
    return
  }
  dailyMatches.value = await searchDailyMemory(dailyQuery.value)
}

async function dream() {
  dreaming.value = true
  try {
    const date = new Date(dreamDate.value).toLocaleDateString('sv-SE')
    const result = await runMemoryDream(date)
    message.success(`已整理 ${result.entries} 条记忆`)
    await loadContextData()
  } catch (e) {
    message.error(e instanceof Error ? e.message : '记忆整理失败')
  } finally {
    dreaming.value = false
  }
}

async function refreshPreview() {
  preview.value = await getContextPreview(previewProfile.value)
}

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
    message.success('已保存')
    await loadContextData()
  } catch (e) {
    message.error(e instanceof Error ? e.message : '保存失败')
  } finally {
    saving.value = false
  }
}

watch(() => props.file, () => {
  void load()
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
    <p v-if="updatedAt" class="meta">
      最近修改：{{ updatedAt }}
    </p>

    <div class="editor-area">
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

    <template v-if="file === 'AGENTS.md'">
      <section class="context-panel">
        <h3>跨任务记忆</h3>
        <div class="inline-actions"><n-date-picker v-model:value="dreamDate" type="date" clearable :actions="null" /><n-button type="primary" :loading="dreaming" @click="dream">整理记忆</n-button></div>
        <div class="inline-actions"><n-input v-model:value="dailyQuery" placeholder="搜索日记内容" @keyup.enter="runDailySearch" /><n-button @click="runDailySearch">搜索</n-button></div>
        <p class="hint">已整理 {{ dailyItems.length }} 天。记忆仅在需要时检索，不会默认加入每次对话。</p>
        <div v-for="match in dailyMatches" :key="match.id" class="result"><strong>{{ match.date }} · {{ match.category }} · 匹配度 {{ match.score }}</strong><span>{{ match.summary }}</span><small v-if="match.sources?.[0]">来源：{{ match.sources[0].session_id }} / {{ match.sources[0].message_id }}</small></div>
      </section>
      <section class="context-panel">
        <h3>最终上下文预览</h3>
        <div class="inline-actions"><n-select v-model:value="previewProfile" :options="profileOptions" @update:value="refreshPreview" /><n-tag v-if="preview">约 {{ preview.token_estimate }} tokens</n-tag></div>
        <div v-for="source in preview?.sources" :key="source.id" class="source-row"><strong>{{ source.label }}</strong><span>优先级 {{ source.priority }} · {{ source.injected ? '已加入' : '未加入' }} · {{ source.characters }} 字符</span></div>
        <n-input :value="preview?.compiled_content || ''" type="textarea" readonly :autosize="{ minRows: 12, maxRows: 28 }" />
      </section>
    </template>

    <div class="pane-footer">
      <template v-if="editing">
        <n-button :disabled="saving" @click="cancelEdit">
          取消
        </n-button>
        <n-button type="primary" :loading="saving" @click="save">
          保存
        </n-button>
      </template>
      <n-button
        v-else
        type="primary"
        ghost
        :disabled="loading"
        @click="startEdit"
      >
        编辑
      </n-button>
    </div>
  </section>
</template>

<style scoped>
.pane h2 {
  margin: 0 0 8px;
  font-size: 18px;
  color: var(--noesis-color-text-heading);
}

.hint,
.meta {
  margin: 0 0 12px;
  color: var(--noesis-color-text-secondary);
  font-size: 13px;
}

.editor-area {
  max-width: 720px;
}

.context-panel { max-width: 720px; margin-top: 28px; }
.context-panel h3 { margin-bottom: 10px; }
.inline-actions { display: flex; align-items: center; gap: 8px; margin-bottom: 10px; }
.inline-actions > :first-child { flex: 1; }
.result, .source-row { display: flex; flex-direction: column; gap: 2px; padding: 8px 0; border-bottom: 1px solid var(--noesis-color-border-subtle, rgba(0,0,0,.08)); font-size: 13px; }

.pane-footer {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  margin-top: 12px;
  max-width: 720px;
}
</style>
