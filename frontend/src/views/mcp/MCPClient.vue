<script setup lang="ts">
import type { McpServerStatusItem } from '@/api/mcp'
import { CodeSlash, Refresh } from '@vicons/ionicons-v5'
import {
  NButton,
  NEmpty,
  NIcon,
  NInput,
  NLayout,
  NLayoutContent,
  NLayoutSider,
  NSelect,
  NSpace,
  NSpin,
  NSwitch,
  NTag,
  NText,
  useDialog,
  useMessage,
} from 'naive-ui'
import { computed, onMounted, ref } from 'vue'
import {
  deleteMcpServer,
  getMcpConfig,
  listMcpServers,
  listMcpServerStatus,
  listMcpServerTools,
  saveMcpConfig,
  setMcpServerEnabled,
  upsertMcpServer,
} from '@/api/mcp'
import { useBreakpoint } from '@/hooks/useBreakpoint'

const message = useMessage()
const dialog = useDialog()
const { isMobile } = useBreakpoint()

const loading = ref(true)
const refreshing = ref(false)
const saving = ref(false)
const error = ref<string | null>(null)
const servers = ref<McpServerStatusItem[]>([])

const configPath = ref('个人 MCP 配置')
const configExists = ref(false)
const editorText = ref('{\n  "mcpServers": {}\n}\n')
const editorDirty = ref(false)
const mode = ref<'form' | 'json'>('form')
const form = ref({ id: '', display_name: '', transport: 'streamable_http' as 'streamable_http' | 'sse', url: '', headers: '', enabled: true })
const editingServerId = ref<string | null>(null)
const visibleTools = ref<Record<string, Array<{ name: string, description: string }>>>({})

const connectedCount = computed(() => servers.value.filter((s) => s.status === 'ok').length)
const serverFormDirty = computed(() => Boolean(form.value.id || form.value.display_name || form.value.url || form.value.headers || editingServerId.value))

onMounted(async () => {
  await Promise.all([loadConfig(), refreshStatus({ initial: true })])
})

/** 进入页面 / 保存后自动握手（对齐 Cursor：打开即显示绿点与 tool 数） */
async function refreshStatus(opts?: { initial?: boolean }) {
  if (opts?.initial) {
    loading.value = true
  } else {
    refreshing.value = true
  }
  error.value = null
  try {
    // 管理页仅展示用户 mcp.json（与右侧编辑器一致）；Composer 仍用合并目录
    const res = await listMcpServerStatus(true, 'user')
    servers.value = res.servers ?? []
  } catch (e: any) {
    // 探测失败时降级为目录列表，避免整页不可用（如镜像未打包平台 mcp.json）
    try {
      const fallback = await listMcpServers('user')
      servers.value = (fallback.servers ?? []).map((s) => ({
        ...s,
        status: 'unknown' as const,
        tool_count: 0,
        message: '',
      }))
      error.value = e.message
        ? `${e.message}（已显示 server 列表，可点刷新重试）`
        : '状态探测失败（已显示 server 列表）'
    } catch {
      error.value = e.message || '状态加载失败'
      if (opts?.initial) {
        servers.value = []
      }
    }
  } finally {
    loading.value = false
    refreshing.value = false
  }
}

async function loadConfig() {
  try {
    const cfg = await getMcpConfig()
    editorText.value = cfg.content
    configPath.value = cfg.path_hint
    configExists.value = cfg.exists
    editorDirty.value = false
  } catch (e: any) {
    message.error(e.message || '读取配置失败')
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
    editorText.value = cfg.content
    configPath.value = cfg.path_hint
    configExists.value = cfg.exists
    editorDirty.value = false
    message.success('配置已保存')
    await refreshStatus()
  } catch (e: any) {
    message.error(e.message || '保存失败')
  } finally {
    saving.value = false
  }
}

function statusText(s: McpServerStatusItem) {
  if (s.status === 'ok') {
    return s.tool_count > 0 ? `${s.tool_count} tools enabled` : 'Connected'
  }
  if (s.status === 'error') {
    return 'Failed'
  }
  return 'Connecting…'
}

function switchMode(next: 'form' | 'json') {
  const hasUnsaved = mode.value === 'json' ? editorDirty.value : serverFormDirty.value
  if (hasUnsaved) {
    dialog.warning({
      title: '放弃未保存修改？',
      content: mode.value === 'json' ? '高级 JSON 的修改尚未保存。' : 'Server 表单的修改尚未保存。',
      positiveText: '放弃并切换',
      negativeText: '继续编辑',
      onPositiveClick: () => {
        resetServerForm()
        mode.value = next
      },
    })
    return
  }
  mode.value = next
}

function editServer(server: McpServerStatusItem) {
  editingServerId.value = server.id
  form.value = { id: server.id, display_name: server.display_name || '', transport: server.transport as 'streamable_http' | 'sse', url: server.url || '', headers: '', enabled: server.enabled }
}

