<script lang="ts" setup>
import type { McpServerCatalogItem } from '@/api/mcp'
import { NButton, NCheckbox, NPopover } from 'naive-ui'
import { useRouter } from 'vue-router'
import { ensureSession } from '@/api/chat'
import { listMcpServers } from '@/api/mcp'
import { getSkillsFsTree } from '@/api/skills'
import ModelSelector from '@/components/Chat/ModelSelector.vue'
import KbScopeSelector from '@/components/KnowledgeBase/KbScopeSelector.vue'

const props = defineProps<{
  qaType: string
  sessionId: string
  disabled?: boolean
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
    const [mcpCatalog, skillsTree] = await Promise.all([
      listMcpServers(),
      getSkillsFsTree().catch(() => null),
    ])
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

onMounted(() => {
  void loadCatalogs()
})

watch(
  () => props.sessionId,
  () => {
    catalogLoaded.value = false
    void loadCatalogs()
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
</script>

<template>
  <div class="composer-toolbar">
    <div class="composer-toolbar__left">
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
            aria-label="配置 Skills 与 MCP"
          >
            <span class="i-carbon:add text-18"></span>
          </button>
        </template>

        <div class="composer-tools-panel" @click.stop>
          <section v-if="showSkillsMenu" class="composer-tools-section">
            <div class="composer-tools-section__title">
              Skills
            </div>
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
          </section>

          <section class="composer-tools-section">
            <div class="composer-tools-section__title">
              MCP Servers
            </div>
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
          </section>
        </div>
      </n-popover>

      <ModelSelector
        v-model="selectedModelId"
        :session-id="sessionId"
        :disabled="disabled"
      />

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

.composer-tools-panel {
  min-width: 280px;
  max-width: min(360px, calc(100vw - 32px));
  max-height: min(420px, calc(100vh - 200px));
  padding: 8px 0;
  overflow-y: auto;
  background: var(--n-color, #fff);
  border: 1px solid var(--n-border-color, rgb(0 0 0 / 9%));
  border-radius: var(--n-border-radius, 8px);
  box-shadow: var(--n-box-shadow, 0 4px 16px rgb(0 0 0 / 12%));
}

.composer-tools-section + .composer-tools-section {
  margin-top: 4px;
  padding-top: 8px;
  border-top: 1px solid var(--n-border-color, rgb(0 0 0 / 9%));
}

.composer-tools-section__title {
  padding: 4px 14px 8px;
  font-size: 12px;
  font-weight: 600;
  color: var(--noesis-text-secondary, #6b7280);
}

.composer-tools-empty {
  padding: 4px 14px 10px;
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

.composer-kb-inline {
  flex-shrink: 0;
  margin-left: 4px;
  min-width: 0;
}
</style>
