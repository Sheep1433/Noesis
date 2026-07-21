<script setup lang="ts">
import type { McpServerStatusItem } from '@/api/mcp'
import { DocumentText, Refresh, Server } from '@vicons/ionicons-v5'
import {
  NButton,
  NEmpty,
  NIcon,
  NLayout,
  NLayoutContent,
  NSpace,
  NSpin,
  NTag,
  NText,
  useMessage,
} from 'naive-ui'
import { computed, onMounted, ref } from 'vue'
import {
  getMcpConfig,
  listMcpServerStatus,
  probeMcpServer,
  saveMcpConfig,
} from '@/api/mcp'
import { useBreakpoint } from '@/hooks/useBreakpoint'

const message = useMessage()
const { isMobile } = useBreakpoint()

const loading = ref(true)
const probing = ref(false)
const saving = ref(false)
const error = ref<string | null>(null)
const servers = ref<McpServerStatusItem[]>([])

const configPath = ref('users/{uid}/mcp.json')
const configExists = ref(false)
const editorText = ref('{\n  "mcpServers": {}\n}\n')
const editorDirty = ref(false)
const showEditor = ref(true)

const platformServers = computed(() => servers.value.filter((s) => s.source === 'platform'))
const userServers = computed(() => servers.value.filter((s) => s.source === 'user'))

onMounted(async () => {
  await Promise.all([loadStatus(false), loadConfig()])
})

async function loadStatus(probe: boolean) {
  if (probe) {
    probing.value = true
  } else {
    loading.value = true
  }
  error.value = null
  try {
    const res = await listMcpServerStatus(probe)
    servers.value = res.servers ?? []
  } catch (e: any) {
    error.value = e.message || '加载失败'
    if (!probe) {
      servers.value = []
    }
  } finally {
    loading.value = false
    probing.value = false
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
    message.success('已保存 mcp.json')
    await loadStatus(false)
  } catch (e: any) {
    message.error(e.message || '保存失败')
  } finally {
    saving.value = false
  }
}

async function refreshProbe() {
  await loadStatus(true)
}

async function probeOne(id: string) {
  try {
    const result = await probeMcpServer(id)
    servers.value = servers.value.map((s) => {
      if (s.id !== id) {
        return s
      }
      return {
        ...s,
        status: result.ok ? 'ok' : 'error',
        tool_count: result.tool_count,
        message: result.message,
      }
    })
  } catch (e: any) {
    message.error(e.message || '探测失败')
  }
}

function statusLabel(s: McpServerStatusItem) {
  if (s.status === 'ok') {
    return s.tool_count > 0 ? `${s.tool_count} tools` : '已连接'
  }
  if (s.status === 'error') {
    return '不可用'
  }
  return '未检测'
}

function statusType(s: McpServerStatusItem): 'success' | 'error' | 'default' {
  if (s.status === 'ok') {
    return 'success'
  }
  if (s.status === 'error') {
    return 'error'
  }
  return 'default'
}
</script>

<template>
  <NLayout class="mcp-page" style="height: 100%">
    <NLayoutContent class="mcp-page__content">
      <div class="mcp-page__header">
        <div>
          <h1 class="mcp-page__title">
            MCP Servers
          </h1>
          <NText depth="3" class="mcp-page__subtitle">
            配置写在用户 mcp.json；本页展示连通状态。会话内启用请在对话 Composer 勾选。
          </NText>
        </div>
        <NSpace>
          <NButton :loading="probing" @click="refreshProbe">
            <template #icon>
              <NIcon :component="Refresh" />
            </template>
            检测连通
          </NButton>
          <NButton
            type="primary"
            :disabled="!editorDirty"
            :loading="saving"
            @click="saveConfig"
          >
            保存配置
          </NButton>
        </NSpace>
      </div>

      <NSpin :show="loading">
        <div v-if="error" class="mcp-page__error">
          {{ error }}
        </div>

        <div class="mcp-page__grid" :class="{ 'mcp-page__grid--stack': isMobile }">
          <section class="mcp-panel">
            <div class="mcp-panel__head">
              <NIcon :component="Server" />
              <span>状态</span>
            </div>

            <div v-if="!servers.length && !loading" class="mcp-panel__empty">
              <NEmpty description="暂无 MCP server。在右侧编辑 mcp.json 后保存。" />
            </div>

            <div v-else class="mcp-server-list">
              <div v-if="platformServers.length" class="mcp-server-group">
                <div class="mcp-server-group__label">
                  平台
                </div>
                <div
                  v-for="s in platformServers"
                  :key="`p-${s.id}`"
                  class="mcp-server-row"
                >
                  <span
                    class="mcp-dot"
                    :class="{
                      'mcp-dot--ok': s.status === 'ok',
                      'mcp-dot--err': s.status === 'error',
                    }"
                  ></span>
                  <div class="mcp-server-row__main">
                    <div class="mcp-server-row__name">
                      {{ s.display_name || s.id }}
                    </div>
                    <div class="mcp-server-row__meta">
                      {{ s.transport }} · {{ s.url || '—' }}
                    </div>
                    <div v-if="s.message && s.status === 'error'" class="mcp-server-row__msg">
                      {{ s.message }}
                    </div>
                  </div>
                  <NTag size="small" :type="statusType(s)" :bordered="false">
                    {{ statusLabel(s) }}
                  </NTag>
                  <NButton text size="tiny" @click="probeOne(s.id)">
                    探测
                  </NButton>
                </div>
              </div>

              <div v-if="userServers.length" class="mcp-server-group">
                <div class="mcp-server-group__label">
                  我的
                </div>
                <div
                  v-for="s in userServers"
                  :key="`u-${s.id}`"
                  class="mcp-server-row"
                >
                  <span
                    class="mcp-dot"
                    :class="{
                      'mcp-dot--ok': s.status === 'ok',
                      'mcp-dot--err': s.status === 'error',
                    }"
                  ></span>
                  <div class="mcp-server-row__main">
                    <div class="mcp-server-row__name">
                      {{ s.display_name || s.id }}
                    </div>
                    <div class="mcp-server-row__meta">
                      {{ s.transport }} · {{ s.url || '—' }}
                    </div>
                    <div v-if="s.message && s.status === 'error'" class="mcp-server-row__msg">
                      {{ s.message }}
                    </div>
                  </div>
                  <NTag size="small" :type="statusType(s)" :bordered="false">
                    {{ statusLabel(s) }}
                  </NTag>
                  <NButton text size="tiny" @click="probeOne(s.id)">
                    探测
                  </NButton>
                </div>
              </div>
            </div>
          </section>

          <section class="mcp-panel mcp-panel--editor">
            <div class="mcp-panel__head">
              <NIcon :component="DocumentText" />
              <span>mcp.json</span>
              <NText depth="3" class="mcp-panel__path">
                {{ configPath }}
                <template v-if="!configExists">
                  （尚未落盘）
                </template>
              </NText>
              <NButton
                v-if="isMobile"
                text
                size="tiny"
                class="mcp-panel__toggle"
                @click="showEditor = !showEditor"
              >
                {{ showEditor ? '收起' : '展开' }}
              </NButton>
            </div>
            <NText depth="3" class="mcp-panel__hint">
              仅支持 transport: streamable_http / sse。平台 server 在 extensions/mcp/mcp.json，不可在此覆盖删除。
            </NText>
            <textarea
              v-show="showEditor"
              class="mcp-editor"
              :value="editorText"
              spellcheck="false"
              @input="onEditorInput(($event.target as HTMLTextAreaElement).value)"
            ></textarea>
          </section>
        </div>
      </NSpin>
    </NLayoutContent>
  </NLayout>
