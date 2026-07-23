<script lang="ts" setup>
import type { McpServerCatalogItem } from '@/api/mcp'
import type FileUploadManager from '@/views/FileUploadManager.vue'
import { NButton, NCheckbox, NPopover } from 'naive-ui'
import { useRouter } from 'vue-router'
import { ensureSession } from '@/api/chat'
import { listMcpServers } from '@/api/mcp'
import { getSkillsFsTree } from '@/api/skills'
import ModelSelector from '@/components/Chat/ModelSelector.vue'
import KbScopeSelector from '@/components/KnowledgeBase/KbScopeSelector.vue'
import { collectSkillPackages } from '@/utils/skillsTree'

type MenuView = 'root' | 'mcp' | 'skills' | 'kb'

const props = defineProps<{
  qaType: string
  sessionId: string
  disabled?: boolean
  /** ACTIVE 才 ensure 写 extra；COMPOSING 只改本地 v-model */
  persistSessionExtra?: boolean
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
const showFileUpload = computed(() =>
  props.qaType === 'COMMON_QA'
  || props.qaType === 'SUPER_AGENT_QA'
  || props.qaType === 'DEEP_RESEARCH_QA',
)
const showUploadImage = computed(() => showFileUpload.value)
const plusOpen = ref(false)
const menuView = ref<MenuView>('root')

const docInputRef = ref<HTMLInputElement | null>(null)
const imageInputRef = ref<HTMLInputElement | null>(null)

const mcpServers = ref<McpServerCatalogItem[]>([])
const skillPackages = ref<{ id: string, source: string }[]>([])
const catalogLoaded = ref(false)
const catalogLoading = ref(false)

async function loadCatalogs() {
  if (catalogLoading.value) {
    return
  }
  catalogLoading.value = true
  try {
    const [mcpResult, skillsResult] = await Promise.allSettled([
      listMcpServers(),
      getSkillsFsTree(),
    ])
    if (mcpResult.status === 'fulfilled') {
      mcpServers.value = mcpResult.value.servers ?? []
    } else {
      console.warn('加载 MCP 目录失败', mcpResult.reason)
    }
    if (skillsResult.status === 'fulfilled') {
      skillPackages.value = collectSkillPackages(skillsResult.value)
    } else {
      console.warn('加载 Skills 目录失败', skillsResult.reason)
    }
    catalogLoaded.value = true
  } catch (e) {
    console.warn('加载 Composer 目录失败', e)
  } finally {
    catalogLoading.value = false
  }
}

watch(plusOpen, (open) => {
  if (open) {
    menuView.value = 'root'
    if (!catalogLoaded.value) {
      void loadCatalogs()
    }
  }
})

watch(
  () => props.sessionId,
  () => {
    catalogLoaded.value = false
  },
)

async function persistExtra(patch: Record<string, unknown>) {
  if (!props.persistSessionExtra || !props.sessionId) {
    return
  }
  try {
    await ensureSession(props.sessionId, { extra: patch })
  } catch (e) {
    console.warn('保存会话工具选择失败', e)
  }
}

function toggleMcp(id: string, checked: boolean) {
  const set = new Set(selectedMcpServers.value)
  if (checked) {
    set.add(id)
  } else {
    set.delete(id)
  }
  const next = [...set]
  selectedMcpServers.value = next
  void persistExtra({ mcp_servers: next })
}

function toggleSkill(id: string, checked: boolean) {
  let current = selectedSkills.value
  if (skillsAllEnabled.value) {
    current = skillPackages.value.map((p) => p.id)
    skillsAllEnabled.value = false
  }
  const set = new Set(current)
  if (checked) {
    set.add(id)
  } else {
    set.delete(id)
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

function openMcpConfig() {
  plusOpen.value = false
  void router.push({ name: 'Extensions', query: { tab: 'mcp' } })
}

function pickDocuments() {
  docInputRef.value?.click()
}

function pickImages() {
  imageInputRef.value?.click()
}

function onDocFilesSelected(e: Event) {
  const input = e.target as HTMLInputElement
  const files = input.files ? [...input.files] : []
  input.value = ''
  if (!files.length) {
    return
  }
  props.fileUploadRef?.enqueueFiles?.(files)
  plusOpen.value = false
}

function onImageFilesSelected(e: Event) {
  const input = e.target as HTMLInputElement
  const files = input.files ? [...input.files] : []
  input.value = ''
  if (!files.length) {
    return
  }
  props.fileUploadRef?.enqueueFiles?.(files)
  plusOpen.value = false
}

const mcpSummary = computed(() => {
  const n = selectedMcpServers.value.length
  return n > 0 ? `${n}` : ''
})

const skillSummary = computed(() => {
  if (skillsAllEnabled.value) {
    return '全部'
  }
  const n = selectedSkills.value.length
  return n > 0 ? `${n}` : ''
})

const kbSummary = computed(() => {
  if (!kbSearchEnabled.value) {
    return '关'
  }
  const n = selectedKbCollections.value.length
  return n > 0 ? `${n}` : ''
})
</script>

<template>
  <div class="composer-toolbar">
    <div class="composer-toolbar__left">
      <input
        ref="docInputRef"
        type="file"
        class="composer-hidden-input"
        accept=".doc,.docx,.ppt,.pptx,.pdf,.txt,.xlsx,.csv,.md"
        multiple
        @change="onDocFilesSelected"
      >
      <input
        ref="imageInputRef"
        type="file"
        class="composer-hidden-input"
        accept="image/jpeg,image/png,image/webp,image/gif"
        multiple
        @change="onImageFilesSelected"
      >

      <n-popover
        v-model:show="plusOpen"
        trigger="click"
        placement="top-start"
        :show-arrow="false"
        :disabled="disabled"
        raw
        class="composer-tools-popover"
      >
        <template #trigger>
          <button
            type="button"
            class="composer-plus-btn"
            :disabled="disabled"
            aria-label="附件与工具"
          >
            <span class="i-carbon:add text-18"></span>
          </button>
        </template>

        <div class="composer-tools-panel" @click.stop>
          <!-- 一级：上传 / MCP / Skills -->
          <template v-if="menuView === 'root'">
            <button
              v-if="showFileUpload"
              type="button"
              class="composer-menu-item"
              @click="pickDocuments"
            >
              <span class="i-material-symbols:file-open-outline composer-menu-item__icon"></span>
              <span class="composer-menu-item__label">上传文件</span>
            </button>
            <button
              v-if="showUploadImage"
              type="button"
              class="composer-menu-item"
              @click="pickImages"
            >
              <span class="i-mdi:file-image-outline composer-menu-item__icon"></span>
              <span class="composer-menu-item__label">上传图片</span>
            </button>

            <button
              v-if="showKbScope"
              type="button"
              class="composer-menu-item"
              @click="menuView = 'kb'"
            >
              <span class="i-carbon:book composer-menu-item__icon"></span>
              <span class="composer-menu-item__label">知识库</span>
              <span v-if="kbSummary" class="composer-menu-item__badge">{{ kbSummary }}</span>
              <span class="i-carbon:chevron-right composer-menu-item__chevron"></span>
            </button>

            <button
              type="button"
              class="composer-menu-item"
              @click="menuView = 'mcp'"
            >
              <span class="i-carbon:api composer-menu-item__icon"></span>
              <span class="composer-menu-item__label">MCP</span>
              <span v-if="mcpSummary" class="composer-menu-item__badge">{{ mcpSummary }}</span>
              <span class="i-carbon:chevron-right composer-menu-item__chevron"></span>
            </button>

            <button
              v-if="showSkillsMenu"
              type="button"
              class="composer-menu-item"
              @click="menuView = 'skills'"
            >
              <span class="i-carbon:notebook composer-menu-item__icon"></span>
              <span class="composer-menu-item__label">Skills</span>
              <span v-if="skillSummary" class="composer-menu-item__badge">{{ skillSummary }}</span>
              <span class="i-carbon:chevron-right composer-menu-item__chevron"></span>
            </button>
          </template>

          <!-- 二级：MCP -->
          <template v-else-if="menuView === 'mcp'">
            <button
              type="button"
              class="composer-menu-back"
              @click="menuView = 'root'"
            >
              <span class="i-carbon:chevron-left"></span>
              <span>MCP</span>
            </button>
            <div v-if="catalogLoading" class="composer-tools-empty">
              加载中…
            </div>
            <div v-else-if="!mcpServers.length" class="composer-tools-empty">
              暂无 MCP Server
            </div>
            <label
              v-for="server in mcpServers"
              :key="server.id"
              class="composer-tool-row"
            >
              <n-checkbox
                :checked="selectedMcpServers.includes(server.id)"
                @update:checked="(checked) => toggleMcp(server.id, checked)"
              />
              <span class="composer-tool-row__label">{{ server.display_name || server.id }} · {{ server.source }}</span>
            </label>
            <n-button
              quaternary
              size="tiny"
              class="composer-tools-config-btn"
              @click="openMcpConfig"
            >
              打开 MCP 配置…
            </n-button>
          </template>

          <!-- 二级：Skills -->
          <template v-else-if="menuView === 'skills'">
            <button
              type="button"
              class="composer-menu-back"
              @click="menuView = 'root'"
            >
              <span class="i-carbon:chevron-left"></span>
              <span>Skills</span>
            </button>
            <div v-if="catalogLoading" class="composer-tools-empty">
              加载中…
            </div>
            <div v-else-if="!skillPackages.length" class="composer-tools-empty">
              暂无 Skills
            </div>
            <label
              v-for="skill in skillPackages"
              :key="skill.id"
              class="composer-tool-row"
            >
              <n-checkbox
                :checked="isSkillChecked(skill.id)"
                @update:checked="(checked) => toggleSkill(skill.id, checked)"
              />
              <span class="composer-tool-row__label">{{ skill.id }} ({{ skill.source }})</span>
            </label>
          </template>

          <!-- 二级：知识库 -->
          <template v-else-if="menuView === 'kb'">
            <button
              type="button"
              class="composer-menu-back"
              @click="menuView = 'root'"
            >
              <span class="i-carbon:chevron-left"></span>
              <span>知识库</span>
            </button>
            <div class="composer-kb-panel">
              <KbScopeSelector
                v-model="selectedKbCollections"
                v-model:kb-search-enabled="kbSearchEnabled"
                embedded
                :session-id="sessionId"
                :persist-session-extra="persistSessionExtra"
                :disabled="disabled"
              />
            </div>
          </template>
        </div>
      </n-popover>

      <ModelSelector
        v-model="selectedModelId"
        :session-id="sessionId"
        :persist-session-extra="persistSessionExtra"
        :disabled="disabled"
      />
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

.composer-hidden-input {
  display: none;
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

.composer-tools-panel {
  min-width: 240px;
  max-width: min(320px, calc(100vw - 32px));
  max-height: min(420px, calc(100vh - 200px));
  padding: 6px 0;
  overflow-y: auto;
  background: var(--n-color, #fff);
  border: 1px solid var(--n-border-color, rgb(0 0 0 / 9%));
  border-radius: var(--n-border-radius, 8px);
  box-shadow: var(--n-box-shadow, 0 4px 16px rgb(0 0 0 / 12%));
}

.composer-menu-item,
.composer-menu-back {
  display: flex;
  align-items: center;
  gap: 10px;
  width: 100%;
  margin: 0;
  padding: 8px 14px;
  border: none;
  background: transparent;
  color: var(--noesis-text-primary, #111);
  font-size: 13px;
  line-height: 1.4;
  text-align: left;
  cursor: pointer;
  transition: background-color 0.15s ease;
}

.composer-menu-item:hover,
.composer-menu-back:hover {
  background: var(--noesis-color-primary-bg-subtle, rgb(0 0 0 / 4%));
}

.composer-menu-item__icon {
  flex-shrink: 0;
  width: 16px;
  height: 16px;
  font-size: 16px;
  color: var(--noesis-text-secondary, #6b7280);
}

.composer-menu-item__label {
  flex: 1;
  min-width: 0;
}

.composer-menu-item__badge {
  flex-shrink: 0;
  padding: 0 6px;
  border-radius: 999px;
  background: var(--noesis-color-primary-bg-subtle, rgb(0 0 0 / 6%));
  color: var(--noesis-text-secondary, #6b7280);
  font-size: 11px;
  line-height: 18px;
}

.composer-menu-item__chevron {
  flex-shrink: 0;
  color: var(--noesis-text-secondary, #6b7280);
  font-size: 14px;
}

.composer-menu-back {
  margin-bottom: 2px;
  padding-bottom: 8px;
  border-bottom: 1px solid var(--n-border-color, rgb(0 0 0 / 9%));
  font-weight: 600;
  color: var(--noesis-text-secondary, #6b7280);
}

.composer-tools-empty {
  padding: 8px 14px 12px;
  font-size: 12px;
  color: var(--noesis-text-secondary, #6b7280);
}

.composer-tool-row {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  padding: 6px 14px;
  cursor: pointer;
  transition: background-color 0.15s ease;
}

.composer-tool-row:hover {
  background: var(--noesis-color-primary-bg-subtle, rgb(0 0 0 / 4%));
}

.composer-tool-row__label {
  flex: 1;
  min-width: 0;
  font-size: 13px;
  line-height: 1.45;
  color: var(--noesis-text-primary, #111);
  word-break: break-word;
}

.composer-tools-config-btn {
  margin: 4px 8px 2px;
}

.composer-kb-panel {
  padding: 4px 0 8px;
}

.composer-kb-panel :deep(.kb-scope-embedded) {
  width: 100%;
  max-width: none;
  padding: 0 14px 4px;
}

.composer-kb-panel :deep(.kb-scope-embedded__title) {
  display: none;
}
</style>
