<script lang="ts" setup>
import type { DropdownOption } from 'naive-ui'
import type { McpServerCatalogItem } from '@/api/mcp'
import type { ChatModelOption } from '@/api/models'
import type FileUploadManager from '@/views/FileUploadManager.vue'
import { useRouter } from 'vue-router'
import { ensureSession } from '@/api/chat'
import { listMcpServers } from '@/api/mcp'
import { getChatModels } from '@/api/models'
import { getSkillsFsTree } from '@/api/skills'
import KbScopeSelector from '@/components/KnowledgeBase/KbScopeSelector.vue'

const props = defineProps<{
  qaType: string
  sessionId: string
  disabled?: boolean
  fileUploadRef?: InstanceType<typeof FileUploadManager> | null
}>()

const router = useRouter()

const selectedModelId = defineModel<string>('modelId', { default: '' })
const selectedKbCollections = defineModel<string[]>('kbCollections', { default: () => [] })
const kbSearchEnabled = defineModel<boolean>('kbSearchEnabled', { default: true })
const selectedMcpServers = defineModel<string[]>('mcpServers', { default: () => [] })
const selectedSkills = defineModel<string[]>('enabledSkills', { default: () => [] })
const skillsAllEnabled = defineModel<boolean>('skillsAllEnabled', { default: true })

const showKbScope = computed(() => props.qaType === 'COMMON_QA')
const showSkillsMenu = computed(() => props.qaType === 'SUPER_AGENT_QA')
const plusOpen = ref(false)
const uploadOptions = computed(() => props.fileUploadRef?.options ?? [])

const models = ref<ChatModelOption[]>([])
const mcpServers = ref<McpServerCatalogItem[]>([])
const skillPackages = ref<{ id: string, source: string }[]>([])
const catalogLoaded = ref(false)
const catalogLoading = ref(false)

const currentModelLabel = computed(() => {
  const hit = models.value.find((m) => m.id === selectedModelId.value)
  return hit?.label || (catalogLoading.value ? '加载中…' : '选择模型')
})

async function loadCatalogs() {
  if (catalogLoading.value) {
    return
  }
  catalogLoading.value = true
  try {
    const [modelCatalog, mcpCatalog, skillsTree] = await Promise.all([
      getChatModels(),
      listMcpServers(),
      getSkillsFsTree().catch(() => null),
    ])
    models.value = modelCatalog.models ?? []
    if (!selectedModelId.value) {
      selectedModelId.value = modelCatalog.default_id
    }
    mcpServers.value = mcpCatalog.servers ?? []
    const pkgs: { id: string, source: string }[] = []
    for (const section of [skillsTree?.platform, skillsTree?.user]) {
      if (!section?.tree) {
        continue
      }
      for (const node of section.tree) {
        if (!node.isLeaf) {
          pkgs.push({ id: node.label, source: node.source })
        }
      }
    }
    skillPackages.value = pkgs
    catalogLoaded.value = true
  } catch (e) {
    console.warn('加载 Composer 目录失败', e)
  } finally {
    catalogLoading.value = false
  }
}

watch(plusOpen, (open) => {
  if (open && !catalogLoaded.value) {
    void loadCatalogs()
  }
})

watch(
  () => props.sessionId,
  () => {
    catalogLoaded.value = false
  },
)

async function persistExtra(patch: Record<string, unknown>) {
  if (!props.sessionId) {
    return
  }
  try {
    await ensureSession(props.sessionId, { extra: patch })
  } catch (e) {
    console.warn('保存会话工具选择失败', e)
  }
}

async function onSelectModel(key: string) {
  selectedModelId.value = key
  await persistExtra({ model_id: key })
}

function toggleMcp(id: string) {
  const set = new Set(selectedMcpServers.value)
  if (set.has(id)) {
    set.delete(id)
  } else {
    set.add(id)
  }
  const next = [...set]
  selectedMcpServers.value = next
  void persistExtra({ mcp_servers: next })
}

function toggleSkill(id: string) {
  let current = selectedSkills.value
  if (skillsAllEnabled.value) {
    current = skillPackages.value.map((p) => p.id)
    skillsAllEnabled.value = false
  }
  const set = new Set(current)
  if (set.has(id)) {
    set.delete(id)
  } else {
    set.add(id)
  }
  const next = [...set]
  selectedSkills.value = next
  void persistExtra({ enabled_skills: next })
}

function isSkillChecked(id: string) {
  if (skillsAllEnabled.value) {
    return true
  }
  return selectedSkills.value.includes(id)
}

const modelOptions = computed<DropdownOption[]>(() =>
  models.value.map((m) => ({
    label: m.label,
    key: `model:${m.id}`,
  })),
)

const skillOptions = computed<DropdownOption[]>(() =>
  skillPackages.value.map((p) => ({
    label: () =>
      h('div', { class: 'composer-check-row' }, [
        h('span', { class: isSkillChecked(p.id) ? 'i-carbon:checkmark' : 'composer-check-spacer' }),
        h('span', {}, `${p.id} (${p.source})`),
      ]),
    key: `skill:${p.id}`,
  })),
)