function resetServerForm() {
  editingServerId.value = null
  form.value = { id: '', display_name: '', transport: 'streamable_http', url: '', headers: '', enabled: true }
}

async function saveServer() {
  try {
    const parsedHeaders = form.value.headers.trim() ? JSON.parse(form.value.headers) as Record<string, string> : undefined
    await upsertMcpServer(editingServerId.value || form.value.id, {
      transport: form.value.transport,
      url: form.value.url,
      display_name: form.value.display_name,
      enabled: form.value.enabled,
      headers_action: parsedHeaders ? 'replace' : 'keep',
      ...(parsedHeaders ? { headers: parsedHeaders } : {}),
    })
    message.success('MCP Server 已保存')
    resetServerForm()
    await Promise.all([loadConfig(), refreshStatus()])
  } catch (e: any) {
    message.error(e.message || '保存 Server 失败')
  }
}

async function toggleServer(server: McpServerStatusItem, enabled: boolean) {
  await setMcpServerEnabled(server.id, enabled)
  await Promise.all([loadConfig(), refreshStatus()])
}

function removeServer(server: McpServerStatusItem) {
  dialog.warning({
    title: `删除 ${server.display_name || server.id}？`,
    content: '该 Server 将无法再被 Agent 使用，此操作无法撤销。',
    positiveText: '删除',
    negativeText: '取消',
    async onPositiveClick() {
      await deleteMcpServer(server.id)
      await Promise.all([loadConfig(), refreshStatus()])
    },
  })
}

async function showTools(server: McpServerStatusItem) {
  try {
    visibleTools.value[server.id] = (await listMcpServerTools(server.id, true)).tools
  } catch (e: any) {
    message.error(e.message || '加载工具目录失败')
  }
}
</script>

<template>
  <div class="mcp-management">
    <header class="panel-header">
      <p v-if="!isMobile" class="panel-subtitle">
        管理个人 MCP 服务；打开本页会自动检测连通与工具数。
      </p>
      <n-space class="panel-header-actions">
        <n-button :type="mode === 'form' ? 'primary' : 'default'" @click="switchMode('form')">
          表单
        </n-button>
        <n-button :type="mode === 'json' ? 'primary' : 'default'" @click="switchMode('json')">
          高级 JSON
        </n-button>
        <n-button :loading="refreshing" :disabled="loading" @click="refreshStatus()">
          <template #icon>
            <n-icon :component="Refresh" />
          </template>
          刷新状态
        </n-button>
        <n-button
          v-if="mode === 'json'"
          type="primary"
          :disabled="!editorDirty"
          :loading="saving"
          @click="saveConfig"
        >
          保存
        </n-button>
      </n-space>
    </header>

    <div v-if="loading" class="loading">
      <n-spin size="large" />
      <span>正在连接 MCP…</span>
    </div>

    <div v-else-if="error && !servers.length" class="error-wrap">
      <n-empty :description="error">
        <template #extra>
          <n-button @click="refreshStatus({ initial: true })">
            重试
          </n-button>
        </template>
      </n-empty>
    </div>

    <div v-else-if="mode === 'form'" class="form-mode">
      <div class="server-form">
        <n-input v-model:value="form.id" :disabled="Boolean(editingServerId)" placeholder="Server ID，如 context7" />
        <n-input v-model:value="form.display_name" placeholder="显示名称" />
        <n-select v-model:value="form.transport" :options="[{ label: 'Streamable HTTP', value: 'streamable_http' }, { label: 'SSE', value: 'sse' }]" />
        <n-input v-model:value="form.url" placeholder="https://example.com/mcp" />
        <n-input v-model:value="form.headers" type="textarea" :rows="2" placeholder="Headers JSON；编辑时留空保留现有敏感值，例如 {&quot;Authorization&quot;:&quot;Bearer …&quot;}" />
        <label class="form-enabled"><n-switch v-model:value="form.enabled" /> 启用</label>
        <n-space><n-button type="primary" :disabled="!form.id || !form.url" @click="saveServer">{{ editingServerId ? '保存修改' : '添加 Server' }}</n-button><n-button v-if="editingServerId" @click="resetServerForm">取消</n-button></n-space>
      </div>
      <n-empty v-if="!servers.length" description="暂无 MCP Server" />
      <div v-for="server in servers" :key="server.id" class="form-server-card">
        <div class="form-server-meta">
          <strong>{{ server.display_name || server.id }}</strong>
          <span>{{ server.url }}</span>
          <n-tag size="small" :type="server.status === 'ok' ? 'success' : server.status === 'error' ? 'error' : 'default'">{{ statusText(server) }}</n-tag>
          <span v-if="server.checked_at" class="checked-at">检查于 {{ new Date(server.checked_at).toLocaleString() }}</span>
          <span v-if="server.message && server.status === 'error'" class="server-card__err">{{ server.message }}</span>
        </div>
        <n-space align="center">
          <n-switch :value="server.enabled" @update:value="value => toggleServer(server, value)" />
          <n-button size="small" @click="editServer(server)">编辑</n-button>
          <n-button size="small" @click="showTools(server)">工具目录</n-button>
          <n-button size="small" type="error" quaternary @click="removeServer(server)">删除</n-button>
        </n-space>
        <ul v-if="visibleTools[server.id]" class="tool-list">
          <li v-for="tool in visibleTools[server.id]" :key="tool.name"><strong>{{ tool.name }}</strong><span>{{ tool.description }}</span></li>
        </ul>
      </div>
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
              <button
                v-for="s in servers"
                :key="s.id"
                type="button"
                class="server-card"
              >
                <span
                  class="server-card__dot"
                  :class="{
                    'server-card__dot--ok': s.status === 'ok',
                    'server-card__dot--err': s.status === 'error',
                    'server-card__dot--pending': s.status === 'unknown',
                  }"
                ></span>
                <div class="server-card__body">
                  <div class="server-card__name">
                    {{ s.display_name || s.id }}
                  </div>
                  <div class="server-card__status">
                    {{ statusText(s) }}
                  </div>
                  <div v-if="s.status === 'error' && s.message" class="server-card__err">
                    {{ s.message }}
                  </div>
                </div>
              </button>
            </div>
          </template>
        </div>
      </n-layout-sider>

      <n-layout-content content-style="padding: 0;" :native-scrollbar="false">
        <div v-if="isMobile" class="mobile-status">
          <div
            v-for="s in servers"
            :key="s.id"
            class="server-card server-card--compact"
          >
            <span
              class="server-card__dot"
              :class="{
                'server-card__dot--ok': s.status === 'ok',
                'server-card__dot--err': s.status === 'error',
              }"
            ></span>
            <div class="server-card__body">
              <div class="server-card__name">
                {{ s.display_name || s.id }}
              </div>
              <div class="server-card__status">
                {{ statusText(s) }}
              </div>
            </div>
          </div>
        </div>

        <div class="editor-pane">
          <div class="editor-pane__head">
            <n-icon :component="CodeSlash" size="18" />
            <span class="editor-pane__title">高级 JSON 配置</span>
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

