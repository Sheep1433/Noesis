<script setup lang="ts">
import type { ContextPreview, CortexMemoryPreference, MachineMemoryHealth, MachineMemoryItem, MachineMemorySource, MachineMemoryStatus, MachineMemoryType } from '@/api/settings'
import { NButton, NInput, NSelect, NSwitch, NTag, useDialog, useMessage } from 'naive-ui'
import { computed, onMounted, ref, watch } from 'vue'
import {
  changeMachineMemoryState,
  deleteMachineMemory,
  getContextPreview,
  getCortexMemoryPreference,
  getMachineMemoryHealth,
  getMachineMemorySource,
  getUserMemoryFile,
  listMachineMemory,
  putUserMemoryFile,
  reviseMachineMemory,
  updateCortexMemoryPreference,
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
const preference = ref<CortexMemoryPreference>()
const preferenceSaving = ref(false)
const memories = ref<MachineMemoryItem[]>([])
const health = ref<MachineMemoryHealth>()
const memoryLoading = ref(false)
const memoryStatus = ref<MachineMemoryStatus>()
const memoryType = ref<MachineMemoryType>()
const memoryScope = ref<string>()
const memoryQuery = ref('')
const knownMemoryScopes = ref<Record<string, string>>({})
const memoryDrafts = ref<Record<string, { statement: string, applicability: string }>>({})
const memorySources = ref<Record<string, MachineMemorySource>>({})
const memoryActionId = ref('')

const statusOptions = [
  { label: '全部状态', value: '' },
  { label: '可使用', value: 'active' },
  { label: '待确认', value: 'candidate' },
  { label: '存在冲突', value: 'needs_review' },
  { label: '已停用', value: 'disabled' },
  { label: '已失效', value: 'invalidated' },
  { label: '历史版本', value: 'superseded' },
]
const typeOptions = [
  { label: '全部类型', value: '' },
  { label: '决策', value: 'decision' },
  { label: '任务经验', value: 'experience' },
  { label: '工作步骤', value: 'workflow' },
  { label: '注意事项', value: 'gotcha' },
]
const scopeOptions = computed(() => [
  { label: '全部范围', value: '' },
  ...Object.entries(knownMemoryScopes.value).map(([value, label]) => ({ label, value })),
])
const statusLabels: Record<MachineMemoryStatus, string> = {
  active: '可使用',
  candidate: '待确认',
  needs_review: '存在冲突',
  disabled: '已停用',
  invalidated: '已失效',
  superseded: '历史版本',
}
const typeLabels: Record<MachineMemoryType, string> = {
  decision: '决策',
  experience: '任务经验',
  workflow: '工作步骤',
  gotcha: '注意事项',
}

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
  } catch (error) {
    message.error(error instanceof Error ? error.message : '加载失败')
  } finally {
    loading.value = false
  }
}

async function loadContextData() {
  if (props.file !== 'AGENTS.md') {
    return
  }
  try {
    [preference.value, preview.value, health.value] = await Promise.all([
      getCortexMemoryPreference(),
      getContextPreview(previewProfile.value),
      getMachineMemoryHealth(),
    ])
    await loadMemories()
  } catch (error) {
    message.error(error instanceof Error ? error.message : '上下文信息加载失败')
  }
}

async function loadMemories() {
  memoryLoading.value = true
  try {
    memories.value = await listMachineMemory(
      memoryStatus.value,
      memoryType.value,
      memoryScope.value,
      memoryQuery.value,
    )
    knownMemoryScopes.value = {
      ...knownMemoryScopes.value,
      ...Object.fromEntries(memories.value.map((item) => [item.scope_id, item.scope_label])),
    }
    memoryDrafts.value = Object.fromEntries(memories.value.map((item) => [
      item.id,
      { statement: item.statement, applicability: item.applicability },
    ]))
  } catch (error) {
    message.error(error instanceof Error ? error.message : '经验记忆加载失败')
  } finally {
    memoryLoading.value = false
  }
}

async function saveMemory(item: MachineMemoryItem) {
  const draft = memoryDrafts.value[item.id]
  if (!draft) {
    return
  }
  memoryActionId.value = item.id
  try {
    await reviseMachineMemory(item.id, draft.statement, draft.applicability)
    message.success('经验记忆已更新')
    await loadMemories()
  } catch (error) {
    message.error(error instanceof Error ? error.message : '经验记忆更新失败')
  } finally {
    memoryActionId.value = ''
  }
}