const mcpOptions = computed<DropdownOption[]>(() => {
  const items: DropdownOption[] = mcpServers.value.map((s) => ({
    label: () =>
      h('div', { class: 'composer-check-row' }, [
        h('span', {
          class: selectedMcpServers.value.includes(s.id)
            ? 'i-carbon:checkmark'
            : 'composer-check-spacer',
        }),
        h('span', {}, `${s.display_name || s.id} · ${s.source}`),
      ]),
    key: `mcp:${s.id}`,
  }))
  items.push({ type: 'divider', key: 'mcp-div' })
  items.push({ label: '打开 MCP 配置…', key: 'mcp:config' })
  return items
})

const plusMenuOptions = computed<DropdownOption[]>(() => {
  const opts: DropdownOption[] = []

  if (uploadOptions.value.length) {
    opts.push({
      type: 'group',
      label: '附件',
      key: 'upload-group',
      children: uploadOptions.value.map((item, idx) => ({
        key: `upload:${idx}`,
        type: 'render' as const,
        render: item.type === 'render' && item.render
          ? () => h({ render: item.render })
          : () => h('span'),
      })),
    })
  }

  opts.push({
    label: 'Models',
    key: 'models',
    children: modelOptions.value.length
      ? modelOptions.value
      : [{ label: catalogLoading.value ? '加载中…' : '无可用模型', key: 'models:empty', disabled: true }],
  })

  if (showSkillsMenu.value) {
    opts.push({
      label: 'Skills',
      key: 'skills',
      children: skillOptions.value.length
        ? skillOptions.value
        : [{ label: catalogLoading.value ? '加载中…' : '暂无 Skills', key: 'skills:empty', disabled: true }],
    })
  }

  opts.push({
    label: 'MCP Servers',
    key: 'mcp',
    children: mcpOptions.value,
  })

  return opts
})

function onPlusSelect(key: string | number) {
  const k = String(key)
  if (k.startsWith('model:')) {
    void onSelectModel(k.slice('model:'.length))
    return
  }
  if (k.startsWith('skill:')) {
    toggleSkill(k.slice('skill:'.length))
    return
  }
  if (k === 'mcp:config') {
    plusOpen.value = false
    void router.push({ name: 'McpManagement' })
    return
  }
  if (k.startsWith('mcp:')) {
    toggleMcp(k.slice('mcp:'.length))
  }
}
</script>

<template>
  <div class="composer-toolbar">
    <div class="composer-toolbar__left">
      <n-dropdown
        v-model:show="plusOpen"
        trigger="click"
        placement="top-start"
        :options="plusMenuOptions"
        :disabled="disabled"
        @select="onPlusSelect"
      >
        <button
          type="button"
          class="composer-plus-btn"
          :disabled="disabled"
          aria-label="添加上下文与工具"
        >
          <span class="i-carbon:add text-18"></span>
        </button>
      </n-dropdown>

      <button
        type="button"
        class="composer-model-trigger"
        :disabled="disabled"
        @click="plusOpen = true"
      >
        <span class="composer-model-trigger__label">{{ currentModelLabel }}</span>
        <span class="i-carbon:chevron-down text-12 opacity-60"></span>
      </button>

      <n-tag
        v-if="selectedMcpServers.length"
        size="small"
        :bordered="false"
        class="composer-chip"
      >
        MCP {{ selectedMcpServers.length }}
      </n-tag>
      <n-tag
        v-if="showSkillsMenu && !skillsAllEnabled"
        size="small"
        :bordered="false"
        class="composer-chip"
      >
        Skills {{ selectedSkills.length }}
      </n-tag>

      <div v-if="showKbScope" class="composer-kb-inline">
        <KbScopeSelector
          v-model="selectedKbCollections"
          v-model:kb-search-enabled="kbSearchEnabled"
          :session-id="sessionId"
          :disabled="disabled"
        />
      </div>
    </div>

    <div class="composer-toolbar__right">
      <slot name="right"></slot>
    </div>
  </div>
</template>

<style scoped>
.composer-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-top: 8px;
  min-height: 32px;
}

.composer-toolbar__left,
.composer-toolbar__right {
  display: flex;
  align-items: center;
  gap: 4px;
  min-width: 0;
}

.composer-toolbar__right {
  margin-left: auto;
  flex-shrink: 0;
}

.composer-plus-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  padding: 0;
  border: none;
  border-radius: 999px;
  background: transparent;
  color: var(--noesis-text-secondary, #6b7280);
  cursor: pointer;
  transition: background-color 0.2s ease, color 0.2s ease;
}

.composer-plus-btn:hover:not(:disabled) {
  background: var(--noesis-color-primary-bg-subtle, rgb(0 0 0 / 4%));
  color: var(--noesis-text-primary, #111);
}

.composer-plus-btn:disabled {
  cursor: not-allowed;
  opacity: 0.55;
}

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
}

.composer-model-trigger:hover:not(:disabled) {
  background: var(--noesis-color-primary-bg-subtle, rgb(0 0 0 / 4%));
  color: var(--noesis-text-primary, #111);
}

.composer-model-trigger__label {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.composer-chip {
  font-size: 11px;
}

.composer-kb-inline {
  margin-left: 4px;
}

:global(.composer-check-row) {
  display: flex;
  align-items: center;
  gap: 8px;
}

:global(.composer-check-spacer) {
  display: inline-block;
  width: 14px;
}
</style>
