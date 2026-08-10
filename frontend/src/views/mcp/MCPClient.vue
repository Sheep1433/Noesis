<script setup lang="ts">
import type { McpServerCatalogItem, McpServerStatusItem } from '@/api/mcp'
import { CodeSlash, Refresh } from '@vicons/ionicons-v5'
import {
  NButton,
  NEmpty,
  NIcon,
  NLayout,
  NLayoutContent,
  NLayoutSider,
  NSpace,
  NSpin,
  NText,
  useMessage,
} from 'naive-ui'
import { computed, onMounted, ref } from 'vue'
import {
  getMcpConfig,
  listMcpServers,
  listMcpServerTools,
  probeMcpServer,
  saveMcpConfig,
} from '@/api/mcp'
import { useBreakpoint } from '@/hooks/useBreakpoint'
import McpServerCard from './McpServerCard.vue'

const message = useMessage()
const { isMobile } = useBreakpoint()

const loading = ref(true)
const refreshing = ref(false)
const saving = ref(false)
const error = ref<string | null>(null)
const servers = ref<McpServerStatusItem[]>([])
const probing = ref<Record<string, boolean>>({})
const expandedServers = ref<Record<string, boolean>>({})
const visibleTools = ref<Record<string, Array<{ name: string, description: string }>>>({})
const toolLoading = ref<Record<string, boolean>>({})
const toolErrors = ref<Record<string, string>>({})
let loadGeneration = 0

const configPath = ref('个人 MCP 配置')
const configExists = ref(false)
const editorText = ref('{\n  "mcpServers": {}\n}\n')
const editorDirty = ref(false)

const connectedCount = computed(() => servers.value.filter((s) => s.status === 'ok').length)

onMounted(loadInitial)

function toStatusItems(items: McpServerCatalogItem[]): McpServerStatusItem[] {
  return items.map((server) => ({
    ...server,
    status: 'unknown' as const,
    tool_count: 0,
    message: '',
  }))
}

function updateServer(serverId: string, patch: Partial<McpServerStatusItem>) {
  servers.value = servers.value.map((server) => server.id === serverId ? { ...server, ...patch } : server)
}

function setProbeState(serverId: string, value: boolean) {
  probing.value = { ...probing.value, [serverId]: value }
}

async function loadInitial() {
  loading.value = true
  error.value = null
  const generation = ++loadGeneration
  probing.value = {}
  expandedServers.value = {}
  visibleTools.value = {}
  toolErrors.value = {}

  const configPromise = getMcpConfig()
    .then((cfg) => {
      if (generation === loadGeneration) {
        applyConfig(cfg)
      }
    })
    .catch((reason: any) => {
      message.error(reason?.message || '读取 MCP 配置失败')
    })

  const serverPromise = listMcpServers('user')
    .then((res) => {
      if (generation !== loadGeneration) {
        return
      }
      servers.value = toStatusItems(res.servers ?? [])
      loading.value = false
      if (servers.value.length) {
        void probeServers(servers.value, generation)
      }
    })
    .catch((reason: any) => {
      if (generation !== loadGeneration) {
        return
      }
      error.value = reason?.message || '读取 MCP Server 列表失败'
      loading.value = false
    })

  await Promise.all([configPromise, serverPromise])
}

function applyConfig(cfg: { content: string, path_hint: string, exists: boolean }) {
  editorText.value = cfg.content
  configPath.value = cfg.path_hint
  configExists.value = cfg.exists
  editorDirty.value = false
}

/** 只刷新目录；每个 Server 的 probe 在后台独立更新，不阻塞列表。 */
async function refreshStatus() {
  refreshing.value = true
  error.value = null
  const generation = ++loadGeneration
  probing.value = {}
  expandedServers.value = {}
  visibleTools.value = {}
  toolErrors.value = {}
  try {
    const res = await listMcpServers('user')
    servers.value = toStatusItems(res.servers ?? [])
    void probeServers(servers.value, generation)
  } catch (e: any) {
    error.value = e.message || '读取 MCP Server 列表失败'
  } finally {
    refreshing.value = false
  }
}

function onEditorInput(value: string) {
  editorText.value = value
  editorDirty.value = true
}

async function saveConfig() {
  saving.value = true
  try {
    const cfg = await saveMcpConfig(editorText.value)
    applyConfig(cfg)
    message.success('配置已保存')
    await refreshStatus()
  } catch (e: any) {
    message.error(e.message || '保存失败')
  } finally {
    saving.value = false
  }
}