.form-mode { flex: 1; min-height: 0; overflow: auto; padding: 4px 2px 20px; }
.server-form { display: grid; gap: 10px; max-width: 680px; padding: 16px; margin-bottom: 16px; border: 1px solid var(--noesis-color-border-light, #d4d0c8); border-radius: 10px; }
.form-enabled { display: flex; gap: 8px; align-items: center; }
.form-server-card { display: flex; gap: 14px; justify-content: space-between; align-items: center; flex-wrap: wrap; padding: 14px 4px; border-bottom: 1px solid var(--noesis-color-border-light, #d4d0c8); }
.form-server-meta { display: flex; flex-direction: column; gap: 4px; min-width: 0; font-size: 12px; color: var(--noesis-color-text-muted, #737373); }
.form-server-meta strong { color: var(--noesis-color-text-body, #262626); font-size: 14px; }
.checked-at { font-size: 11px; }
.tool-list { flex-basis: 100%; margin: 4px 0; display: grid; gap: 6px; }
.tool-list li { display: flex; flex-direction: column; font-size: 12px; color: var(--noesis-color-text-muted, #737373); }

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

.server-card {
  display: flex;
  width: 100%;
  gap: 10px;
  align-items: flex-start;
  text-align: left;
  border: none;
  background: transparent;
  border-radius: 8px;
  padding: 10px 8px;
  cursor: default;
  color: inherit;
}

.server-card:hover {
  background: var(--noesis-color-primary-bg-subtle, rgb(17 17 17 / 4%));
}

.server-card--compact {
  background: var(--noesis-color-bg-elevated, #faf8f3);
  border: 1px solid var(--noesis-color-border-light, #d4d0c8);
  margin-bottom: 8px;
}

.server-card__dot {
  width: 8px;
  height: 8px;
  margin-top: 5px;
  border-radius: 999px;
  flex-shrink: 0;
  background: var(--noesis-color-text-muted, #737373);
}

.server-card__dot--ok {
  background: var(--noesis-color-success, #51cf66);
  box-shadow: 0 0 0 3px rgb(81 207 102 / 18%);
}

.server-card__dot--err {
  background: var(--noesis-color-danger, #ff6b6b);
}

.server-card__dot--pending {
  animation: pulse-dot 1.2s ease-in-out infinite;
}

@keyframes pulse-dot {
  50% {
    opacity: 0.35;
  }
}

.server-card__body {
  min-width: 0;
  flex: 1;
}

.server-card__name {
  font-size: 14px;
  font-weight: 560;
  color: var(--noesis-color-text-body, #262626);
}

.server-card__status {
  margin-top: 2px;
  font-size: 12px;
  color: var(--noesis-color-text-muted, #737373);
}

.server-card__err {
  margin-top: 4px;
  font-size: 11px;
  line-height: 1.4;
  color: var(--noesis-color-danger, #ff6b6b);
  word-break: break-word;
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
