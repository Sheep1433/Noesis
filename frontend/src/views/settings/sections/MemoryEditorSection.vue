<script setup lang="ts">
import type { ContextPreview, MemoryTreeEntry, MemoryTreePayload } from '@/api/settings'
import { NButton, NInput, NSelect, NSwitch, NTag, useDialog, useMessage } from 'naive-ui'
import { computed, onMounted, ref, watch } from 'vue'
import {
  deleteMemoryEntry,
  getContextPreview,
  getMemoryEntry,
  getMemorySettings,
  getMemoryTree,
  getUserMemoryFile,
  putMemoryEntry,
  putMemorySettings,
  putUserMemoryFile,
} from '@/api/settings'
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

const message = useMessage()
const dialog = useDialog()
const content = ref('')
const draft = ref('')
const updatedAt = ref<string>()
const saving = ref(false)
const loading = ref(false)
const editing = ref(false)
const preview = ref<ContextPreview>()
const previewProfile = ref('super_agent')

// 记忆层（md-memory-layer）：开关 + 条目管理 + journal
const memoryEnabled = ref(false)
const memoryToggleSaving = ref(false)
const tree = ref<MemoryTreePayload>()
const treeLoading = ref(false)
const activeEntry = ref<MemoryTreeEntry>()
const entryContent = ref('')
const entryDraft = ref('')
const entryEditing = ref(false)
const entrySaving = ref(false)

const dirty = computed(() => editing.value && draft.value !== content.value)
const entryDirty = computed(() => entryEditing.value && entryDraft.value !== entryContent.value)
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
const groupedEntries = computed(() => {
  const groups: Array<{ type: string, label: string, entries: MemoryTreeEntry[] }> = []
  for (const entry of tree.value?.entries ?? []) {
    let group = groups.find((g) => g.type === entry.memory_type)
    if (!group) {
      group = { type: entry.memory_type, label: entry.type_label, entries: [] }
      groups.push(group)
    }
    group.entries.push(entry)
  }
  return groups
})

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

async function loadMemoryLayer() {
  if (props.file !== 'AGENTS.md') {
    return
  }
  try {
    const [settings, treeData] = await Promise.all([getMemorySettings(), getMemoryTree()])
    memoryEnabled.value = settings.enabled
    tree.value = treeData
  } catch {
    memoryEnabled.value = false
    tree.value = undefined
  }
}

async function toggleMemory(enabled: boolean) {
  memoryToggleSaving.value = true
  try {
    const data = await putMemorySettings(enabled)
    memoryEnabled.value = data.enabled
    message.success(enabled ? '记忆已开启' : '记忆已关闭')
  } finally {
    memoryToggleSaving.value = false
  }
}

async function openEntry(entry: MemoryTreeEntry) {
  activeEntry.value = entry
  entryEditing.value = false
  try {
    const data = await getMemoryEntry(entry.memory_type, entry.slug)
    entryContent.value = data?.content ?? ''
    entryDraft.value = entryContent.value
  } catch {
    entryContent.value = ''
    entryDraft.value = ''
  }
}

async function saveEntry() {
  if (!activeEntry.value) {
    return
  }
  entrySaving.value = true
  try {
    await putMemoryEntry(activeEntry.value.memory_type, activeEntry.value.slug, entryDraft.value)
    entryContent.value = entryDraft.value
    entryEditing.value = false
    message.success('条目已保存')
    tree.value = await getMemoryTree()
  } finally {
    entrySaving.value = false
  }
}

function removeEntry(entry: MemoryTreeEntry) {
  dialog.warning({
    title: '删除记忆条目',
    content: `删除「${entry.label}」后索引行同步移除；情景日志中的原始记录会保留。`,
    positiveText: '删除',
    negativeText: '取消',
    onPositiveClick: async () => {
      await deleteMemoryEntry(entry.memory_type, entry.slug)
      if (activeEntry.value?.slug === entry.slug) {
        activeEntry.value = undefined
        entryContent.value = ''
      }
      tree.value = await getMemoryTree()
      message.success('已删除')
    },
  })
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
  void loadMemoryLayer()
})
watch([dirty, entryDirty], ([a, b]) => emit('dirtyChange', a || b), { immediate: true })