</template>

<style scoped>
.mcp-page__content {
  padding: 20px 24px 32px;
  max-width: 1200px;
  margin: 0 auto;
}

.mcp-page__header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 20px;
  flex-wrap: wrap;
}

.mcp-page__title {
  margin: 0 0 6px;
  font-size: 22px;
  font-weight: 600;
  color: var(--noesis-text-primary, #111);
}

.mcp-page__subtitle {
  font-size: 13px;
}

.mcp-page__error {
  margin-bottom: 12px;
  color: var(--noesis-color-danger, #d03050);
}

.mcp-page__grid {
  display: grid;
  grid-template-columns: minmax(280px, 1fr) minmax(320px, 1.2fr);
  gap: 16px;
  align-items: start;
}

.mcp-page__grid--stack {
  grid-template-columns: 1fr;
}

.mcp-panel {
  border: 1px solid var(--noesis-border-subtle, rgb(0 0 0 / 8%));
  border-radius: var(--noesis-radius-md, 10px);
  background: var(--noesis-bg-elevated, #fff);
  padding: 14px 16px;
  min-height: 200px;
}

.mcp-panel__head {
  display: flex;
  align-items: center;
  gap: 8px;
  font-weight: 600;
  margin-bottom: 10px;
}

.mcp-panel__path {
  margin-left: auto;
  font-size: 12px;
  font-weight: 400;
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
}

.mcp-panel__hint {
  display: block;
  font-size: 12px;
  margin-bottom: 10px;
}

.mcp-panel__empty {
  padding: 24px 0;
}

.mcp-server-group {
  margin-bottom: 16px;
}

.mcp-server-group__label {
  font-size: 12px;
  color: var(--noesis-text-secondary, #6b7280);
  margin-bottom: 8px;
}

.mcp-server-row {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  padding: 10px 8px;
  border-radius: 8px;
}

.mcp-server-row:hover {
  background: var(--noesis-color-primary-bg-subtle, rgb(0 0 0 / 3%));
}

.mcp-server-row__main {
  flex: 1;
  min-width: 0;
}

.mcp-server-row__name {
  font-weight: 500;
  font-size: 14px;
}

.mcp-server-row__meta {
  font-size: 12px;
  color: var(--noesis-text-secondary, #6b7280);
  word-break: break-all;
}

.mcp-server-row__msg {
  margin-top: 4px;
  font-size: 12px;
  color: var(--noesis-color-danger, #d03050);
}

.mcp-dot {
  width: 8px;
  height: 8px;
  margin-top: 6px;
  border-radius: 999px;
  background: var(--noesis-text-tertiary, #9ca3af);
  flex-shrink: 0;
}

.mcp-dot--ok {
  background: #18a058;
}

.mcp-dot--err {
  background: #d03050;
}

.mcp-editor {
  width: 100%;
  min-height: 420px;
  box-sizing: border-box;
  padding: 12px;
  border: 1px solid var(--noesis-border-subtle, rgb(0 0 0 / 10%));
  border-radius: 8px;
  background: var(--noesis-bg-muted, #fafafa);
  color: var(--noesis-text-primary, #111);
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  font-size: 12.5px;
  line-height: 1.5;
  resize: vertical;
}

.mcp-editor:focus {
  outline: 2px solid var(--noesis-color-primary, #2080f0);
  outline-offset: 1px;
}
</style>