async function probeServers(items: McpServerStatusItem[], generation: number) {
  await Promise.all(items.map(async (server) => {
    if (generation !== loadGeneration) {
      return
    }
    if (!server.enabled) {
      updateServer(server.id, { message: 'MCP Server 已停用' })
      return
    }

    setProbeState(server.id, true)
    try {
      const result = await probeMcpServer(server.id)
      if (generation !== loadGeneration) {
        return
      }
      updateServer(server.id, {
        status: result.ok ? 'ok' : 'error',
        tool_count: result.tool_count,
        message: result.message,
        checked_at: result.checked_at,
        error_category: result.error_category,
        correlation_id: result.correlation_id,
      })
    } catch (e: any) {
      if (generation !== loadGeneration) {
        return
      }
      updateServer(server.id, {
        status: 'error',
        message: e.message || 'MCP Server 检测失败',
      })
    } finally {
      if (generation === loadGeneration) {
        setProbeState(server.id, false)
      }
    }
  }))
}

async function toggleTools(server: McpServerStatusItem) {
  if (!server.enabled) {
    return
  }
  const expanded = !expandedServers.value[server.id]
  expandedServers.value = { ...expandedServers.value, [server.id]: expanded }
  if (!expanded || visibleTools.value[server.id] || toolLoading.value[server.id]) {
    return
  }

  const generation = loadGeneration
  toolLoading.value = { ...toolLoading.value, [server.id]: true }
  toolErrors.value = { ...toolErrors.value, [server.id]: '' }
  try {
    const result = await listMcpServerTools(server.id)
    if (generation === loadGeneration) {
      visibleTools.value = { ...visibleTools.value, [server.id]: result.tools }
    }
  } catch (e: any) {
    if (generation === loadGeneration) {
      toolErrors.value = { ...toolErrors.value, [server.id]: e.message || '工具目录加载失败' }
    }
  } finally {
    if (generation === loadGeneration) {
      toolLoading.value = { ...toolLoading.value, [server.id]: false }
    }
  }
}
</script>

<template>
  <div class="mcp-management">
    <header class="panel-header">
      <p v-if="!isMobile" class="panel-subtitle">
        直接编辑个人 mcp.json；Server 列表先展示，状态和工具数在后台逐项加载。
      </p>
      <n-space class="panel-header-actions">
        <n-button :loading="refreshing" :disabled="loading" @click="refreshStatus()">
          <template #icon>
            <n-icon :component="Refresh" />
          </template>
          刷新
        </n-button>
        <n-button type="primary" :disabled="!editorDirty" :loading="saving" @click="saveConfig">
          保存
        </n-button>
      </n-space>
    </header>

    <div v-if="loading" class="loading">
      <n-spin size="large" />
      <span>正在读取 MCP 配置…</span>
    </div>

    <div v-else-if="error && !servers.length" class="error-wrap">
      <n-empty :description="error">
        <template #extra>
          <n-button @click="loadInitial">
            重试
          </n-button>
        </template>
      </n-empty>
    </div>

    <n-layout
      v-else
      has-sider
      class="mcp-layout"
      :class="{ 'mcp-layout--mobile': isMobile }"
      bordered
    >
      <n-layout-sider
        v-if="!isMobile"
        content-style="padding: 0;"
        :width="340"
        bordered
      >
        <div class="status-pane">
          <div class="status-pane__summary">
            <span>{{ servers.length }} servers</span>
            <span class="status-pane__dot">·</span>
            <span>{{ connectedCount }} connected</span>
          </div>

          <p v-if="error" class="status-pane__warn">
            {{ error }}
          </p>

          <n-empty
            v-if="!servers.length"
            class="status-pane__empty"
            description="暂无 server。在右侧写入 mcpServers 后保存。"
            size="small"
          />

          <template v-else>
            <div class="server-group">
              <div class="server-group__label">
                个人 MCP 服务
              </div>
              <McpServerCard
                v-for="s in servers"
                :key="s.id"
                :server="s"
                :probing="Boolean(probing[s.id])"
                :expanded="Boolean(expandedServers[s.id])"
                :tools="visibleTools[s.id]"
                :tool-loading="Boolean(toolLoading[s.id])"
                :tool-error="toolErrors[s.id]"
                @toggle="toggleTools"
              />
            </div>
          </template>
        </div>
      </n-layout-sider>

      <n-layout-content content-style="padding: 0;" :native-scrollbar="false">
        <div v-if="isMobile" class="mobile-status">
          <McpServerCard
            v-for="s in servers"
            :key="s.id"
            :server="s"
            :probing="Boolean(probing[s.id])"
            :expanded="Boolean(expandedServers[s.id])"
            :tools="visibleTools[s.id]"
            :tool-loading="Boolean(toolLoading[s.id])"
            :tool-error="toolErrors[s.id]"
            compact
            @toggle="toggleTools"
          />
        </div>

        <div class="editor-pane">
          <div class="editor-pane__head">
            <n-icon :component="CodeSlash" size="18" />
            <span class="editor-pane__title">MCP JSON 配置</span>
            <n-text depth="3" class="editor-pane__path">
              {{ configPath }}
              <template v-if="!configExists">
                · 新建
              </template>
              <template v-else-if="editorDirty">
                · 已修改
              </template>
            </n-text>
          </div>
          <p class="editor-pane__hint">
            仅 <code>streamable_http</code> / <code>sse</code>。请直接填写完整 URL
            与 headers（需要 API Key 时写入 <code>headers</code>）。
            个人配置使用字面量，不要写环境变量占位符。左侧状态与本文件内容一致。
          </p>
          <textarea
            class="mcp-editor"
            :value="editorText"
            spellcheck="false"
            @input="onEditorInput(($event.target as HTMLTextAreaElement).value)"
          ></textarea>
        </div>
      </n-layout-content>
    </n-layout>
  </div>