onMounted(() => {
  void load()
  void loadContextData()
  void loadMemoryLayer()
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
      <section class="context-panel preference-row">
        <div>
          <h3>记忆</h3>
          <p class="hint">
            开启后，会话结束后自动整理记忆并在新对话中按需引用；关闭后停止整理和引用，已有记忆文件保留可编辑。
          </p>
        </div>
        <n-switch
          :value="memoryEnabled"
          :loading="memoryToggleSaving"
          @update:value="toggleMemory"
        />
      </section>

      <section class="context-panel">
        <h3>已记住的内容</h3>
        <p class="hint">
          一条记忆一个文件，按类型分组；点击查看与编辑。
        </p>
        <p v-if="treeLoading" class="hint">正在加载…</p>
        <template v-else-if="groupedEntries.length">
          <div v-for="group in groupedEntries" :key="group.type" class="memory-group">
            <h4>{{ group.label }}</h4>
            <div
              v-for="entry in group.entries"
              :key="entry.rel_path"
              class="memory-item"
              :class="{ 'memory-item--active': activeEntry?.rel_path === entry.rel_path }"
              @click="openEntry(entry)"
            >
              <div>
                <strong>{{ entry.label }}</strong>
                <p class="hint">{{ entry.description }}</p>
              </div>
              <n-button size="tiny" type="error" ghost @click.stop="removeEntry(entry)">
                删除
              </n-button>
            </div>
          </div>
        </template>
        <p v-else class="hint">还没有长期记忆；开启记忆后，有价值的会话会自动整理出条目。</p>

        <div v-if="activeEntry" class="entry-editor">
          <div class="entry-editor-head">
            <strong>{{ activeEntry.label }}</strong>
            <n-tag size="small">{{ activeEntry.type_label }}</n-tag>
          </div>
          <n-input
            v-if="entryEditing"
            v-model:value="entryDraft"
            type="textarea"
            :autosize="{ minRows: 8, maxRows: 24 }"
            :disabled="entrySaving"
          />
          <FilePreview
            v-else
            :path="activeEntry.rel_path"
            :content="entryContent"
            :show-path="false"
            :show-toolbar="false"
            density="comfortable"
          />
          <div class="pane-footer">
            <template v-if="entryEditing">
              <n-button :disabled="entrySaving" @click="entryEditing = false; entryDraft = entryContent">
                取消
              </n-button>
              <n-button type="primary" :loading="entrySaving" @click="saveEntry">
                保存
              </n-button>
            </template>
            <n-button v-else type="primary" ghost @click="entryEditing = true; entryDraft = entryContent">
              编辑
            </n-button>
          </div>
        </div>
      </section>

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
.pane h4 { margin: 12px 0 6px; }
.hint { color: var(--noesis-color-text-secondary); line-height: 1.6; }
.meta { color: var(--noesis-color-text-tertiary); font-size: 12px; }
.editor-area { max-width: 720px; margin-top: 16px; }
.editor-area--preview { min-height: 180px; }
.pane-footer { display: flex; justify-content: flex-end; gap: 8px; max-width: 720px; margin-top: 12px; }
.context-panel { max-width: 720px; margin-top: 28px; }
.preference-row { display: flex; align-items: center; justify-content: space-between; gap: 20px; padding: 12px; border: 1px solid var(--noesis-color-border-subtle); border-radius: var(--noesis-radius-md); }
.preference-row .hint { margin: 0; }
.inline-actions { display: flex; align-items: center; gap: 8px; margin-bottom: 10px; }
.inline-actions > :first-child { flex: 1; }
.memory-group { margin-top: 8px; }
.memory-item { display: flex; align-items: center; justify-content: space-between; gap: 12px; padding: 10px 12px; border: 1px solid var(--noesis-color-border-subtle); border-radius: var(--noesis-radius-md); margin-bottom: 6px; cursor: pointer; }
.memory-item:hover { background: var(--noesis-color-bg-surface); }
.memory-item--active { border-color: var(--noesis-color-primary); }
.memory-item .hint { margin: 2px 0 0; font-size: 12px; }
.entry-editor { margin-top: 16px; padding: 12px; border: 1px solid var(--noesis-color-border-subtle); border-radius: var(--noesis-radius-md); }
.entry-editor-head { display: flex; align-items: center; gap: 8px; margin-bottom: 8px; }
.compiled-preview { max-height: 480px; overflow: auto; padding: 16px; border: 1px solid var(--noesis-color-border-subtle); border-radius: var(--noesis-radius-md); }
</style>