async function changeMemoryState(item: MachineMemoryItem, operation: 'activate' | 'disable' | 'enable' | 'invalidate') {
  memoryActionId.value = item.id
  try {
    await changeMachineMemoryState(item.id, operation)
    await loadMemories()
  } catch (error) {
    message.error(error instanceof Error ? error.message : '操作失败')
  } finally {
    memoryActionId.value = ''
  }
}

async function showSource(item: MachineMemoryItem) {
  const evidence = item.evidence[0]
  if (!evidence) {
    message.info('暂无可查看的来源')
    return
  }
  try {
    memorySources.value[item.id] = await getMachineMemorySource(item.id, evidence.id)
  } catch (error) {
    message.error(error instanceof Error ? error.message : '来源暂不可用')
  }
}

function removeMemory(item: MachineMemoryItem) {
  dialog.warning({
    title: '删除经验记忆',
    content: '删除后无法恢复。以后遇到相似任务时，系统仍可能根据新的任务记录再次整理出相似经验。',
    positiveText: '删除',
    negativeText: '取消',
    onPositiveClick: async () => {
      memoryActionId.value = item.id
      try {
        await deleteMachineMemory(item.id)
        message.success('已删除')
        await loadMemories()
      } catch (error) {
        message.error(error instanceof Error ? error.message : '删除失败')
      } finally {
        memoryActionId.value = ''
      }
    },
  })
}

async function savePreference(enabled: boolean) {
  preferenceSaving.value = true
  try {
    preference.value = await updateCortexMemoryPreference(enabled)
    message.success('记忆设置已保存')
  } catch (error) {
    message.error(error instanceof Error ? error.message : '记忆设置保存失败')
    preference.value = await getCortexMemoryPreference()
  } finally {
    preferenceSaving.value = false
  }
}

async function refreshPreview() {
  preview.value = await getContextPreview(previewProfile.value)
}