</template>

<style scoped>
.mcp-management {
  height: 100%;
  display: flex;
  flex-direction: column;
  padding: 12px 0 0;
  box-sizing: border-box;
}

.panel-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 12px;
  flex-wrap: wrap;
}

.panel-header-actions {
  flex-shrink: 0;
}

.panel-subtitle {
  margin: 0;
  font-size: 13px;
  line-height: 1.5;
  color: var(--noesis-color-text-muted, #737373);
  max-width: 520px;
}

.panel-subtitle code {
  font-size: 12px;
  padding: 1px 5px;
  border-radius: 4px;
  background: var(--noesis-color-bg-muted, #ebe6dc);
}

.loading,
.error-wrap {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 12px;
  color: var(--noesis-color-text-muted, #737373);
}

.mcp-layout {
  flex: 1;
  min-height: 0;
  border-radius: var(--noesis-radius-md, 10px) var(--noesis-radius-md, 10px) 0 0;
  overflow: hidden;
  background: var(--noesis-color-bg-elevated, #faf8f3);
}

.mcp-layout--mobile :deep(.n-layout-sider) {
  display: none;
}

.status-pane {
  padding: 14px 12px 20px;
  height: 100%;
  box-sizing: border-box;
  overflow: auto;
}

.status-pane__summary {
  font-size: 12px;
  color: var(--noesis-color-text-muted, #737373);
  padding: 0 6px 12px;
}

.status-pane__dot {
  margin: 0 4px;
}

.status-pane__warn {
  margin: 0 6px 10px;
  font-size: 12px;
  color: var(--noesis-color-danger, #ff6b6b);
}

.status-pane__empty {
  padding: 32px 8px;
}

.server-group {
  margin-bottom: 14px;
}

.server-group__label {
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 0.04em;
  text-transform: uppercase;
  color: var(--noesis-color-text-muted, #737373);
  padding: 4px 8px 8px;
}

.mobile-status {
  padding: 12px 14px 0;
}

.editor-pane {
  height: 100%;
  display: flex;
  flex-direction: column;
  padding: 14px 16px 16px;
  box-sizing: border-box;
}

.editor-pane__head {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 6px;
}

.editor-pane__title {
  font-weight: 600;
  font-size: 14px;
}

.editor-pane__path {
  margin-left: auto;
  font-size: 12px;
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
}

.editor-pane__hint {
  margin: 0 0 10px;
  font-size: 12px;
  color: var(--noesis-color-text-muted, #737373);
  line-height: 1.45;
}

.editor-pane__hint code {
  font-size: 11px;
  padding: 0 4px;
  border-radius: 3px;
  background: var(--noesis-color-bg-muted, #ebe6dc);
}

.mcp-editor {
  flex: 1;
  width: 100%;
  min-height: 360px;
  box-sizing: border-box;
  padding: 14px 16px;
  border: 1px solid var(--noesis-color-border-light, #d4d0c8);
  border-radius: 8px;
  background: var(--noesis-color-bg, #f4f1ea);
  color: var(--noesis-color-text-body, #262626);
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  font-size: 12.5px;
  line-height: 1.55;
  resize: none;
}

.mcp-editor:focus {
  outline: none;
  border-color: var(--noesis-color-border-focus, #111);
  box-shadow: 0 0 0 3px var(--noesis-color-primary-ring, rgb(17 17 17 / 18%));
}
</style>