const compiledMarkdown = computed(() => {
  const source = preview.value?.compiled_content || ''
  return source ? MarkdownInstance.render(source) : ''
})
const activeBulletinMemories = computed(() => memories.value.filter((item) => item.status === 'active'))
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
    message.success('已保存')
    await loadContextData()
  } catch (error) {
    message.error(error instanceof Error ? error.message : '保存失败')
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
      <section v-if="preference" class="context-panel preference-row">
        <div>
          <h3>任务经验记忆</h3>
          <p class="hint">
            开启后，系统会整理任务经验并在相关任务中使用；关闭后停止整理和使用。
          </p>
        </div>
        <n-switch
          :value="preference.enabled"
          :loading="preferenceSaving"
          @update:value="savePreference"
        />
      </section>

      <section class="context-panel">
        <div class="memory-heading">
          <div>
            <h3>已整理的经验</h3>
            <p class="hint">
              只会在适用范围、来源和状态都符合当前任务时使用。
            </p>
          </div>
          <n-select v-model:value="memoryStatus" :options="statusOptions" clearable @update:value="loadMemories" />
          <n-select v-model:value="memoryType" :options="typeOptions" clearable @update:value="loadMemories" />
          <n-select v-model:value="memoryScope" :options="scopeOptions" clearable @update:value="loadMemories" />
          <n-input v-model:value="memoryQuery" clearable placeholder="搜索经验内容" @keyup.enter="loadMemories" />
          <n-button @click="loadMemories">搜索</n-button>
        </div>
        <div v-if="health" class="health-row">
          <n-tag>等待处理 {{ health.pending }}</n-tag>
          <n-tag v-if="health.partial" type="warning">部分完成 {{ health.partial }}</n-tag>
          <n-tag v-if="health.failed || health.dead" type="error">处理失败 {{ health.failed + health.dead }}</n-tag>
          <n-tag v-if="health.skipped">已跳过 {{ health.skipped }}</n-tag>
          <n-tag v-if="health.workspace_pending || health.index_pending">等待更新 {{ health.workspace_pending + health.index_pending }}</n-tag>
          <n-tag v-if="health.workspace_failed || health.index_failed" type="error">视图更新失败 {{ health.workspace_failed + health.index_failed }}</n-tag>
          <span v-if="health.last_capture_at">最近记录 {{ new Date(health.last_capture_at).toLocaleString() }}</span>
          <span v-if="health.last_consolidation_at">最近整理 {{ new Date(health.last_consolidation_at).toLocaleString() }}</span>
        </div>
        <p v-if="memoryLoading" class="hint">正在加载…</p>
        <p v-else-if="!memories.length" class="hint">暂无经验记忆。</p>
        <article v-for="item in memories" :key="item.id" class="memory-card">
          <div class="memory-card-title">
            <strong>{{ item.subject }}</strong>
            <n-tag size="small">{{ typeLabels[item.memory_type] }}</n-tag>
            <n-tag size="small" :type="item.status === 'active' ? 'success' : item.status === 'needs_review' ? 'warning' : 'default'">{{ statusLabels[item.status] }}</n-tag>
            <span>{{ item.scope_label }} · 第 {{ item.version }} 版 · {{ item.evidence_count }} 个来源</span>
          </div>
          <n-input v-model:value="memoryDrafts[item.id].statement" type="textarea" :autosize="{ minRows: 2, maxRows: 8 }" />
          <n-input v-model:value="memoryDrafts[item.id].applicability" placeholder="适用条件" />
          <div class="memory-actions">
            <n-button size="tiny" :loading="memoryActionId === item.id" @click="saveMemory(item)">保存修改</n-button>
            <n-button size="tiny" quaternary @click="showSource(item)">查看来源</n-button>
            <n-button v-if="item.status === 'candidate'" size="tiny" type="primary" ghost @click="changeMemoryState(item, 'activate')">确认适用</n-button>
            <n-button v-if="['active', 'candidate', 'needs_review'].includes(item.status)" size="tiny" @click="changeMemoryState(item, 'disable')">停用</n-button>
            <n-button v-if="item.status === 'disabled'" size="tiny" @click="changeMemoryState(item, 'enable')">启用</n-button>
            <n-button v-if="!['superseded', 'invalidated'].includes(item.status)" size="tiny" @click="changeMemoryState(item, 'invalidate')">标记失效</n-button>
            <n-button size="tiny" type="error" ghost :loading="memoryActionId === item.id" @click="removeMemory(item)">删除</n-button>
          </div>
          <div v-if="memorySources[item.id]" class="source-preview">
            <span v-if="memorySources[item.id].availability === 'available'">{{ memorySources[item.id].excerpt }}</span>
            <span v-else>原始来源已不可用。</span>
          </div>
        </article>
      </section>

      <section class="context-panel">
        <h3>最终上下文预览</h3>
        <div class="inline-actions">
          <n-select v-model:value="previewProfile" :options="profileOptions" @update:value="refreshPreview" />
          <n-tag v-if="preview">
            约 {{ preview.token_estimate }} tokens
          </n-tag>
        </div>
        <h4>用户显式上下文</h4>
        <div v-if="preview" ref="previewRef" class="compiled-preview markdown-body" v-html="compiledMarkdown"></div>
        <h4>自动 Memory Bulletin</h4>
        <div class="compiled-preview">
          <p v-if="!preference?.enabled" class="hint">经验记忆已关闭，下一次运行不会注入自动 Bulletin。</p>
          <p v-else-if="!activeBulletinMemories.length" class="hint">当前没有可参与自动召回的经验。</p>
          <template v-else>
            <p class="hint">以下是可参与自动召回的经验示意；实际运行会按当前问题与项目范围筛选。</p>
            <ul>
              <li v-for="item in activeBulletinMemories" :key="`preview-${item.id}`">
                <strong>[{{ typeLabels[item.memory_type] }}]</strong> {{ item.statement }}
                <div class="hint">适用：{{ item.applicability || '当前项目范围' }}</div>
              </li>
            </ul>
          </template>
        </div>
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
.preference-row { display: flex; align-items: center; justify-content: space-between; gap: 20px; padding: 12px; border: 1px solid var(--noesis-color-border-subtle); border-radius: var(--noesis-radius-md); }
.preference-row .hint { margin: 0; }
.inline-actions { display: flex; align-items: center; gap: 8px; margin-bottom: 10px; }
.inline-actions > :first-child { flex: 1; }
.memory-heading { display: grid; grid-template-columns: 1fr 140px 140px; gap: 10px; align-items: start; }
.health-row, .memory-actions, .memory-card-title { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; }
.memory-card { display: grid; gap: 10px; padding: 14px 0; border-bottom: 1px solid var(--noesis-color-border-subtle); }
.memory-card-title span { color: var(--noesis-color-text-secondary); font-size: 12px; }
.source-preview { padding: 10px; color: var(--noesis-color-text-secondary); background: var(--noesis-color-bg-surface); border-radius: var(--noesis-radius-sm); font-size: 12px; }
.compiled-preview { max-height: 480px; overflow: auto; padding: 16px; border: 1px solid var(--noesis-color-border-subtle); border-radius: var(--noesis-radius-md); }
</style>
